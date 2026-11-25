from __future__ import annotations

from typing import Any

import polars as pl

PRODUCT_DISPLAY_NAMES = {
    "gla":                 "Group Life Assurance",
    "lsdb":                "Lump Sum Disability Benefit",
    "ci":                  "Critical Illness",
    "spouse_gla":          "Spouse GLA",
    "spouse_lsdb":         "Spouse LSDB",
    "terminal":            "Terminal Illness Cover",
    "accident_death":      "Accidental Death",
    "accident_disability": "Accidental Disability",
    "dismemberment":       "Dismemberment",
}

METRIC_COLS = [
    "total_cover",
    "basic_technical_premium",
    "aids_technical_premium",
    "co_premium",
    "waiver_premium",
    "prb_premium",
    "ctc_premium",
    "reinstatement_premium",
    "total_annual_premium",
    "profit_margin_amount",
]


def _ensure_cols(df: pl.DataFrame) -> pl.DataFrame:
    for c in METRIC_COLS:
        if c not in df.columns:
            df = df.with_columns(pl.lit(0.0).alias(c))
    return df


def scheme_summary(all_results: dict[str, pl.DataFrame]) -> pl.DataFrame:
    rows: list[dict[str, Any]] = []
    for pkey, df in all_results.items():
        df = _ensure_cols(df)
        active = df.filter(pl.col("product_active"))
        row: dict[str, Any] = {
            "product_key":   pkey,
            "product_name":  PRODUCT_DISPLAY_NAMES.get(pkey, pkey),
            "n_members":     active.height,
        }
        for c in METRIC_COLS:
            row[c] = float(active[c].sum())
        row["total_risk_premium"] = row["total_annual_premium"] + row["profit_margin_amount"]
        rows.append(row)

    df = pl.DataFrame(rows)

    grand: dict[str, Any] = {
        "product_key":  "GRAND_TOTAL",
        "product_name": "GRAND TOTAL",
        "n_members":    None,
    }
    for c in METRIC_COLS + ["total_risk_premium"]:
        grand[c] = float(df[c].sum())
    df = pl.concat([df, pl.DataFrame([grand])], how="diagonal_relaxed")
    return df


def category_summary(all_results: dict[str, pl.DataFrame]) -> pl.DataFrame:
    parts = []
    for pkey, df in all_results.items():
        df = _ensure_cols(df)
        active = df.filter(pl.col("product_active"))
        if active.height == 0:
            continue
        by_cat = active.group_by("category_number").agg([
            pl.len().alias("n_members"),
            pl.col("total_cover").sum().alias("total_cover"),
            pl.col("basic_technical_premium").sum().alias("basic_technical_premium"),
            pl.col("total_annual_premium").sum().alias("total_annual_premium"),
            pl.col("profit_margin_amount").sum().alias("profit_margin_amount"),
        ]).sort("category_number")
        by_cat = by_cat.with_columns(pl.lit(pkey).alias("product_key"))
        by_cat = by_cat.with_columns(
            pl.lit(PRODUCT_DISPLAY_NAMES.get(pkey, pkey)).alias("product_name")
        )
        by_cat = by_cat.with_columns(
            (pl.col("total_annual_premium") + pl.col("profit_margin_amount"))
                .alias("total_risk_premium")
        )
        parts.append(by_cat)

    if not parts:
        return pl.DataFrame()
    combined = pl.concat(parts, how="diagonal_relaxed")
    return combined.select([
        "product_key", "product_name", "category_number",
        "n_members", "total_cover",
        "basic_technical_premium", "total_annual_premium",
        "profit_margin_amount", "total_risk_premium",
    ]).sort(["product_key", "category_number"])


def member_summary(all_results: dict[str, pl.DataFrame]) -> pl.DataFrame:
    if not all_results:
        return pl.DataFrame()

    anchor_key = "gla" if "gla" in all_results else next(iter(all_results))
    anchor = all_results[anchor_key].select([
        "member_no", "member_name", "gender", "age", "category_number", "annual_salary",
    ])

    out = anchor
    for pkey, df in all_results.items():
        df = _ensure_cols(df)
        slim = df.select([
            "member_no",
            pl.col("total_cover").alias(f"{pkey}_cover"),
            pl.col("total_annual_premium").alias(f"{pkey}_premium"),
            pl.col("profit_margin_amount").alias(f"{pkey}_margin"),
        ])
        out = out.join(slim, on="member_no", how="left")

    prem_cols = [f"{p}_premium" for p in all_results]
    marg_cols = [f"{p}_margin" for p in all_results]

    out = out.with_columns([
        pl.sum_horizontal([pl.col(c) for c in prem_cols]).alias("total_premium_all_products"),
        pl.sum_horizontal([pl.col(c) for c in marg_cols]).alias("total_margin_all_products"),
    ])
    out = out.with_columns(
        (pl.col("total_premium_all_products") + pl.col("total_margin_all_products"))
            .alias("total_risk_all_products")
    )
    return out.sort("member_no")