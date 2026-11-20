from __future__ import annotations

from datetime import date

import polars as pl
import pytest
from pricing_engine.helpers_common import (
    _apply_mortality,
    attach_category,
    compute_age,
    compute_ceasing_age,
    mark_product_active,
    pivot_categories_wide,
    resolve_nra,
)


@pytest.mark.unit
def test_compute_age_uses_comm_date():
    df = pl.DataFrame({"date_of_birth": [date(1990, 1, 1), date(2000, 12, 31)]})

    result = compute_age(df, date(2025, 1, 1))

    age_vals = result["age"].to_list()
    assert age_vals[0] == pytest.approx(35.00068446269678)
    assert age_vals[1] == pytest.approx(24.00273785078713)
    assert result["age_lb"].to_list() == [35, 24]
    assert result["age_nb"].to_list() == [36, 25]


@pytest.mark.unit
def test_pivot_categories_wide_and_attach_category():
    categories = pl.DataFrame(
        {
            "product": ["gla", "gla", "lsdb"],
            "category_number": [1, 2, 1],
            "field": ["category_flag", "category_flag", "category_flag"],
            "value": [10, 20, 30],
        }
    )

    wide = pivot_categories_wide(categories, "gla")
    assert "category_flag_join" in wide.columns
    assert set(wide["category_number"].to_list()) == {1, 2}

    df = pl.DataFrame({"category_flag": [10.0, 20.0]})
    result = attach_category(df, wide)
    assert result["category_number"].to_list() == [1, 2]


@pytest.mark.unit
def test_mark_product_active_uses_scheme_rating_and_categories():
    df = pl.DataFrame({"category_number": [1, 2, 3]})

    on = mark_product_active(df, "Yes", (1, 3))
    assert on["product_active"].to_list() == [True, False, True]

    off = mark_product_active(df, "No", (1, 3))
    assert off["product_active"].to_list() == [False, False, False]


@pytest.mark.unit
def test_resolve_nra_uses_custom_or_category_value():
    df = pl.DataFrame({"nra": [70.0, None], "nra_cat": ["Custom", "60"]})
    result = resolve_nra(df, custom_col="nra", category_col="nra_cat")
    assert result["nra_final"].to_list() == [70.0, 60.0]

    df2 = pl.DataFrame({"nra_cat": [None, "62"]})
    result2 = resolve_nra(df2, category_col="nra_cat")
    assert result2["nra_final"].to_list() == [65.0, 62.0]


@pytest.mark.unit
def test_compute_ceasing_age_with_nra_and_deferred_cases():
    df = pl.DataFrame(
        {
            "status": ["A", "DB", "DB"],
            "nra_final": [65, 60, 60],
            "deferred_retirals": ["No", "Yes", "Yes"],
            "deferred_retirement_age": [None, 70, 72],
        }
    )

    nra_only = compute_ceasing_age(df, "nra_only")
    assert nra_only["ceasing_age"].to_list() == [65, 60, 60]

    deferred = compute_ceasing_age(df, "deferred_or_nra")
    assert deferred["ceasing_age"].to_list() == [65.0, 60.0, 60.0]

    missing = pl.DataFrame({"status": ["A"], "nra_final": [65]})
    assert compute_ceasing_age(missing, "deferred_or_nra")["ceasing_age"].to_list() == [65]

    with pytest.raises(ValueError):
        compute_ceasing_age(df, "unknown_mode")


@pytest.mark.unit
def test_apply_mortality_multiplies_rate_basic():
    df = pl.DataFrame(
        {
            "rate_basic": [1.0, 2.0],
            "gender": ["F", "M"],
            "age_nb": [30, 30],
        }
    )

    basis = {
        "mortality": pl.DataFrame(
            {
                "product": ["gla", "gla"],
                "age": [30, 30],
                "gender": ["F", "M"],
                "rate": [2.0, 3.0],
            }
        )
    }

    result = _apply_mortality(df, {"product": "gla"}, basis, "rate_basic")
    assert result["rate_basic"].to_list() == [2.0, 6.0]
