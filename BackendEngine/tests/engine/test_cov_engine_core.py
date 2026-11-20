"""Coverage-oriented unit tests for pricing_engine core helper modules.

Targets: helpers_common, helpers (dispatch), helpers_gla, helpers_education,
recipes, engine_paths.  All frames are minimal inline polars DataFrames.
"""
from __future__ import annotations

import runpy
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import polars as pl
import pricing_engine.helpers as helpers_mod
import pytest
from pricing_engine import engine_paths
from pricing_engine.helpers import apply_cover_caps, apply_special_premium
from pricing_engine.helpers_common import (
    _apply_baseline_yr,
    _apply_ci_industry_loading,
    _apply_ci_mortality_dynamic,
    _apply_ci_standalone_loading,
    _apply_industry,
    _apply_mortality_gender_swap,
    _apply_product_type_adj,
    _apply_salary_band,
    _apply_salary_factor,
    _apply_trend,
    _apply_waiting_pd,
    _as_yes,
    _cap_by_assumption_limit,
    _cap_by_prior_cover,
    _cap_category_max_cover,
    _cap_category_max_cover_field,
    _cap_spouse_cover_cap,
    _co_eligibility_mask,
    _co_rate,
    _gip_asof,
    _gip_key_part,
    _gip_wp_months,
    _gipf,
    _lookup_margin,
    _lookup_spouse_factor,
    _normalise_percent,
    _vec_asof_by_gender,
    _vec_asof_cascade,
    _vec_wp_months,
    _vec_yearfrac_30_360,
    apply_ctr_max_cover,
    apply_factor,
    apply_income_taper,
    apply_prb_premium,
    apply_profit_margin,
    attach_category,
    co_premium_ci,
    co_premium_spouse,
    compute_cover,
    compute_qx,
    deactivate_when_status,
    mark_product_active,
    pivot_categories_wide,
    resolve_nra,
    tax_gross_up,
    zero_addon,
    zero_if_status,
)
from pricing_engine.helpers_education import (
    _apply_educate_a_child_composite,
    _apply_education_guardian_composite,
    _edu_guardian_salary_tier,
)
from pricing_engine.helpers_gla import (
    _apply_ti_scales_gla,
    _cap_by_lsdb_or_gla,
    _cap_gla_and_lsdb_max_multiple,
    attach_gla_cover_for_ti,
    co_premium_gla,
    co_premium_lsdb,
)
from pricing_engine.recipes import RECIPES

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# shared fixtures / builders
# ---------------------------------------------------------------------------

def _ctx(rating=None, limits=None, globals_=None, calc=date(2026, 1, 1),
         scheme_size="small", primary="", secondary="", **scheme_kw):
    scheme = SimpleNamespace(rating=rating or {}, **scheme_kw)
    ctx = SimpleNamespace(
        scheme=scheme,
        globals=globals_ or {},
        calc_date=calc,
        scheme_size=scheme_size,
        primary_industry=primary,
        secondary_industry=secondary,
        assump_limits=limits or {},
    )
    ctx.limits_for = lambda key, _c=ctx: _c.assump_limits.get(key, {})
    return ctx


def _conversion_option_table():
    """Minimal conversion_option assumption table."""
    rows = [
        # product, age_condition, co_type, rate
        ("gla", "<=65", "retirement_and_withdrawal", 0.50),
        ("gla", ">65", "retirement_and_withdrawal", 0.40),
        ("gla", "<=65", "withdrawal_only", 0.30),
        ("gla", ">65", "withdrawal_only", 0.20),
        ("gla", "code_map", "retirement_and_withdrawal", 1.0),
        ("gla", "code_map", "withdrawal_only", 2.0),
        ("lsdb", "<=65", "all", 0.50),
        ("lsdb", ">65", "all", 0.40),
        ("spouse", "<=65", "all", 0.50),
        ("spouse", ">65", "all", 0.30),
        ("ci", "<=65", "all", 0.25),
    ]
    return pl.DataFrame(
        {
            "product": [r[0] for r in rows],
            "age_condition": [r[1] for r in rows],
            "co_type": [r[2] for r in rows],
            "rate": [r[3] for r in rows],
        }
    )


# ---------------------------------------------------------------------------
# helpers_common: category plumbing
# ---------------------------------------------------------------------------

def test_pivot_categories_wide_empty_product_returns_schema_frame():
    categories = pl.DataFrame(
        {
            "product": ["gla"],
            "category_number": [1],
            "field": ["category_flag"],
            "value": [10.0],
        }
    )
    wide = pivot_categories_wide(categories, "no_such_product")
    assert wide.columns == ["category_number"]
    assert wide.height == 0


def test_pivot_categories_wide_pivots_all_fields():
    categories = pl.DataFrame(
        {
            "product": ["gla", "gla", "gla", "gla", "lsdb", "lsdb"],
            "category_number": [1, 1, 2, 2, 1, 1],
            "field": ["category_flag", "nra_cat", "category_flag", "nra_cat",
                      "category_flag", "nra_cat"],
            "value": [101.0, 65.0, 102.0, 60.0, 201.0, 62.0],
        }
    )
    wide = pivot_categories_wide(categories, "gla").sort("category_number")
    assert wide["category_number"].to_list() == [1, 2]
    assert wide["nra_cat"].to_list() == [65.0, 60.0]
    assert wide["category_flag_join"].to_list() == [101.0, 102.0]


def test_attach_category_derives_number_from_flag_when_no_join_column():
    cats_wide = pl.DataFrame({"category_number": pl.Series([], dtype=pl.Int64)})
    df = pl.DataFrame({"category_flag": [1001.0, 1003.0]})
    out = attach_category(df, cats_wide)
    assert out["category_number"].to_list() == [2, 4]


def test_attach_category_returns_unchanged_without_flag_or_number():
    cats_wide = pl.DataFrame({"category_number": pl.Series([], dtype=pl.Int64)})
    df = pl.DataFrame({"member_no": [1]})
    assert attach_category(df, cats_wide).columns == df.columns


def test_attach_category_keeps_existing_category_number():
    cats_wide = pl.DataFrame({"category_number": pl.Series([], dtype=pl.Int64)})
    df = pl.DataFrame({"category_flag": [1001.0], "category_number": [7]})
    out = attach_category(df, cats_wide)
    assert out["category_number"].to_list() == [7]


@pytest.mark.parametrize("rating,expected", [
    ("Yes", True),
    ("Standalone", True),   # anything other than 'No' activates the scheme
    ("Accelerator", True),
    ("No", False),
    ("no", False),
    (None, False),
])
def test_mark_product_active_rating_variants(rating, expected):
    df = pl.DataFrame({"category_number": [1, 9]})
    out = mark_product_active(df, rating, (1,))
    assert out["product_active"].to_list() == [expected, False]


def test_resolve_nra_custom_only_column():
    df = pl.DataFrame({"nra": [70.0, None]})
    out = resolve_nra(df, custom_col="nra", category_col="nra_cat")
    assert out["nra_final"].to_list() == [70.0, 65.0]


def test_resolve_nra_custom_prefix_uses_custom_value():
    df = pl.DataFrame(
        {"nra": [62.0, 99.0], "nra_cat": ["Custom", "63"]}
    )
    out = resolve_nra(df)
    assert out["nra_final"].to_list() == [62.0, 63.0]


# ---------------------------------------------------------------------------
# helpers_common: apply_factor dispatch + factor implementations
# ---------------------------------------------------------------------------

def _mortality_basis(product="gla"):
    return {
        "mortality": pl.DataFrame(
            {
                "product": [product, product],
                "age": [30, 30],
                "gender": ["F", "M"],
                "rate": [2.0, 3.0],
            }
        )
    }


def test_apply_factor_mortality_dispatch():
    df = pl.DataFrame(
        {"rate_basic": [1.0, 1.0], "gender": ["F", "M"], "age_nb": [30, 30]}
    )
    out = apply_factor(df, {"kind": "mortality", "product": "gla"},
                       _mortality_basis(), {}, None)
    assert out["rate_basic"].to_list() == [2.0, 3.0]


def test_apply_factor_mortality_gender_swap_uses_opposite_gender():
    df = pl.DataFrame(
        {"rate_basic": [1.0, 1.0], "gender": ["M", "F"], "age_nb": [30, 30]}
    )
    out = apply_factor(
        df, {"kind": "mortality_gender_swap", "product": "gla", "age_col": "age_nb"},
        _mortality_basis(), {}, None,
    )
    # M looks up F rate (2.0); F looks up M rate (3.0)
    assert out["rate_basic"].to_list() == [2.0, 3.0]
    assert "__swapped_gender" not in out.columns


def test_apply_mortality_gender_swap_missing_rate_yields_zero():
    df = pl.DataFrame({"rate_basic": [1.0], "gender": ["X"], "age_nb": [30]})
    out = _apply_mortality_gender_swap(df, {"product": "gla"}, _mortality_basis(),
                                       "rate_basic")
    assert out["rate_basic"].to_list() == [0.0]


def test_apply_salary_band_both_genders_asof():
    df = pl.DataFrame(
        {
            "rate_basic": [10.0, 10.0, 10.0],
            "gender": ["M", "F", "M"],
            "annual_salary": [30_000.0, 80_000.0, 50_000.0],
        }
    )
    basis = {
        "salary_band": pl.DataFrame(
            {
                "product": ["gla", "gla"],
                "gender": ["both", "both"],
                "salary_from": [0.0, 50_000.0],
                "inc_adj": [1.0, 1.2],
            }
        )
    }
    out = _apply_salary_band(df, {"product": "gla", "factor": "inc_adj"},
                             basis, "rate_basic")
    assert out["rate_basic"].to_list() == pytest.approx([10.0, 12.0, 12.0])
    assert "__sb_inc_adj" not in out.columns


