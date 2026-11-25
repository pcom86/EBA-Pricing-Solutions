from __future__ import annotations

import re
from datetime import date

import polars as pl

try:
    from ctr_loader.products_registry import resolve_ci_mortality_table
except ImportError:
    resolve_ci_mortality_table = None  # type: ignore[assignment]

__all__ = [
    "_apply_baseline_yr",
    "_apply_ci_industry_loading",
    "_apply_ci_mortality_dynamic",
    "_apply_ci_standalone_loading",
    "_apply_industry",
    "_apply_mortality",
    "_apply_mortality_gender_swap",
    "_apply_product_type_adj",
    "_apply_salary_band",
    "_apply_salary_factor",
    "_apply_trend",
    "_apply_waiting_pd",
    "_as_yes",
    "_cap_by_assumption_limit",
    "_cap_by_prior_cover",
    "_cap_category_max_cover",
    "_cap_category_max_cover_field",
    "_cap_spouse_cover_cap",
    "_co_eligibility_mask",
    "_co_rate",
    "_gip_asof",
    "_gip_key_part",
    "_gip_wp_months",
    "_gipf",
    "_lookup_margin",
    "_lookup_spouse_factor",
    "_normalise_percent",
    "_vec_asof_by_gender",
    "_vec_asof_cascade",
    "_vec_wp_months",
    "_vec_yearfrac_30_360",
    "apply_ctr_max_cover",
    "apply_factor",
    "apply_income_taper",
    "apply_prb_premium",
    "apply_profit_margin",
    "attach_category",
    "co_premium_ci",
    "co_premium_spouse",
    "compute_age",
    "compute_ceasing_age",
    "compute_cover",
    "compute_qx",
    "deactivate_when_status",
    "finalise_cover",
    "mark_product_active",
    "pivot_categories_wide",
    "resolve_nra",
    "tax_gross_up",
    "zero_addon",
    "zero_if_status",
]


def compute_age(df: pl.DataFrame, comm_date: date) -> pl.DataFrame:
    days = (pl.lit(comm_date) - pl.col("date_of_birth")).dt.total_days()
    age_exact = days / 365.25
    age_floor = age_exact.floor().cast(pl.Int64)
    return df.with_columns([
        age_exact.alias("age"),
        age_floor.alias("age_lb"),
        age_floor.add(1).alias("age_nb"),
    ])


def pivot_categories_wide(categories: pl.DataFrame, product_key: str) -> pl.DataFrame:
    sub = categories.filter(pl.col("product") == product_key)
    if sub.height == 0:
        return pl.DataFrame(schema={"category_number": pl.Int64})
    wide = sub.pivot(values="value", index="category_number", on="field")
    if "category_flag" in wide.columns:
        wide = wide.with_columns(
            pl.col("category_flag").cast(pl.Float64, strict=False).alias("category_flag_join")
        )
    return wide


def attach_category(df: pl.DataFrame, cats_wide: pl.DataFrame) -> pl.DataFrame:
    if "category_flag_join" not in cats_wide.columns:
        if "category_flag" in df.columns and "category_number" not in df.columns:
            df = df.with_columns(
                (pl.col("category_flag").cast(pl.Float64, strict=False) - 999)
                .cast(pl.Int64, strict=False)
                .alias("category_number")
            )
        return df
    return df.join(cats_wide, left_on="category_flag",
                   right_on="category_flag_join", how="left", suffix="_cat")


def mark_product_active(df: pl.DataFrame,
                         scheme_rating: str | None,
                         active_categories: tuple[int, ...]) -> pl.DataFrame:
    scheme_on = scheme_rating is not None and scheme_rating.strip().lower() != "no"
    return df.with_columns(
        (pl.lit(scheme_on) &
         pl.col("category_number").is_in(list(active_categories))
        ).alias("product_active")
    )


def resolve_nra(df: pl.DataFrame,
                custom_col: str = "nra",
                category_col: str = "nra_cat") -> pl.DataFrame:
    have_cat = category_col in df.columns
    have_custom = custom_col in df.columns

    if not have_cat and not have_custom:
        return df.with_columns(pl.lit(65.0).alias("nra_final"))
    if not have_cat:
        return df.with_columns(
            pl.col(custom_col).cast(pl.Float64, strict=False)
              .fill_null(65.0).alias("nra_final")
        )
    nra_cat_str = pl.col(category_col).cast(pl.Utf8, strict=False)
    if have_custom:
        return df.with_columns(
            pl.when(nra_cat_str.str.starts_with("Custom"))
              .then(pl.col(custom_col).cast(pl.Float64, strict=False))
              .otherwise(pl.col(category_col).cast(pl.Float64, strict=False))
              .fill_null(65.0)
              .alias("nra_final")
        )
    return df.with_columns(
        pl.col(category_col).cast(pl.Float64, strict=False).fill_null(65.0).alias("nra_final")
    )


def compute_ceasing_age(df: pl.DataFrame, mode: str) -> pl.DataFrame:
    if mode == "nra_only":
        return df.with_columns(pl.col("nra_final").alias("ceasing_age"))

    if mode == "deferred_or_nra":
        has_deferred = "deferred_retirals" in df.columns
        has_deferred_age = "deferred_retirement_age" in df.columns
        if not has_deferred or not has_deferred_age:
            return df.with_columns(pl.col("nra_final").alias("ceasing_age"))
        deferred_on = pl.col("deferred_retirals").cast(pl.Utf8) == "Yes"
        return df.with_columns(
            pl.when(pl.col("status") == "DB").then(pl.col("nra_final"))
              .when(deferred_on)
              .then(pl.col("deferred_retirement_age").cast(pl.Float64, strict=False))
              .otherwise(pl.col("nra_final"))
              .alias("ceasing_age")
        )
    raise ValueError(f"Unknown ceasing mode: {mode}")


