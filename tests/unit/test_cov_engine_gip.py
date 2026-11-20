"""Coverage-focused tests for ``pricing_engine.helpers_gip`` (Group Income Protection).

Strategy
--------
* Load the real basis / assumption parquet snapshots once per interpreter
  (``ctr_loader.get_basis`` / ``get_assumptions`` are ``lru_cache``-backed).
* Derive a guaranteed-consistent pricing scenario from the snapshot tables
  themselves (annuity-factor composite key -> wp / ip / nra / esc / industry
  lookup values) instead of hard-coding product-specific labels.
* Drive ``_apply_gip_composite`` (vectorised) and
  ``_apply_gip_composite_legacy`` (row loop) on small synthetic member frames
  and assert:
    - expected cover maths for the configured categories,
    - vectorised == legacy outputs (the vectorised path was written as a
      drop-in replacement for the legacy loop and is validated against it),
    - exclusion / deactivation edge cases,
    - documented error paths and the small pure helpers.
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
from pricing_engine.helpers_gip import (
    _apply_gip_composite,
    _apply_gip_composite_legacy,
    _gip_annuity_fs,
    _gip_annuity_fs_fast,
    _gip_backdating_loading,
    _gip_build_lookup_tables,
    _gip_eomonth,
    _gip_esc_key,
    _gip_yearfrac_30_360,
)

ROOT = Path(__file__).resolve().parents[2]
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
def gip_tables(basis):
    return basis.for_product("gip")


@pytest.fixture(scope="module")
def gip_assump_tables(assump):
    return assump.for_product("gip")


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
def gip_scenario(gip_tables):
    """Derive a self-consistent GIP pricing scenario from the snapshot tables.

    Picks a real ``annuity_factors`` row and back-solves every lookup the
    engine performs so the crafted member actually prices (non-null FO/FS).
    """
    B = gip_tables

    af = B["annuity_factors"].filter(pl.col("product") == "gip")
    esc_tbl = B["annuity_esc_loading"].filter(pl.col("product") == "gip")
    nra_tbl = B["annuity_nra_interpolation"].filter(pl.col("product") == "gip")
    wpann_tbl = B["wp_annuity_mapping"].filter(pl.col("product") == "gip")
    ip_tbl = B["ip_loading"].filter(pl.col("product") == "gip")
    mort = B["mortality"].filter(pl.col("product") == "gip")
    ind = B["industry"].filter(pl.col("product") == "gip")
    wp_ld = B["wp_loading"].filter(pl.col("product") == "gip")

    esc_rows = list(esc_tbl.iter_rows(named=True))
    if not any(_num(r["esc_pct"]) == 0.0 for r in esc_rows):
        pytest.skip("gip annuity_esc_loading has no 0% escalation row "
                    "(vectorised FS0 path requires it)")

    # esc_key in the annuity table == str(int(round(esc_pct * 100)))
    esc_by_key = {}
    for r in esc_rows:
        p = _num(r["esc_pct"])
        if p is not None:
            esc_by_key.setdefault(str(int(round(p * 100))), p)

    nra_map = {int(r["nra"]): r for r in nra_tbl.iter_rows(named=True)}
    # af.nra stores the interpolation table's age_lookup value; map it back
    # to the member's nra key (M), keyed by float for dtype-robust lookup.
    nra_by_lookup = {}
    for k, r in nra_map.items():
        if _num(r["age_lookup"]) is not None:
            nra_by_lookup[float(r["age_lookup"])] = k

    wpann = {int(r["wp_raw"]): int(r["wp_annuity"])
             for r in wpann_tbl.iter_rows(named=True)}
    wp_raw_by_ann = {v: k for k, v in wpann.items()}

    ip_label_by_months = {}
    for r in ip_tbl.iter_rows(named=True):
        if r["mapped_months"] is not None:
            ip_label_by_months.setdefault(_nkey(r["mapped_months"]),
                                          r["ip_label"])

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

    cand = af.filter(
        pl.col("esc_key").cast(pl.Utf8).is_in(list(esc_by_key))
        & pl.col("nra").cast(pl.Float64).is_in(
            [float(k) for k in nra_by_lookup if _num(k) is not None])
        & pl.col("wp").cast(pl.Float64).is_in(
            [float(k) for k in wp_raw_by_ann])
        & pl.col("ip").cast(pl.Float64).is_in(
            [float(k) for k in ip_label_by_months if _num(k) is not None])
        & pl.col("sal_1").is_not_null()
        & pl.col("sal_2").is_not_null()
        & pl.col("sal_3").is_not_null()
    )
    chosen = None
    for row in cand.iter_rows(named=True):
        size = row["scheme_size"]
        E = int(row["age"])
        wraw = wp_raw_by_ann[int(float(row["wp"]))]
        M = int(nra_by_lookup[float(row["nra"])])
        if not isinstance(size, str) or size.lower() not in ind_sizes:
            continue
        if (E, row["gender"]) not in mort_keys:
            continue
        # a wp_loading row must exist with age_band_from <= E for this
        # size/waiting-period, otherwise the BK factor is null -> premium 0
        if wp_ld.filter(
                (pl.col("scheme_size") == size.lower())
                & (pl.col("wp_months") == wraw)
                & (pl.col("age_band_from") <= E)).height == 0:
            continue
        # the replacement-ratio and salary-band asof lookups must hit a
        # band at or below the member's values, else the factors are null
        if rr_rows and min(r["rr_from"] for r in rr_rows) > 0.75:
            continue
        if not any(r["salary_from"] <= 480_000.0
                   for r in sb_rows if r["gender"] == row["gender"]):
            continue
        # need a plausible member: >=16 at commencement and enough months
        # to NRD to clear the waiting period.
        if E < 20 or (M - (E - 0.5)) * 12 < wraw:
            continue
        chosen = row
        break
    if chosen is None:
        pytest.skip("no fully consistent gip annuity_factors row found "
                    "in basis snapshot")

    db_ld = B["db_loading"].filter(pl.col("product") == "gip")
    db_key_rows = [r for r in db_ld.iter_rows(named=True)
                   if _num(r["loading"]) is not None
                   and float(r["loading"]) != 0.0]
    db_key = (db_key_rows[0]["combined_key"] if db_key_rows
              else db_ld["combined_key"][0])
    secondary = (ind.filter(pl.col("scheme_size") == chosen["scheme_size"].lower())
                 ["secondary"][0])

    return SimpleNamespace(
        af_row=chosen,
        size=chosen["scheme_size"],
        E=int(chosen["age"]),
        gender=chosen["gender"],
        M=int(nra_by_lookup[float(chosen["nra"])]),
        wp_raw=int(wp_raw_by_ann[int(float(chosen["wp"]))]),
        ip_label=ip_label_by_months[_nkey(chosen["ip"])],
        esc_pct=float(esc_by_key[str(chosen["esc_key"])]),
        db_key=db_key,
        secondary=secondary,
    )


# ---------------------------------------------------------------------------
# builders
# ---------------------------------------------------------------------------

def _member(no, cat, *, age, age_nb, cease, nra, salary, gender,
            waiver=0.0, active=True, hcc=0.0, dob=date(1985, 6, 15)):
    return {
        "member_no": no,
        "category_number": cat,
        "product_active": active,
        "age": float(age),
        "ceasing_age": float(cease),
        "nra_final": float(nra),
        "annual_salary": float(salary),
        "waiver": float(waiver),
        "age_nb": int(age_nb),
        "gender": gender,
        "date_of_birth": dob,
        "hcc_waiver": float(hcc),
    }


def _scheme(s, cat_params):
    """SchemeRecord with gip_* config for each category in ``cat_params``."""
    def m(field, default):
        return {c: p.get(field, default) for c, p in cat_params.items()}

    return SchemeRecord(
        secondary=s.secondary,
        pear_tgp_pct=1.0,
        gip_core_benefit_perc=m("perc", [0.75, 0.0, 0.0, 0.0]),
        gip_core_benefit_layer=m("layer", [1e9, 1e9, 1e9, 1e9]),
        gip_waiting_period=m("wp", f"{s.wp_raw} months"),
        gip_initial_period=m("ip", s.ip_label),
        gip_core_disability_definition=m("core_def", ""),
        gip_hcc_disability_definition=m("ext_def", s.db_key),
        gip_core_benefit_perc_additional=m("add_p", 0.0),
        gip_core_benefit_layer_additional=m("add_l", 0.0),
        gip_core_overall_maximum=m("ac", 1e9),
        gip_employer_waiver=m("emp_w", 0.0),
        gip_waiver_pct_specified=m("wspec", "N/A"),
        gip_tax_options=m("tax_opt", "As per data"),
        gip_escalation_pct=m("esc", s.esc_pct),
        gip_backdating=m("bd", "None"),
        gip_include_hcc_waiver=m("hcc_on", "No"),
        gip_hcc_payment_period=m("hcc_pp", 0.0),
        gip_hcc_payment_type=m("hcc_pt", "Nominal Amount"),
        gip_hcc_nominal_amount=m("hcc_nom", 0.0),
        gip_hcc_escalation_pct=m("hcc_esc", 0.0),
        gip_include_conversion_option=m("co", "No"),
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
    """Assert two priced frames agree on the named columns (per member)."""
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
def test_gip_composite_prices_included_member(gip_scenario, gip_tables,
                                              gip_assump_tables):
    s = gip_scenario
    scheme = _scheme(s, {1: {}})
    J = 40_000.0
    df = pl.DataFrame([
        _member(1, 1, age=s.E - 0.5, age_nb=s.E, cease=s.M, nra=s.M,
                salary=J * 12, gender=s.gender),
    ])
    out = _apply_gip_composite(df, {"kind": "gip_composite"}, gip_tables,
                               gip_assump_tables, _ctx(scheme, s.size))

    assert out.height == 1
    for c in OUT_COLS + ["product_active", "reporting_over_cease"]:
        assert c in out.columns, f"missing output column {c}"
    row = out.row(0, named=True)
    # 75% of monthly salary; overall maximum does not bind.
    assert row["total_cover"] == pytest.approx(0.75 * J, rel=1e-9)
    assert row["basic_technical_premium"] > 0.0
    assert row["basic_technical_premium"] == pytest.approx(
        row["total_cover"] * row["rate_basic"] / 1000.0, rel=1e-9)
    assert row["aids_technical_premium"] == 0.0
    assert row["rate_aids"] == 0.0
    assert row["product_active"] is True
    assert row["reporting_over_cease"] is False
    # incidence is a rate-like quantity; premium = incidence * cover * FS
    assert row["reporting_incidence"] >= 0.0
    assert row["basic_excl_esc"] >= 0.0


@pytest.mark.unit
def test_gip_composite_tax_option_variants(gip_scenario, gip_tables,
                                           gip_assump_tables):
    s = gip_scenario
    scheme = _scheme(s, {
        1: {"tax_opt": "As per data"},                # BA = CQ (gross cover)
        2: {"tax_opt": "Apply net-of-tax maximum"},   # BA = CW
        3: {"tax_opt": "Tax Replicator"},             # BA = CK
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
    out = _apply_gip_composite(df, {"kind": "gip_composite"}, gip_tables,
                               gip_assump_tables, _ctx(scheme, s.size))
    rows = {r["member_no"]: r for r in out.iter_rows(named=True)}

    gross = 0.75 * J
    assert rows[1]["total_cover"] == pytest.approx(gross, rel=1e-9)
    # net-of-tax maximum can only reduce the cover
    assert 0.0 < rows[2]["total_cover"] <= gross + 1e-9
    # tax replicator nets tax off the grossed cover -> strictly below gross
    assert 0.0 < rows[3]["total_cover"] < gross
    for r in rows.values():
        assert r["basic_technical_premium"] >= 0.0


@pytest.mark.unit
def test_gip_composite_waiver_spec_variants(gip_scenario, gip_tables,
                                            gip_assump_tables):
    s = gip_scenario
    scheme = _scheme(s, {
        1: {"wspec": "N/A"},
        2: {"wspec": "As Per Data"},
        3: {"wspec": "Same For All", "emp_w": 0.10},
        4: {"wspec": "Something Unrecognised"},
    })
    J = 40_000.0
    df = pl.DataFrame([
        _member(1, 1, age=s.E - 0.5, age_nb=s.E, cease=s.M, nra=s.M,
                salary=J * 12, gender=s.gender),
        _member(2, 2, age=s.E - 0.5, age_nb=s.E, cease=s.M, nra=s.M,
                salary=J * 12, gender=s.gender, waiver=0.10),
        _member(3, 3, age=s.E - 0.5, age_nb=s.E, cease=s.M, nra=s.M,
                salary=J * 12, gender=s.gender),
        _member(4, 4, age=s.E - 0.5, age_nb=s.E, cease=s.M, nra=s.M,
                salary=J * 12, gender=s.gender, waiver=0.10),
    ])
    out = _apply_gip_composite(df, {"kind": "gip_composite"}, gip_tables,
                               gip_assump_tables, _ctx(scheme, s.size))
    rows = {r["member_no"]: r for r in out.iter_rows(named=True)}

    assert rows[1]["total_cover"] == pytest.approx(0.75 * J, rel=1e-9)
    # 'As Per Data' adds the member waiver percentage to the benefit layer
    assert rows[2]["total_cover"] == pytest.approx(0.85 * J, rel=1e-9)
    # 'Same For All' adds the employer waiver percentage
    assert rows[3]["total_cover"] == pytest.approx(0.85 * J, rel=1e-9)
    # unrecognised spec falls through to base + 0
    assert rows[4]["total_cover"] == pytest.approx(0.75 * J, rel=1e-9)


@pytest.mark.unit
def test_gip_composite_benefit_layers_and_caps(gip_scenario, gip_tables,
                                               gip_assump_tables):
    s = gip_scenario
    scheme = _scheme(s, {
        1: {"perc": [0.50, 0.25, 0.0, 0.0],
            "layer": [20_000.0, 1e9, 1e9, 1e9]},                     # 2 layers
        2: {"perc": [0.75, 0.0, 0.0, 0.0], "layer": [1e9] * 4,
            "ac": 25_000.0},                                        # AC binds
        3: {"perc": [0.75, 0.0, 0.0, 0.0], "layer": [1e9] * 4,
            "add_p": 0.05, "add_l": 10_000.0},                      # additional layer
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
    out = _apply_gip_composite(df, {"kind": "gip_composite"}, gip_tables,
                               gip_assump_tables, _ctx(scheme, s.size))
    rows = {r["member_no"]: r for r in out.iter_rows(named=True)}

    # layer 1: 50% of min(J, 20000); layer 2: 25% of min(1e9, J-20000)
    assert rows[1]["total_cover"] == pytest.approx(
        0.50 * 20_000 + 0.25 * 20_000, rel=1e-9)
    # overall maximum caps the cover
    assert rows[2]["total_cover"] == pytest.approx(25_000.0, rel=1e-9)
    # core + additional benefit
    assert rows[3]["total_cover"] == pytest.approx(
        0.75 * J + 0.05 * 10_000, rel=1e-9)


@pytest.mark.unit
def test_gip_composite_excludes_ineligible_members(gip_scenario, gip_tables,
                                                   gip_assump_tables):
    s = gip_scenario
    scheme = _scheme(s, {1: {}})
    members = [
        _member(1, 1, age=s.E - 0.5, age_nb=s.E, cease=s.M, nra=s.M,
                salary=480_000.0, gender=s.gender),
        _member(2, 1, age=15.0, age_nb=16, cease=s.M, nra=s.M,
                salary=480_000.0, gender=s.gender),          # under 16
        _member(3, 1, age=s.M + 2.0, age_nb=int(s.M) + 3, cease=s.M, nra=s.M,
                salary=480_000.0, gender=s.gender),          # over ceasing age
        _member(4, 1, age=s.M - (s.wp_raw - 1.0) / 12.0, age_nb=s.E,
                cease=s.M, nra=s.M, salary=480_000.0,
                gender=s.gender),                            # months-to-NRD < WP
        _member(5, 1, age=s.E - 0.5, age_nb=s.E, cease=s.M, nra=s.M,
                salary=480_000.0, gender=s.gender, active=False),  # product off
        _member(6, None, age=s.E - 0.5, age_nb=s.E, cease=s.M, nra=s.M,
                salary=480_000.0, gender=s.gender),          # no category
    ]
    df = pl.DataFrame(members)
    out = _apply_gip_composite(df, {"kind": "gip_composite"}, gip_tables,
                               gip_assump_tables, _ctx(scheme, s.size))
    rows = {r["member_no"]: r for r in out.iter_rows(named=True)}

    assert rows[1]["total_cover"] > 0.0
    assert rows[1]["product_active"] is True

    for m in (2, 3, 4, 5, 6):
        assert rows[m]["total_cover"] == 0.0
        assert rows[m]["basic_technical_premium"] == 0.0
        assert rows[m]["rate_basic"] == 0.0

    # members failing eligibility are deactivated (except those already off
    # or without a category - those were never active)
    assert rows[2]["product_active"] is False
    assert rows[3]["product_active"] is False
    assert rows[4]["product_active"] is False
    assert rows[5]["product_active"] is False

    # only the over-ceasing-age member is flagged for reporting
    assert rows[3]["reporting_over_cease"] is True
    assert rows[2]["reporting_over_cease"] is False
    if s.wp_raw >= 1:
        # member 4 fails the months-to-NRD gate but is below ceasing age
        # (with wp_raw == 0 it would instead be an over-cease member)
        assert rows[4]["reporting_over_cease"] is False


@pytest.mark.unit
def test_gip_composite_hcc_waiver_and_conversion_option(gip_scenario,
                                                        gip_tables,
                                                        gip_assump_tables):
    s = gip_scenario
    scheme = _scheme(s, {
        1: {"hcc_on": "Yes", "hcc_pp": 24.0, "hcc_pt": "Nominal Amount",
            "hcc_nom": 500.0, "hcc_esc": 0.05, "co": "Yes"},
        2: {"hcc_on": "Yes", "hcc_pp": 24.0, "hcc_pt": "Member Data",
            "hcc_esc": 0.0},
    })
    df = pl.DataFrame([
        _member(1, 1, age=s.E - 0.5, age_nb=s.E, cease=s.M, nra=s.M,
                salary=480_000.0, gender=s.gender),
        _member(2, 2, age=s.E - 0.5, age_nb=s.E, cease=s.M, nra=s.M,
                salary=480_000.0, gender=s.gender, hcc=350.0),
    ])
    out = _apply_gip_composite(df, {"kind": "gip_composite"}, gip_tables,
                               gip_assump_tables, _ctx(scheme, s.size))
    rows = {r["member_no"]: r for r in out.iter_rows(named=True)}

    # HCC waiver premium (nominal-amount and member-data flavours) is positive
    assert rows[1]["waiver_premium"] > 0.0
    assert rows[2]["waiver_premium"] > 0.0
    # DB is proportional to the HCC amount x escalation factor:
    # cat1 uses nominal 500 escalated at 5% for (24-12)/12 = 1 year,
    # cat2 uses the member's 350 hcc_waiver un-escalated.
    if rows[1]["waiver_premium"] > 0.0 and rows[2]["waiver_premium"] > 0.0:
        assert rows[1]["waiver_premium"] / rows[2]["waiver_premium"] == \
            pytest.approx((500.0 * (1.05 ** 1)) / 350.0, rel=1e-6)

    # conversion option: premium is a fixed percentage of the basic premium
    co_rate = float(gip_assump_tables["conversion_option"]
                    .filter(pl.col("product") == "gip")["rate"][0])
    plim = {r["param_name"]: r["param_value"]
            for r in gip_assump_tables["product_limits"]
            .filter(pl.col("product") == "gip").iter_rows(named=True)}
    f451 = float(plim.get("max_co_age", 65))
    f452 = float(plim.get("withdrawal_only_pre_nra_cap_years", 5))
    if s.E <= min(s.M - f452, f451):
        assert rows[1]["co_premium"] == pytest.approx(
            rows[1]["basic_technical_premium"] * co_rate, rel=1e-6)
    else:
        assert rows[1]["co_premium"] == 0.0
    assert rows[2]["co_premium"] == 0.0


@pytest.mark.unit
def test_gip_composite_backdating_loading_label(gip_scenario, gip_tables,
                                                gip_assump_tables):
    s = gip_scenario
    bd_tbl = gip_tables["backdating_loading"].filter(
        (pl.col("product") == "gip") & (pl.col("scheme_wp") == 6))
    if bd_tbl.height == 0:
        pytest.skip("no wp=6 backdating labels in basis snapshot")
    label = bd_tbl["backdating_option"][0]
    scheme = _scheme(s, {1: {"bd": label}})
    df = pl.DataFrame([
        _member(1, 1, age=s.E - 0.5, age_nb=s.E, cease=s.M, nra=s.M,
                salary=480_000.0, gender=s.gender),
    ])
    out = _apply_gip_composite(df, {"kind": "gip_composite"}, gip_tables,
                               gip_assump_tables, _ctx(scheme, s.size))
    assert out.row(0, named=True)["basic_technical_premium"] >= 0.0


@pytest.mark.unit
def test_gip_composite_unknown_industry_raises(gip_scenario, gip_tables,
                                               gip_assump_tables):
    s = gip_scenario
    scheme = _scheme(s, {1: {}})
    scheme = replace(scheme, secondary="__NO_SUCH_INDUSTRY__")
    df = pl.DataFrame([
        _member(1, 1, age=s.E - 0.5, age_nb=s.E, cease=s.M, nra=s.M,
                salary=480_000.0, gender=s.gender),
    ])
    with pytest.raises(KeyError, match="industry"):
        _apply_gip_composite(df, {"kind": "gip_composite"}, gip_tables,
                             gip_assump_tables, _ctx(scheme, s.size))
    with pytest.raises(KeyError, match="industry"):
        _apply_gip_composite_legacy(df, {"kind": "gip_composite"}, gip_tables,
                                    gip_assump_tables, _ctx(scheme, s.size))


# ---------------------------------------------------------------------------
# vectorised vs legacy equivalence
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_gip_vectorised_matches_legacy(gip_scenario, gip_tables,
                                       gip_assump_tables):
    s = gip_scenario
    scheme = _scheme(s, {
        1: {"tax_opt": "As per data"},
        2: {"tax_opt": "Apply net-of-tax maximum", "hcc_on": "Yes",
            "hcc_pp": 36.0, "hcc_pt": "Nominal Amount", "hcc_nom": 750.0,
            "hcc_esc": 0.03},
        3: {"tax_opt": "Tax Replicator", "wspec": "As Per Data", "co": "Yes"},
        4: {"perc": [0.6, 0.2, 0.1, 0.0],
            "layer": [10_000.0, 20_000.0, 30_000.0, 1e9],
            "add_p": 0.02, "add_l": 5_000.0},
    })
    J = 40_000.0
    members = [
        _member(1, 1, age=s.E - 0.5, age_nb=s.E, cease=s.M, nra=s.M,
                salary=J * 12, gender=s.gender),
        _member(2, 2, age=s.E - 0.5, age_nb=s.E, cease=s.M, nra=s.M,
                salary=J * 12, gender=s.gender, dob=date(1988, 3, 20)),
        _member(3, 3, age=s.E - 0.5, age_nb=s.E, cease=s.M, nra=s.M,
                salary=J * 12, gender=s.gender, waiver=0.15),
        _member(4, 4, age=s.E - 0.5, age_nb=s.E, cease=s.M, nra=s.M,
                salary=J * 12, gender=s.gender),
        # excluded-but-active member: months to NRD below the waiting period
        _member(5, 1, age=s.M - (s.wp_raw - 1.0) / 12.0, age_nb=s.E,
                cease=s.M, nra=s.M, salary=J * 12, gender=s.gender),
        # excluded + over ceasing age
        _member(6, 1, age=s.M + 1.5, age_nb=int(s.M) + 2, cease=s.M, nra=s.M,
                salary=J * 12, gender=s.gender),
    ]
    df = pl.DataFrame(members)
    spec = {"kind": "gip_composite"}
    ctx = _ctx(scheme, s.size)

    out_v = _apply_gip_composite(df.clone(), spec, gip_tables,
                                 gip_assump_tables, ctx)
    out_l = _apply_gip_composite_legacy(df.clone(), spec, gip_tables,
                                        gip_assump_tables, ctx)

    _equiv_frame(out_v, out_l, [
        "total_cover", "basic_technical_premium", "co_premium",
        "waiver_premium", "basic_excl_esc", "reporting_incidence",
        "reporting_incidence_x_cover", "rate_basic", "product_active",
        "reporting_over_cease",
    ])


# ---------------------------------------------------------------------------
# small pure helpers
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_gip_eomonth():
    assert _gip_eomonth(2026, 2) == date(2026, 2, 28)
    assert _gip_eomonth(2024, 2) == date(2024, 2, 29)   # leap year
    assert _gip_eomonth(2026, 12) == date(2026, 12, 31)
    assert _gip_eomonth(2026, 4) == date(2026, 4, 30)


@pytest.mark.unit
def test_gip_yearfrac_30_360():
    # 30/360: Jan 31 -> day 30, then 1 month + (1-30) days to Mar 1
    assert _gip_yearfrac_30_360(date(2026, 1, 31), date(2026, 3, 1)) == \
        pytest.approx(31 / 360)
    assert _gip_yearfrac_30_360(date(2026, 5, 15), date(2026, 5, 15)) == 0.0
    assert _gip_yearfrac_30_360(date(2025, 12, 31), date(2026, 1, 31)) == \
        pytest.approx(30 / 360)
    assert _gip_yearfrac_30_360(date(2026, 1, 1), date(2027, 1, 1)) == 1.0


@pytest.mark.unit
def test_gip_esc_key():
    assert _gip_esc_key(None) == "0"
    assert _gip_esc_key(0.0) == "0"
    assert _gip_esc_key(0.05) == "5"
    assert _gip_esc_key(0.02) == "2"


@pytest.mark.unit
def test_gip_backdating_loading_helper(gip_tables):
    assert _gip_backdating_loading(gip_tables, None) == 0.0
    assert _gip_backdating_loading(gip_tables, "  none ") == 0.0
    assert _gip_backdating_loading(gip_tables, "__no_such_label__") == 0.0

    tbl = gip_tables["backdating_loading"].filter(
        (pl.col("product") == "gip") & (pl.col("scheme_wp") == 6))
    if tbl.height:
        label = tbl["backdating_option"][0]
        expected = float(tbl["loading"][0])
        assert _gip_backdating_loading(gip_tables, label) == \
            pytest.approx(expected)


@pytest.mark.unit
def test_gip_annuity_fs_missing_esc_row_returns_fo(gip_tables):
    basis = {"annuity_esc_loading": gip_tables["annuity_esc_loading"]}
    # FO is None -> early return 0
    assert _gip_annuity_fs(None, 0.5, {}, "sal_1", 0.0, basis, 0.05,
                           "Small", 30, "M", 3, 12, 60, 61) == 0.0
    # esc_pct with no matching row -> FO returned unchanged
    assert _gip_annuity_fs(10.0, 0.5, {}, "sal_1", 0.0, basis, 9.999,
                           "Small", 30, "M", 3, 12, 60, 61) == 10.0


@pytest.mark.unit
def test_gip_annuity_fs_with_real_esc_row(gip_tables):
    esc_tbl = gip_tables["annuity_esc_loading"].filter(
        pl.col("product") == "gip")
    erow = esc_tbl.row(0, named=True)
    esc_pct = _num(erow["esc_pct"])
    if esc_pct is None:
        pytest.skip("first gip esc row has non-numeric esc_pct")
    GE = float(erow["progression_loading"] or 0.0)
    # empty af_map -> all sub-lookups miss -> (FO + 0 + 0) * (1 + GE)
    basis = {"annuity_esc_loading": gip_tables["annuity_esc_loading"]}
    res = _gip_annuity_fs(10.0, 0.5, {}, "sal_1", 0.0, basis, esc_pct,
                          "Small", 30, "M", 3, 12, 60, 61)
    assert res == pytest.approx(10.0 * (1.0 + GE), rel=1e-9)


@pytest.mark.unit
def test_gip_annuity_fs_fast_variants():
    esc_map = {0.05: {"map_lower_key": "0", "map_upper_key": "5",
                      "interp_weight": 0.0, "progression_loading": 0.1}}
    # FO None -> 0
    assert _gip_annuity_fs_fast(None, 0.5, {}, "sal_1", 0.0, esc_map, 0.05,
                                "Small", 30, "M", 3, 12, 60, 61) == 0.0
    # esc row found, af_map empty -> (FO)*(1+progression_loading)
    assert _gip_annuity_fs_fast(10.0, 0.5, {}, "sal_1", 0.0, esc_map, 0.05,
                                "Small", 30, "M", 3, 12, 60, 61) == \
        pytest.approx(11.0)
    # esc row missing entirely -> FO unchanged
    assert _gip_annuity_fs_fast(7.0, 0.5, {}, "sal_1", 0.0, esc_map, 0.99,
                                "Small", 30, "M", 3, 12, 60, 61) == 7.0
    # esc_pct resolvable through the numeric-grid fallback (key 0.05 vs 5%)
    assert _gip_annuity_fs_fast(7.0, 0.0, {}, "sal_1", 0.0, esc_map, "junk",
                                "Small", 30, "M", 3, 12, 60, 61) == 7.0


@pytest.mark.unit
def test_gip_build_lookup_tables_contents(gip_scenario, gip_tables,
                                          gip_assump_tables):
    s = gip_scenario
    scheme = _scheme(s, {1: {}})
    L = _gip_build_lookup_tables(gip_tables, gip_assump_tables,
                                 _ctx(scheme, s.size))
    for key in ("esc_map", "mort_map", "af_map", "bands", "nra_map",
                "db_map", "wpann", "sb_by_g", "co_cost"):
        assert key in L
    assert L["size"] == s.size.lower()
    assert L["size_title"] == s.size
    assert (s.E, s.gender) in L["mort_map"]
    assert s.M in L["nra_map"]
    assert s.wp_raw in L["wpann"]
    assert L["bands"], "gip tax bands must not be empty"
    hcc = {r["param_name"]: float(r["param_value"])
           for r in gip_tables["gip_hcc_basis"]
           .filter(pl.col("product") == "gip").iter_rows(named=True)}
    assert L["HCC_RATE"] == pytest.approx(hcc["hcc_pricing_rate"])
    assert L["J5"] == pytest.approx(hcc["hcc_reduction_factor"])


# ---------------------------------------------------------------------------
# real CTR integration (skipped when no suitable workbook is present)
# ---------------------------------------------------------------------------

def _first_ctr_with(product_key):
    if not CTR_TEST_DIR.is_dir():
        return None
    for path in sorted(CTR_TEST_DIR.rglob("*.ctr")):
        try:
            ctr = load_ctr(path)
        except Exception:
            continue
        if product_key in ctr.active_products:
            return ctr
    return None


@pytest.mark.unit
def test_gip_priced_from_real_ctr_when_available(basis, assump):
    ctr = _first_ctr_with("gip")
    if ctr is None:
        pytest.skip("no bundled CTR workbook with GIP active")
    members = ctr.members.get("gip_sheet")
    if members is None or members.height == 0:
        pytest.skip("CTR has no populated 'Member Data - GIP' sheet")

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
    out = price("gip", ctr, basis, assump, ctx)

    assert out.height == members.height
    for c in ["total_cover", "basic_technical_premium", "rate_basic"]:
        assert c in out.columns
        assert out[c].null_count() == 0
    # at least one member must have priced on a real scheme
    assert out["basic_technical_premium"].sum() >= 0.0
    # cover/premium consistency invariant
    active = out.filter(pl.col("total_cover") > 0)
    assert (active["basic_technical_premium"]
            / active["total_cover"] * 1000
            - active["rate_basic"]).abs().max() < 1e-6