def test_apply_salary_band_per_gender():
    df = pl.DataFrame(
        {
            "rate_basic": [10.0, 10.0],
            "gender": ["M", "F"],
            "annual_salary": [80_000.0, 80_000.0],
        }
    )
    basis = {
        "salary_band": pl.DataFrame(
            {
                "product": ["gla"] * 4,
                "gender": ["M", "M", "F", "F"],
                "salary_from": [0.0, 50_000.0, 0.0, 50_000.0],
                "inc_adj": [1.0, 1.5, 1.0, 2.0],
            }
        )
    }
    out = _apply_salary_band(df, {"product": "gla", "factor": "inc_adj"},
                             basis, "rate_basic")
    result = dict(zip(out["gender"].to_list(), out["rate_basic"].to_list()))
    assert result == {"M": 15.0, "F": 20.0}


def test_apply_salary_band_missing_value_column_raises():
    basis = {
        "salary_band": pl.DataFrame(
            {
                "product": ["gla"],
                "gender": ["both"],
                "salary_from": [0.0],
                "acaf_adj": [1.0],
            }
        )
    }
    df = pl.DataFrame({"rate_basic": [1.0], "gender": ["M"],
                       "annual_salary": [1.0]})
    with pytest.raises(ValueError, match="no value column"):
        _apply_salary_band(df, {"product": "gla", "factor": "inc_adj"},
                           basis, "rate_basic")


def test_apply_salary_factor_fallback_column_name():
    df = pl.DataFrame(
        {
            "rate_basic": [10.0],
            "gender": ["M"],
            "annual_salary": [100.0],
        }
    )
    basis = {
        "salary_band": pl.DataFrame(
            {
                "product": ["adb"],
                "gender": ["both"],
                "salary_from": [0.0],
                "Salary Factor": [1.25],
            }
        )
    }
    out = _apply_salary_factor(df, {"product": "adb"}, basis, "rate_basic")
    assert out["rate_basic"].to_list() == [12.5]


def test_apply_salary_factor_no_value_col_raises():
    basis = {
        "salary_band": pl.DataFrame(
            {"product": ["adb"], "gender": ["both"], "salary_from": [0.0],
             "other": [1.0]}
        )
    }
    df = pl.DataFrame({"rate_basic": [1.0], "gender": ["M"],
                       "annual_salary": [1.0]})
    with pytest.raises(ValueError, match="no value col"):
        _apply_salary_factor(df, {"product": "adb"}, basis, "rate_basic")


def test_apply_industry_secondary_match_by_size():
    basis = {
        "industry": pl.DataFrame(
            {
                "product": ["gla", "gla", "gla"],
                "primary": ["P1", "P1", "P1"],
                "secondary": ["Retail", "Retail", "Mining"],
                "scheme_size": ["small", "large", "small"],
                "inc_adj": [1.1, 9.9, 1.5],
            }
        )
    }
    ctx = _ctx(secondary="Retail", scheme_size="small")
    df = pl.DataFrame({"rate_basic": [10.0]})
    out = _apply_industry(df, {"product": "gla", "factor": "inc_adj",
                               "by_scheme_size": True}, basis, ctx, "rate_basic")
    assert out["rate_basic"].to_list() == pytest.approx([11.0])


def test_apply_industry_primary_match_refines_by_secondary():
    basis = {
        "industry": pl.DataFrame(
            {
                "product": ["gla", "gla"],
                "primary": ["Mining", "Mining"],
                "secondary": ["Retail", "Other"],
                "inc_adj": [1.3, 1.7],
            }
        )
    }
    ctx = _ctx(primary="Mining", secondary="Retail")
    df = pl.DataFrame({"rate_basic": [10.0]})
    out = _apply_industry(
        df, {"product": "gla", "factor": "inc_adj", "match": "primary",
             "by_scheme_size": False},
        basis, ctx, "rate_basic",
    )
    assert out["rate_basic"].to_list() == pytest.approx([13.0])


def test_apply_industry_no_match_defaults_to_one():
    basis = {
        "industry": pl.DataFrame(
            {
                "product": ["gla"],
                "primary": ["P"],
                "secondary": ["Other"],
                "inc_adj": [5.0],
            }
        )
    }
    ctx = _ctx(secondary="Retail")
    df = pl.DataFrame({"rate_basic": [10.0]})
    out = _apply_industry(df, {"product": "gla", "factor": "inc_adj"},
                          basis, ctx, "rate_basic")
    assert out["rate_basic"].to_list() == [10.0]


def test_apply_industry_unknown_match_raises():
    ctx = _ctx()
    df = pl.DataFrame({"rate_basic": [1.0]})
    basis = {
        "industry": pl.DataFrame(
            {"product": ["gla"], "primary": ["P"], "secondary": ["S"],
             "inc_adj": [1.0]}
        )
    }
    with pytest.raises(ValueError, match="Unknown industry match"):
        _apply_industry(df, {"product": "gla", "factor": "inc_adj",
                             "match": "bogus"}, basis, ctx, "rate_basic")


def test_apply_trend_both_genders_and_year_fallback():
    # no row for calc year 2026 -> falls back to max available year (2024)
    basis = {
        "trend": pl.DataFrame(
            {
                "product": ["gla", "gla"],
                "year": [2024, 2024],
                "gender": ["both", "both"],
                "salary_from": [0.0, 100_000.0],
                "factor": [1.0, 1.1],
            }
        )
    }
    ctx = _ctx(calc=date(2026, 6, 1))
    df = pl.DataFrame(
        {"rate_basic": [10.0, 10.0], "gender": ["M", "F"],
         "annual_salary": [50_000.0, 150_000.0]}
    )
    out = _apply_trend(df, {"product": "gla"}, basis, ctx, "rate_basic")
    assert out["rate_basic"].to_list() == pytest.approx([10.0, 11.0])


def test_apply_trend_per_gender_current_year():
    basis = {
        "trend": pl.DataFrame(
            {
                "product": ["gla", "gla"],
                "year": [2026, 2026],
                "gender": ["M", "F"],
                "salary_from": [0.0, 0.0],
                "factor": [1.2, 1.4],
            }
        )
    }
    ctx = _ctx(calc=date(2026, 1, 1))
    df = pl.DataFrame(
        {"rate_basic": [10.0, 10.0], "gender": ["M", "F"],
         "annual_salary": [1.0, 1.0]}
    )
    out = _apply_trend(df, {"product": "gla"}, basis, ctx, "rate_basic")
    result = dict(zip(out["gender"].to_list(), out["rate_basic"].to_list()))
    assert result["M"] == pytest.approx(12.0)
    assert result["F"] == pytest.approx(14.0)


def test_apply_waiting_pd_multiplies_by_matched_factor():
    basis = {
        "waiting_periods": pl.DataFrame(
            {
                "product": ["lsdb", "lsdb"],
                "wp_months": [3.0, 6.0],
                "inc_adj": [1.0, 0.9],
            }
        )
    }
    df = pl.DataFrame({"rate_basic": [10.0, 10.0], "waiting_period": [3, 12]})
    out = _apply_waiting_pd(df, {"product": "lsdb"}, basis, "rate_basic")
    assert out["rate_basic"].to_list() == [10.0, 10.0]  # 12 has no row -> 1.0


def test_apply_waiting_pd_factor_applied():
    basis = {
        "waiting_periods": pl.DataFrame(
            {"product": ["lsdb"], "wp_months": [6.0], "inc_adj": [0.9]}
        )
    }
    df = pl.DataFrame({"rate_basic": [10.0], "waiting_period": [6]})
    out = _apply_waiting_pd(df, {"product": "lsdb"}, basis, "rate_basic")
    assert out["rate_basic"].to_list() == pytest.approx([9.0])


def test_apply_baseline_yr_uses_calc_year_row_else_one():
    basis = {
        "trend": pl.DataFrame(
            {"product": ["lsdb"], "year": [2026], "gender": ["both"],
             "salary_from": [0.0], "factor": [1.05]}
        )
    }
    df = pl.DataFrame({"rate_basic": [10.0]})
    ctx = _ctx(calc=date(2026, 1, 1))
    out = _apply_baseline_yr(df, {"product": "lsdb"}, basis, ctx, "rate_basic")
    assert out["rate_basic"].to_list() == pytest.approx([10.5])

    ctx2 = _ctx(calc=date(2030, 1, 1))
    out2 = _apply_baseline_yr(df, {"product": "lsdb"}, basis, ctx2, "rate_basic")
    assert out2["rate_basic"].to_list() == pytest.approx([10.0])


def test_apply_product_type_adj_variant_lookup():
    basis = {
        "product_type_adj": pl.DataFrame(
            {
                "product": ["lsdb", "lsdb"],
                "product_type": ["Accelerator", "Standalone"],
                "adj": [0.9, 1.0],
            }
        )
    }
    df = pl.DataFrame({"rate_basic": [10.0]})
    accel = _apply_product_type_adj(df, {"product": "lsdb"}, basis,
                                    _ctx(rating={"lsdb": "Accelerator"}),
                                    "rate_basic")
    assert accel["rate_basic"].to_list() == pytest.approx([9.0])
    stand = _apply_product_type_adj(df, {"product": "lsdb"}, basis,
                                    _ctx(rating={"lsdb": "Standalone"}),
                                    "rate_basic")
    assert stand["rate_basic"].to_list() == pytest.approx([10.0])