def apply_factor(df, factor, basis_tables, assump_tables, ctx, target="rate_basic"):
    kind = factor["kind"]
    if kind == "mortality":              return _apply_mortality(df, factor, basis_tables, target)
    if kind == "mortality_gender_swap":  return _apply_mortality_gender_swap(df, factor, basis_tables, target)
    if kind == "salary_band":            return _apply_salary_band(df, factor, basis_tables, target)
    if kind == "salary_factor":          return _apply_salary_factor(df, factor, basis_tables, target)
    if kind == "industry":               return _apply_industry(df, factor, basis_tables, ctx, target)
    if kind == "trend":                  return _apply_trend(df, factor, basis_tables, ctx, target)
    if kind == "waiting_pd":             return _apply_waiting_pd(df, factor, basis_tables, target)
    if kind == "baseline_yr":            return _apply_baseline_yr(df, factor, basis_tables, ctx, target)
    if kind == "product_type_adj":       return _apply_product_type_adj(df, factor, basis_tables, ctx, target)
    if kind == "ci_standalone_loading":  return _apply_ci_standalone_loading(df, factor, assump_tables, ctx, target)
    if kind == "ci_industry_loading":    return _apply_ci_industry_loading(df, factor, basis_tables, ctx, target)
    if kind == "ci_mortality_dynamic":
        enriched = {**factor, "_ctx_ci_rating": ctx.scheme.rating.get("ci", "")}
        return _apply_ci_mortality_dynamic(df, enriched, basis_tables, assump_tables, target)
    raise ValueError(f"Unknown factor kind: {kind}")


def _apply_mortality(df, factor, basis, target):
    product = factor["product"]
    age_col = factor.get("age_col", "age_nb")
    mort = basis["mortality"].filter(pl.col("product") == product)\
                              .select(["age", "gender", "rate"])\
                              .rename({"rate": f"__mort_{product}"})
    mort = mort.with_columns([
        pl.col("age").cast(pl.Int64),
        pl.col("gender").cast(pl.Utf8),
    ])
    df = df.with_columns([
        pl.col(age_col).cast(pl.Int64).alias(age_col),
        pl.col("gender").cast(pl.Utf8),
    ])
    df = df.join(mort, left_on=[age_col, "gender"],
                       right_on=["age", "gender"], how="left")
    df = df.with_columns(
        (pl.col(target).fill_null(1.0) *
         pl.col(f"__mort_{product}").fill_null(0.0)).alias(target)
    ).drop(f"__mort_{product}")
    return df


def _apply_ci_mortality_dynamic(df, factor, basis, assump, target):
    if resolve_ci_mortality_table is None:
        raise RuntimeError("products_registry.resolve_ci_mortality_table not importable")

    ci_map_tbl = assump["ci_table_mapping"]
    ci_rating = factor.get("_ctx_ci_rating") or ""
    variant = ci_rating.strip().lower()
    if variant not in ("accelerator", "standalone"):
        raise ValueError(
            f"[ci_mortality_dynamic] cannot resolve variant from scheme rating "
            f"'{ci_rating!r}' — expected 'Accelerator' or 'Standalone'."
        )

    if "product_option" not in df.columns:
        return df.with_columns([
            (pl.col(target) * pl.lit(0.0)).alias(target),
            pl.lit("Existing").alias("__ci_variant_type"),
        ])

    unique_opts = df["product_option"].unique().drop_nulls().to_list()

    resolutions = {}
    for opt in unique_opts:
        try:
            resolutions[opt] = resolve_ci_mortality_table(ci_map_tbl, opt, variant)
        except KeyError:
            continue

    if not resolutions:
        return df.with_columns([
            (pl.col(target) * pl.lit(0.0)).alias(target),
            pl.lit("Existing").alias("__ci_variant_type"),
        ])

    res_df = pl.DataFrame([
        {
            "product_option": opt,
            "__ci_mort_product": r["table_key"],
            "__ci_age_def":      r["age_definition"],
            "__ci_variant_type": r["variant_type"],
        }
        for opt, r in resolutions.items()
    ])
    df = df.join(res_df, on="product_option", how="left")

    df = df.with_columns([
        pl.col("age").floor().cast(pl.Int64).alias("__age_lb"),
        pl.col("age_nb").cast(pl.Int64).alias("__age_nb_int"),
    ])
    df = df.with_columns(
        pl.when(pl.col("__ci_age_def") == "ALB")
          .then(pl.col("__age_lb"))
          .otherwise(pl.col("__age_nb_int"))
          .alias("__ci_lookup_age")
    )

    needed_products = list({r["table_key"] for r in resolutions.values()})
    mort = basis["mortality"].filter(pl.col("product").is_in(needed_products))\
                              .select(["product", "age", "gender", "rate"])\
                              .rename({"product": "__ci_mort_product", "rate": "__ci_mort_rate"})
    mort = mort.with_columns([
        pl.col("age").cast(pl.Int64),
        pl.col("gender").cast(pl.Utf8),
    ])

    df = df.with_columns([pl.col("gender").cast(pl.Utf8)])
    df = df.join(
        mort,
        left_on=["__ci_mort_product", "__ci_lookup_age", "gender"],
        right_on=["__ci_mort_product", "age", "gender"],
        how="left",
    )

    df = df.with_columns(
        (pl.col(target).fill_null(1.0) * pl.col("__ci_mort_rate").fill_null(0.0)).alias(target)
    )
    return df.drop(["__ci_mort_product", "__ci_age_def", "__age_lb", "__age_nb_int",
                    "__ci_lookup_age", "__ci_mort_rate"])


