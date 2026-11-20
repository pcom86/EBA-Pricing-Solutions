from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import polars as pl

from pricing_engine.engine import PricingContext, build_context


@dataclass
class DummyScheme:
    commencement_date: date
    rating: dict
    education_product: str | None = None
    education_nra: dict | None = None
    education_deferred: dict | None = None
    education_deferred_age: dict | None = None
    express_over_gla: dict | None = None

    def __getattr__(self, name):
        return None


def test_build_context_creates_context_for_minimal_ctr():
    scheme = DummyScheme(commencement_date=date(2025, 1, 1), rating={"gla": "Yes"})

    ctr = type("Ctr", (), {})()
    ctr.scheme = scheme
    ctr.members = {"gla_sheet": pl.DataFrame({"member_no": [1]})}
    ctr.active_products = ["gla"]
    ctr.active_categories = {"gla": (1,)}
    ctr.categories = pl.DataFrame(
        {
            "product": ["gla"],
            "category_number": [1],
            "field": ["category_flag"],
            "value": [1.0],
        }
    )

    assump = type("Assump", (), {})()
    assump.globals = {}
    assump.tables = {}

    ctx = build_context(ctr, assump)

    assert isinstance(ctx, PricingContext)
    assert ctx.member_count == 1
    assert ctx.scheme_size == "Small"
