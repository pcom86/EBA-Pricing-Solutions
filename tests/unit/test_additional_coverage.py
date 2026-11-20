from __future__ import annotations

from datetime import date
from typing import ClassVar

import polars as pl
import pytest
from pricing_engine.engine import build_context
from pricing_engine.helpers_common import (
    apply_factor,
    compute_ceasing_age,
    mark_product_active,
    resolve_nra,
)


@pytest.mark.unit
def test_mark_product_active_handles_missing_scheme_rating():
    df = pl.DataFrame({"category_number": [1, 2]})
    result = mark_product_active(df, None, (1,))
    assert result["product_active"].to_list() == [False, False]


@pytest.mark.unit
def test_resolve_nra_defaults_when_no_nra_inputs_exist():
    df = pl.DataFrame({"member_no": [1, 2]})
    result = resolve_nra(df)
    assert result["nra_final"].to_list() == [65.0, 65.0]


@pytest.mark.unit
def test_compute_ceasing_age_falls_back_to_nra_when_deferred_data_missing():
    df = pl.DataFrame(
        {
            "status": ["A", "DB"],
            "nra_final": [64, 60],
            "deferred_retirals": ["No", None],
        }
    )
    result = compute_ceasing_age(df, "deferred_or_nra")
    assert result["ceasing_age"].to_list() == [64, 60]


@pytest.mark.unit
def test_build_context_uses_scheme_size_and_product_limits():
    class DummyScheme:
        commencement_date = date(2025, 1, 1)
        rating: ClassVar = {"gla": "Yes"}
        region = "North"
        primary = "Primary"
        secondary = "Secondary"

    class DummyAssump:
        globals: ClassVar = {"scheme_size_large_threshold": 2}
        tables: ClassVar = {
            "product_limits": pl.DataFrame(
                {
                    "product": ["gla"],
                    "param_name": ["max_cover"],
                    "param_value": [250000.0],
                    "param_type": ["float"],
                }
            )
        }

    ctr = type("Ctr", (), {})()
    ctr.scheme = DummyScheme()
    ctr.members = {"gla_sheet": pl.DataFrame({"member_no": [1, 2]})}
    ctr.active_products = ["gla"]
    ctr.active_categories = {"gla": (1,)}
    ctr.categories = pl.DataFrame({"product": ["gla"], "category_number": [1], "field": ["category_flag"], "value": [1.0]})

    ctx = build_context(ctr, DummyAssump())

    assert ctx.member_count == 2
    assert ctx.scheme_size == "Large"
    assert ctx.limits_for("gla")["max_cover"] == 250000.0


@pytest.mark.unit
def test_apply_factor_rejects_unknown_kind():
    df = pl.DataFrame({"rate_basic": [1.0]})
    with pytest.raises(ValueError, match="Unknown factor kind"):
        apply_factor(df, {"kind": "not_real"}, {}, {}, None, target="rate_basic")