def _apply_mortality_gender_swap(df, factor, basis, target):
    product = factor["product"]
    age_col = factor.get("age_col", "age_nb")
    mort = basis["mortality"].filter(pl.col("product") == product)\
                              .select(["age", "gender", "rate"])\
                              .rename({"rate": f"__mort_{product}"})
    mort = mort.with_columns([
        pl.col("age").cast(pl.Int64),
        pl.col("gender").cast(pl.Utf8),
    ])
    df = df.with_columns([
        pl.col(age_col).cast(pl.Int64).alias(age_col),
        pl.col("gender").cast(pl.Utf8),
        pl.when(pl.col("gender") == "M").then(pl.lit("F"))
          .when(pl.col("gender") == "F").then(pl.lit("M"))
          .otherwise(pl.col("gender"))
          .alias("__swapped_gender"),
    ])
    df = df.join(
        mort,
        left_on=[age_col, "__swapped_gender"],
        right_on=["age", "gender"],
        how="left",
    )
    df = df.with_columns(
        (pl.col(target).fill_null(1.0) *
         pl.col(f"__mort_{product}").fill_null(0.0)).alias(target)
    ).drop([f"__mort_{product}", "__swapped_gender"])
    return df


def _apply_salary_band(df, factor, basis, target):
    product = factor["product"]
    which = factor["factor"]
    sb = basis["salary_band"].filter(pl.col("product") == product)

    genders_in_sb = set(sb["gender"].unique().to_list())
    use_both = genders_in_sb == {"both"}

    if which == "inc_adj":
        candidates = ["inc_adj", "inc_adj/Salary Factor", "Salary Factor"]
    elif which == "acaf_adj":
        candidates = ["acaf_adj"]
    else:
        candidates = [which]
    src_col = next((c for c in candidates if c in sb.columns), None)
    if src_col is None:
        raise ValueError(f"salary_band[{product}]: no value column for '{which}'. Cols: {sb.columns}")

    sb_clean = sb.select(["gender", "salary_from", src_col])\
                 .rename({src_col: f"__sb_{which}",
                          "salary_from": f"__sb_from_{which}"})
    sb_clean = sb_clean.with_columns([
        pl.col(f"__sb_from_{which}").cast(pl.Float64, strict=False),
        pl.col(f"__sb_{which}").cast(pl.Float64, strict=False),
    ]).drop_nulls(f"__sb_from_{which}").sort(["gender", f"__sb_from_{which}"])

    df = df.with_columns(pl.col("annual_salary").cast(pl.Float64, strict=False))

    if use_both:
        sb_g = sb_clean.filter(pl.col("gender") == "both").drop("gender")\
                       .sort(f"__sb_from_{which}")
        df = df.sort("annual_salary").join_asof(
            sb_g, left_on="annual_salary",
                  right_on=f"__sb_from_{which}", strategy="backward"
        )
    else:
        parts = []
        for g in ("M", "F"):
            sg = sb_clean.filter(pl.col("gender") == g).drop("gender")\
                         .sort(f"__sb_from_{which}")
            dg = df.filter(pl.col("gender") == g).sort("annual_salary")
            if sg.height and dg.height:
                dg = dg.join_asof(sg, left_on="annual_salary",
                                     right_on=f"__sb_from_{which}",
                                     strategy="backward")
            parts.append(dg)
        df = pl.concat(parts, how="diagonal_relaxed")

    df = df.with_columns(
        (pl.col(target).fill_null(1.0) *
         pl.col(f"__sb_{which}").fill_null(1.0)).alias(target)
    )
    drop_cols = [c for c in [f"__sb_{which}", f"__sb_from_{which}"] if c in df.columns]
    if drop_cols:
        df = df.drop(drop_cols)
    return df


def _apply_salary_factor(df, factor, basis, target):
    product = factor["product"]
    sb = basis["salary_band"].filter(pl.col("product") == product)

    val_col = None
    for candidate in ("inc_adj", "inc_adj/Salary Factor", "Salary Factor"):
        if candidate in sb.columns:
            val_col = candidate
            break
    if val_col is None:
        raise ValueError(f"salary_factor: no value col in {product} salary_band. "
                         f"Cols: {sb.columns}")

    sb_clean = (
        sb.select(["gender", "salary_from", val_col])
          .rename({val_col: "__sf_val", "salary_from": "__sf_from"})
          .with_columns([
              pl.col("__sf_from").cast(pl.Float64, strict=False),
              pl.col("__sf_val").cast(pl.Float64, strict=False),
              pl.col("gender").cast(pl.Utf8),
          ])
          .drop_nulls("__sf_from")
    )

    df = df.with_columns(pl.col("annual_salary").cast(pl.Float64, strict=False))

    genders_in_sb = set(sb_clean["gender"].unique().to_list())
    use_both = genders_in_sb == {"both"}

    if use_both:
        sb_g = sb_clean.filter(pl.col("gender") == "both") \
                       .drop("gender") \
                       .sort("__sf_from")
        df = df.sort("annual_salary").join_asof(
            sb_g, left_on="annual_salary",
                  right_on="__sf_from", strategy="backward"
        )
    else:
        df = df.with_columns(pl.col("gender").cast(pl.Utf8))
        parts = []
        for g in ("M", "F"):
            sg = sb_clean.filter(pl.col("gender") == g) \
                         .drop("gender") \
                         .sort("__sf_from")
            dg = df.filter(pl.col("gender") == g).sort("annual_salary")
            if sg.height and dg.height:
                dg = dg.join_asof(sg, left_on="annual_salary",
                                     right_on="__sf_from",
                                     strategy="backward")
            parts.append(dg)
        df = pl.concat(parts, how="diagonal_relaxed")

    df = df.with_columns(
        (pl.col(target) * pl.col("__sf_val").fill_null(1.0)).alias(target)
    )
    drop_cols = [c for c in ["__sf_val", "__sf_from"] if c in df.columns]
    if drop_cols:
        df = df.drop(drop_cols)
    return df