def test_apply_ci_standalone_loading_scales_new_options():
    assump = {
        "ci_table_mapping": pl.DataFrame(
            {
                "product_option": ["OptNew", "OptOld"],
                "variant_type": ["New", "Existing"],
                "accelerator_table": ["T", "T"],
                "standalone_table": ["T", "T"],
                "age_definition": ["ALB", "ALB"],
            }
        )
    }
    ctx = _ctx(rating={"ci": "Standalone"},
               globals_={"ci_standalone_loading": 0.10})
    df = pl.DataFrame({"rate_basic": [10.0, 10.0],
                       "product_option": ["OptNew", "OptOld"]})
    out = _apply_ci_standalone_loading(df, {}, assump, ctx, "rate_basic")
    assert out["rate_basic"].to_list() == pytest.approx([11.0, 10.0])


def test_apply_ci_standalone_loading_skipped_when_not_standalone():
    df = pl.DataFrame({"rate_basic": [10.0], "product_option": ["OptNew"]})
    ctx = _ctx(rating={"ci": "Accelerator"})
    out = _apply_ci_standalone_loading(df, {}, {}, ctx, "rate_basic")
    assert out["rate_basic"].to_list() == [10.0]


def test_apply_ci_standalone_loading_missing_global_raises():
    ctx = _ctx(rating={"ci": "Standalone"}, globals_={})
    df = pl.DataFrame({"rate_basic": [1.0], "product_option": ["X"]})
    with pytest.raises(KeyError, match="ci_standalone_loading"):
        _apply_ci_standalone_loading(df, {}, {"ci_table_mapping": pl.DataFrame()},
                                     ctx, "rate_basic")


def test_apply_ci_standalone_loading_missing_product_option_raises():
    ctx = _ctx(rating={"ci": "Standalone"},
               globals_={"ci_standalone_loading": 0.1})
    df = pl.DataFrame({"rate_basic": [1.0]})
    with pytest.raises(KeyError, match="product_option"):
        _apply_ci_standalone_loading(
            df, {},
            {"ci_table_mapping": pl.DataFrame(
                {"product_option": ["X"], "variant_type": ["New"]})},
            ctx, "rate_basic")


def test_apply_ci_industry_loading_class_mapping():
    basis = {
        "industry": pl.DataFrame(
            {"product": ["ci"], "grouping": ["Mining"], "class": ["B"]}
        )
    }
    ctx = _ctx(primary="Mining")
    df = pl.DataFrame({"rate_basic": [15.0]})
    out = _apply_ci_industry_loading(df, {}, basis, ctx, "rate_basic")
    assert out["rate_basic"].to_list() == pytest.approx([12.0])


def test_apply_ci_industry_loading_missing_industry_raises():
    basis = {
        "industry": pl.DataFrame(
            {"product": ["ci"], "grouping": ["Mining"], "class": ["A"]}
        )
    }
    with pytest.raises(KeyError, match="no row for primary_industry"):
        _apply_ci_industry_loading(pl.DataFrame({"rate_basic": [1.0]}), {},
                                   basis, _ctx(primary="Fishing"), "rate_basic")


def test_apply_ci_industry_loading_unknown_class_raises():
    basis = {
        "industry": pl.DataFrame(
            {"product": ["ci"], "grouping": ["Mining"], "class": ["Z"]}
        )
    }
    with pytest.raises(ValueError, match="unknown class letter"):
        _apply_ci_industry_loading(pl.DataFrame({"rate_basic": [1.0]}), {},
                                   basis, _ctx(primary="Mining"), "rate_basic")


def _ci_inputs():
    ci_map = pl.DataFrame(
        {
            "product_option": ["OptA"],
            "accelerator_table": ["CI_Sanlam_Accelerator"],
            "standalone_table": ["CI_Sanlam_Standalone"],
            "age_definition": ["ALB"],
            "variant_type": ["New"],
        }
    )
    basis = {
        "mortality": pl.DataFrame(
            {
                "product": ["ci_sanlam_acc", "ci_sanlam_acc"],
                "age": [30, 31],
                "gender": ["F", "F"],
                "rate": [3.0, 3.5],
            }
        )
    }
    return ci_map, basis


def test_apply_factor_ci_mortality_dynamic_alb_lookup():
    ci_map, basis = _ci_inputs()
    df = pl.DataFrame(
        {
            "rate_basic": [2.0, 2.0],
            "product_option": ["OptA", "Unmapped"],
            "age": [30.4, 30.4],
            "age_nb": [31, 31],
            "gender": ["F", "F"],
        }
    )
    ctx = _ctx(rating={"ci": "Accelerator"})
    out = apply_factor(df, {"kind": "ci_mortality_dynamic"}, basis,
                       {"ci_table_mapping": ci_map}, ctx)
    # OptA resolves to ci_sanlam_acc, ALB -> floor(30.4)=30 -> rate 3.0 -> 6.0
    # Unmapped option -> no lookup -> rate 0.0
    assert out["rate_basic"].to_list() == [6.0, 0.0]
    assert "__ci_variant_type" in out.columns


def test_apply_ci_mortality_dynamic_anb_uses_age_nb():
    ci_map, basis = _ci_inputs()
    ci_map = ci_map.with_columns(pl.lit("ANB").alias("age_definition"))
    df = pl.DataFrame(
        {
            "rate_basic": [2.0],
            "product_option": ["OptA"],
            "age": [30.4],
            "age_nb": [31],
            "gender": ["F"],
        }
    )
    out = _apply_ci_mortality_dynamic(
        df, {"_ctx_ci_rating": "accelerator"}, basis,
        {"ci_table_mapping": ci_map}, "rate_basic")
    # ANB -> age_nb 31 -> rate 3.5 -> 7.0
    assert out["rate_basic"].to_list() == [7.0]


def test_apply_ci_mortality_dynamic_invalid_variant_raises():
    df = pl.DataFrame({"rate_basic": [1.0], "product_option": ["X"],
                       "age": [30.0], "age_nb": [31], "gender": ["F"]})
    ctx = _ctx(rating={"ci": "Yes"})
    with pytest.raises(ValueError, match="cannot resolve variant"):
        apply_factor(df, {"kind": "ci_mortality_dynamic"}, {},
                     {"ci_table_mapping": pl.DataFrame()}, ctx)


def test_apply_ci_mortality_dynamic_no_product_option_zeroes_rate():
    df = pl.DataFrame({"rate_basic": [5.0], "age": [30.0], "age_nb": [31],
                       "gender": ["F"]})
    out = _apply_ci_mortality_dynamic(
        df, {"_ctx_ci_rating": "standalone"}, {},
        {"ci_table_mapping": pl.DataFrame()}, "rate_basic")
    assert out["rate_basic"].to_list() == [0.0]
    assert out["__ci_variant_type"].to_list() == ["Existing"]


def test_apply_ci_mortality_dynamic_no_resolutions_zeroes_rate():
    ci_map, basis = _ci_inputs()
    df = pl.DataFrame(
        {"rate_basic": [5.0], "product_option": ["NotMapped"],
         "age": [30.0], "age_nb": [31], "gender": ["F"]}
    )
    out = _apply_ci_mortality_dynamic(
        df, {"_ctx_ci_rating": "accelerator"}, basis,
        {"ci_table_mapping": ci_map}, "rate_basic")
    assert out["rate_basic"].to_list() == [0.0]
    assert out["__ci_variant_type"].to_list() == ["Existing"]


# ---------------------------------------------------------------------------
# helpers_common: compute_cover + caps
# ---------------------------------------------------------------------------

def test_compute_cover_multiple_or_data():
    df = pl.DataFrame(
        {
            "cover_type": ["Multiple", "Cover in member data"],
            "benefit_multiple": [2.0, 2.0],
            "annual_salary": [100_000.0, 100_000.0],
            "gla": [0.0, 80_000.0],
        }
    )
    out = compute_cover(df, {"cover_rule": "multiple_or_data",
                             "cover_column": "gla"})
    assert out["pre_tax_cover"].to_list() == [200_000.0, 80_000.0]


def test_compute_cover_creates_missing_cover_column():
    df = pl.DataFrame(
        {
            "cover_type": ["Other"],
            "benefit_multiple": [2.0],
            "annual_salary": [100_000.0],
        }
    )
    out = compute_cover(df, {"cover_rule": "multiple_or_data",
                             "cover_column": "gla"})
    assert out["pre_tax_cover"].to_list() == [0.0]


def test_compute_cover_multiple_or_data_or_gla():
    df = pl.DataFrame(
        {
            "cover_type": ["Multiple", "Set equal to GLA Cover", "Other"],
            "benefit_multiple": [2.0, 2.0, 2.0],
            "annual_salary": [100_000.0, 100_000.0, 100_000.0],
            "terminal_illness": [0.0, 0.0, 50_000.0],
            "__gla_cover_for_ti": [None, 75_000.0, 75_000.0],
        }
    )
    out = compute_cover(df, {"cover_rule": "multiple_or_data_or_gla",
                             "cover_column": "terminal_illness"})
    assert out["pre_tax_cover"].to_list() == [200_000.0, 75_000.0, 50_000.0]


