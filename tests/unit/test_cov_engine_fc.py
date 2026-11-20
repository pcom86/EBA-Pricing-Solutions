"""Coverage-focused tests for ``pricing_engine.helpers_fc`` (Funeral Cover /
Family Cover + extended-family rate card + ``apply_post_rate_adjust``).

Strategy
--------
* Load the real basis / assumption parquet snapshots once per interpreter
  (``ctr_loader.get_basis`` / ``get_assumptions`` are ``lru_cache``-backed).
* Drive the FC composite pipeline stage-by-stage (the exported ``_fc_*``
  functions) so intermediate rating-factor columns can be asserted, then the
  full ``_apply_fc_composite`` for cover/premium/add-on invariants.
* Cover the extended-FC rate card (``build_ext_fc_rate_card`` and helpers)
  and every ``apply_post_rate_adjust`` kind, including unknown-kind errors.
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
from pricing_engine.helpers_fc import (
    _aggregate_ext_fc_age_bands,
    _apply_fc_composite,
    _build_ext_fc_age_rate_table,
    _ext_fc_salary_factor_lookup,
    _fc_apply_joins_and_benefits,
    _fc_apply_rating_factor_pipeline,
    _fc_compute_basic_premium_and_finalize_cover,
    _fc_db_loading,
    _fc_demographics_join,
    _fc_family_composition_join,
    _fc_second_spouse_loading,
    apply_post_rate_adjust,
    build_ext_fc_rate_card,
)

ROOT = Path(__file__).resolve().parents[2]
BASIS_DIR = ROOT / "BackendEngine" / "basis_snapshots" / "basis_v2026_08"
ASSUMP_DIR = ROOT / "BackendEngine" / "assumption_snapshots" / "assumption_A2026_08"
CTR_TEST_DIR = ROOT / "BackendEngine" / "Test"

CALC_DATE = date(2026, 8, 1)

FC_SPEC = {
    "kind": "fc_composite",
    "namibia_rate_cut_global": "namibia_competitive_factor",
    "db_gate_global": "active_selection_db_load",
}

BENEFITS = {
    "primary_insured": 50_000.0,
    "spouse": 40_000.0,
    "child_14_plus": 15_000.0,
    "child_6_13": 10_000.0,
    "child_1_5": 8_000.0,
    "child_under_1": 5_000.0,
    "stillborn": 3_000.0,
    "parents_cover": 20_000.0,
}


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def basis():
    return get_basis(BASIS_DIR)


@pytest.fixture(scope="module")
def assump():
    return get_assumptions(ASSUMP_DIR)


@pytest.fixture(scope="module")
def fc_tables(basis):
    return basis.for_product("fc")


@pytest.fixture(scope="module")
def ext_fc_tables(basis):
    return basis.for_product("ext_fc")


@pytest.fixture(scope="module")
def fc_assump_tables(assump):
    return assump.for_product("fc")


@pytest.fixture(scope="module")
def gla_secondary(fc_tables):
    ind = fc_tables["industry"].filter(pl.col("product") == "gla")
    if ind.height == 0 or "secondary" not in ind.columns:
        return ""
    return ind["secondary"][0]


@pytest.fixture(scope="module")
def fc_scheme():
    return SchemeRecord(
        country_scale="South Africa",
        fc_benefit_amounts={1: dict(BENEFITS), 2: dict(BENEFITS)},
        fc_max_no_of_spouses={1: 2.0, 2: 1.0},
        fc_parents_included={1: "Yes", 2: "No"},
        fc_parents_in_law_included={1: "Yes", 2: "No"},
        fc_include_ctc={1: "Yes", 2: "No"},
        fc_pup_death={1: "Yes", 2: "No"},
        profit_refund_loading_by_product={"fc": 0.20},
    )


def _ctx(scheme, globals_map, secondary=""):
    return PricingContext(
        scheme=scheme,
        globals=dict(globals_map),
        calc_date=CALC_DATE,
        member_count=10,
        scheme_size="Small",
        secondary_industry=secondary,
        primary_industry="",
    )


def _member(no, cat, *, age, age_nb, cease, salary, gender,
            status="A", active=True, dod=None):
    return {
        "member_no": no,
        "category_number": cat,
        "product_active": active,
        "age": float(age),
        "age_nb": int(age_nb),
        "ceasing_age": float(cease),
        "annual_salary": float(salary),
        "gender": gender,
        "status": status,
        "date_of_disability": dod,
    }


def _demo_vals(fc_tables, age_nb):
    """(pct_married, n_children_0_13, n_children_14_plus) for an age_nb."""
    demo = fc_tables["fc_demographics"].filter(pl.col("age") == int(age_nb))
    if demo.height == 0:
        return 0.0, 0.0, 0.0
    r = demo.row(0, named=True)
    return (float(r["pct_married"] or 0.0),
            float(r["n_children_0_13"] or 0.0),
            float(r["n_children_14_plus"] or 0.0))


def _fam_vals(fc_tables, age_nb):
    """(n_parents, n_in_laws) via backward asof on age_from."""
    fam = (fc_tables["fc_family_composition"]
           .with_columns(pl.col("age_from").cast(pl.Float64, strict=False))
           .drop_nulls("age_from").sort("age_from"))
    hit = fam.filter(pl.col("age_from") <= float(age_nb))
    if hit.height == 0:
        return 0.0, 0.0
    r = hit.tail(1).row(0, named=True)
    return float(r["n_parents"] or 0.0), float(r["n_in_laws"] or 0.0)


def _second_spouse_loading(assump_tables, max_spouses):
    scalar = 0.0
    pl_tbl = assump_tables["product_limits"].filter(
        (pl.col("product") == "fc")
        & (pl.col("param_name") == "second_spouse_loading"))
    if pl_tbl.height and pl_tbl["param_value"][0] is not None:
        scalar = float(pl_tbl["param_value"][0])
    return 1.0 if max_spouses <= 1 else 1.0 + scalar * (max_spouses - 1.0)


def _expected_cover(fc_tables, assump_tables, *, age_nb, benefits, max_spouses,
                    parents_yes, inlaws_yes):
    pm, n013, n14 = _demo_vals(fc_tables, age_nb)
    np_, nl = _fam_vals(fc_tables, age_nb)
    if not parents_yes:
        np_ = 0.0
    if not inlaws_yes:
        nl = 0.0
    ssl = _second_spouse_loading(assump_tables, max_spouses)
    return (benefits["primary_insured"]
            + pm * benefits["spouse"] * ssl
            + n013 * benefits["child_6_13"]
            + n14 * benefits["child_14_plus"]
            + (np_ + nl * pm) * benefits["parents_cover"])


def _pl_scalar(assump_tables, param_name):
    r = assump_tables["product_limits"].filter(
        (pl.col("product") == "fc") & (pl.col("param_name") == param_name))
    if r.height and r["param_value"][0] is not None:
        return float(r["param_value"][0])
    return 0.0


# ---------------------------------------------------------------------------
# pipeline stages
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_fc_demographics_join(fc_tables):
    df = pl.DataFrame([
        _member(1, 1, age=40.5, age_nb=41, cease=65, salary=300_000, gender="M"),
        _member(2, 1, age=10.5, age_nb=11, cease=65, salary=0.0, gender="F"),
    ])
    out = _fc_demographics_join(df, fc_tables)
    for c in ("pct_married", "n_children_0_13", "n_children_14_plus"):
        assert c in out.columns
        assert out[c].null_count() == 0
    # expected values come straight from the demographics basis table
    pm1, n1, n14_1 = _demo_vals(fc_tables, 41)
    pm2, n2, n14_2 = _demo_vals(fc_tables, 11)
    assert out["pct_married"].to_list() == pytest.approx([pm1, pm2])
    assert out["n_children_0_13"].to_list() == pytest.approx([n1, n2])
    assert out["n_children_14_plus"].to_list() == pytest.approx([n14_1, n14_2])


@pytest.mark.unit
def test_fc_demographics_join_missing_table():
    df = pl.DataFrame([
        _member(1, 1, age=40.5, age_nb=41, cease=65, salary=300_000, gender="M"),
    ])
    out = _fc_demographics_join(df, None)
    assert out["pct_married"].to_list() == [0.0]
    out = _fc_demographics_join(df, {})
    assert out["n_children_0_13"].to_list() == [0.0]


@pytest.mark.unit
def test_fc_family_composition_join(fc_tables, fc_scheme):
    ctx = _ctx(fc_scheme, {})
    df = pl.DataFrame([
        _member(1, 1, age=40.5, age_nb=41, cease=65, salary=300_000, gender="M"),
        _member(2, 2, age=40.5, age_nb=41, cease=65, salary=300_000, gender="M"),
        _member(3, 1, age=40.5, age_nb=41, cease=65, salary=300_000, gender="M"),
    ])
    out = _fc_family_composition_join(df, fc_tables, ctx)
    rows = {r["member_no"]: r for r in out.iter_rows(named=True)}
    np_raw, nl_raw = _fam_vals(fc_tables, 41)
    # cat 1 includes parents AND parents-in-law
    assert rows[1]["n_parents"] == pytest.approx(np_raw)
    assert rows[1]["n_in_laws"] == pytest.approx(nl_raw)
    # cat 2 includes neither
    assert rows[2]["n_parents"] == 0.0
    assert rows[2]["n_in_laws"] == 0.0
    # same inputs -> same gated values
    assert rows[3]["n_parents"] == pytest.approx(np_raw)


@pytest.mark.unit
def test_fc_family_composition_join_missing_table(fc_scheme):
    df = pl.DataFrame([
        _member(1, 1, age=40.5, age_nb=41, cease=65, salary=300_000, gender="M"),
    ])
    out = _fc_family_composition_join(df, {}, _ctx(fc_scheme, {}))
    assert out["n_parents"].to_list() == [0.0]
    assert out["n_in_laws"].to_list() == [0.0]


@pytest.mark.unit
def test_fc_second_spouse_loading(fc_assump_tables, fc_scheme):
    ctx = _ctx(fc_scheme, {})
    df = pl.DataFrame([
        _member(1, 1, age=40.5, age_nb=41, cease=65, salary=300_000, gender="M"),
        _member(2, 2, age=40.5, age_nb=41, cease=65, salary=300_000, gender="M"),
        _member(3, 9, age=40.5, age_nb=41, cease=65, salary=300_000, gender="M"),
    ])
    out = _fc_second_spouse_loading(df, fc_assump_tables, ctx)
    ssl = _second_spouse_loading(fc_assump_tables, 2)
    assert out["second_spouse_loading"].to_list() == pytest.approx(
        [ssl, 1.0, 1.0])   # cat2 max=1 -> 1.0; cat9 unmapped -> default 1.0


@pytest.mark.unit
def test_fc_second_spouse_loading_no_assump(fc_scheme):
    df = pl.DataFrame([
        _member(1, 1, age=40.5, age_nb=41, cease=65, salary=300_000, gender="M"),
    ])
    out = _fc_second_spouse_loading(df, None, _ctx(fc_scheme, {}))
    assert out["second_spouse_loading"].to_list() == [1.0]


@pytest.mark.unit
def test_fc_db_loading_gate_off(fc_assump_tables, fc_scheme):
    ctx = _ctx(fc_scheme, {"active_selection_db_load": 0})
    df = pl.DataFrame([
        _member(1, 1, age=40.5, age_nb=41, cease=65, salary=300_000,
                gender="M", status="DB", dod=date(2023, 1, 1)),
    ])
    out = _fc_db_loading(df, {"gate_global": "active_selection_db_load"},
                         fc_assump_tables, ctx)
    assert out["db_loading"].to_list() == [1.0]


@pytest.mark.unit
def test_fc_db_loading_gate_on(fc_assump_tables, fc_scheme):
    if "fc_db_loading" not in fc_assump_tables:
        pytest.skip("fc_db_loading table missing from assumption snapshot")
    tbl = fc_assump_tables["fc_db_loading"]
    ctx = _ctx(fc_scheme, {"active_selection_db_load": 1.0})
    df = pl.DataFrame([
        # disabled ~3.5y ago -> years_disabled = 4
        _member(1, 1, age=40.5, age_nb=41, cease=65, salary=300_000,
                gender="M", status="DB", dod=date(2023, 2, 1)),
        _member(2, 1, age=40.5, age_nb=41, cease=65, salary=300_000,
                gender="M", status="A"),                    # active -> 1.0
        _member(3, 1, age=40.5, age_nb=41, cease=65, salary=300_000,
                gender="M", status="DB"),                   # DB, no DoD -> 1.0
    ])
    out = _fc_db_loading(df, {"gate_global": "active_selection_db_load"},
                         fc_assump_tables, ctx)
    years = min(int((CALC_DATE - date(2023, 2, 1)).days / 365.25) + 1, 5)
    hit = tbl.filter(pl.col("years_disabled") == years)
    expected = float(hit["loading_factor"][0]) if hit.height else 1.0
    assert out["db_loading"].to_list() == pytest.approx([expected, 1.0, 1.0])


@pytest.mark.unit
def test_fc_db_loading_missing_disability_column(fc_assump_tables, fc_scheme):
    ctx = _ctx(fc_scheme, {"active_selection_db_load": 1})
    df = pl.DataFrame([_member(1, 1, age=40.5, age_nb=41, cease=65,
                               salary=300_000, gender="M", status="DB")])
    df = df.drop("date_of_disability")
    out = _fc_db_loading(df, {"gate_global": "active_selection_db_load"},
                         fc_assump_tables, ctx)
    assert out["db_loading"].to_list() == [1.0]


@pytest.mark.unit
def test_fc_rating_pipeline_produces_factors(fc_tables, gla_secondary,
                                             fc_scheme):
    df = pl.DataFrame([
        _member(1, 1, age=40.5, age_nb=41, cease=65, salary=300_000, gender="M"),
        _member(2, 1, age=35.5, age_nb=36, cease=65, salary=150_000, gender="F"),
    ])
    ctx = _ctx(fc_scheme, {}, secondary=gla_secondary)
    out = _fc_apply_rating_factor_pipeline(df, fc_tables, ctx)
    for c in ("__ar", "__av", "__as", "__at", "__au"):
        assert c in out.columns
        assert out[c].null_count() == 0
    # mortality-based factors are non-negative per-1000 rates
    assert (out["__ar"] >= 0).all()
    assert (out["__av"] >= 0).all()


@pytest.mark.unit
def test_fc_benefit_join_and_cover_formula(fc_tables, fc_assump_tables,
                                           fc_scheme, gla_secondary):
    df = pl.DataFrame([
        _member(1, 1, age=40.5, age_nb=41, cease=65, salary=300_000, gender="M"),
        _member(2, 2, age=40.5, age_nb=41, cease=65, salary=300_000, gender="F"),
    ])
    ctx = _ctx(fc_scheme, {}, secondary=gla_secondary)
    out = _fc_apply_joins_and_benefits(df, FC_SPEC, fc_tables,
                                       fc_assump_tables, ctx)
    expected_1 = _expected_cover(fc_tables, fc_assump_tables, age_nb=41,
                                 benefits=BENEFITS, max_spouses=2,
                                 parents_yes=True, inlaws_yes=True)
    expected_2 = _expected_cover(fc_tables, fc_assump_tables, age_nb=41,
                                 benefits=BENEFITS, max_spouses=1,
                                 parents_yes=False, inlaws_yes=False)
    rows = {r["member_no"]: r["__fc_ag_cover"]
            for r in out.iter_rows(named=True)}
    assert rows[1] == pytest.approx(expected_1, rel=1e-9)
    assert rows[2] == pytest.approx(expected_2, rel=1e-9)


@pytest.mark.unit
def test_fc_basic_premium_and_cover_gating(fc_tables, fc_assump_tables,
                                           fc_scheme, gla_secondary):
    df = pl.DataFrame([
        _member(1, 1, age=40.5, age_nb=41, cease=65, salary=300_000, gender="M"),
        _member(2, 1, age=66.5, age_nb=67, cease=65, salary=300_000,
                gender="M"),                                        # over cease
        _member(3, 1, age=40.5, age_nb=41, cease=65, salary=300_000,
                gender="M", active=False),                          # inactive
    ])
    ctx = _ctx(fc_scheme, {}, secondary=gla_secondary)
    d = _fc_apply_joins_and_benefits(df, FC_SPEC, fc_tables,
                                     fc_assump_tables, ctx)
    d = _fc_apply_rating_factor_pipeline(d, fc_tables, ctx)
    d = _fc_compute_basic_premium_and_finalize_cover(d, fc_tables)
    rows = {r["member_no"]: r for r in d.iter_rows(named=True)}

    # member 1 premium = bb + bc + bd + be (recomputed from intermediates)
    comp1 = (rows[1]["__bb"] + rows[1]["__bc"] + rows[1]["__bd"]
             + rows[1]["__be"])
    assert rows[1]["basic_technical_premium"] == pytest.approx(comp1,
                                                             rel=1e-12)
    assert rows[1]["total_cover"] > 0.0
    assert rows[1]["basic_technical_premium"] > 0.0
    # gated members: premium AND cover are zeroed out
    assert rows[2]["total_cover"] == 0.0          # over ceasing age
    assert rows[2]["basic_technical_premium"] == 0.0
    assert rows[3]["total_cover"] == 0.0          # product inactive
    assert rows[3]["basic_technical_premium"] == 0.0


# ---------------------------------------------------------------------------
# full composite
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_fc_composite_end_to_end(fc_tables, fc_assump_tables, fc_scheme,
                                 gla_secondary):
    df = pl.DataFrame([
        _member(1, 1, age=40.5, age_nb=41, cease=65, salary=300_000, gender="M"),
        _member(2, 1, age=35.5, age_nb=36, cease=65, salary=150_000, gender="F"),
        _member(3, 2, age=40.5, age_nb=41, cease=65, salary=300_000, gender="M"),
    ])
    ctx = _ctx(fc_scheme, {}, secondary=gla_secondary)
    out = _apply_fc_composite(df, FC_SPEC, fc_tables, fc_assump_tables, ctx)

    assert out.height == 3
    for c in ("total_cover", "basic_technical_premium",
              "aids_technical_premium", "rate_basic", "rate_aids",
              "ctc_premium", "waiver_premium", "prb_premium"):
        assert c in out.columns
        assert out[c].null_count() == 0
    # staging columns are dropped
    for c in ("__bb", "__bc", "__bd", "__be", "__ar", "__av", "__as",
              "__at", "__au", "__fc_ag_cover"):
        assert c not in out.columns

    expected_1 = _expected_cover(fc_tables, fc_assump_tables, age_nb=41,
                                 benefits=BENEFITS, max_spouses=2,
                                 parents_yes=True, inlaws_yes=True)
    expected_2 = _expected_cover(fc_tables, fc_assump_tables, age_nb=36,
                                 benefits=BENEFITS, max_spouses=2,
                                 parents_yes=True, inlaws_yes=True)
    expected_3 = _expected_cover(fc_tables, fc_assump_tables, age_nb=41,
                                 benefits=BENEFITS, max_spouses=1,
                                 parents_yes=False, inlaws_yes=False)
    rows = {r["member_no"]: r for r in out.iter_rows(named=True)}
    assert rows[1]["total_cover"] == pytest.approx(expected_1, rel=1e-9)
    assert rows[2]["total_cover"] == pytest.approx(expected_2, rel=1e-9)
    assert rows[3]["total_cover"] == pytest.approx(expected_3, rel=1e-9)

    # ctc premium only on cat 1 (include_ctc=Yes)
    ctc_scalar = _pl_scalar(fc_assump_tables, "ctc_cost")
    base1 = rows[1]["basic_technical_premium"]
    assert rows[1]["ctc_premium"] == pytest.approx(base1 * ctc_scalar, rel=1e-9)
    assert rows[3]["ctc_premium"] == 0.0

    # waiver premium only on cat 1 (fc_pup_death=Yes)
    pup = _pl_scalar(fc_assump_tables, "pup_cost_death")
    assert rows[1]["waiver_premium"] == pytest.approx(base1 * pup, rel=1e-9)
    assert rows[2]["waiver_premium"] == pytest.approx(
        rows[2]["basic_technical_premium"] * pup, rel=1e-9)
    assert rows[3]["waiver_premium"] == 0.0

    # prb premium = 20% of basic for active members at/below ceasing age
    assert rows[1]["prb_premium"] == pytest.approx(base1 * 0.20, rel=1e-9)

    # rate consistency
    assert rows[1]["rate_basic"] == pytest.approx(
        base1 / rows[1]["total_cover"] * 1000, rel=1e-9)


@pytest.mark.unit
def test_fc_composite_namibia_rate_cut(fc_tables, fc_assump_tables,
                                       gla_secondary):
    df = pl.DataFrame([
        _member(1, 1, age=40.5, age_nb=41, cease=65, salary=300_000, gender="M"),
    ])
    sa_scheme = SchemeRecord(
        country_scale="South Africa",
        fc_benefit_amounts={1: dict(BENEFITS)},
    )
    nam_scheme = replace(sa_scheme, country_scale="Namibia")
    cut = -0.15
    ctx_sa = _ctx(sa_scheme, {"namibia_competitive_factor": cut},
                  secondary=gla_secondary)
    ctx_nam = _ctx(nam_scheme, {"namibia_competitive_factor": cut},
                   secondary=gla_secondary)

    out_sa = _apply_fc_composite(df.clone(), FC_SPEC, fc_tables,
                                 fc_assump_tables, ctx_sa)
    out_nam = _apply_fc_composite(df.clone(), FC_SPEC, fc_tables,
                                  fc_assump_tables, ctx_nam)

    prem_sa = out_sa["basic_technical_premium"][0]
    prem_nam = out_nam["basic_technical_premium"][0]
    # Namibia applies (1 + namibia_competitive_factor) to the basic premium
    assert prem_nam == pytest.approx(prem_sa * (1.0 + cut), rel=1e-9)
    # cover unchanged by the rate cut
    assert out_nam["total_cover"][0] == pytest.approx(
        out_sa["total_cover"][0], rel=1e-9)


@pytest.mark.unit
def test_fc_composite_missing_fc_tables(fc_tables, fc_assump_tables,
                                        fc_scheme, gla_secondary):
    """With the fc_* basis tables absent the premium must be 0 but the cover
    is still the primary-insured benefit (family percentages default to 0)."""
    partial = {k: v for k, v in fc_tables.items()
               if k not in ("fc_rates", "fc_demographics",
                            "fc_family_composition")}
    df = pl.DataFrame([
        _member(1, 1, age=40.5, age_nb=41, cease=65, salary=300_000, gender="M"),
    ])
    ctx = _ctx(fc_scheme, {}, secondary=gla_secondary)
    out = _apply_fc_composite(df, FC_SPEC, partial, fc_assump_tables, ctx)
    row = out.row(0, named=True)
    # member premium (__bb) still applies: mortality/industry/salary tables
    # are present; only the fc_rates-driven child terms fall to zero.
    assert row["basic_technical_premium"] >= 0.0
    # demographics/family tables missing -> only the primary insured benefit
    assert row["total_cover"] == pytest.approx(BENEFITS["primary_insured"])


@pytest.mark.unit
def test_fc_composite_missing_assump_tables(fc_tables, fc_scheme,
                                            gla_secondary):
    df = pl.DataFrame([
        _member(1, 1, age=40.5, age_nb=41, cease=65, salary=300_000, gender="M"),
    ])
    ctx = _ctx(fc_scheme, {}, secondary=gla_secondary)
    out = _apply_fc_composite(df, FC_SPEC, fc_tables, None, ctx)
    row = out.row(0, named=True)
    assert row["ctc_premium"] == 0.0
    assert row["waiver_premium"] == 0.0
    assert row["basic_technical_premium"] >= 0.0


# ---------------------------------------------------------------------------
# extended FC rate card
# ---------------------------------------------------------------------------

def _ext_scheme(**kw):
    base = {
        "ext_fc_parents_selected": {1: "Yes"},
        "ext_fc_extended_family_selected": {1: "Yes"},
        "ext_fc_pct_male": {1: 0.60},
        "ext_fc_avg_salary": {1: 120_000.0},
        "ext_fc_parent_dist_available": {1: "No"},
        "ext_fc_family_dist_available": {1: "No"},
        "ext_fc_age_bands": {1: {
            "parents": [{"start_age": 50, "end_age": 75, "cover": 20_000.0}],
            "extended_family": [{"start_age": 18, "end_age": 65,
                                 "cover": 10_000.0},
                                {"start_age": 66, "end_age": 80,
                                 "cover": 5_000.0}],
        }},
    }
    base.update(kw)
    return SchemeRecord(**base)


@pytest.mark.unit
def test_build_ext_fc_rate_card(ext_fc_tables):
    scheme = _ext_scheme()
    ctr = SimpleNamespace(active_categories={"ext_fc": (1,)}, members={})
    out = build_ext_fc_rate_card(scheme, ctr, ext_fc_tables)

    assert out.height == 3          # 1 parents band + 2 extended-family bands
    assert set(out.columns) == {"category_number", "relative_type",
                                "band_index", "start_age", "end_age",
                                "cover", "risk_premium_per_life_pm"}
    assert (out["risk_premium_per_life_pm"] >= 0).all()
    assert set(out["relative_type"].to_list()) == {"parents", "extended_family"}
    ext = out.filter(pl.col("relative_type") == "extended_family") \
             .sort("start_age")
    assert ext["cover"].to_list() == [10_000.0, 5_000.0]


@pytest.mark.unit
def test_build_ext_fc_rate_card_no_active_categories(ext_fc_tables):
    ctr = SimpleNamespace(active_categories={}, members={})
    out = build_ext_fc_rate_card(_ext_scheme(), ctr, ext_fc_tables)
    assert out.height == 0
    assert "risk_premium_per_life_pm" in out.columns


@pytest.mark.unit
def test_build_ext_fc_rate_card_product_not_selected(ext_fc_tables):
    scheme = _ext_scheme(ext_fc_parents_selected={1: "No"},
                         ext_fc_extended_family_selected={1: "No"})
    ctr = SimpleNamespace(active_categories={"ext_fc": (1,)}, members={})
    out = build_ext_fc_rate_card(scheme, ctr, ext_fc_tables)
    assert out.height == 0


@pytest.mark.unit
def test_ext_fc_salary_factor_lookup():
    tbl = pl.DataFrame({
        "rate_type": ["basic"] * 3 + ["aids"],
        "gender": ["male", "male", "male", "female"],
        "salary_band_from": [0.0, 100_000.0, 200_000.0, 0.0],
        "factor": [1.0, 1.2, 1.4, 0.9],
    })
    # mid-band lookup
    assert _ext_fc_salary_factor_lookup(150_000, 120_000, tbl, "basic",
                                        "male") == pytest.approx(1.2)
    # below lowest band -> first row
    assert _ext_fc_salary_factor_lookup(-1.0, 120_000, tbl, "basic",
                                        "male") == pytest.approx(1.0)
    # above top band -> last eligible
    assert _ext_fc_salary_factor_lookup(1e9, 120_000, tbl, "basic",
                                        "male") == pytest.approx(1.4)
    # no matching rate_type/gender -> 0
    assert _ext_fc_salary_factor_lookup(100_000, 120_000, tbl, "basic",
                                        "female") == 0.0


@pytest.mark.unit
def test_build_ext_fc_age_rate_table(ext_fc_tables):
    out = _build_ext_fc_age_rate_table(
        category_number=1,
        relative_type="parents",
        pct_male=0.6,
        avg_salary=120_000.0,
        dist_available=False,
        live_census_dist=None,
        basis_tables=ext_fc_tables,
    )
    assert set(out.columns) == {"age", "weighted_risk_rate", "dist_weight"}
    assert out.height > 0
    assert (out["weighted_risk_rate"] >= 0).all()
    assert out["dist_weight"].null_count() == 0


@pytest.mark.unit
def test_build_ext_fc_age_rate_table_live_dist(ext_fc_tables):
    live = {55: 0.5, 60: 0.5}
    out = _build_ext_fc_age_rate_table(
        category_number=1, relative_type="parents", pct_male=0.5,
        avg_salary=120_000.0, dist_available=True, live_census_dist=live,
        basis_tables=ext_fc_tables)
    w = dict(zip(out["age"].to_list(), out["dist_weight"].to_list()))
    assert w.get(55) == pytest.approx(0.5)
    assert w.get(60) == pytest.approx(0.5)


@pytest.mark.unit
def test_aggregate_ext_fc_age_bands():
    rate_tbl = pl.DataFrame({
        "age": [50, 60, 70],
        "weighted_risk_rate": [0.02, 0.04, 0.08],
        "dist_weight": [1.0, 1.0, 0.0],
    })
    bands = [{"start_age": 50, "end_age": 60, "cover": 10_000.0},
             {"start_age": 61, "end_age": 80, "cover": 5_000.0}]
    out = _aggregate_ext_fc_age_bands(rate_tbl, bands)
    # band 1: mean weighted rate over dist -> (0.02+0.04)/2 * 10000/1000/12
    assert out[0]["risk_premium_per_life_pm"] == pytest.approx(
        (0.03) * 10_000.0 / 1000.0 / 12.0)
    # band 2: ages 61-80 -> only age 70, dist_weight 0 -> rate 0
    assert out[1]["risk_premium_per_life_pm"] == 0.0
    assert out[0]["band_index"] == 0 and out[1]["band_index"] == 1


# ---------------------------------------------------------------------------
# apply_post_rate_adjust
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_post_rate_adjust_db_definition_multiplicative():
    assump = {
        "lsdb_features": pl.DataFrame({
            "feature": ["db_definitions", "db_definitions", "other"],
            "cost_basis": ["basic", "aids", "basic"],
            "key": ["own_occupation", "ignored", "x"],
            "value": [0.25, 9.0, 9.0],
        })
    }
    df = pl.DataFrame({
        "definition_of_disability": ["Own Occupation", "Unknown"],
        "basic_technical_premium": [100.0, 200.0],
    })
    out = apply_post_rate_adjust(
        df, {"kind": "db_definition_multiplicative"}, assump)
    assert out["basic_technical_premium"].to_list() == pytest.approx(
        [125.0, 200.0])


@pytest.mark.unit
def test_post_rate_adjust_spouse_factor_multiplicative():
    assump = {
        "spouse_factors": pl.DataFrame({
            "factor_type": ["basic_spouses_factor"] * 2,
            "cost_basis": ["basic"] * 2,
            "gender": ["M", "F"],
            "value": [0.10, 0.20],
        })
    }
    df = pl.DataFrame({
        "gender": ["M", "F", "X"],
        "basic_technical_premium": [100.0, 100.0, 100.0],
    })
    out = apply_post_rate_adjust(
        df, {"kind": "spouse_factor_multiplicative"}, assump)
    assert out["basic_technical_premium"].to_list() == pytest.approx(
        [110.0, 120.0, 100.0])


@pytest.mark.unit
def test_post_rate_adjust_spouse_db_definition_multiplicative():
    assump = {
        "spouse_factors": pl.DataFrame({
            "factor_type": ["db_definition_factor"],
            "cost_basis": ["basic"],
            "gender": ["both"],
            "value": [0.15],
        })
    }
    df = pl.DataFrame({"basic_technical_premium": [100.0]})
    out = apply_post_rate_adjust(
        df, {"kind": "spouse_db_definition_multiplicative"}, assump)
    assert out["basic_technical_premium"].to_list() == pytest.approx([115.0])


@pytest.mark.unit
def test_post_rate_adjust_missing_assump_tables():
    df = pl.DataFrame({"basic_technical_premium": [100.0]})
    for kind in ("db_definition_multiplicative", "spouse_factor_multiplicative",
                 "spouse_db_definition_multiplicative"):
        out = apply_post_rate_adjust(df, {"kind": kind}, {})
        assert out["basic_technical_premium"].to_list() == [100.0]


@pytest.mark.unit
def test_post_rate_adjust_fc_kinds_dispatch(fc_tables, fc_assump_tables,
                                            fc_scheme):
    df = pl.DataFrame([
        _member(1, 1, age=40.5, age_nb=41, cease=65, salary=300_000, gender="M"),
    ])
    ctx = _ctx(fc_scheme, {})
    out = apply_post_rate_adjust(
        df, {"kind": "fc_demographics_join"}, {}, basis_tables=fc_tables,
        ctx=ctx)
    assert "pct_married" in out.columns
    out = apply_post_rate_adjust(
        df, {"kind": "fc_family_composition_join"}, {},
        basis_tables=fc_tables, ctx=ctx)
    assert "n_parents" in out.columns
    out = apply_post_rate_adjust(
        df, {"kind": "fc_second_spouse_loading"}, fc_assump_tables, ctx=ctx)
    assert "second_spouse_loading" in out.columns
    out = apply_post_rate_adjust(
        df, {"kind": "fc_db_loading"}, fc_assump_tables, ctx=ctx)
    assert "db_loading" in out.columns


@pytest.mark.unit
def test_post_rate_adjust_unknown_kind_raises():
    df = pl.DataFrame({"basic_technical_premium": [1.0]})
    with pytest.raises(ValueError, match="Unknown post_rate_adjust"):
        apply_post_rate_adjust(df, {"kind": "not_a_kind"}, {})


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
def test_fc_priced_from_real_ctr_when_available(basis, assump):
    ctr = _first_ctr_with("fc")
    if ctr is None:
        pytest.skip("no bundled CTR workbook with FC active")
    members = ctr.members.get("fc_sheet")
    if members is None or members.height == 0:
        pytest.skip("CTR has no populated 'Member Data - FC' sheet")

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
    out = price("fc", ctr, basis, assump, ctx)

    assert out.height == members.height
    for c in ["total_cover", "basic_technical_premium", "rate_basic"]:
        assert c in out.columns
        assert out[c].null_count() == 0
    assert out["basic_technical_premium"].sum() >= 0.0
    active = out.filter(pl.col("total_cover") > 0)
    assert (active["basic_technical_premium"]
            / active["total_cover"] * 1000
            - active["rate_basic"]).abs().max() < 1e-6