def _apply_industry(df, factor, basis, ctx, target):
    product = factor["product"]
    which = factor["factor"]
    by_size = factor.get("by_scheme_size", True)
    match = factor.get("match", "secondary")

    ind = basis["industry"].filter(pl.col("product") == product)

    if match == "secondary":
        ind = ind.filter(pl.col("secondary") == ctx.secondary_industry)
    elif match == "primary":
        ind = ind.filter(pl.col("primary") == ctx.primary_industry)
        sec_match = ind.filter(pl.col("secondary") == ctx.secondary_industry)
        if sec_match.height:
            ind = sec_match
    else:
        raise ValueError(f"Unknown industry match: {match}")

    if by_size and "scheme_size" in ind.columns:
        ss_match = ind.filter(pl.col("scheme_size") == ctx.scheme_size.lower())
        if ss_match.height:
            ind = ss_match

    if ind.height == 0 or which not in ind.columns:
        val = 1.0
    else:
        raw = ind[which][0]
        val = float(raw) if raw is not None else 1.0

    return df.with_columns((pl.col(target) * pl.lit(val)).alias(target))


def _apply_ci_standalone_loading(df, factor, assump, ctx, target):
    ci_rating = (ctx.scheme.rating.get("ci") or "").strip()
    if ci_rating != "Standalone":
        return df

    loading = ctx.globals.get("ci_standalone_loading")
    if loading is None:
        raise KeyError(
            "[ci_standalone_loading] ctx.globals['ci_standalone_loading'] not found. "
            "Add row to Globals sheet of assumption_master and rebuild globals.parquet."
        )

    if "ci_table_mapping" not in assump:
        raise KeyError("[ci_standalone_loading] assump has no 'ci_table_mapping'")

    if "product_option" not in df.columns:
        raise KeyError("[ci_standalone_loading] df has no 'product_option' column")

    ci_map = assump["ci_table_mapping"]

    new_options = set(
        ci_map.filter(pl.col("variant_type") == "New")["product_option"].to_list()
    )
    if not new_options:
        return df

    factor_scale = 1.0 + float(loading)

    return df.with_columns(
        pl.when(pl.col("product_option").is_in(list(new_options)))
          .then(pl.col(target) * pl.lit(factor_scale))
          .otherwise(pl.col(target))
          .alias(target)
    )


def _apply_ci_industry_loading(df, factor, basis, ctx, target):
    ind = basis["industry"].filter(pl.col("product") == "ci")
    row = ind.filter(pl.col("grouping") == ctx.primary_industry)
    if row.height == 0:
        raise KeyError(
            f"[ci_industry_loading] no row for primary_industry "
            f"'{ctx.primary_industry}' in CI industry basis."
        )
    class_letter = row["class"][0]

    CLASS_LOADINGS = {"A": 10/15, "B": 12/15, "C": 13/15, "D": 14/15, "E": 0.96}
    loading = CLASS_LOADINGS.get(class_letter)
    if loading is None:
        raise ValueError(
            f"[ci_industry_loading] unknown class letter '{class_letter}' "
            f"for industry '{ctx.primary_industry}'."
        )

    return df.with_columns(
        (pl.col(target) * pl.lit(loading)).alias(target)
    )


def _apply_trend(df, factor, basis, ctx, target):
    product = factor["product"]
    year = ctx.calc_date.year
    tr = basis["trend"].filter(
        (pl.col("product") == product) & (pl.col("year") == year)
    )
    if tr.height == 0:
        tr_all = basis["trend"].filter(pl.col("product") == product)
        year_max = tr_all["year"].max()
        tr = tr_all.filter(pl.col("year") == year_max)

    tr = tr.with_columns([
        pl.col("salary_from").cast(pl.Float64, strict=False).alias("__tr_from"),
        pl.col("factor").cast(pl.Float64, strict=False).alias("__trend"),
    ]).select(["gender", "__tr_from", "__trend"])

    df = df.with_columns(pl.col("annual_salary").cast(pl.Float64, strict=False))

    genders = set(tr["gender"].unique().to_list())
    if genders == {"both"}:
        tr_g = tr.filter(pl.col("gender") == "both").drop("gender").sort("__tr_from")
        df = df.sort("annual_salary").join_asof(
            tr_g, left_on="annual_salary",
                  right_on="__tr_from", strategy="backward"
        )
    else:
        parts = []
        for g in ("M", "F"):
            tg = tr.filter(pl.col("gender") == g).drop("gender").sort("__tr_from")
            dg = df.filter(pl.col("gender") == g).sort("annual_salary")
            if tg.height and dg.height:
                dg = dg.join_asof(tg, left_on="annual_salary",
                                     right_on="__tr_from", strategy="backward")
            parts.append(dg)
        df = pl.concat(parts, how="diagonal_relaxed")

    df = df.with_columns(
        (pl.col(target) * pl.col("__trend").fill_null(1.0)).alias(target)
    )
    drop_cols = [c for c in ["__trend", "__tr_from"] if c in df.columns]
    if drop_cols:
        df = df.drop(drop_cols)
    return df


def _apply_waiting_pd(df, factor, basis, target):
    wp = basis["waiting_periods"].filter(pl.col("product") == factor["product"])
    wp = wp.select(["wp_months", "inc_adj"]).with_columns([
        pl.col("wp_months").cast(pl.Float64, strict=False),
        pl.col("inc_adj").cast(pl.Float64, strict=False),
    ]).rename({"inc_adj": "__wp"})
    df = df.with_columns(
        pl.col("waiting_period").cast(pl.Float64, strict=False).alias("__wp_key")
    )
    df = df.join(wp, left_on="__wp_key", right_on="wp_months", how="left")
    df = df.with_columns(
        (pl.col(target) * pl.col("__wp").fill_null(1.0)).alias(target)
    ).drop(["__wp", "__wp_key"])
    return df


def _apply_baseline_yr(df, factor, basis, ctx, target):
    tr = basis["trend"].filter(
        (pl.col("product") == factor["product"]) & (pl.col("year") == ctx.calc_date.year)
    )
    val = float(tr["factor"][0]) if tr.height else 1.0
    return df.with_columns((pl.col(target) * pl.lit(val)).alias(target))