@pytest.mark.parametrize("rule", [
    "fc_composite", "gip_composite", "tip_composite",
    "education_guardian_composite", "educate_a_child_composite",
])
def test_compute_cover_composite_rules_zero(rule):
    df = pl.DataFrame({"cover_type": ["Multiple"], "benefit_multiple": [9.0],
                       "annual_salary": [1.0], "x": [5.0]})
    out = compute_cover(df, {"cover_rule": rule, "cover_column": "x"})
    assert out["pre_tax_cover"].to_list() == [0.0]


def test_compute_cover_unknown_rule_raises():
    df = pl.DataFrame({"cover_type": ["x"], "c": [1.0]})
    with pytest.raises(ValueError, match="Unknown cover_rule"):
        compute_cover(df, {"cover_rule": "nope", "cover_column": "c"})


def test_cap_category_max_cover():
    df = pl.DataFrame({"pre_tax_cover": [100.0, 50.0],
                       "maximum_cover_amount": [60.0, None]})
    out = _cap_category_max_cover(df, {}, None, {})
    assert out["pre_tax_cover"].to_list() == [60.0, 50.0]


def test_cap_category_max_cover_missing_column_noop():
    df = pl.DataFrame({"pre_tax_cover": [100.0]})
    out = _cap_category_max_cover(df, {}, None, {})
    assert out["pre_tax_cover"].to_list() == [100.0]


def test_cap_category_max_cover_field():
    df = pl.DataFrame({"pre_tax_cover": [100.0, 10.0], "my_cap": [40.0, None]})
    out = _cap_category_max_cover_field(df, {"field": "my_cap"}, None, {})
    assert out["pre_tax_cover"].to_list() == [40.0, 10.0]


def test_cap_by_prior_cover():
    df = pl.DataFrame({"member_no": [1, 2], "pre_tax_cover": [100.0, 100.0]})
    prior = {"gla": pl.DataFrame({"member_no": [1], "total_cover": [60.0]})}
    out = _cap_by_prior_cover(df, {"prior": "gla"}, None, prior)
    out = out.sort("member_no")
    assert out["pre_tax_cover"].to_list() == [60.0, 100.0]


def test_cap_by_prior_cover_missing_prior_noop():
    df = pl.DataFrame({"member_no": [1], "pre_tax_cover": [100.0]})
    out = _cap_by_prior_cover(df, {"prior": "gla"}, None, {})
    assert out["pre_tax_cover"].to_list() == [100.0]


def test_cap_spouse_cover_cap():
    df = pl.DataFrame(
        {
            "member_no": [1, 2, 3],
            "pre_tax_cover": [300_000.0, 300_000.0, 300_000.0],
            "annual_salary": [100_000.0, 0.0, 100_000.0],
        }
    )
    prior = {"gla": pl.DataFrame({"member_no": [1, 2],
                                 "total_cover": [150_000.0, 180_000.0]})}
    ctx = _ctx(limits={"spouse_gla": {"max_multiple": 2}})
    cap = {"prior": "gla", "max_multiple_key": ("spouse_gla", "max_multiple")}
    out = _cap_spouse_cover_cap(df, cap, ctx, prior).sort("member_no")
    # m1: min(300k, min(200k sal cap, 150k gla)) = 150k
    # m2: salary 0 -> min(300k, 180k) = 180k
    # m3: no gla -> min(300k, min(200k, 300k)) = 200k
    assert out["pre_tax_cover"].to_list() == [150_000.0, 180_000.0, 200_000.0]


def test_cap_spouse_cover_cap_missing_prior_noop():
    df = pl.DataFrame({"member_no": [1], "pre_tax_cover": [5.0]})
    out = _cap_spouse_cover_cap(df, {"prior": "gla",
                                     "max_multiple_key": ("spouse_gla", "m")},
                                _ctx(), {})
    assert out["pre_tax_cover"].to_list() == [5.0]


def test_cap_by_assumption_limit():
    df = pl.DataFrame({"pre_tax_cover": [100.0, 30.0]})
    ctx = _ctx(limits={"spouse_lsdb": {"max_cover": 50.0}})
    out = _cap_by_assumption_limit(
        df, {"product_key": "spouse_lsdb", "param_key": "max_cover"}, ctx, {})
    assert out["pre_tax_cover"].to_list() == [50.0, 30.0]


def test_cap_by_assumption_limit_absent_noop():
    df = pl.DataFrame({"pre_tax_cover": [100.0]})
    ctx = _ctx(limits={"gla": {}})
    out = _cap_by_assumption_limit(df, {"product_key": "gla"}, ctx, {})
    assert out["pre_tax_cover"].to_list() == [100.0]


def test_apply_ctr_max_cover_ctr_only_and_null_cap():
    df = pl.DataFrame(
        {
            "category_number": [1, 2, 3],
            "pre_tax_cover": [100.0, 100.0, 100.0],
        }
    )
    ctx = _ctx(max_cover_per_category={"gla": {1: 50.0, 2: None}})
    out = apply_ctr_max_cover(df, ctx, "gla").sort("category_number")
    # cat1 capped, cat2 cap null -> unchanged, cat3 no cap -> unchanged
    assert out["pre_tax_cover"].to_list() == [50.0, 100.0, 100.0]


def test_apply_ctr_max_cover_assumption_fallback_only():
    df = pl.DataFrame({"category_number": [1], "pre_tax_cover": [100.0]})
    ctx = _ctx()  # no max_cover_per_category attr
    out = apply_ctr_max_cover(df, ctx, "gla",
                              assump_fallback_limits={"max_cover": 40.0})
    assert out["pre_tax_cover"].to_list() == [40.0]


# ---------------------------------------------------------------------------
# helpers_common: tax_gross_up
# ---------------------------------------------------------------------------

def _tax_tables(rows):
    return pl.DataFrame(
        {
            "country": [r[0] for r in rows],
            "tax_year": [r[1] for r in rows],
            "band_from": [r[2] for r in rows],
            "basic_amount": [r[3] for r in rows],
            "margin": [r[4] for r in rows],
        }
    )


def test_tax_gross_up_single_band():
    tax = _tax_tables([("SA", "2026/27", 0.0, 0.0, 0.25)])
    df = pl.DataFrame({"pre_tax_cover": [100_000.0],
                       "cover_net_of_tax": ["Yes"]})
    out = tax_gross_up(df, tax)
    # tax = 0 + 100000*0.25 = 25000; grossed = 100000 + 25000/0.75
    assert out["tax_on_cover"].to_list() == pytest.approx([33_333.3333], rel=1e-4)


def test_tax_gross_up_new_band_breach():
    tax = _tax_tables(
        [
            ("SA", "2026/27", 0.0, 0.0, 0.20),
            ("SA", "2026/27", 120_000.0, 24_000.0, 0.40),
        ]
    )
    df = pl.DataFrame({"pre_tax_cover": [100_000.0],
                       "cover_net_of_tax": ["Yes"]})
    out = tax_gross_up(df, tax)
    # tax_required = 20000 -> grossed 125000 -> breaches 120000 band.
    # tax_if_new = 20000*(1/0.8-1)*(0.8/0.6) + 20000 + 0/0.6 = 26666.67
    assert out["tax_on_cover"].to_list() == pytest.approx([26_666.6667], rel=1e-4)


def test_tax_gross_up_not_net_of_tax_returns_zero():
    tax = _tax_tables([("SA", "2026/27", 0.0, 0.0, 0.25)])
    df = pl.DataFrame({"pre_tax_cover": [100_000.0],
                       "cover_net_of_tax": ["No"]})
    out = tax_gross_up(df, tax)
    assert out["tax_on_cover"].to_list() == [0.0]


def test_tax_gross_up_ignores_gip_rows_and_other_countries():
    tax = _tax_tables(
        [
            ("SA", "2026/27_gip", 0.0, 0.0, 0.99),   # excluded
            ("NA", "2026/27", 0.0, 0.0, 0.99),      # excluded
            ("SA", "2026/27", 0.0, 0.0, 0.20),
        ]
    )
    df = pl.DataFrame({"pre_tax_cover": [100_000.0],
                       "cover_net_of_tax": ["Yes"]})
    out = tax_gross_up(df, tax)
    # 100000*0.2 = 20000 -> 20000/0.8 = 25000
    assert out["tax_on_cover"].to_list() == pytest.approx([25_000.0])


# ---------------------------------------------------------------------------
# helpers_common: taper / vector utils / eligibility
# ---------------------------------------------------------------------------

def test_apply_income_taper_no_matching_rows_returns_input():
    df = pl.DataFrame({"reduction_period": ["None", "Other"],
                       "pre_tax_cover": [1.0, 2.0]})
    out = apply_income_taper(df)
    assert out["pre_tax_cover"].to_list() == [1.0, 2.0]


def test_apply_income_taper_default_six_month_reduction():
    df = pl.DataFrame(
        {
            "age": [60.0],
            "nra_final": [65.0],
            "annual_salary": [120_000.0],
            "reduction_period": ["Income Tapering"],
            "status": ["A"],
            "pre_tax_cover": [600_000.0],
        }
    )
    out = apply_income_taper(df)
    # months_floor = (65-60)*12 = 60; 60 - 6 = 54; 120000*54/12 = 540000
    assert out["pre_tax_cover"].to_list() == [540_000.0]


@pytest.mark.parametrize("value,expected", [
    ("3 months", 3),
    ("12", 12),
    ("wp 6 m", 6),
    ("", None),
    (None, None),
    ("months", None),
])
def test_vec_wp_months_parsing(value, expected):
    assert _vec_wp_months(value) == expected
    assert _gip_wp_months(value) == expected


