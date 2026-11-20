from __future__ import annotations

import calendar
import math
import re
from datetime import date
from typing import Any

import polars as pl

from . import helpers_common
from .helpers_common import _co_eligibility_mask, _co_rate

__all__ = [
    "_apply_ti_scales_gla",
    "_cap_by_lsdb_or_gla",
    "_cap_gla_and_lsdb_max_multiple",
    "attach_gla_cover_for_ti",
    "co_premium_gla",
    "co_premium_lsdb",
]


def _apply_ti_scales_gla(df, spec, basis, prior):
    ti = basis["ti_factors"]
    if ti is None or ti.height == 0:
        return df.with_columns(pl.lit(0.0).alias("basic_technical_premium"))

    df = df.with_columns([
        pl.when(
            (pl.col("first_year_of_cover").cast(pl.Utf8) == "Yes") &
            (pl.col("spread_initial_year_cost").cast(pl.Utf8) != "Yes")
        ).then(pl.lit("first_year"))
         .when(pl.col("spread_initial_year_cost").cast(pl.Utf8) == "Yes")
             .then(pl.lit("uniform"))
         .otherwise(pl.lit("ongoing"))
         .alias("__ti_cost_basis")
    ])

    ti_long_m = ti.select(["cost_basis", "age",
                            pl.col("non_aids_m").alias("__ti_basic"),
                            pl.col("aids_m").alias("__ti_aids")])\
                   .with_columns(pl.lit("M").alias("gender"))
    ti_long_f = ti.select(["cost_basis", "age",
                            pl.col("non_aids_f").alias("__ti_basic"),
                            pl.col("aids_f").alias("__ti_aids")])\
                   .with_columns(pl.lit("F").alias("gender"))
    ti_long = pl.concat([ti_long_m, ti_long_f])

    df = df.with_columns([
        pl.col("age_nb").cast(pl.Int64),
        pl.col("gender").cast(pl.Utf8),
    ])
    ti_long = ti_long.with_columns([
        pl.col("age").cast(pl.Int64),
        pl.col("gender").cast(pl.Utf8),
        pl.col("cost_basis").cast(pl.Utf8),
    ])
    df = df.join(
        ti_long,
        left_on=["__ti_cost_basis", "age_nb", "gender"],
        right_on=["cost_basis", "age", "gender"],
        how="left",
    )

    gla = prior.get("gla")
    if gla is None:
        return df.with_columns([
            pl.lit(0.0).alias("basic_technical_premium"),
            pl.lit(0.0).alias("aids_technical_premium"),
        ])

    gla_cols = gla.select([
        "member_no",
        pl.col("basic_technical_premium").alias("__gla_basic"),
        pl.col("aids_technical_premium").alias("__gla_aids"),
        pl.col("total_cover").alias("__gla_cover"),
    ])
    df = df.join(gla_cols, on="member_no", how="left")

    df = df.with_columns([
        pl.when(pl.col("__gla_cover") > 0)
          .then(pl.col("__gla_basic") * pl.col("__ti_basic").fill_null(0.0) *
                pl.col("total_cover") / pl.col("__gla_cover"))
          .otherwise(0.0).alias("basic_technical_premium"),
        pl.when(pl.col("__gla_cover") > 0)
          .then(pl.col("__gla_aids") * pl.col("__ti_aids").fill_null(0.0) *
                pl.col("total_cover") / pl.col("__gla_cover"))
          .otherwise(0.0).alias("aids_technical_premium"),
    ])

    df = df.with_columns(
        pl.when(pl.col("total_cover") > 0)
          .then(pl.col("basic_technical_premium") / pl.col("total_cover") * 1000)
          .otherwise(0.0).alias("rate_basic")
    )
    df = df.with_columns(pl.lit(0.0).alias("rate_aids"))

    return df.drop(["__ti_cost_basis", "__ti_basic", "__ti_aids",
                    "__gla_basic", "__gla_aids", "__gla_cover"])


def _cap_by_lsdb_or_gla(df, cap, ctx, prior_results):
    lsdb_df = prior_results.get("lsdb")
    gla_df = prior_results.get("gla")
    if lsdb_df is None and gla_df is None:
        return df
    ref_df = lsdb_df if lsdb_df is not None else gla_df
    ref = ref_df.select(["member_no", "total_cover"])\
                .rename({"total_cover": "__ref_cover"})
    df = df.join(ref, on="member_no", how="left")
    df = df.with_columns(
        pl.min_horizontal([
            pl.col("pre_tax_cover"),
            pl.col("__ref_cover").fill_null(pl.col("pre_tax_cover")),
        ]).alias("pre_tax_cover")
    ).drop("__ref_cover")
    return df