def _apply_product_type_adj(df, factor, basis, ctx, target):
    pta = basis["product_type_adj"].filter(pl.col("product") == factor["product"])
    variant_label = "Accelerator" if ctx.scheme.rating.get(factor["product"]) == "Accelerator" else "Standalone"
    row = pta.filter(pl.col("product_type") == variant_label)
    val = float(row["adj"][0]) if row.height else 1.0
    return df.with_columns((pl.col(target) * pl.lit(val)).alias(target))


def compute_cover(df: pl.DataFrame, recipe: dict) -> pl.DataFrame:
    rule = recipe["cover_rule"]
    cover_col = recipe["cover_column"]

    if cover_col not in df.columns:
        df = df.with_columns(pl.lit(0.0).alias(cover_col))
    else:
        df = df.with_columns(
            pl.col(cover_col).cast(pl.Float64, strict=False).fill_null(0.0).alias(cover_col)
        )

    if rule == "multiple_or_data":
        df = df.with_columns(
            pl.when(pl.col("cover_type").cast(pl.Utf8) == "Multiple")
              .then(pl.col("benefit_multiple").cast(pl.Float64, strict=False)
                       * pl.col("annual_salary"))
              .otherwise(pl.col(cover_col))
              .alias("pre_tax_cover")
        )
    elif rule == "multiple_or_data_or_gla":
        df = df.with_columns(
            pl.when(pl.col("cover_type").cast(pl.Utf8) == "Multiple")
              .then(pl.col("benefit_multiple").cast(pl.Float64, strict=False)
                       * pl.col("annual_salary"))
              .when(pl.col("cover_type").cast(pl.Utf8) == "Set equal to GLA Cover")
              .then(pl.col("__gla_cover_for_ti").cast(pl.Float64, strict=False)
                       .fill_null(0.0))
              .otherwise(pl.col(cover_col))
              .alias("pre_tax_cover")
        )
    elif rule == "fc_composite" or rule == "gip_composite" or rule == "tip_composite" or rule == "education_guardian_composite" or rule == "educate_a_child_composite":
        df = df.with_columns(pl.lit(0.0).alias("pre_tax_cover"))
    else:
        raise ValueError(f"Unknown cover_rule: {rule}")
    return df


def _cap_category_max_cover(df, cap, ctx, prior_results):
    if "maximum_cover_amount" in df.columns:
        df = df.with_columns(
            pl.min_horizontal([
                pl.col("pre_tax_cover"),
                pl.col("maximum_cover_amount").cast(pl.Float64, strict=False)
                    .fill_null(pl.col("pre_tax_cover")),
            ]).alias("pre_tax_cover")
        )
    return df


def _cap_category_max_cover_field(df, cap, ctx, prior_results):
    field_name = cap.get("field", "max_cover")
    if field_name in df.columns:
        df = df.with_columns(
            pl.min_horizontal([
                pl.col("pre_tax_cover"),
                pl.col(field_name).cast(pl.Float64, strict=False)
                    .fill_null(pl.col("pre_tax_cover")),
            ]).alias("pre_tax_cover")
        )
    return df


def _cap_by_prior_cover(df, cap, ctx, prior_results):
    prior_key = cap["prior"]
    prior_df = prior_results.get(prior_key)
    if prior_df is None:
        return df
    prior = prior_df.select(["member_no", "total_cover"])\
                    .rename({"total_cover": "__prior_cover"})
    df = df.join(prior, on="member_no", how="left")
    df = df.with_columns(
        pl.min_horizontal([
            pl.col("pre_tax_cover"),
            pl.col("__prior_cover").fill_null(pl.col("pre_tax_cover")),
        ]).alias("pre_tax_cover")
    ).drop("__prior_cover")
    return df


def _cap_spouse_cover_cap(df, cap, ctx, prior_results):
    prior_df = prior_results.get(cap["prior"])
    if prior_df is None:
        return df
    prior = prior_df.select(["member_no", "total_cover"])\
                    .rename({"total_cover": "__main_gla_cover"})
    df = df.join(prior, on="member_no", how="left")

    prod_key, param_key = cap["max_multiple_key"]
    limits = ctx.limits_for(prod_key)
    max_mul = float(limits.get(param_key, 2))

    df = df.with_columns(
        pl.min_horizontal([
            pl.col("pre_tax_cover"),
            pl.min_horizontal([
                pl.when(pl.col("annual_salary") > 0)
                  .then(pl.col("annual_salary") * pl.lit(max_mul))
                  .otherwise(pl.col("__main_gla_cover")),
                pl.col("__main_gla_cover").fill_null(pl.col("pre_tax_cover")),
            ])
        ]).alias("pre_tax_cover")
    ).drop("__main_gla_cover")
    return df


def _cap_by_assumption_limit(df, cap, ctx, prior_results):
    prod_key = cap["product_key"]
    param_key = cap.get("param_key", "max_cover")
    limits = ctx.limits_for(prod_key)
    cap_val = limits.get(param_key)
    if cap_val is None:
        return df
    cap_val = float(cap_val)
    df = df.with_columns(
        pl.min_horizontal([
            pl.col("pre_tax_cover"),
            pl.lit(cap_val),
        ]).alias("pre_tax_cover")
    )
    return df