def test_vec_asof_cascade_backward_lookup():
    rows = [
        {"salary_from": 0.0, "inc": 1.0},
        {"salary_from": 50_000.0, "inc": 1.5},
    ]
    df = pl.DataFrame({"annual_salary": [30_000.0, 80_000.0, 50_000.0]})
    expr = _vec_asof_cascade(rows, "salary_from", "inc", pl.col("annual_salary"))
    out = df.with_columns(expr.alias("f"))
    assert out["f"].to_list() == [1.0, 1.5, 1.5]


def test_vec_asof_by_gender():
    sb_by_g = {
        "M": [{"salary_from": 0.0, "v": 1.0}, {"salary_from": 50_000.0, "v": 1.5}],
        "F": [{"salary_from": 0.0, "v": 2.0}, {"salary_from": 50_000.0, "v": 2.5}],
    }
    df = pl.DataFrame(
        {"gender": ["M", "F", "X"], "annual_salary": [80_000.0, 80_000.0, 80_000.0]}
    )
    expr = _vec_asof_by_gender(sb_by_g, "v", pl.col("annual_salary"),
                               pl.col("gender"))
    out = df.with_columns(expr.alias("f"))
    assert out["f"].to_list() == [1.5, 2.5, None]


def test_vec_yearfrac_30_360():
    df = pl.DataFrame(
        {
            "d0": [date(2024, 1, 15), date(2024, 1, 31), date(2024, 1, 15),
                   date(2024, 12, 15)],
            "d1": [date(2024, 7, 15), date(2024, 3, 31), date(2024, 3, 31),
                   date(2025, 1, 15)],
        }
    )
    out = df.with_columns(
        _vec_yearfrac_30_360(pl.col("d0"), pl.col("d1")).alias("yf")
    )
    assert out["yf"].to_list() == pytest.approx([0.5, 60 / 360, 76 / 360, 30 / 360])


def test_co_eligibility_mask_with_and_without_statuses():
    df = pl.DataFrame(
        {
            "product_active": [True, True, True],
            "age": [40.0, 70.0, 40.0],
            "ceasing_age": [65.0, 65.0, 65.0],
            "status": ["A", "A", "DB"],
        }
    )
    out = df.with_columns(_co_eligibility_mask(df).alias("e"))
    assert out["e"].to_list() == [True, False, True]
    out2 = df.with_columns(_co_eligibility_mask(df, ("DB",)).alias("e"))
    assert out2["e"].to_list() == [True, False, False]


def test_co_eligibility_mask_ignores_statuses_when_column_missing():
    df = pl.DataFrame({"product_active": [True], "age": [40.0],
                       "ceasing_age": [65.0]})
    out = df.with_columns(_co_eligibility_mask(df, ("DB",)).alias("e"))
    assert out["e"].to_list() == [True]


# ---------------------------------------------------------------------------
# helpers_common: conversion-option premiums
# ---------------------------------------------------------------------------

def test_co_rate_lookup_and_missing():
    tbl = _conversion_option_table().filter(pl.col("product") == "gla")
    assert _co_rate(tbl, "<=65", "withdrawal_only") == 0.30
    assert _co_rate(tbl, "code_map", "retirement_and_withdrawal") == 1.0
    assert _co_rate(tbl, "<=99", "all") == 0.0


def test_co_premium_spouse():
    df = pl.DataFrame(
        {
            "age": [60.0, 60.0, 66.0],
            "nra_final": [65.0, 70.0, 65.0],
            "include_conversion_option": ["Yes", "Yes", "Yes"],
            "basic_technical_premium": [100.0, 100.0, 100.0],
        }
    )
    out = co_premium_spouse(df, None,
                            {"conversion_option": _conversion_option_table()},
                            _ctx())
    # r1: age<65, nra<=65 -> 0.5; r2: age<65, nra>65 -> 0.3; r3: age>=65 -> 0
    assert out["co_premium"].to_list() == pytest.approx([50.0, 30.0, 0.0])


def test_co_premium_spouse_excluded_when_not_yes():
    df = pl.DataFrame(
        {
            "age": [60.0],
            "nra_final": [65.0],
            "include_conversion_option": ["No"],
            "basic_technical_premium": [100.0],
        }
    )
    out = co_premium_spouse(df, None,
                            {"conversion_option": _conversion_option_table()},
                            _ctx())
    assert out["co_premium"].to_list() == [0.0]


def test_co_premium_ci_cutoff_and_status():
    df = pl.DataFrame(
        {
            "age": [40.0, 62.0, 40.0],
            "nra_final": [65.0, 65.0, 65.0],
            "status": ["A", "A", "P"],
            "include_conversion_option": ["Yes", "Yes", "Yes"],
            "basic_technical_premium": [100.0, 100.0, 100.0],
        }
    )
    ctx = _ctx(limits={"ci": {"max_co_age": 65,
                              "withdrawal_only_pre_nra_cap": 5}})
    out = co_premium_ci(df, None,
                        {"conversion_option": _conversion_option_table()}, ctx)
    # cutoff = min(65-5,65)=60; r1 40<60 & A -> 0.25*100; r2 62 -> 0; r3 P -> 0
    assert out["co_premium"].to_list() == [25.0, 0.0, 0.0]


# ---------------------------------------------------------------------------
# helpers_common: misc small functions
# ---------------------------------------------------------------------------

def test_zero_addon_adds_zero_column():
    df = pl.DataFrame({"x": [1]})
    out = zero_addon(df, _ctx(), target="waiver_premium")
    assert out["waiver_premium"].to_list() == [0.0]


@pytest.mark.parametrize("value,expected", [
    ("Yes", True), ("yes", True), ("Y", True), ("true", True),
    ("1", True), ("active", True), (" No ", False), (None, False),
    (0, False), (1, True),
])
def test_as_yes(value, expected):
    assert _as_yes(value) is expected


@pytest.mark.parametrize("value,expected", [
    (None, None),
    ("25%", 0.25),
    ("0.30", 0.30),
    (0.4, 0.4),
    (40, 0.40),
    ("abc", None),
    ("", None),
    ("  ", None),
])
def test_normalise_percent(value, expected):
    result = _normalise_percent(value)
    if expected is None:
        assert result is None
    else:
        assert result == pytest.approx(expected)


def test_lookup_spouse_factor_case_insensitive():
    df = pl.DataFrame({"key": ["FactorA", "Other"], "value": [1.5, 2.5]})
    assert _lookup_spouse_factor(df, "factora") == 1.5
    assert _lookup_spouse_factor(df, "missing") == 0.0


def test_compute_qx():
    df = pl.DataFrame(
        {
            "total_cover": [1000.0, 0.0],
            "basic_technical_premium": [30.0, 10.0],
            "aids_technical_premium": [10.0, 0.0],
        }
    )
    out = compute_qx(df)
    assert out["qx"].to_list() == [0.04, 0.0]


def test_zero_if_status_edge_cases():
    df = pl.DataFrame({"status": ["A"], "basic_technical_premium": [5.0]})
    assert zero_if_status(df, ()).equals(df)
    no_status = pl.DataFrame({"basic_technical_premium": [5.0]})
    assert zero_if_status(no_status, ("DB",)).equals(no_status)


def test_deactivate_when_status_empty_tuple_noop():
    df = pl.DataFrame({"product_active": [True], "status": ["DB"]})
    assert deactivate_when_status(df, ())["product_active"].to_list() == [True]


def test_gip_helpers():
    assert _gipf(None) == 0.0
    assert _gipf(2.5) == 2.5
    assert _gip_key_part(None) == ""
    assert _gip_key_part(3.0) == "3"
    assert _gip_key_part(3.5) == "3.5"
    assert _gip_key_part("abc") == "abc"
    rows = [{"k": 0, "v": "a"}, {"k": 10, "v": "b"}]
    assert _gip_asof(rows, "k", "v", 5) == "a"
    assert _gip_asof(rows, "k", "v", 10) == "b"
    assert _gip_asof(rows, "k", "v", -1) is None


# ---------------------------------------------------------------------------
# helpers_common: profit margin / prb premium
# ---------------------------------------------------------------------------

def _profit_margin_table():
    return pl.DataFrame(
        {
            "product": ["gla", "gla", "lsdb", "lsdb"],
            "margin_type": ["standard", "dc", "standard", "dc"],
            "margin": [0.10, 0.05, 0.20, 0.07],
        }
    )


def test_lookup_margin():
    assump = {"profit_margins": _profit_margin_table()}
    assert _lookup_margin(assump, "gla", "standard") == 0.10
    assert _lookup_margin(assump, "gla", "nope") == 0.0
    assert _lookup_margin({}, "gla", "standard") == 0.0


def test_apply_profit_margin_net_risk_basis():
    df = pl.DataFrame(
        {
            "basic_technical_premium": [100.0],
            "aids_technical_premium": [50.0],
        }
    )
    recipe = {"profit_margin_key": ("gla", "standard")}
    out = apply_profit_margin(df, recipe,
                              {"profit_margins": _profit_margin_table()})
    assert out["profit_margin_amount"].to_list() == pytest.approx([15.0])
    assert "__margin_rate" not in out.columns


def test_apply_profit_margin_total_annual_basis():
    df = pl.DataFrame(
        {
            "basic_technical_premium": [100.0],
            "aids_technical_premium": [50.0],
            "total_annual_premium": [200.0],
        }
    )
    recipe = {"profit_margin_key": ("gla", "standard"),
              "profit_margin_basis": "total_annual"}
    out = apply_profit_margin(df, recipe,
                              {"profit_margins": _profit_margin_table()})
    assert out["profit_margin_amount"].to_list() == pytest.approx([20.0])


