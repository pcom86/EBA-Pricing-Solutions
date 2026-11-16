from __future__ import annotations
from datetime import date
from typing import Any
import math
import polars as pl
import calendar
import re
from . import helpers_common
from . import helpers_fc
from .helpers_common import *
from .helpers_fc import *
from . import helpers_gla
from .helpers_gla import *
from . import helpers_education
from .helpers_education import *
from . import helpers_gip
from .helpers_gip import *
from . import helpers_tip
from .helpers_tip import *



def apply_cover_caps(df, recipe, ctx, assump_tables, prior_results):
    caps = recipe.get("cover_caps", [])
    if not caps:
        return df

    for cap in caps:
        kind = cap["kind"]

        if kind == "category_max_cover":
            df = _cap_category_max_cover(df, cap, ctx, prior_results)

        elif kind == "category_max_cover_field":
            df = _cap_category_max_cover_field(df, cap, ctx, prior_results)

        elif kind == "gla_and_lsdb_max_multiple":
            df = _cap_gla_and_lsdb_max_multiple(df, cap, ctx, prior_results)

        elif kind == "cap_by_prior_cover":
            df = _cap_by_prior_cover(df, cap, ctx, prior_results)

        elif kind == "cap_by_lsdb_or_gla":
            df = _cap_by_lsdb_or_gla(df, cap, ctx, prior_results)

        elif kind == "spouse_cover_cap":
            df = _cap_spouse_cover_cap(df, cap, ctx, prior_results)

        elif kind == "cap_by_assumption_limit":
            df = _cap_by_assumption_limit(df, cap, ctx, prior_results)

        else:
            raise ValueError(f"Unknown cover cap kind: {kind}")

    return df


def apply_special_premium(df: pl.DataFrame,
                           spec: dict,
                           basis_tables: dict[str, pl.DataFrame],
                           prior_results: dict[str, pl.DataFrame],
                           assump_tables: dict | None = None,
                           ctx=None) -> pl.DataFrame:
    kind = spec["kind"]
    if kind == "ti_scales_gla":
        return _apply_ti_scales_gla(df, spec, basis_tables, prior_results)
    if kind == "fc_composite":
        return _apply_fc_composite(df, spec, basis_tables, assump_tables, ctx)
    if kind == "gip_composite":
        return _apply_gip_composite(df, spec, basis_tables, assump_tables, ctx)
    if kind == "tip_composite":
        return _apply_tip_composite(df, spec, basis_tables, assump_tables, ctx)
    if kind == "education_guardian_composite":
        return _apply_education_guardian_composite(df, spec, basis_tables, assump_tables, ctx)
    if kind == "educate_a_child_composite":
        return _apply_educate_a_child_composite(df, spec, basis_tables, assump_tables, ctx)
    raise ValueError(f"Unknown special_premium kind: {kind}")