def apply_ctr_max_cover(
    df: pl.DataFrame,
    ctx,
    product_key: str,
    assump_fallback_limits: dict | None = None,
    assump_fallback_key: str = "max_cover",
) -> pl.DataFrame:
    ctr_caps: dict[int, float] = (
        getattr(ctx.scheme, "max_cover_per_category", {}).get(product_key) or {}
    )
    assump_cap = None
    if assump_fallback_limits is not None:
        raw = assump_fallback_limits.get(assump_fallback_key)
        if raw is not None:
            try:
                assump_cap = float(raw)
            except (TypeError, ValueError):
                assump_cap = None

    if not ctr_caps and assump_cap is None:
        return df

    if ctr_caps:
        cap_rows = []
        for cat_num, ctr_val in ctr_caps.items():
            eff = float(ctr_val) if ctr_val is not None else None
            cap_rows.append({"category_number": int(cat_num), "__eff_cap": eff})
        cap_df = pl.DataFrame(
            cap_rows,
            schema={"category_number": pl.Int64, "__eff_cap": pl.Float64},
        )

        df = df.with_columns(pl.col("category_number").cast(pl.Int64))
        df = df.join(cap_df, on="category_number", how="left")

        if assump_cap is not None:
            df = df.with_columns(
                pl.col("__eff_cap").fill_null(assump_cap).alias("__eff_cap")
            )
    else:
        df = df.with_columns(pl.lit(assump_cap).alias("__eff_cap"))

    df = df.with_columns(
        pl.when(pl.col("__eff_cap").is_not_null())
          .then(
              pl.min_horizontal(
                  pl.col("pre_tax_cover").cast(pl.Float64, strict=False),
                  pl.col("__eff_cap"),
              )
          )
          .otherwise(pl.col("pre_tax_cover"))
          .alias("pre_tax_cover")
    ).drop("__eff_cap")

    return df


def tax_gross_up(df, tax_tables, country="SA"):
    bands_base = (
        tax_tables.filter(
            (pl.col("country") == country)
            & (~pl.col("tax_year").cast(pl.Utf8).str.contains("_gip"))
        )
                  .select(["band_from", "basic_amount", "margin"])
                  .with_columns([
                      pl.col("band_from").cast(pl.Float64, strict=False),
                      pl.col("basic_amount").cast(pl.Float64, strict=False),
                      pl.col("margin").cast(pl.Float64, strict=False),
                  ])
                  .drop_nulls("band_from")
    )
    bands_1 = bands_base.rename({"band_from": "band_from_1",
                                  "basic_amount": "basic_1",
                                  "margin": "margin_1"}).sort("band_from_1")
    bands_2 = bands_base.rename({"band_from": "band_from_2",
                                  "basic_amount": "basic_2",
                                  "margin": "margin_2"}).sort("band_from_2")

    df = df.with_columns(pl.col("pre_tax_cover").cast(pl.Float64, strict=False))
    df = df.sort("pre_tax_cover").join_asof(
        bands_1, left_on="pre_tax_cover", right_on="band_from_1", strategy="backward"
    )
    df = df.with_columns(
        (pl.col("basic_1") +
         (pl.col("pre_tax_cover") - pl.col("band_from_1")) * pl.col("margin_1")
        ).alias("tax_cover_required")
    )
    df = df.with_columns(
        (pl.col("pre_tax_cover") +
         pl.col("tax_cover_required") / (1 - pl.col("margin_1"))
        ).alias("required_cover_grossed")
    )
    df = df.sort("required_cover_grossed").join_asof(
        bands_2, left_on="required_cover_grossed", right_on="band_from_2", strategy="backward"
    )
    df = df.with_columns(
        (pl.col("band_from_2") > pl.col("band_from_1")).alias("new_band_breached")
    )
    df = df.with_columns(
        (
            (pl.col("band_from_2") - pl.col("pre_tax_cover")) *
                (1/(1 - pl.col("margin_1")) - 1) *
                (1 - pl.col("margin_1")) / (1 - pl.col("margin_2"))
          + (pl.col("band_from_2") - pl.col("pre_tax_cover"))
          + (pl.col("pre_tax_cover") + pl.col("tax_cover_required") - pl.col("band_from_2"))
              / (1 - pl.col("margin_2"))
        ).alias("tax_cover_if_new_band")
    )
    df = df.with_columns(
        (pl.col("pre_tax_cover") + pl.col("tax_cover_if_new_band"))
            .alias("required_cover_new_band")
    )
    df = df.with_columns(
        pl.when(pl.col("cover_net_of_tax").cast(pl.Utf8) != "Yes").then(0.0)
          .when(~pl.col("new_band_breached"))
              .then(pl.col("required_cover_grossed") - pl.col("pre_tax_cover"))
          .otherwise(pl.col("required_cover_new_band") - pl.col("pre_tax_cover"))
          .alias("tax_on_cover")
    )
    drop_cols = [c for c in [
        "band_from_1", "basic_1", "margin_1",
        "band_from_2", "basic_2", "margin_2",
        "tax_cover_required", "required_cover_grossed",
        "new_band_breached", "tax_cover_if_new_band",
        "required_cover_new_band",
    ] if c in df.columns]
    return df.drop(drop_cols)


def apply_income_taper(df: pl.DataFrame) -> pl.DataFrame:
    if "reduction_period" not in df.columns:
        return df
    mask = pl.col("reduction_period").cast(pl.Utf8) == "Income Tapering"
    if df.filter(mask).height == 0:
        return df
    pl.col("nra_final")
    months_floor = ((pl.col("nra_final") - pl.col("age")) * 12).floor()
    rp = (pl.col("reduction_period_months").cast(pl.Float64, strict=False)
          if "reduction_period_months" in df.columns else pl.lit(6.0))
    tapered = pl.min_horizontal(
        pl.col("pre_tax_cover"),
        pl.col("annual_salary") * pl.max_horizontal(pl.lit(0.0), months_floor - rp) / 12.0,
    )
    new_cover = (pl.when(pl.col("status") == "DB").then(pl.lit(0.0))
                   .when(mask).then(tapered)
                   .otherwise(pl.col("pre_tax_cover")))
    return df.with_columns(new_cover.alias("pre_tax_cover"))