def test_apply_profit_margin_annuity_pv_basis_includes_waiver():
    df = pl.DataFrame(
        {
            "basic_technical_premium": [100.0],
            "aids_technical_premium": [50.0],
            "waiver_premium": [10.0],
        }
    )
    recipe = {"profit_margin_key": ("gla", "standard"),
              "profit_margin_basis": "annuity_pv"}
    out = apply_profit_margin(df, recipe,
                              {"profit_margins": _profit_margin_table()})
    assert out["profit_margin_amount"].to_list() == pytest.approx([16.0])


def test_apply_profit_margin_by_cover_type_mapping():
    df = pl.DataFrame(
        {
            "cover_type": ["Multiple", "Cover in member data"],
            "basic_technical_premium": [100.0, 100.0],
            "aids_technical_premium": [0.0, 0.0],
        }
    )
    recipe = {
        "profit_margin_key_by_cover_type": {
            "Multiple": ("lsdb", "standard"),
            "_default": ("lsdb", "dc"),
        }
    }
    out = apply_profit_margin(df, recipe,
                              {"profit_margins": _profit_margin_table()})
    assert out["profit_margin_amount"].to_list() == pytest.approx([20.0, 7.0])


def test_apply_profit_margin_mapping_requires_cover_type():
    df = pl.DataFrame({"basic_technical_premium": [1.0],
                       "aids_technical_premium": [0.0]})
    recipe = {"profit_margin_key_by_cover_type": {"Multiple": ("lsdb", "standard")}}
    with pytest.raises(KeyError, match="cover_type"):
        apply_profit_margin(df, recipe, {})


def test_apply_prb_premium_no_scheme_gives_zero():
    df = pl.DataFrame({"x": [1]})
    out = apply_prb_premium(df, SimpleNamespace(), "gla")
    assert out["prb_premium"].to_list() == [0.0]


def test_apply_prb_premium_zero_loading_gives_zero():
    ctx = SimpleNamespace(
        scheme=SimpleNamespace(profit_refund_loading_by_product={})
    )
    df = pl.DataFrame({"x": [1]})
    out = apply_prb_premium(df, ctx, "gla")
    assert out["prb_premium"].to_list() == [0.0]


def test_apply_prb_premium_computes_loading_on_eligible_rows():
    ctx = SimpleNamespace(
        scheme=SimpleNamespace(
            profit_refund_loading_by_product={"gla": 0.10}
        )
    )
    df = pl.DataFrame(
        {
            "product_active": [True, False, True],
            "age": [40.0, 40.0, 70.0],
            "ceasing_age": [65.0, 65.0, 65.0],
            "total_annual_premium": [1000.0, 1000.0, 1000.0],
            "co_premium": [100.0, 100.0, 100.0],
        }
    )
    out = apply_prb_premium(df, ctx, "gla")
    # base = (1000-100)*0.1 = 90, only when active & age<=ceasing
    assert out["prb_premium"].to_list() == [90.0, 0.0, 0.0]


# ---------------------------------------------------------------------------
# helpers_gla
# ---------------------------------------------------------------------------

def _ti_factors():
    return pl.DataFrame(
        {
            "cost_basis": ["first_year", "uniform", "ongoing"],
            "age": [30, 30, 30],
            "non_aids_m": [2.0, 1.0, 1.2],
            "aids_m": [0.5, 0.2, 0.3],
            "non_aids_f": [1.5, 1.1, 1.3],
            "aids_f": [0.4, 0.25, 0.35],
        }
    )


def _ti_members():
    return pl.DataFrame(
        {
            "member_no": [1, 2, 3, 4],
            "first_year_of_cover": ["Yes", "No", "No", "Yes"],
            "spread_initial_year_cost": ["No", "Yes", "No", "No"],
            "age_nb": [30, 30, 30, 99],
            "gender": ["M", "F", "M", "M"],
            "total_cover": [100_000.0, 100_000.0, 100_000.0, 100_000.0],
        }
    )


def test_apply_ti_scales_gla_full_path():
    prior = {
        "gla": pl.DataFrame(
            {
                "member_no": [1, 2, 3, 4],
                "basic_technical_premium": [100.0, 200.0, 300.0, 100.0],
                "aids_technical_premium": [10.0, 20.0, 30.0, 10.0],
                "total_cover": [50_000.0, 40_000.0, 60_000.0, 50_000.0],
            }
        )
    }
    out = _apply_ti_scales_gla(_ti_members(), {},
                               {"ti_factors": _ti_factors()}, prior)
    out = out.sort("member_no")
    # m1 first_year M: 100*2.0*100k/50k = 400 ; aids 10*0.5*2 = 10
    # m2 uniform F:    200*1.1*100k/40k = 550 ; aids 20*0.25*2.5 = 12.5
    # m3 ongoing M:    300*1.2*100k/60k = 600 ; aids 30*0.3*100k/60k = 15
    # m4 age 99 -> no ti row -> 0
    assert out["basic_technical_premium"].to_list() == pytest.approx(
        [400.0, 550.0, 600.0, 0.0])
    assert out["aids_technical_premium"].to_list() == pytest.approx(
        [10.0, 12.5, 15.0, 0.0])
    assert out["rate_basic"].to_list() == pytest.approx([4.0, 5.5, 6.0, 0.0])
    assert out["rate_aids"].to_list() == [0.0, 0.0, 0.0, 0.0]


def test_apply_ti_scales_gla_empty_ti_table():
    basis = {"ti_factors": pl.DataFrame(
        {"cost_basis": [], "age": [], "non_aids_m": [], "aids_m": [],
         "non_aids_f": [], "aids_f": []})}
    out = _apply_ti_scales_gla(_ti_members(), {}, basis, {})
    assert out["basic_technical_premium"].to_list() == [0.0] * 4


def test_apply_ti_scales_gla_no_gla_prior_zeroes():
    out = _apply_ti_scales_gla(_ti_members(), {},
                               {"ti_factors": _ti_factors()}, {})
    out = out.sort("member_no")
    assert out["basic_technical_premium"].to_list() == [0.0] * 4
    assert out["aids_technical_premium"].to_list() == [0.0] * 4


def test_cap_by_lsdb_or_gla_prefers_lsdb():
    df = pl.DataFrame({"member_no": [1, 2], "pre_tax_cover": [100.0, 100.0]})
    prior = {
        "lsdb": pl.DataFrame({"member_no": [1], "total_cover": [70.0]}),
        "gla": pl.DataFrame({"member_no": [1, 2], "total_cover": [50.0, 50.0]}),
    }
    out = _cap_by_lsdb_or_gla(df, {}, None, prior).sort("member_no")
    assert out["pre_tax_cover"].to_list() == [70.0, 100.0]


def test_cap_by_lsdb_or_gla_falls_back_to_gla():
    df = pl.DataFrame({"member_no": [1, 2], "pre_tax_cover": [100.0, 100.0]})
    prior = {"gla": pl.DataFrame({"member_no": [2], "total_cover": [40.0]})}
    out = _cap_by_lsdb_or_gla(df, {}, None, prior).sort("member_no")
    assert out["pre_tax_cover"].to_list() == [100.0, 40.0]


def test_cap_by_lsdb_or_gla_no_priors_noop():
    df = pl.DataFrame({"member_no": [1], "pre_tax_cover": [100.0]})
    assert _cap_by_lsdb_or_gla(df, {}, None, {})["pre_tax_cover"].to_list() == [100.0]


def test_cap_gla_and_lsdb_max_multiple_accelerator():
    df = pl.DataFrame(
        {
            "member_no": [1, 2, 3],
            "pre_tax_cover": [600_000.0, 600_000.0, 600_000.0],
            "annual_salary": [100_000.0, 0.0, 100_000.0],
        }
    )
    prior = {"gla": pl.DataFrame(
        {"member_no": [1, 2], "total_cover": [300_000.0, 250_000.0]})}
    ctx = _ctx(rating={"lsdb": "Accelerator"},
               limits={"lsdb": {"max_multiple": 5}})
    out = _cap_gla_and_lsdb_max_multiple(df, {}, ctx, prior).sort("member_no")
    # m1: min(600k, min(300k, 500k)) = 300k
    # m2: salary 0 -> min(600k, min(250k, 250k)) = 250k
    # m3: no gla -> min(600k, min(600k, 500k)) = 500k
    assert out["pre_tax_cover"].to_list() == [300_000.0, 250_000.0, 500_000.0]


def test_cap_gla_and_lsdb_max_multiple_non_accelerator_noop():
    df = pl.DataFrame({"member_no": [1], "pre_tax_cover": [600_000.0],
                       "annual_salary": [1.0]})
    ctx = _ctx(rating={"lsdb": "Standalone"})
    out = _cap_gla_and_lsdb_max_multiple(df, {}, ctx,
                                         {"gla": pl.DataFrame(
                                             {"member_no": [1],
                                              "total_cover": [1.0]})})
    assert out["pre_tax_cover"].to_list() == [600_000.0]


def test_cap_gla_and_lsdb_max_multiple_no_gla_prior_noop():
    df = pl.DataFrame({"member_no": [1], "pre_tax_cover": [600_000.0],
                       "annual_salary": [1.0]})
    ctx = _ctx(rating={"lsdb": "Accelerator"})
    out = _cap_gla_and_lsdb_max_multiple(df, {}, ctx, {})
    assert out["pre_tax_cover"].to_list() == [600_000.0]


