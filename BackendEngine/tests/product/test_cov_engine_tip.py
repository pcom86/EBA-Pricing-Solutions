"""Coverage-focused tests for ``pricing_engine.helpers_tip`` (Term/Temporary
Income Protection).

Strategy
--------
* Load the real basis / assumption parquet snapshots once per interpreter
  (``ctr_loader.get_basis`` / ``get_assumptions`` are ``lru_cache``-backed).
* Derive a guaranteed-consistent TIP pricing scenario from the snapshot
  tables themselves: pick a real ``tip_annuity_factors`` row and back-solve
  the member attributes that the L1..L4 interpolation joins require
  (age-interp, payment-period map, esc loading, wp annuity map, ip loading).
* Drive ``_apply_tip_composite`` (vectorised) and
  ``_apply_tip_composite_legacy`` (row loop) on small synthetic member frames
  and assert expected cover maths, vectorised==legacy equivalence, exclusion
  edge cases and documented error paths.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import polars as pl
import pytest
from ctr_loader.assumption_loader import get_assumptions
from ctr_loader.basis_loader import get_basis
from ctr_loader.ctr_loader_v2 import load_ctr
from ctr_loader.ctr_schemas import SchemeRecord
from pricing_engine.engine import PricingContext, price
from pricing_engine.helpers_tip import (
    _apply_tip_composite,
    _apply_tip_composite_legacy,
    _tip_annuity_fs,
    _tip_build_lookup_tables,
    _tip_esc_str,
)

ROOT = Path(__file__).resolve().parents[3]
BASIS_DIR = ROOT / "BackendEngine" / "basis_snapshots" / "basis_v2026_08"
ASSUMP_DIR = ROOT / "BackendEngine" / "assumption_snapshots" / "assumption_A2026_08"
CTR_TEST_DIR = ROOT / "BackendEngine" / "Test"

CALC_DATE = date(2026, 8, 1)
OUT_COLS = [
    "total_cover",
    "basic_technical_premium",
    "aids_technical_premium",
    "co_premium",
    "waiver_premium",
    "basic_excl_esc",
    "reporting_incidence",
    "reporting_incidence_x_cover",
    "rate_basic",
    "rate_aids",
]


# ---------------------------------------------------------------------------
# fixtures / scenario derivation
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def basis():
    return get_basis(BASIS_DIR)


@pytest.fixture(scope="module")
def assump():
    return get_assumptions(ASSUMP_DIR)


@pytest.fixture(scope="module")
def tip_tables(basis):
    return basis.for_product("tip")


@pytest.fixture(scope="module")
def tip_assump_tables(assump):
    return assump.for_product("tip")


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _nkey(v):
    """Normalise a lookup-table key so float/int/str dtypes compare equal."""
    n = _num(v)
    return n if n is not None else v


@pytest.fixture(scope="module")
def tip_scenario(tip_tables):
    """Derive a self-consistent TIP pricing scenario from the snapshot tables.

    Finds a member configuration where the four annuity interpolation joins
    (L1..L4) all resolve, so the crafted member produces a positive premium.
    """
    B = tip_tables

    af = B["tip_annuity_factors"].filter(pl.col("product") == "tip")
    esc_tbl = B["tip_esc_loading"].filter(pl.col("product") == "tip")
    age_tbl = B["tip_age_interp"].filter(pl.col("product") == "tip")
    pp_tbl = B["tip_payment_period_map"].filter(pl.col("product") == "tip")
    wpann_tbl = B["tip_wp_ann_map"].filter(pl.col("product") == "tip")
    db_tbl = B["tip_db_loading"].filter(pl.col("product") == "tip")
    ip_tbl = B["ip_loading"].filter(pl.col("product") == "gip")
    mort = B["mortality"].filter(pl.col("product") == "gip")
    ind = B["industry"].filter(pl.col("product") == "gip")
    wp_ld = B["wp_loading"].filter(pl.col("product") == "gip")

    if af.height == 0 or esc_tbl.height == 0:
        pytest.skip("tip basis tables missing from snapshot")

    age_map = {int(r["age"]): r for r in age_tbl.iter_rows(named=True)}
    pp_map = {int(r["pp_key"]): r for r in pp_tbl.iter_rows(named=True)}
    ppl_map = {r["pp_label"]: int(r["pp_months"])
               for r in pp_tbl.iter_rows(named=True)
               if r["pp_months"] is not None}
    wpann = {int(r["wp_raw"]): int(r["wp_annuity"])
             for r in wpann_tbl.iter_rows(named=True)}
    wp_raw_by_ann = {v: k for k, v in wpann.items()}
    ip_label_by_months = {}
    for r in ip_tbl.iter_rows(named=True):
        if r["mapped_months"] is not None:
            ip_label_by_months.setdefault(_nkey(r["mapped_months"]),
                                          r["ip_label"])

    esc_rows = [r for r in esc_tbl.iter_rows(named=True)
                if _num(r["esc_pct"]) is not None]

    mort_keys = {
        (int(r["age"]), r["gender"])
        for r in mort.select(["age", "gender", "rate"]).iter_rows(named=True)
        if r["age"] is not None and _num(r["rate"]) is not None
        and float(r["rate"]) > 0
    }
    ind_sizes = {str(s).lower() for s in ind["scheme_size"].to_list()
                 if s is not None}
    rr_rows = (B["rr_loading"].filter(pl.col("product") == "gip")
               .select(["rr_from", "loading"]).sort("rr_from").to_dicts())
    sb_rows = (B["salary_band"].filter(pl.col("product") == "gip")
               .select(["gender", "salary_from"]).to_dicts())
    asb_rows = (B["annuity_salary_bands"].filter(pl.col("product") == "gip")
                .select(["salary_from", "sal_tier"]).to_dicts())
    rr_min = min((r["rr_from"] for r in rr_rows), default=None)
    asb_min = min((r["salary_from"] for r in asb_rows), default=None)
    sb_min = {g: min((r["salary_from"] for r in sb_rows
                     if r["gender"] == g), default=None)
              for g in ("M", "F")}

    # set of all (age, gender, wp, ip, esc_key, scheme_size, max_pp) keys present
    af_rows = {}
    for r in af.iter_rows(named=True):
        af_rows[(r["age"], r["gender"], r["wp"], r["ip"], r["esc_key"],
                 r["scheme_size"], r["max_pp"])] = r

    db_defs = [r["db_def"] for r in db_tbl.iter_rows(named=True)
               if _num(r["loading"]) is not None and float(r["loading"]) != 0.0]

    # reverse lookups for the interpolation joins
    closest_age_to_rows = {}
    for E, arow in age_map.items():
        closest_age_to_rows.setdefault(arow["closest_age"], []).append((E, arow))
    esc_by_lower = {}
    for er in esc_rows:
        esc_by_lower.setdefault(_tip_esc_str(er["map_lower"]), er)
    pp_by_closest = {}
    for k, prow in pp_map.items():
        if prow["closest_pp"] is not None and prow["interp_pp"] is not None:
            pp_by_closest.setdefault(prow["closest_pp"], []).append((k, prow))
    pp_months_avail = {int(m) for m in ppl_map.values()}
    pp_label_by_months = {int(m): l for l, m in ppl_map.items()}

    # iterate real annuity-factor rows (L1 hits by construction) and require
    # the L2/L3/L4 interpolation partners to exist too.
    found = None
    for row in af.iter_rows(named=True):
        size = row["scheme_size"]
        if not isinstance(size, str) or size.lower() not in ind_sizes:
            continue
        er = esc_by_lower.get(str(row["esc_key"]))
        if er is None:
            continue
        wraw = wp_raw_by_ann.get(_nkey(row["wp"]))
        if wraw is None:
            continue
        if _nkey(row["ip"]) not in ip_label_by_months:
            continue
        # asof lookups must reach a band at/below the member values
        if rr_min is not None and rr_min > 0.75:
            continue
        if asb_min is not None and asb_min > 480_000.0:
            continue
        if sb_min.get(row["gender"]) is None or sb_min[row["gender"]] > 480_000.0:
            continue
        WU = _tip_esc_str(er["map_upper"])
        WT = _tip_esc_str(er["map_lower"])
        WD = row["age"]
        gender = row["gender"]
        wann = row["wp"]
        ipm = row["ip"]
        for E, arow in closest_age_to_rows.get(WD, []):
            if E < 17 or (E, gender) not in mort_keys:
                continue
            # a wp_loading factor must exist for this waiting period and a
            # member of age E (else BG is null -> premium 0)
            if wp_ld.filter(
                    (pl.col("scheme_size") == size.lower())
                    & (pl.col("wp_months") == wraw)
                    & (pl.col("age_band_from") <= E)).height == 0:
                continue
            WE = arow["interp_age"]
            for pp_key, prow in pp_by_closest.get(row["max_pp"], []):
                if pp_key not in pp_months_avail:
                    continue
                WM = prow["interp_pp"]
                k2 = (WE, gender, wann, ipm, WT, size, row["max_pp"])
                k3 = (WD, gender, wann, ipm, WT, size, WM)
                k4 = (WD, gender, wann, ipm, WU, size, row["max_pp"])
                partners = [af_rows.get(k) for k in (k2, k3, k4)]
                if any(p is None for p in partners):
                    continue
                # every interpolation source row needs a usable factor in
                # whichever salary tier column the member resolves to
                if any(p[c] is None
                       for p in [row] + partners
                       for c in ("sal_1", "sal_2", "sal_3")):
                    continue
                M = int(E + (pp_key + wraw + 11) // 12 + 1)
                if (M - (E - 0.5)) * 12 < wraw:
                    continue
                found = SimpleNamespace(
                    E=E, gender=gender, size=size, M=M,
                    esc_pct=float(er["esc_pct"]), wp_raw=wraw,
                    ip_label=ip_label_by_months[_nkey(ipm)], pp_key=pp_key,
                )
                break
            if found:
                break
        if found:
            break

    if found is None:
        pytest.skip("no consistent TIP annuity interpolation chain found")

    pp_label = pp_label_by_months[found.pp_key]

    secondary = (ind.filter(pl.col("scheme_size") == found.size.lower())
                 ["secondary"][0])

    return SimpleNamespace(
        E=found.E, gender=found.gender, size=found.size, M=found.M,
        esc_pct=found.esc_pct, wp_raw=found.wp_raw, ip_label=found.ip_label,
        pp_label=pp_label, db_def=db_defs[0] if db_defs else "",
        secondary=secondary,
    )


# ---------------------------------------------------------------------------
# builders
# ---------------------------------------------------------------------------

def _member(no, cat, *, age, age_nb, cease, nra, nra_final="__same__", salary,
            gender, waiver=0.0, monthly=None, active=True):
    return {
        "member_no": no,
        "category_number": cat,
        "product_active": active,
        "age": float(age),
        "ceasing_age": float(cease),
        "nra_final": float(nra) if nra_final == "__same__" else nra_final,
        "nra": float(nra),
        "annual_salary": float(salary),
        "monthly_salary": monthly,
        "waiver": float(waiver),
        "age_nb": int(age_nb),
        "gender": gender,
    }


def _scheme(s, cat_params):
    def m(field, default):
        return {c: p.get(field, default) for c, p in cat_params.items()}

    return SchemeRecord(
        secondary=s.secondary,
        pear_tgp_pct=1.0,
        tip_core_benefit_perc=m("perc", [0.75, 0.0, 0.0, 0.0]),
        tip_core_benefit_layer=m("layer", [1e9, 1e9, 1e9, 1e9]),
        tip_waiting_period=m("wp", f"{s.wp_raw} months"),
        tip_initial_period=m("ip", s.ip_label),
        tip_core_disability_definition=m("core_def", s.db_def),
        tip_core_benefit_perc_additional=m("add_p", 0.0),
        tip_core_benefit_layer_additional=m("add_l", 0.0),
        tip_core_overall_maximum=m("ac", 1e9),
        tip_employer_waiver=m("emp_w", 0.0),
        tip_waiver_pct_specified=m("wspec", "N/A"),
        tip_tax_options=m("tax_opt", "As per data"),
        tip_escalation_pct=m("esc", s.esc_pct),
        tip_payment_period=m("pp", s.pp_label),
    )


def _ctx(scheme, size, member_count=10):
    return PricingContext(
        scheme=scheme,
        globals={},
        calc_date=CALC_DATE,
        member_count=member_count,
        scheme_size=size,
    )


def _equiv_frame(a: pl.DataFrame, b: pl.DataFrame, cols) -> None:
    ja = a.select(["member_no"] + cols).sort("member_no")
    jb = b.select(["member_no"] + cols).sort("member_no")
    assert ja.height == jb.height
    for c in cols:
        va, vb = ja[c].to_list(), jb[c].to_list()
        for x, y in zip(va, vb):
            if isinstance(x, bool) or isinstance(y, bool):
                assert bool(x) == bool(y), f"column {c}: {x!r} != {y!r}"
            else:
                assert float(x or 0.0) == pytest.approx(float(y or 0.0), rel=1e-6, abs=1e-9), \
                    f"column {c}: {x!r} != {y!r}"


# ---------------------------------------------------------------------------
# vectorised composite
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_tip_composite_prices_included_member(tip_scenario, tip_tables,
                                              tip_assump_tables):
    s = tip_scenario
    scheme = _scheme(s, {1: {}})
    J = 40_000.0
    df = pl.DataFrame([
        _member(1, 1, age=s.E - 0.5, age_nb=s.E, cease=s.M, nra=s.M,
                salary=J * 12, gender=s.gender),
    ])
    out = _apply_tip_composite(df, {"kind": "tip_composite"}, tip_tables,
                               tip_assump_tables, _ctx(scheme, s.size))

    assert out.height == 1
    for c in OUT_COLS + ["product_active", "reporting_over_cease"]:
        assert c in out.columns, f"missing output column {c}"
    row = out.row(0, named=True)
    assert row["total_cover"] == pytest.approx(0.75 * J, rel=1e-9)
    assert row["basic_technical_premium"] > 0.0
    assert row["basic_technical_premium"] == pytest.approx(
        row["total_cover"] * row["rate_basic"] / 1000.0, rel=1e-9)
    assert row["aids_technical_premium"] == 0.0
    assert row["co_premium"] == 0.0
    assert row["waiver_premium"] == 0.0
    assert row["basic_excl_esc"] >= 0.0
    assert row["product_active"] is True


@pytest.mark.unit
def test_tip_composite_monthly_salary_and_nra_fallback(tip_scenario,
                                                       tip_tables,
                                                       tip_assump_tables):
    s = tip_scenario
    scheme = _scheme(s, {1: {}})
    df = pl.DataFrame([
        # monthly_salary present -> J = monthly, not annual/12
        _member(1, 1, age=s.E - 0.5, age_nb=s.E, cease=s.M, nra=s.M,
                salary=600_000.0, monthly=30_000.0, gender=s.gender),
        # monthly_salary = 0 -> falls back to annual/12
        _member(2, 1, age=s.E - 0.5, age_nb=s.E, cease=s.M, nra=s.M,
                salary=480_000.0, monthly=0.0, gender=s.gender),
        # nra_final null -> falls back to 'nra' column
        _member(3, 1, age=s.E - 0.5, age_nb=s.E, cease=s.M, nra=s.M,
                nra_final=None, salary=480_000.0, gender=s.gender),
    ])
    out = _apply_tip_composite(df, {"kind": "tip_composite"}, tip_tables,
                               tip_assump_tables, _ctx(scheme, s.size))
    rows = {r["member_no"]: r for r in out.iter_rows(named=True)}

    assert rows[1]["total_cover"] == pytest.approx(0.75 * 30_000, rel=1e-9)
    assert rows[2]["total_cover"] == pytest.approx(0.75 * 40_000, rel=1e-9)
    assert rows[3]["total_cover"] == pytest.approx(0.75 * 40_000, rel=1e-9)


@pytest.mark.unit
def test_tip_composite_tax_option_variants(tip_scenario, tip_tables,
                                           tip_assump_tables):
    s = tip_scenario
    scheme = _scheme(s, {
        1: {"tax_opt": "As per data"},
        2: {"tax_opt": "Apply net-of-tax maximum"},
        3: {"tax_opt": "Tax Replicator"},
    })
    J = 40_000.0
    df = pl.DataFrame([
        _member(1, 1, age=s.E - 0.5, age_nb=s.E, cease=s.M, nra=s.M,
                salary=J * 12, gender=s.gender),
        _member(2, 2, age=s.E - 0.5, age_nb=s.E, cease=s.M, nra=s.M,
                salary=J * 12, gender=s.gender),
        _member(3, 3, age=s.E - 0.5, age_nb=s.E, cease=s.M, nra=s.M,
                salary=J * 12, gender=s.gender),
    ])
    out = _apply_tip_composite(df, {"kind": "tip_composite"}, tip_tables,
                               tip_assump_tables, _ctx(scheme, s.size))
    rows = {r["member_no"]: r for r in out.iter_rows(named=True)}

    gross = 0.75 * J
    assert rows[1]["total_cover"] == pytest.approx(gross, rel=1e-9)
    assert 0.0 < rows[2]["total_cover"] <= gross + 1e-9
    # Tax Replicator: AX = min(net-of-tax TH, AB) -> strictly below gross here
    assert 0.0 < rows[3]["total_cover"] < gross


@pytest.mark.unit
def test_tip_composite_waiver_and_layer_variants(tip_scenario, tip_tables,
                                                 tip_assump_tables):
    s = tip_scenario
    scheme = _scheme(s, {
        1: {"wspec": "As Per Data"},
        2: {"wspec": "Same For All", "emp_w": 0.10},
        3: {"perc": [0.50, 0.25, 0.0, 0.0],
            "layer": [20_000.0, 1e9, 1e9, 1e9],
            "add_p": 0.05, "add_l": 10_000.0, "ac": 25_000.0},
    })
    J = 40_000.0
    df = pl.DataFrame([
        _member(1, 1, age=s.E - 0.5, age_nb=s.E, cease=s.M, nra=s.M,
                salary=J * 12, gender=s.gender, waiver=0.10),
        _member(2, 2, age=s.E - 0.5, age_nb=s.E, cease=s.M, nra=s.M,
                salary=J * 12, gender=s.gender),
        _member(3, 3, age=s.E - 0.5, age_nb=s.E, cease=s.M, nra=s.M,
                salary=J * 12, gender=s.gender),
    ])
    out = _apply_tip_composite(df, {"kind": "tip_composite"}, tip_tables,
                               tip_assump_tables, _ctx(scheme, s.size))
    rows = {r["member_no"]: r for r in out.iter_rows(named=True)}

    assert rows[1]["total_cover"] == pytest.approx(0.85 * J, rel=1e-9)
    assert rows[2]["total_cover"] == pytest.approx(0.85 * J, rel=1e-9)
    # 0.5*20000 + 0.25*20000 + 0.05*10000 = 15500 -> capped by AB=25000? no,
    # below cap: total = 15500
    assert rows[3]["total_cover"] == pytest.approx(15_500.0, rel=1e-9)


@pytest.mark.unit
def test_tip_composite_excludes_ineligible_members(tip_scenario, tip_tables,
                                                   tip_assump_tables):
    s = tip_scenario
    scheme = _scheme(s, {1: {}})
    members = [
        _member(1, 1, age=s.E - 0.5, age_nb=s.E, cease=s.M, nra=s.M,
                salary=480_000.0, gender=s.gender),
        _member(2, 1, age=15.0, age_nb=16, cease=s.M, nra=s.M,
                salary=480_000.0, gender=s.gender),            # under 16
        _member(3, 1, age=s.M + 2.0, age_nb=int(s.M) + 3, cease=s.M,
                nra=s.M, salary=480_000.0, gender=s.gender),  # over cease
        _member(4, 1, age=s.M - (s.wp_raw - 1.0) / 12.0, age_nb=s.E,
                cease=s.M, nra=s.M, salary=480_000.0,
                gender=s.gender),                             # < WP to NRD
        _member(5, 1, age=s.E - 0.5, age_nb=s.E, cease=s.M, nra=s.M,
                salary=480_000.0, gender=s.gender, active=False),
        _member(6, None, age=s.E - 0.5, age_nb=s.E, cease=s.M, nra=s.M,
                salary=480_000.0, gender=s.gender),
    ]
    df = pl.DataFrame(members)
    out = _apply_tip_composite(df, {"kind": "tip_composite"}, tip_tables,
                               tip_assump_tables, _ctx(scheme, s.size))
    rows = {r["member_no"]: r for r in out.iter_rows(named=True)}

    assert rows[1]["total_cover"] > 0.0
    for m in (2, 3, 4, 5, 6):
        assert rows[m]["total_cover"] == 0.0
        assert rows[m]["basic_technical_premium"] == 0.0
        assert rows[m]["rate_basic"] == 0.0

    # excluded-but-active members are deactivated
    assert rows[2]["product_active"] is False
    assert rows[3]["product_active"] is False
    assert rows[4]["product_active"] is False
    # zero-branch members (no category / already inactive) keep reporting flag
    # semantics: over-cease only reported for genuinely excluded members
    assert rows[3]["reporting_over_cease"] is True
    assert rows[6]["reporting_over_cease"] is False
    assert rows[5]["reporting_over_cease"] is False


@pytest.mark.unit
def test_tip_composite_unknown_industry_raises(tip_scenario, tip_tables,
                                               tip_assump_tables):
    s = tip_scenario
    scheme = _scheme(s, {1: {}})
    scheme = replace(scheme, secondary="__NO_SUCH_INDUSTRY__")
    df = pl.DataFrame([
        _member(1, 1, age=s.E - 0.5, age_nb=s.E, cease=s.M, nra=s.M,
                salary=480_000.0, gender=s.gender),
    ])
    with pytest.raises(KeyError, match="industry"):
        _apply_tip_composite(df, {"kind": "tip_composite"}, tip_tables,
                             tip_assump_tables, _ctx(scheme, s.size))
    with pytest.raises(KeyError, match="industry"):
        _apply_tip_composite_legacy(df, {"kind": "tip_composite"}, tip_tables,
                                    tip_assump_tables, _ctx(scheme, s.size))


# ---------------------------------------------------------------------------
# vectorised vs legacy equivalence
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_tip_vectorised_matches_legacy(tip_scenario, tip_tables,
                                       tip_assump_tables):
    s = tip_scenario
    scheme = _scheme(s, {
        1: {"tax_opt": "As per data"},
        2: {"tax_opt": "Apply net-of-tax maximum", "wspec": "As Per Data"},
        3: {"tax_opt": "Tax Replicator", "wspec": "Same For All",
            "emp_w": 0.10},
        4: {"perc": [0.6, 0.2, 0.1, 0.0],
            "layer": [10_000.0, 20_000.0, 30_000.0, 1e9],
            "add_p": 0.02, "add_l": 5_000.0},
    })
    J = 40_000.0
    members = [
        _member(1, 1, age=s.E - 0.5, age_nb=s.E, cease=s.M, nra=s.M,
                salary=J * 12, gender=s.gender),
        _member(2, 2, age=s.E - 0.5, age_nb=s.E, cease=s.M, nra=s.M,
                salary=J * 12, gender=s.gender, waiver=0.10),
        _member(3, 3, age=s.E - 0.5, age_nb=s.E, cease=s.M, nra=s.M,
                salary=J * 12, gender=s.gender, monthly=35_000.0),
        _member(4, 4, age=s.E - 0.5, age_nb=s.E, cease=s.M, nra=s.M,
                salary=J * 12, gender=s.gender),
        # excluded members
        _member(5, 1, age=s.M - (s.wp_raw - 1.0) / 12.0, age_nb=s.E,
                cease=s.M, nra=s.M, salary=J * 12, gender=s.gender),
        _member(6, 1, age=s.M + 1.5, age_nb=int(s.M) + 2, cease=s.M,
                nra=s.M, salary=J * 12, gender=s.gender),
        _member(7, 1, age=s.E - 0.5, age_nb=s.E, cease=s.M, nra=s.M,
                salary=J * 12, gender=s.gender, active=False),
        _member(8, None, age=s.E - 0.5, age_nb=s.E, cease=s.M, nra=s.M,
                salary=J * 12, gender=s.gender),
    ]
    df = pl.DataFrame(members)
    spec = {"kind": "tip_composite"}
    ctx = _ctx(scheme, s.size)

    out_v = _apply_tip_composite(df.clone(), spec, tip_tables,
                                 tip_assump_tables, ctx)
    out_l = _apply_tip_composite_legacy(df.clone(), spec, tip_tables,
                                        tip_assump_tables, ctx)

    _equiv_frame(out_v, out_l, [
        "total_cover", "basic_technical_premium", "basic_excl_esc",
        "reporting_incidence", "reporting_incidence_x_cover", "rate_basic",
        "product_active", "reporting_over_cease",
    ])


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_tip_esc_str():
    assert _tip_esc_str(None) == "0"
    assert _tip_esc_str("Uncapped") == "Uncapped"
    assert _tip_esc_str("uncapped") == "Uncapped"
    assert _tip_esc_str(5.0) == "5"
    assert _tip_esc_str("5.0") == "5"
    assert _tip_esc_str("abc") == "abc"


@pytest.mark.unit
def test_tip_annuity_fs_edges():
    # WB == 0 -> annuity factor is zero
    res = _tip_annuity_fs({}, "sal_1", 30, "M", 3, 12, "Small",
                          age_row={"closest_age": 30, "interp_age": 31,
                                   "age_factor": 0.5},
                          pp_row={"closest_pp": 12, "interp_pp": 24,
                                  "pp_factor": 0.5},
                          esc_row={"map_lower": "0", "map_upper": "5",
                                   "interp_adj": 0.0,
                                   "progression_loading": 0.0},
                          WB=0)
    assert res == 0.0


@pytest.mark.unit
def test_tip_build_lookup_tables_contents(tip_scenario, tip_tables,
                                          tip_assump_tables):
    s = tip_scenario
    scheme = _scheme(s, {1: {}})
    LT = _tip_build_lookup_tables(tip_tables, tip_assump_tables,
                                  _ctx(scheme, s.size))
    for key in ("mort_map", "sb_by_g", "rr_rows", "wp_rows", "asb_rows",
                    "ip_mm", "tip_db", "af_map", "age_map", "pp_map",
                    "ppl_map", "esc_map", "wpann", "bands"):
        assert key in LT
    assert LT["size_t"] == s.size
    assert (s.E, s.gender) in LT["mort_map"]
    assert s.db_def in LT["tip_db"]
    assert s.ip_label in LT["ip_mm"]
    assert s.pp_label in LT["ppl_map"]
    assert s.wp_raw in LT["wpann"]


# ---------------------------------------------------------------------------
# real CTR integration (skipped when no suitable workbook is present)
# ---------------------------------------------------------------------------

def _first_ctr_with(product_key):
    if not CTR_TEST_DIR.is_dir():
        return None
    for path in sorted(CTR_TEST_DIR.rglob("*.ctr")):
        try:
            ctr = load_ctr(path)
        except Exception:  # noqa: BLE001, S112 - skip unparsable CTR fixtures
            continue
        if product_key in ctr.active_products:
            return ctr
    return None


@pytest.mark.unit
def test_tip_priced_from_real_ctr_when_available(basis, assump):
    ctr = _first_ctr_with("tip")
    if ctr is None:
        pytest.skip("no bundled CTR workbook with TIP active")
    members = ctr.members.get("tip_sheet")
    if members is None or members.height == 0:
        pytest.skip("CTR has no populated 'Member Data - TIP' sheet")

    threshold = assump.globals.get("scheme_size_large_threshold", 400)
    ctx = PricingContext(
        scheme=ctr.scheme,
        globals=assump.globals,
        calc_date=ctr.scheme.commencement_date or CALC_DATE,
        member_count=members.height,
        scheme_size="Large" if members.height >= threshold else "Small",
        secondary_industry=ctr.scheme.secondary or "",
        primary_industry=ctr.scheme.primary or "",
        region_selected=ctr.scheme.region or "",
    )
    out = price("tip", ctr, basis, assump, ctx)

    assert out.height == members.height
    for c in ["total_cover", "basic_technical_premium", "rate_basic"]:
        assert c in out.columns
        assert out[c].null_count() == 0
    assert out["basic_technical_premium"].sum() >= 0.0
    active = out.filter(pl.col("total_cover") > 0)
    assert (active["basic_technical_premium"]
            / active["total_cover"] * 1000
            - active["rate_basic"]).abs().max() < 1e-6