def _cap_gla_and_lsdb_max_multiple(df, cap, ctx, prior_results):
    variant_is_accel = (ctx.scheme.rating.get("lsdb") == "Accelerator")
    if not variant_is_accel:
        return df
    gla_df = prior_results.get("gla")
    if gla_df is None:
        return df
    gla_cover = gla_df.select(["member_no", "total_cover"])\
                      .rename({"total_cover": "__gla_cover"})
    df = df.join(gla_cover, on="member_no", how="left")

    limits = ctx.limits_for("lsdb")
    max_mul = float(limits.get("max_multiple", 5))

    df = df.with_columns(
        pl.min_horizontal([
            pl.col("pre_tax_cover"),
            pl.min_horizontal([
                pl.col("__gla_cover").fill_null(pl.col("pre_tax_cover")),
                pl.when(pl.col("annual_salary") > 0)
                  .then(pl.col("annual_salary") * pl.lit(max_mul))
                  .otherwise(pl.col("__gla_cover"))
            ])
        ]).alias("pre_tax_cover")
    ).drop("__gla_cover")
    return df


def attach_gla_cover_for_ti(df, prior_results):
    gla = prior_results.get("gla")
    if gla is None:
        return df.with_columns(pl.lit(0.0).alias("__gla_cover_for_ti"))
    return df.join(
        gla.select(["member_no", pl.col("total_cover").alias("__gla_cover_for_ti")]),
        on="member_no", how="left"
    )


def co_premium_gla(df, cats_wide, assump_tables, ctx, target="co_premium"):
    co_tbl = assump_tables["conversion_option"].filter(pl.col("product") == "gla")
    rate_le65_rw = _co_rate(co_tbl, "<=65", "retirement_and_withdrawal")
    rate_gt65_rw = _co_rate(co_tbl, ">65",  "retirement_and_withdrawal")
    rate_le65_wo = _co_rate(co_tbl, "<=65", "withdrawal_only")
    rate_gt65_wo = _co_rate(co_tbl, ">65",  "withdrawal_only")
    code_rw = int(_co_rate(co_tbl, "code_map", "retirement_and_withdrawal"))
    code_wo = int(_co_rate(co_tbl, "code_map", "withdrawal_only"))

    limits = ctx.limits_for("gla")
    max_co_age = limits.get("max_co_age", 80)
    pre_nra_cap = limits.get("withdrawal_only_pre_nra_cap", 0)

    df = df.with_columns([
        pl.when(pl.col("conversion_option_structure") == "Retirement and Withdrawal")
          .then(pl.lit(code_rw))
          .when(pl.col("conversion_option_structure") == "Withdrawal only")
          .then(pl.lit(code_wo))
          .otherwise(pl.lit(0))
          .alias("__co_code")
    ])
    df = df.with_columns([
        pl.when(pl.col("nra_final") <= 65)
          .then(pl.when(pl.col("__co_code") == code_rw).then(pl.lit(rate_le65_rw))
                  .when(pl.col("__co_code") == code_wo).then(pl.lit(rate_le65_wo))
                  .otherwise(0.0))
          .otherwise(pl.when(pl.col("__co_code") == code_rw).then(pl.lit(rate_gt65_rw))
                       .when(pl.col("__co_code") == code_wo).then(pl.lit(rate_gt65_wo))
                       .otherwise(0.0))
          .alias("__co_pct_base")
    ])
    df = df.with_columns([
        pl.when(pl.col("age") > max_co_age).then(0.0)
          .when(pl.col("age") <= (pl.col("nra_final") - pre_nra_cap * (pl.col("__co_code") - 1)))
              .then(pl.col("__co_pct_base"))
          .when((pl.col("age") > pl.col("nra_final")) & (pl.col("age") <= pl.col("ceasing_age")))
              .then((2 - pl.col("__co_code")) * pl.col("__co_pct_base"))
          .otherwise(0.0)
          .alias("co_pct")
    ])
    df = df.with_columns([
        pl.when(pl.col("include_conversion_option").cast(pl.Utf8) == "Yes")
          .then(pl.col("co_pct") * pl.col("basic_technical_premium"))
          .otherwise(0.0).alias(target)
    ]).drop(["__co_code", "__co_pct_base"])
    return df


def co_premium_lsdb(df, cats_wide, assump_tables, ctx, target="co_premium",
                     ineligible_statuses: tuple[str, ...] = ()):
    co_tbl = assump_tables["conversion_option"].filter(pl.col("product") == "lsdb")
    rate_le65 = _co_rate(co_tbl, "<=65", "all")
    rate_gt65 = _co_rate(co_tbl, ">65",  "all")

    limits = ctx.limits_for("lsdb")
    max_co_age  = limits.get("max_co_age", 65)
    pre_nra_cap = limits.get("co_pre_nra_cap", 5)

    df = df.with_columns([
        pl.when(pl.col("nra_final") <= 65).then(pl.lit(rate_le65))
          .otherwise(pl.lit(rate_gt65)).alias("__co_rate")
    ])

    df = df.with_columns([
        pl.when(
            pl.col("age") < pl.min_horizontal([
                pl.col("nra_final") - pl.lit(pre_nra_cap),
                pl.lit(max_co_age),
            ])
        )
        .then(pl.col("__co_rate"))
        .otherwise(0.0)
        .alias("co_pct")
    ])

    elig = _co_eligibility_mask(df, ineligible_statuses)
    df = df.with_columns([
        pl.when(
            (pl.col("include_conversion_option").cast(pl.Utf8) == "Yes") & elig
        )
        .then(pl.col("co_pct") * pl.col("basic_technical_premium"))
        .otherwise(0.0)
        .alias(target)
    ]).drop("__co_rate")
    return df