def finalise_cover(df: pl.DataFrame, recipe: dict) -> pl.DataFrame:
    apply_tax = recipe.get("apply_tax_gross_up", False)
    if apply_tax:
        df = df.with_columns(
            pl.when(~pl.col("product_active")).then(0.0)
              .when((pl.col("status") == "P") | (pl.col("age") > pl.col("ceasing_age")))
                  .then(pl.col("reduced").cast(pl.Float64, strict=False).fill_null(0.0))
              .when(pl.col("tax_on_cover") < 0).then(pl.col("pre_tax_cover"))
              .otherwise(pl.col("pre_tax_cover") + pl.col("tax_on_cover"))
              .alias("total_cover")
        )
    else:
        df = df.with_columns(
            pl.when(~pl.col("product_active")).then(0.0)
              .when(pl.col("age") > pl.col("ceasing_age")).then(0.0)
              .otherwise(pl.col("pre_tax_cover"))
              .alias("total_cover")
        )
    return df


def zero_if_status(df: pl.DataFrame, statuses: tuple[str, ...]) -> pl.DataFrame:
    if not statuses or "status" not in df.columns:
        return df

    is_zero = pl.col("status").cast(pl.Utf8).is_in(list(statuses))

    zero_cols = [
        "basic_technical_premium",
        "aids_technical_premium",
        "co_premium",
        "waiver_premium",
        "prb_premium",
        "ctc_premium",
        "reinstatement_premium",
        "total_annual_premium",
    ]
    present = [c for c in zero_cols if c in df.columns]
    return df.with_columns([
        pl.when(is_zero).then(0.0).otherwise(pl.col(c)).alias(c) for c in present
    ])


def deactivate_when_status(df: pl.DataFrame, statuses: tuple[str, ...]) -> pl.DataFrame:
    if not statuses:
        return df
    return df.with_columns(
        (pl.col("product_active") & ~pl.col("status").is_in(list(statuses)))
        .alias("product_active")
    )


def _vec_wp_months(wp_str):
    if not wp_str:
        return None
    m = re.search(r"(\d+)", str(wp_str))
    return int(m.group(1)) if m else None


def _vec_asof_cascade(rows, key_col, val_col, x_col):
    expr = pl.lit(None)
    for r in rows:
        expr = pl.when(pl.lit(r[key_col]) <= x_col).then(pl.lit(r[val_col])).otherwise(expr)
    return expr


def _vec_asof_by_gender(sb_by_g, val_col, x_col, gender_col):
    expr_m = _vec_asof_cascade(sb_by_g.get("M", []), "salary_from", val_col, x_col)
    expr_f = _vec_asof_cascade(sb_by_g.get("F", []), "salary_from", val_col, x_col)
    return pl.when(gender_col == "M").then(expr_m).when(gender_col == "F").then(expr_f).otherwise(None)


def _vec_yearfrac_30_360(d0_col, d1_col):
    d0_day = d0_col.dt.day().cast(pl.Int32)
    d1_day = d1_col.dt.day().cast(pl.Int32)
    d0_month = d0_col.dt.month().cast(pl.Int32)
    d1_month = d1_col.dt.month().cast(pl.Int32)
    d0_year = d0_col.dt.year().cast(pl.Int32)
    d1_year = d1_col.dt.year().cast(pl.Int32)
    dd0 = pl.when(d0_day == 31).then(30).otherwise(d0_day)
    dd1 = pl.when((d1_day == 31) & (dd0 == 30)).then(30).otherwise(d1_day)
    return (
        (d1_year - d0_year) * 360
        + (d1_month - d0_month) * 30
        + (dd1 - dd0)
    ) / 360.0


def _co_eligibility_mask(df: pl.DataFrame,
                          ineligible_statuses: tuple[str, ...] = ()) -> pl.Expr:
    mask = pl.col("product_active") & (pl.col("age") <= pl.col("ceasing_age"))
    if ineligible_statuses and "status" in df.columns:
        mask = mask & ~pl.col("status").cast(pl.Utf8).is_in(list(ineligible_statuses))
    return mask


def co_premium_spouse(df, cats_wide, assump_tables, ctx, target="co_premium"):
    co_tbl = assump_tables["conversion_option"].filter(pl.col("product") == "spouse")
    rate_le65 = _co_rate(co_tbl, "<=65", "all")
    rate_gt65 = _co_rate(co_tbl, ">65",  "all")

    df = df.with_columns([
        pl.when(pl.col("age") < 65)
          .then(pl.when(pl.col("nra_final") <= 65).then(pl.lit(rate_le65))
                  .otherwise(pl.lit(rate_gt65)))
          .otherwise(0.0)
          .alias("co_pct")
    ])
    df = df.with_columns([
        pl.when(pl.col("include_conversion_option").cast(pl.Utf8) == "Yes")
          .then(pl.col("co_pct") * pl.col("basic_technical_premium"))
          .otherwise(0.0).alias(target)
    ])
    return df


def co_premium_ci(df, cats_wide, assump_tables, ctx, target="co_premium"):
    co_tbl = assump_tables["conversion_option"].filter(pl.col("product") == "ci")
    co_rate = _co_rate(co_tbl, "<=65", "all")

    limits = ctx.limits_for("ci")
    max_co_age = limits.get("max_co_age", 65)
    pre_nra_cap = limits.get("withdrawal_only_pre_nra_cap", 5)

    df = df.with_columns([
        pl.min_horizontal([
            pl.col("nra_final") - pl.lit(pre_nra_cap),
            pl.lit(max_co_age),
        ]).alias("__co_cutoff")
    ])
    df = df.with_columns([
        pl.when((pl.col("age") < pl.col("__co_cutoff")) & (pl.col("status") == "A"))
          .then(pl.lit(co_rate))
          .otherwise(0.0).alias("co_pct")
    ])
    df = df.with_columns([
        pl.when(pl.col("include_conversion_option").cast(pl.Utf8) == "Yes")
          .then(pl.col("co_pct") * pl.col("basic_technical_premium"))
          .otherwise(0.0).alias(target)
    ]).drop("__co_cutoff")
    return df