def test_attach_gla_cover_for_ti():
    df = pl.DataFrame({"member_no": [1, 2]})
    out = attach_gla_cover_for_ti(df, {})
    assert out["__gla_cover_for_ti"].to_list() == [0.0, 0.0]

    prior = {"gla": pl.DataFrame({"member_no": [1], "total_cover": [42.0]})}
    out2 = attach_gla_cover_for_ti(df, prior).sort("member_no")
    assert out2["__gla_cover_for_ti"].to_list() == [42.0, None]


def test_co_premium_gla():
    df = pl.DataFrame(
        {
            "conversion_option_structure": [
                "Retirement and Withdrawal",   # code 1
                "Withdrawal only",             # code 2
                "Withdrawal only",             # code 2
                "Other",                       # code 0
                "Retirement and Withdrawal",   # code 1, nra>65
                "Retirement and Withdrawal",   # age > max_co_age
                "Retirement and Withdrawal",   # nra < age <= ceasing
                "Retirement and Withdrawal",   # include = No
            ],
            "nra_final": [65.0, 65.0, 65.0, 65.0, 70.0, 65.0, 65.0, 65.0],
            "age": [40.0, 63.0, 58.0, 40.0, 66.0, 82.0, 66.0, 40.0],
            "ceasing_age": [65.0, 65.0, 65.0, 65.0, 70.0, 85.0, 70.0, 65.0],
            "include_conversion_option": ["Yes"] * 7 + ["No"],
            "basic_technical_premium": [100.0] * 8,
        }
    )
    ctx = _ctx(limits={"gla": {"max_co_age": 80,
                               "withdrawal_only_pre_nra_cap": 5}})
    out = co_premium_gla(df, None,
                         {"conversion_option": _conversion_option_table()}, ctx)
    # r1: code1 nra<=65 -> 0.5, 40<=65 -> 50
    # r2: code2, age 63 > nra-5=60, not >nra -> 0
    # r3: code2, 58<=60 -> rate 0.3 -> 30
    # r4: code0 -> 0
    # r5: code1 nra 70 -> gt65 rate 0.4 -> 40
    # r6: age 82 > 80 -> 0
    # r7: code1, nra<age<=ceasing -> (2-1)*0.5 = 0.5 -> 50
    # r8: include No -> 0
    assert out["co_premium"].to_list() == pytest.approx(
        [50.0, 0.0, 30.0, 0.0, 40.0, 0.0, 50.0, 0.0])


def test_co_premium_lsdb():
    df = pl.DataFrame(
        {
            "nra_final": [65.0, 70.0, 65.0, 65.0, 65.0],
            "age": [40.0, 66.0, 60.0, 40.0, 40.0],
            "ceasing_age": [65.0] * 5,
            "product_active": [True, True, True, True, False],
            "status": ["A", "A", "A", "DB", "A"],
            "include_conversion_option": ["Yes"] * 5,
            "basic_technical_premium": [100.0] * 5,
        }
    )
    ctx = _ctx(limits={"lsdb": {"max_co_age": 65, "co_pre_nra_cap": 5}})
    out = co_premium_lsdb(df, None,
                          {"conversion_option": _conversion_option_table()},
                          ctx, ineligible_statuses=("DB",))
    # r1: 40 < min(60,65) -> 0.5 -> 50
    # r2: nra>65 rate .4 but 66 !< min(65,65) -> 0
    # r3: 60 !< 60 -> 0
    # r4: DB ineligible -> 0
    # r5: inactive -> 0
    assert out["co_premium"].to_list() == pytest.approx(
        [50.0, 0.0, 0.0, 0.0, 0.0])


def test_co_premium_lsdb_gt65_rate():
    df = pl.DataFrame(
        {
            "nra_final": [70.0],
            "age": [60.0],
            "ceasing_age": [70.0],
            "product_active": [True],
            "status": ["A"],
            "include_conversion_option": ["Yes"],
            "basic_technical_premium": [100.0],
        }
    )
    ctx = _ctx(limits={"lsdb": {"max_co_age": 65, "co_pre_nra_cap": 5}})
    out = co_premium_lsdb(df, None,
                          {"conversion_option": _conversion_option_table()}, ctx)
    # 60 < min(65,65) -> rate_gt65 = 0.4 -> 40
    assert out["co_premium"].to_list() == pytest.approx([40.0])


# ---------------------------------------------------------------------------
# helpers_education
# ---------------------------------------------------------------------------

def test_edu_guardian_salary_tier():
    bands = [(0.0, 1), (50_000.0, 2), (100_000.0, 3)]
    assert _edu_guardian_salary_tier(0.0, bands) == 1
    assert _edu_guardian_salary_tier(49_999.0, bands) == 1
    assert _edu_guardian_salary_tier(50_000.0, bands) == 2
    assert _edu_guardian_salary_tier(1_000_000.0, bands) == 3


def _edu_basis():
    return {
        "education_guardian_salary_bands": pl.DataFrame(
            {"salary_from": [0.0, 50_000.0], "sal_tier": [1, 2]}
        ),
        "education_guardian_cover": pl.DataFrame(
            {
                "age": [30, 40],
                "sal_1": [100.0, 200.0],
                "sal_2": [150.0, 250.0],
            }
        ),
    }


def _edu_members():
    return pl.DataFrame(
        {
            "gender": ["F", "M", "F", "F", "F", "F"],
            "age": [35.0, 45.0, 20.0, 35.0, 35.0, 70.0],
            "annual_salary": [60_000.0, 30_000.0, 60_000.0, 60_000.0, 60_000.0,
                              60_000.0],
            "product_active": [True, True, True, True, False, True],
            "status": ["A", "A", "A", "P", "A", "A"],
            "ceasing_age": [65.0, 65.0, 65.0, 65.0, 65.0, 65.0],
            "rate_basic": [2.0] * 6,
            "rate_aids": [0.5] * 6,
        }
    )


def test_education_guardian_composite_full():
    assump = {
        "product_limits": pl.DataFrame(
            {
                "product": ["education_guardian"],
                "param_name": ["male_age_offset"],
                "param_value": [5.0],
            }
        )
    }
    out = _apply_education_guardian_composite(
        _edu_members(), {}, _edu_basis(), assump, _ctx())
    # r1 F 35 -> asof 30, tier2 -> 150
    # r2 M 45 offset 5 -> 40 -> asof 40, tier1 -> 200
    # r3 age 20 < min age -> 0
    # r4 status P -> 0
    # r5 inactive -> 0
    # r6 age 70 > ceasing -> 0
    assert out["total_cover"].to_list() == [150.0, 200.0, 0.0, 0.0, 0.0, 0.0]
    assert out["basic_technical_premium"].to_list() == pytest.approx(
        [0.3, 0.4, 0.0, 0.0, 0.0, 0.0])
    assert out["aids_technical_premium"].to_list() == pytest.approx(
        [0.075, 0.1, 0.0, 0.0, 0.0, 0.0])


def test_education_guardian_composite_no_male_offset():
    members = pl.DataFrame(
        {
            "gender": ["M"],
            "age": [45.0],
            "annual_salary": [30_000.0],
            "product_active": [True],
            "status": ["A"],
            "ceasing_age": [65.0],
            "rate_basic": [2.0],
            "rate_aids": [0.0],
        }
    )
    out = _apply_education_guardian_composite(
        members, {}, _edu_basis(), None, _ctx())
    # no offset -> lookup 45 -> asof 40 -> tier1 -> 200
    assert out["total_cover"].to_list() == [200.0]


def test_education_guardian_composite_missing_bands_zeroes():
    out = _apply_education_guardian_composite(
        _edu_members(), {}, {}, None, _ctx())
    assert out["total_cover"].to_list() == [0.0] * 6
    assert out["basic_technical_premium"].to_list() == [0.0] * 6


def test_education_guardian_composite_missing_cover_table_zeroes():
    basis = {"education_guardian_salary_bands": pl.DataFrame(
        {"salary_from": [0.0], "sal_tier": [1]})}
    out = _apply_education_guardian_composite(
        _edu_members(), {}, basis, None, _ctx())
    assert out["total_cover"].to_list() == [0.0] * 6


def test_educate_a_child_composite():
    df = pl.DataFrame(
        {
            "category_number": [1, 2, 1],
            "annual_salary": [100_000.0, 100_000.0, 50_000.0],
            "product_active": [True, True, True],
            "status": ["A", "A", "P"],
            "age": [40.0, 40.0, 40.0],
            "ceasing_age": [65.0, 65.0, 65.0],
            "rate_basic": [2.0, 2.0, 2.0],
            "rate_aids": [0.0, 0.0, 0.0],
        }
    )
    ctx = _ctx(education_multiple={1: 2.0}, education_max_cover={1: 150_000.0})
    out = _apply_educate_a_child_composite(df, {}, {}, None, ctx)
    # r1: 2*100k = 200k -> capped 150k
    # r2: no multiple for cat 2 -> 0
    # r3: capped cover but status P -> 0
    assert out["total_cover"].to_list() == [150_000.0, 0.0, 0.0]
    assert out["basic_technical_premium"].to_list() == pytest.approx(
        [300.0, 0.0, 0.0])


