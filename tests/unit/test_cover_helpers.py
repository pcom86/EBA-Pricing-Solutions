from __future__ import annotations

from types import SimpleNamespace

import polars as pl
import pytest
from pricing_engine.helpers_common import (
    apply_ctr_max_cover,
    apply_income_taper,
    deactivate_when_status,
    finalise_cover,
    zero_if_status,
)


@pytest.mark.unit
def test_apply_ctr_max_cover_uses_category_caps_then_assumption_fallback():
    df = pl.DataFrame(
        {
            "category_number": [1, 2, 3],
            "pre_tax_cover": [100000.0, 100000.0, 100000.0],
        }
    )
    ctx = SimpleNamespace(
        scheme=SimpleNamespace(max_cover_per_category={"gla": {1: 50000.0, 2: 75000.0}})
    )

    result = apply_ctr_max_cover(
        df, ctx, "gla", assump_fallback_limits={"max_cover": 60000.0}
    )

    assert result["pre_tax_cover"].to_list() == [50000.0, 75000.0, 60000.0]


@pytest.mark.unit
def test_apply_ctr_max_cover_leaves_values_unchanged_without_limits():
    df = pl.DataFrame({"category_number": [1], "pre_tax_cover": [100000.0]})
    ctx = SimpleNamespace(scheme=SimpleNamespace(max_cover_per_category={}))

    result = apply_ctr_max_cover(df, ctx, "gla")

    assert result["pre_tax_cover"].to_list() == [100000.0]


@pytest.mark.unit
def test_apply_income_taper_caps_cover_and_zeros_db_status():
    df = pl.DataFrame(
        {
            "age": [60.0, 60.0, 60.0],
            "nra_final": [65.0, 65.0, 65.0],
            "annual_salary": [120000.0, 120000.0, 120000.0],
            "reduction_period": ["Income Tapering", "Income Tapering", "None"],
            "reduction_period_months": [6, 6, 6],
            "status": ["A", "DB", "A"],
            "pre_tax_cover": [600000.0, 600000.0, 600000.0],
        }
    )

    result = apply_income_taper(df)

    assert result["pre_tax_cover"].to_list() == [540000.0, 0.0, 600000.0]


@pytest.mark.unit
def test_apply_income_taper_returns_input_when_no_reduction_period_column():
    df = pl.DataFrame({"pre_tax_cover": [100.0]})

    result = apply_income_taper(df)

    assert result.equals(df)


@pytest.mark.unit
def test_finalise_cover_applies_activity_and_ceasing_rules():
    df = pl.DataFrame(
        {
            "product_active": [True, False, True],
            "status": ["A", "A", "A"],
            "age": [60.0, 60.0, 66.0],
            "ceasing_age": [65.0, 65.0, 65.0],
            "pre_tax_cover": [100000.0, 100000.0, 100000.0],
        }
    )

    result = finalise_cover(df, {"apply_tax_gross_up": False})

    assert result["total_cover"].to_list() == [100000.0, 0.0, 0.0]


@pytest.mark.unit
def test_finalise_cover_uses_reduced_cover_for_pensioner_or_over_ceasing_age():
    df = pl.DataFrame(
        {
            "product_active": [True, True, True],
            "status": ["P", "A", "A"],
            "age": [60.0, 66.0, 60.0],
            "ceasing_age": [65.0, 65.0, 65.0],
            "pre_tax_cover": [100000.0, 100000.0, 100000.0],
            "reduced": [25000.0, 30000.0, 0.0],
            "tax_on_cover": [0.0, 0.0, -1.0],
        }
    )

    result = finalise_cover(df, {"apply_tax_gross_up": True})

    assert result["total_cover"].to_list() == [25000.0, 30000.0, 100000.0]


@pytest.mark.unit
def test_zero_if_status_zeros_only_pricing_amount_columns_for_matching_rows():
    df = pl.DataFrame(
        {
            "status": ["A", "DB"],
            "basic_technical_premium": [10.0, 20.0],
            "co_premium": [2.0, 3.0],
            "total_annual_premium": [12.0, 23.0],
            "other_value": [7, 8],
        }
    )

    result = zero_if_status(df, ("DB",))

    assert result["basic_technical_premium"].to_list() == [10.0, 0.0]
    assert result["co_premium"].to_list() == [2.0, 0.0]
    assert result["total_annual_premium"].to_list() == [12.0, 0.0]
    assert result["other_value"].to_list() == [7, 8]


@pytest.mark.unit
def test_deactivate_when_status_preserves_other_members():
    df = pl.DataFrame(
        {"product_active": [True, True, False], "status": ["A", "DB", "A"]}
    )

    result = deactivate_when_status(df, ("DB",))

    assert result["product_active"].to_list() == [True, False, False]