def _co_rate(co_tbl, age_condition, co_type) -> float:
    row = co_tbl.filter(
        (pl.col("age_condition") == age_condition) & (pl.col("co_type") == co_type)
    )
    if row.height == 0:
        return 0.0
    return float(row["rate"][0])


def zero_addon(df, ctx, target: str) -> pl.DataFrame:
    return df.with_columns(pl.lit(0.0).alias(target))


def _as_yes(value) -> bool:
    if value is None:
        return False
    s = str(value).strip().lower()
    return s in {"yes", "y", "true", "1", "active"}


def _normalise_percent(value):
    if value is None:
        return None
    if isinstance(value, str):
        s = value.strip().replace("%", "")
        if not s:
            return None
        try:
            v = float(s)
        except ValueError:
            return None
    else:
        try:
            v = float(value)
        except (TypeError, ValueError):
            return None
    if v > 1:
        v = v / 100.0
    return v


def _lookup_spouse_factor(df, key: str) -> float:
    row = df.filter(pl.col("key").cast(pl.Utf8).str.to_lowercase() == key.lower())
    if row.height:
        return float(row["value"][0])
    return 0.0


def compute_qx(df: pl.DataFrame) -> pl.DataFrame:
    return df.with_columns(
        pl.when(pl.col("total_cover") > 0)
          .then((pl.col("basic_technical_premium") + pl.col("aids_technical_premium"))
                / pl.col("total_cover"))
          .otherwise(0.0).alias("qx")
    )


def apply_profit_margin(df, recipe, assump_tables):
    
    basis_mode = recipe.get("profit_margin_basis", "net_risk")

    if basis_mode == "total_annual":
        base_col = "total_annual_premium"
    else:
        base_col = None

    mapping = recipe.get("profit_margin_key_by_cover_type")
    if mapping:
        if "cover_type" not in df.columns:
            raise KeyError(
                "[apply_profit_margin] recipe uses profit_margin_key_by_cover_type "
                "but df has no 'cover_type' column (attach_category must run first)"
            )

        default_key = mapping.get("_default")
        margin_by_type: dict[str, float] = {}
        for ct, key in mapping.items():
            if ct == "_default":
                continue
            prod, mtype = key
            margin_by_type[ct] = _lookup_margin(assump_tables, prod, mtype)

        default_margin = 0.0
        if default_key is not None:
            prod, mtype = default_key
            default_margin = _lookup_margin(assump_tables, prod, mtype)

        df = df.with_columns(
            pl.col("cover_type")
              .cast(pl.Utf8)
              .replace_strict(margin_by_type, default=default_margin,
                              return_dtype=pl.Float64)
              .alias("__margin_rate")
        )
    else:
        prod, mtype = recipe["profit_margin_key"]
        rate = _lookup_margin(assump_tables, prod, mtype)
        df = df.with_columns(pl.lit(rate).alias("__margin_rate"))

    if base_col == "total_annual_premium":
        df = df.with_columns(
            (pl.col("total_annual_premium") * pl.col("__margin_rate"))
            .alias("profit_margin_amount")
        )
    else:
        df = df.with_columns(
            ((pl.col("basic_technical_premium") + pl.col("aids_technical_premium"))
             * pl.col("__margin_rate"))
            .alias("profit_margin_amount")
        )

   
    if basis_mode == "total_annual":
        base = pl.col("total_annual_premium")
    elif basis_mode == "annuity_pv":
        base = (
            pl.col("basic_technical_premium")
            + pl.col("aids_technical_premium")
            + pl.col("waiver_premium").fill_null(0.0)
        )
    else:
        base = pl.col("basic_technical_premium") + pl.col("aids_technical_premium")
 
    df = df.with_columns((base * pl.col("__margin_rate")).alias("profit_margin_amount"))
    return df.drop("__margin_rate")


def _lookup_margin(assump_tables, product: str, margin_type: str) -> float:
    tbl = assump_tables.get("profit_margins")
    if tbl is None:
        return 0.0
    row = tbl.filter(
        (pl.col("product") == product) & (pl.col("margin_type") == margin_type)
    )
    if row.height == 0:
        return 0.0
    raw = row["margin"][0]
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.0


def apply_prb_premium(df: pl.DataFrame, ctx, product_key: str) -> pl.DataFrame:
    scheme = getattr(ctx, "scheme", None)
    if scheme is None:
        return df.with_columns(pl.lit(0.0).alias("prb_premium"))

    prb_by_product = getattr(scheme, "profit_refund_loading_by_product", {}) or {}

    try:
        pro_loading = float(prb_by_product.get(product_key, 0.0) or 0.0)
    except (TypeError, ValueError):
        pro_loading = 0.0

    if pro_loading == 0.0:
        return df.with_columns(pl.lit(0.0).alias("prb_premium"))

    co = pl.col("co_premium").fill_null(0.0) if "co_premium" in df.columns else pl.lit(0.0)
    tot = pl.col("total_annual_premium").fill_null(0.0) if "total_annual_premium" in df.columns else pl.lit(0.0)

    prb_base = tot - co

    return df.with_columns(
        pl.when(
            pl.col("product_active")
            & (pl.col("age") <= pl.col("ceasing_age"))
        )
        .then(prb_base * pl.lit(pro_loading))
        .otherwise(0.0)
        .alias("prb_premium")
    )


def _gipf(x):
    return 0.0 if x is None else float(x)


def _gip_key_part(x):
    if x is None:
        return ""
    if isinstance(x, (int, float)) and float(x).is_integer():
        return str(int(x))
    return str(x)


def _gip_asof(rows, key_col, val_col, x):
    hit = None
    for r in rows:
        if r[key_col] <= x:
            hit = r[val_col]
        else:
            break
    return hit


def _gip_wp_months(wp_str):
    if not wp_str:
        return None
    import re
    m = re.search(r"(\d+)", str(wp_str))
    return int(m.group(1)) if m else None