def test_educate_a_child_composite_no_category_column_zeroes():
    df = pl.DataFrame({"member_no": [1]})
    out = _apply_educate_a_child_composite(df, {}, {}, None, _ctx())
    assert out["total_cover"].to_list() == [0.0]
    assert out["basic_technical_premium"].to_list() == [0.0]


def test_educate_a_child_composite_none_ctx():
    df = pl.DataFrame(
        {
            "category_number": [1],
            "annual_salary": [100_000.0],
            "product_active": [True],
            "status": ["A"],
            "age": [40.0],
            "ceasing_age": [65.0],
            "rate_basic": [2.0],
            "rate_aids": [0.0],
        }
    )
    out = _apply_educate_a_child_composite(df, {}, {}, None, None)
    assert out["total_cover"].to_list() == [0.0]


# ---------------------------------------------------------------------------
# helpers.py dispatchers
# ---------------------------------------------------------------------------

def test_apply_cover_caps_dispatches_all_kinds():
    df = pl.DataFrame(
        {
            "member_no": [1],
            "pre_tax_cover": [600_000.0],
            "annual_salary": [100_000.0],
            "maximum_cover_amount": [500_000.0],
            "maximum_cover": [400_000.0],
        }
    )
    prior = {"gla": pl.DataFrame({"member_no": [1], "total_cover": [350_000.0]}),
             "lsdb": pl.DataFrame({"member_no": [1], "total_cover": [300_000.0]})}
    ctx = _ctx(rating={"lsdb": "Accelerator"},
               limits={"lsdb": {"max_multiple": 5},
                       "spouse_gla": {"max_multiple": 2},
                       "p": {"max_cover": 250_000.0}})
    recipe = {
        "cover_caps": [
            {"kind": "category_max_cover"},                      # -> 500k
            {"kind": "category_max_cover_field",
             "field": "maximum_cover"},                          # -> 400k
            {"kind": "cap_by_prior_cover", "prior": "gla"},      # -> 350k
            {"kind": "cap_by_lsdb_or_gla"},                      # -> 300k (lsdb)
            {"kind": "gla_and_lsdb_max_multiple"},               # -> 300k
            {"kind": "spouse_cover_cap", "prior": "gla",
             "max_multiple_key": ("spouse_gla", "max_multiple")},  # -> 200k
            {"kind": "cap_by_assumption_limit", "product_key": "p",
             "param_key": "max_cover"},                          # -> 250k? no: min(200k,250k)=200k
        ]
    }
    out = apply_cover_caps(df, recipe, ctx, {}, prior)
    assert out["pre_tax_cover"].to_list() == [200_000.0]


def test_apply_cover_caps_empty_and_unknown():
    df = pl.DataFrame({"pre_tax_cover": [1.0]})
    assert apply_cover_caps(df, {}, None, {}, {}) is df
    assert apply_cover_caps(df, {"cover_caps": []}, None, {}, {}) is df
    with pytest.raises(ValueError, match="Unknown cover cap kind"):
        apply_cover_caps(df, {"cover_caps": [{"kind": "bogus"}]}, None, {}, {})


def test_apply_special_premium_ti_scales_gla():
    prior = {
        "gla": pl.DataFrame(
            {
                "member_no": [1],
                "basic_technical_premium": [100.0],
                "aids_technical_premium": [10.0],
                "total_cover": [50_000.0],
            }
        )
    }
    df = pl.DataFrame(
        {
            "member_no": [1],
            "first_year_of_cover": ["Yes"],
            "spread_initial_year_cost": ["No"],
            "age_nb": [30],
            "gender": ["M"],
            "total_cover": [100_000.0],
        }
    )
    out = apply_special_premium(df, {"kind": "ti_scales_gla"},
                                {"ti_factors": _ti_factors()}, prior)
    assert out["basic_technical_premium"].to_list() == pytest.approx([400.0])


def test_apply_special_premium_education_composites():
    edu_out = apply_special_premium(
        _edu_members(), {"kind": "education_guardian_composite"},
        _edu_basis(), {}, assump_tables=None, ctx=_ctx())
    assert edu_out["total_cover"].to_list() == [150.0, 200.0, 0.0, 0.0, 0.0, 0.0]

    eac_df = pl.DataFrame(
        {
            "category_number": [1],
            "annual_salary": [100_000.0],
            "product_active": [True],
            "status": ["A"],
            "age": [40.0],
            "ceasing_age": [65.0],
            "rate_basic": [2.0],
            "rate_aids": [0.0],
        }
    )
    ctx = _ctx(education_multiple={1: 1.0}, education_max_cover={})
    eac_out = apply_special_premium(
        eac_df, {"kind": "educate_a_child_composite"}, {}, {},
        assump_tables=None, ctx=ctx)
    assert eac_out["total_cover"].to_list() == [100_000.0]


@pytest.mark.parametrize("kind,fname", [
    ("fc_composite", "_apply_fc_composite"),
    ("gip_composite", "_apply_gip_composite"),
    ("tip_composite", "_apply_tip_composite"),
])
def test_apply_special_premium_dispatches_composites(monkeypatch, kind, fname):
    seen = {}

    def stub(df, spec, basis_tables, assump_tables, ctx):
        seen["called"] = True
        return df.with_columns(pl.lit(1.0).alias("__dispatched"))

    monkeypatch.setattr(helpers_mod, fname, stub)
    out = apply_special_premium(pl.DataFrame({"x": [1]}), {"kind": kind},
                                {}, {}, assump_tables={}, ctx=_ctx())
    assert seen.get("called") is True
    assert out["__dispatched"].to_list() == [1.0]


def test_apply_special_premium_unknown_kind_raises():
    with pytest.raises(ValueError, match="Unknown special_premium kind"):
        apply_special_premium(pl.DataFrame({"x": [1]}), {"kind": "bogus"},
                              {}, {})


def test_helpers_module_reexports_common_names():
    # helpers.py star-imports the product helper modules; check a few names
    assert helpers_mod.apply_cover_caps is apply_cover_caps
    assert callable(helpers_mod.compute_age)
    assert callable(helpers_mod.co_premium_gla)
    assert callable(helpers_mod._apply_education_guardian_composite)


# ---------------------------------------------------------------------------
# recipes.py — registry invariants
# ---------------------------------------------------------------------------

_VALID_COVER_RULES = {
    "multiple_or_data", "multiple_or_data_or_gla", "fc_composite",
    "gip_composite", "tip_composite", "education_guardian_composite",
    "educate_a_child_composite",
}
_VALID_CAP_KINDS = {
    "category_max_cover", "category_max_cover_field",
    "gla_and_lsdb_max_multiple", "cap_by_prior_cover", "cap_by_lsdb_or_gla",
    "spouse_cover_cap", "cap_by_assumption_limit",
}
_VALID_FACTOR_KINDS = {
    "mortality", "mortality_gender_swap", "salary_band", "salary_factor",
    "industry", "trend", "waiting_pd", "baseline_yr", "product_type_adj",
    "ci_standalone_loading", "ci_industry_loading", "ci_mortality_dynamic",
}
_VALID_SPECIAL_KINDS = {
    "ti_scales_gla", "fc_composite", "gip_composite", "tip_composite",
    "education_guardian_composite", "educate_a_child_composite",
}


def test_recipes_registry_contents_and_invariants():
    assert set(RECIPES) == {
        "gla", "lsdb", "accident_death", "accident_disability",
        "dismemberment", "terminal", "spouse_gla", "spouse_lsdb", "ci",
        "fc", "gip", "tip", "education_guardian", "educate_a_child",
    }
    for key, recipe in RECIPES.items():
        assert recipe["cover_rule"] in _VALID_COVER_RULES, key
        assert recipe["ceasing_source"] in ("nra_only", "deferred_or_nra"), key
        for cap in recipe.get("cover_caps", []):
            assert cap["kind"] in _VALID_CAP_KINDS, (key, cap)
        for f in recipe.get("rate_factors", []):
            assert f["kind"] in _VALID_FACTOR_KINDS, (key, f)
        sp = recipe.get("special_premium")
        if sp is not None:
            assert sp["kind"] in _VALID_SPECIAL_KINDS, (key, sp)


def test_recipes_spouse_products_have_no_plain_rate_factors():
    # spouse recipes select rate_factors_*_source based on member sheet
    assert "rate_factors" not in RECIPES["spouse_gla"]
    assert RECIPES["spouse_gla"]["rate_factors_gla_source"]
    assert RECIPES["spouse_gla"]["rate_factors_sp_source"]


# ---------------------------------------------------------------------------
# engine_paths.py
# ---------------------------------------------------------------------------

def test_engine_paths_exports_and_snapshot_dir():
    assert isinstance(engine_paths.ENGINE_ROOT, Path)
    assert engine_paths.CTR_LOADER_DIR == engine_paths.CTR_LOADER
    assert set(engine_paths.__all__) == {
        "ENGINE_ROOT", "CTR_LOADER_DIR", "CTR_LOADER", "snapshot_dir"}

    basis_dir = engine_paths.snapshot_dir("basis")
    assert basis_dir.is_dir()
    assert any(basis_dir.glob("*.parquet"))

    assump_dir = engine_paths.snapshot_dir("assumption")
    assert assump_dir.is_dir()
    assert any(assump_dir.glob("*.parquet"))

    with pytest.raises(ValueError, match="must be 'basis' or 'assumption'"):
        engine_paths.snapshot_dir("not_a_kind")


def test_engine_paths_main_block_runs():
    # exercises the `if __name__ == "__main__":` logging block
    runpy.run_module("pricing_engine.engine_paths", run_name="__main__")
