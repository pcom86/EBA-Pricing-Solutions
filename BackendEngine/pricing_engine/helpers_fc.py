from __future__ import annotations

import polars as pl

from .helpers_common import (
    _apply_industry,
    _apply_mortality,
    _apply_mortality_gender_swap,
    _apply_salary_band,
    _apply_trend,
)

__all__ = [
    "_aggregate_ext_fc_age_bands",
    "_apply_fc_composite",
    "_build_ext_fc_age_rate_table",
    "_ext_fc_salary_factor_lookup",
    "_fc_apply_joins_and_benefits",
    "_fc_apply_namibia_rate_cut_and_rate_basic",
    "_fc_apply_rating_factor_pipeline",
    "_fc_compute_basic_premium_and_finalize_cover",
    "_fc_compute_ctc_premium",
    "_fc_compute_prb_premium",
    "_fc_compute_waiver_premium",
    "_fc_db_loading",
    "_fc_demographics_join",
    "_fc_family_composition_join",
    "_fc_second_spouse_loading",
    "apply_post_rate_adjust",
    "build_ext_fc_rate_card",
]


def _apply_fc_composite(df: pl.DataFrame,
                         spec: dict,
                         basis_tables: dict[str, pl.DataFrame],
                         assump_tables: dict | None,
                         ctx) -> pl.DataFrame:
    df = _fc_apply_joins_and_benefits(df, spec, basis_tables, assump_tables, ctx)
    df = _fc_apply_rating_factor_pipeline(df, basis_tables, ctx)
    df = _fc_compute_basic_premium_and_finalize_cover(df, basis_tables)
    df = _fc_apply_namibia_rate_cut_and_rate_basic(df, spec, ctx)
    df = _fc_compute_ctc_premium(df, assump_tables, ctx)
    df = _fc_compute_waiver_premium(df, assump_tables, ctx)
    df = _fc_compute_prb_premium(df, ctx)

    drop_cols = [c for c in ["__bb", "__bc", "__bd", "__be",
                              "__ar", "__av", "__as", "__at", "__au",
                              "__fc_ag_cover",
                              "__fc_ben_primary_insured", "__fc_ben_spouse",
                              "__fc_ben_child_14_plus", "__fc_ben_child_6_13",
                              "__fc_ben_child_1_5", "__fc_ben_child_under_1",
                              "__fc_ben_stillborn", "__fc_ben_parents_cover"]
                 if c in df.columns]
    return df.drop(drop_cols)


def _fc_apply_joins_and_benefits(df, spec, basis_tables, assump_tables, ctx):
    df = _fc_demographics_join(df, basis_tables)
    df = _fc_family_composition_join(df, basis_tables, ctx)
    df = _fc_second_spouse_loading(df, assump_tables, ctx)
    df = _fc_db_loading(df, {"gate_global": spec.get("db_gate_global",
                                                     "active_selection_db_load")},
                        assump_tables, ctx)

    benefit_map = (
        getattr(ctx.scheme, "fc_benefit_amounts", {}) or {} if ctx else {}
    )
    ben_rows = []
    for cat, benefits in benefit_map.items():
        row = {"category_number": int(cat)}
        for k in ("primary_insured", "spouse", "child_14_plus", "child_6_13",
                  "child_1_5", "child_under_1", "stillborn", "parents_cover"):
            row[f"__fc_ben_{k}"] = float(benefits.get(k, 0.0))
        ben_rows.append(row)

    if ben_rows:
        ben_df = pl.DataFrame(ben_rows)
        df = df.with_columns(pl.col("category_number").cast(pl.Int64))
        df = df.join(ben_df, on="category_number", how="left")
    else:
        for k in ("primary_insured", "spouse", "child_14_plus", "child_6_13",
                  "child_1_5", "child_under_1", "stillborn", "parents_cover"):
            df = df.with_columns(pl.lit(0.0).alias(f"__fc_ben_{k}"))
    for k in ("primary_insured", "spouse", "child_14_plus", "child_6_13",
              "child_1_5", "child_under_1", "stillborn", "parents_cover"):
        df = df.with_columns(pl.col(f"__fc_ben_{k}").fill_null(0.0))

    df = df.with_columns(
        (
            pl.col("__fc_ben_primary_insured")
            + pl.col("pct_married") * pl.col("__fc_ben_spouse")
              * pl.col("second_spouse_loading")
            + pl.col("n_children_0_13")  * pl.col("__fc_ben_child_6_13")
            + pl.col("n_children_14_plus") * pl.col("__fc_ben_child_14_plus")
            + (pl.col("n_parents")
               + pl.col("n_in_laws") * pl.col("pct_married"))
              * pl.col("__fc_ben_parents_cover")
        ).alias("__fc_ag_cover")
    )
    return df


def _fc_apply_rating_factor_pipeline(df, basis_tables, ctx):
    tmp = df.with_columns(pl.lit(1.0).alias("__ar"))
    tmp = _apply_mortality(tmp, {"kind": "mortality", "product": "gla",
                                  "age_col": "age_nb"}, basis_tables, "__ar")

    tmp = tmp.with_columns(pl.lit(1.0).alias("__av"))
    tmp = _apply_mortality_gender_swap(tmp, {"kind": "mortality_gender_swap",
                                              "product": "gla", "age_col": "age_nb"},
                                        basis_tables, "__av")

    tmp = tmp.with_columns(pl.lit(1.0).alias("__as"))
    tmp = _apply_salary_band(tmp, {"kind": "salary_band", "product": "gla",
                                    "factor": "inc_adj"}, basis_tables, "__as")


    tmp = tmp.with_columns(pl.lit(1.0).alias("__at"))
    tmp = _apply_industry(tmp, {"kind": "industry", "product": "gla",
                                 "factor": "inc_adj", "by_scheme_size": True},
                          basis_tables, ctx, "__at")


    tmp = tmp.with_columns(pl.lit(1.0).alias("__au"))
    tmp = _apply_trend(tmp, {"kind": "trend", "product": "gla"},
                       basis_tables, ctx, "__au")

    return tmp


def _fc_compute_basic_premium_and_finalize_cover(df, basis_tables):
    fc_rates_basic_0  = 0.0
    fc_rates_basic_14 = 0.0
    fc_rates_aids_14  = 0.0
    if basis_tables is not None and "fc_rates" in basis_tables:
        fr = basis_tables["fc_rates"]
        r0 = fr.filter(pl.col("age_from") == 0)
        r14 = fr.filter(pl.col("age_from") == 14)
        if r0.height:
            fc_rates_basic_0 = float(r0["basic_rate"][0] or 0.0)
        if r14.height:
            fc_rates_basic_14 = float(r14["basic_rate"][0] or 0.0)
            fc_rates_aids_14  = float(r14["aids_rate"][0] or 0.0)

    df = df.with_columns([
        (pl.col("__ar") / 1000.0 * pl.col("__as") * pl.col("__at")
         * pl.col("__au") * pl.col("__fc_ben_primary_insured")).alias("__bb"),

        (pl.col("__av") / 1000.0 * pl.col("__as") * pl.col("__at")
         * pl.col("__au") * pl.col("__fc_ben_spouse")
         * pl.col("second_spouse_loading") * pl.col("pct_married")).alias("__bc"),

        (pl.lit(fc_rates_basic_0) * pl.col("n_children_0_13") / 1000.0
         * pl.col("__as") * pl.col("__at")
         * pl.col("__fc_ben_child_6_13")).alias("__bd"),

        ((pl.lit(fc_rates_basic_14) + pl.lit(fc_rates_aids_14))
         * pl.col("n_children_14_plus") / 1000.0
         * pl.col("__as") * pl.col("__at")
         * pl.col("__fc_ben_child_14_plus")).alias("__be"),
    ])
    df = df.with_columns(
        (pl.col("__bb") + pl.col("__bc") + pl.col("__bd") + pl.col("__be"))
        .alias("basic_technical_premium")
    )


    df = df.with_columns([
        pl.lit(0.0).alias("aids_technical_premium"),
        pl.lit(0.0).alias("rate_aids"),
    ])

    df = df.with_columns(
        pl.when(~pl.col("product_active")).then(0.0)
          .when(pl.col("age") > pl.col("ceasing_age")).then(0.0)
          .otherwise(pl.col("__fc_ag_cover"))
          .alias("total_cover")
    )
    df = df.with_columns(
        pl.when(~pl.col("product_active") | (pl.col("age") > pl.col("ceasing_age")))
          .then(0.0)
          .otherwise(pl.col("basic_technical_premium"))
          .alias("basic_technical_premium")
    )
    return df


def _fc_apply_namibia_rate_cut_and_rate_basic(df, spec, ctx):
    nam_cut_key = spec.get("namibia_rate_cut_global", "namibia_competitive_factor")
    country = str(getattr(ctx.scheme, "country_scale", "") or "").strip().lower()
    if country in ("namibia", "nam", "2") and ctx is not None:
        nam_cut = ctx.globals.get(nam_cut_key)
        if nam_cut is not None:
            try:
                nam_cut = float(nam_cut)
                df = df.with_columns(
                    (pl.col("basic_technical_premium") * (1.0 + nam_cut))
                    .alias("basic_technical_premium")
                )
            except (TypeError, ValueError):
                pass

    df = df.with_columns(
        pl.when(pl.col("total_cover") > 0)
          .then(pl.col("basic_technical_premium") / pl.col("total_cover") * 1000)
          .otherwise(0.0)
          .alias("rate_basic")
    )
    return df


def _fc_compute_ctc_premium(df, assump_tables, ctx):
    base = (pl.col("basic_technical_premium").fill_null(0.0)
            + pl.col("aids_technical_premium").fill_null(0.0))
    cat_expr = pl.col("category_number").cast(pl.Int64)

    ctc_scalar = 0.0
    if assump_tables is not None and "product_limits" in assump_tables:
        row = assump_tables["product_limits"].filter(
            (pl.col("product") == "fc") & (pl.col("param_name") == "ctc_cost")
        )
        if row.height:
            try:
                ctc_scalar = float(row["param_value"][0])
            except (TypeError, ValueError):
                ctc_scalar = 0.0

    include_map = getattr(ctx.scheme, "fc_include_ctc", {}) or {}
    ctc_yes_cats = [int(c) for c, v in include_map.items()
                     if isinstance(v, str) and v.strip().lower() == "yes"]

    if ctc_scalar > 0 and ctc_yes_cats:
        df = df.with_columns(
            pl.when(cat_expr.is_in(ctc_yes_cats))
              .then(base * pl.lit(ctc_scalar))
              .otherwise(0.0)
              .alias("ctc_premium")
        )
    else:
        df = df.with_columns(pl.lit(0.0).alias("ctc_premium"))
    return df


def _fc_compute_waiver_premium(df, assump_tables, ctx):
    base = (pl.col("basic_technical_premium").fill_null(0.0)
            + pl.col("aids_technical_premium").fill_null(0.0))
    cat_expr = pl.col("category_number").cast(pl.Int64)

    def _pl_scalar(param_name: str) -> float:
        if assump_tables is None or "product_limits" not in assump_tables:
            return 0.0
        r = assump_tables["product_limits"].filter(
            (pl.col("product") == "fc") & (pl.col("param_name") == param_name)
        )
        if r.height:
            try:
                return float(r["param_value"][0])
            except (TypeError, ValueError):
                return 0.0
        return 0.0

    pup_death      = _pl_scalar("pup_cost_death")
    pup_lsdb       = _pl_scalar("pup_cost_lsdb")
    pup_death_lsdb = _pl_scalar("pup_cost_death_lsdb")

    def _yes_cats(field_name: str) -> list[int]:
        m = getattr(ctx.scheme, field_name, {}) or {}
        return [int(c) for c, v in m.items()
                if isinstance(v, str) and v.strip().lower() == "yes"]

    death_cats      = _yes_cats("fc_pup_death")
    lsdb_cats       = _yes_cats("fc_pup_lsdb")
    death_lsdb_cats = _yes_cats("fc_pup_death_lsdb")

    df = df.with_columns(
        pl.when(cat_expr.is_in(death_cats)).then(base * pl.lit(pup_death))
          .when(cat_expr.is_in(lsdb_cats)).then(base * pl.lit(pup_lsdb))
          .when(cat_expr.is_in(death_lsdb_cats)).then(base * pl.lit(pup_death_lsdb))
          .otherwise(0.0)
          .alias("waiver_premium")
    )
    return df


def _fc_compute_prb_premium(df, ctx):
    prb_map = getattr(ctx.scheme, "profit_refund_loading_by_product", {}) or {}
    fc_prb_rate = 0.0
    if prb_map.get("fc") is not None:
        try:
            fc_prb_rate = float(prb_map["fc"])
        except (TypeError, ValueError):
            fc_prb_rate = 0.0

    if fc_prb_rate > 0:
        prb_base = (pl.col("basic_technical_premium").fill_null(0.0)
                    + pl.col("aids_technical_premium").fill_null(0.0))
        df = df.with_columns(
            pl.when(pl.col("product_active") & (pl.col("age") <= pl.col("ceasing_age")))
              .then(prb_base * pl.lit(fc_prb_rate))
              .otherwise(0.0)
              .alias("prb_premium")
        )
    else:
        df = df.with_columns(pl.lit(0.0).alias("prb_premium"))
    return df


def _fc_demographics_join(df: pl.DataFrame, basis_tables: dict | None) -> pl.DataFrame:
    if basis_tables is None or "fc_demographics" not in basis_tables:
        return df.with_columns([
            pl.lit(0.0).alias("pct_married"),
            pl.lit(0.0).alias("n_children_0_13"),
            pl.lit(0.0).alias("n_children_14_plus"),
        ])

    demo = basis_tables["fc_demographics"].select([
        "age",
        pl.col("pct_married").cast(pl.Float64, strict=False),
        pl.col("n_children_0_13").cast(pl.Float64, strict=False),
        pl.col("n_children_14_plus").cast(pl.Float64, strict=False),
    ]).with_columns(pl.col("age").cast(pl.Int64))

    df = df.with_columns(pl.col("age_nb").cast(pl.Int64))
    df = df.join(demo, left_on="age_nb", right_on="age", how="left")
    return df.with_columns([
        pl.col("pct_married").fill_null(0.0),
        pl.col("n_children_0_13").fill_null(0.0),
        pl.col("n_children_14_plus").fill_null(0.0),
    ])


def _fc_family_composition_join(df: pl.DataFrame,
                                 basis_tables: dict | None,
                                 ctx) -> pl.DataFrame:
    if basis_tables is None or "fc_family_composition" not in basis_tables:
        return df.with_columns([
            pl.lit(0.0).alias("n_parents"),
            pl.lit(0.0).alias("n_in_laws"),
        ])

    fam = basis_tables["fc_family_composition"].select([
        pl.col("age_from").cast(pl.Float64, strict=False).alias("__fam_from"),
        pl.col("n_parents").cast(pl.Float64, strict=False),
        pl.col("n_in_laws").cast(pl.Float64, strict=False),
    ]).drop_nulls("__fam_from").sort("__fam_from")

    df = df.with_columns(pl.col("age_nb").cast(pl.Float64, strict=False))
    df = df.sort("age_nb").join_asof(
        fam, left_on="age_nb", right_on="__fam_from", strategy="backward"
    )
    df = df.with_columns([
        pl.col("n_parents").fill_null(0.0),
        pl.col("n_in_laws").fill_null(0.0),
    ]).drop("__fam_from")

    parents_map = getattr(ctx.scheme, "fc_parents_included", {}) or {} if ctx else {}
    in_laws_map = getattr(ctx.scheme, "fc_parents_in_law_included", {}) or {} if ctx else {}

    parents_yes_cats = [int(c) for c, v in parents_map.items()
                        if isinstance(v, str) and v.strip().lower() == "yes"]
    in_laws_yes_cats = [int(c) for c, v in in_laws_map.items()
                        if isinstance(v, str) and v.strip().lower() == "yes"]

    df = df.with_columns([
        pl.when(pl.col("category_number").cast(pl.Int64).is_in(parents_yes_cats))
          .then(pl.col("n_parents"))
          .otherwise(0.0)
          .alias("n_parents"),
        pl.when(pl.col("category_number").cast(pl.Int64).is_in(in_laws_yes_cats))
          .then(pl.col("n_in_laws"))
          .otherwise(0.0)
          .alias("n_in_laws"),
    ])
    return df


def _fc_second_spouse_loading(df: pl.DataFrame,
                               assump_tables: dict | None,
                               ctx) -> pl.DataFrame:
    loading_scalar = 0.0
    if assump_tables is not None and "product_limits" in assump_tables:
        pl_tbl = assump_tables["product_limits"].filter(
            (pl.col("product") == "fc") &
            (pl.col("param_name") == "second_spouse_loading")
        )
        if pl_tbl.height:
            raw = pl_tbl["param_value"][0]
            try:
                loading_scalar = float(raw)
            except (TypeError, ValueError):
                loading_scalar = 0.0

    max_spouses_map = (
        getattr(ctx.scheme, "fc_max_no_of_spouses", {}) or {} if ctx else {}
    )
    if max_spouses_map:
        ms_df = pl.DataFrame(
            [{"category_number": int(c), "__max_spouses": float(v)}
             for c, v in max_spouses_map.items()],
            schema={"category_number": pl.Int64, "__max_spouses": pl.Float64},
        )
        df = df.with_columns(pl.col("category_number").cast(pl.Int64))
        df = df.join(ms_df, on="category_number", how="left")
    else:
        df = df.with_columns(pl.lit(1.0).alias("__max_spouses"))

    df = df.with_columns(
        pl.col("__max_spouses").fill_null(1.0).alias("__max_spouses")
    )
    df = df.with_columns(
        pl.when(pl.col("__max_spouses") <= 1)
          .then(pl.lit(1.0))
          .otherwise(1.0 + pl.lit(loading_scalar) * (pl.col("__max_spouses") - 1.0))
          .alias("second_spouse_loading")
    ).drop("__max_spouses")
    return df


def _fc_db_loading(df: pl.DataFrame, adj: dict,
                    assump_tables: dict | None, ctx) -> pl.DataFrame:
    gate_global = adj.get("gate_global", "active_selection_db_load")
    gate_on = False
    if ctx is not None:
        gate_raw = (ctx.globals or {}).get(gate_global)
        if isinstance(gate_raw, bool):
            gate_on = gate_raw
        else:
            try:
                gate_on = gate_raw is not None and float(gate_raw) == 1.0
            except (TypeError, ValueError):
                gate_on = False

    if not gate_on or assump_tables is None or "fc_db_loading" not in assump_tables:
        return df.with_columns(pl.lit(1.0).alias("db_loading"))

    tbl = assump_tables["fc_db_loading"].select([
        pl.col("years_disabled").cast(pl.Int64),
        pl.col("loading_factor").cast(pl.Float64, strict=False),
    ]).sort("years_disabled")

    if "date_of_disability" not in df.columns or ctx is None:
        return df.with_columns(pl.lit(1.0).alias("db_loading"))

    comm_date = ctx.calc_date
    df = df.with_columns([
        pl.when(pl.col("date_of_disability").is_not_null())
          .then(
              ((pl.lit(comm_date) - pl.col("date_of_disability"))
                 .dt.total_days() / 365.25).floor().cast(pl.Int64) + 1
          )
          .otherwise(pl.lit(None, dtype=pl.Int64))
          .alias("__years_disabled")
    ])
    df = df.with_columns(
        pl.min_horizontal([pl.col("__years_disabled"), pl.lit(5)])
          .alias("__years_disabled_capped")
    )

    df = df.join(
        tbl.rename({"years_disabled": "__years_disabled_capped",
                    "loading_factor": "__db_loading_raw"}),
        on="__years_disabled_capped", how="left",
    )
    df = df.with_columns(
        pl.when(pl.col("status").cast(pl.Utf8) == "DB")
          .then(pl.col("__db_loading_raw").fill_null(1.0))
          .otherwise(1.0)
          .alias("db_loading")
    ).drop(["__years_disabled", "__years_disabled_capped", "__db_loading_raw"])
    return df


def _ext_fc_salary_factor_lookup(
    category_avg_salary: float,
    assumed_avg_sal: float,
    salary_factors: pl.DataFrame,
    rate_type: str,
    gender: str,
) -> float:
    sub = salary_factors.filter(
        (pl.col("rate_type") == rate_type) & (pl.col("gender") == gender)
    ).sort("salary_band_from")

    if sub.height == 0:
        return 0.0

    eligible = sub.filter(pl.col("salary_band_from") <= category_avg_salary)
    if eligible.height == 0:
        row = sub.row(0, named=True)
    else:
        row = eligible.tail(1).row(0, named=True)
    return float(row["factor"])


def _build_ext_fc_age_rate_table(
    category_number: int,
    relative_type: str,
    pct_male: float,
    avg_salary: float,
    dist_available: bool,
    live_census_dist: dict[int, float] | None,
    basis_tables: dict,
) -> pl.DataFrame:
    base_rates = basis_tables["ext_fc_base_rates"].filter(
        pl.col("relative_type") == relative_type
    )
    salary_factors = basis_tables["ext_fc_salary_factors"]
    basis_params = basis_tables["ext_fc_basis_params"]
    assumed_dist = basis_tables["ext_fc_assumed_age_dist"].filter(
        pl.col("relative_type") == relative_type
    )

    params = {row["param_name"]: row["param_value"] for row in basis_params.iter_rows(named=True)}
    assumed_avg_sal = params.get("assumed_avg_sal", 120000.0)
    basic_rate_adjustor = params.get("basic_rate_adjustor", 1.0)
    aids_rate_adjustor = params.get("aids_rate_adjustor", 1.0)

    pct_male = float(pct_male or 0.0)
    pct_female = 1.0 - pct_male

    basic_male_cat_f    = _ext_fc_salary_factor_lookup(avg_salary, assumed_avg_sal, salary_factors, "basic", "male")
    basic_female_cat_f  = _ext_fc_salary_factor_lookup(avg_salary, assumed_avg_sal, salary_factors, "basic", "female")
    aids_male_cat_f      = _ext_fc_salary_factor_lookup(avg_salary, assumed_avg_sal, salary_factors, "aids", "male")
    aids_female_cat_f    = _ext_fc_salary_factor_lookup(avg_salary, assumed_avg_sal, salary_factors, "aids", "female")

    basic_male_avg_f    = _ext_fc_salary_factor_lookup(assumed_avg_sal, assumed_avg_sal, salary_factors, "basic", "male")
    basic_female_avg_f  = _ext_fc_salary_factor_lookup(assumed_avg_sal, assumed_avg_sal, salary_factors, "basic", "female")
    aids_male_avg_f      = _ext_fc_salary_factor_lookup(assumed_avg_sal, assumed_avg_sal, salary_factors, "aids", "male")
    aids_female_avg_f    = _ext_fc_salary_factor_lookup(assumed_avg_sal, assumed_avg_sal, salary_factors, "aids", "female")

    basic_male_ratio   = basic_male_cat_f / basic_male_avg_f if basic_male_avg_f else 0.0
    basic_female_ratio = basic_female_cat_f / basic_female_avg_f if basic_female_avg_f else 0.0
    aids_male_ratio     = aids_male_cat_f / aids_male_avg_f if aids_male_avg_f else 0.0
    aids_female_ratio   = aids_female_cat_f / aids_female_avg_f if aids_female_avg_f else 0.0

    rates = base_rates.select(["age", "basic_rate", "aids_base_rate"])
    rates = rates.with_columns([
        (pl.col("basic_rate") * basic_rate_adjustor
         * (basic_male_ratio * pct_male + basic_female_ratio * pct_female)).alias("age_basic_rate"),
        (pl.col("aids_base_rate") * aids_rate_adjustor
         * (aids_male_ratio * pct_male + aids_female_ratio * pct_female)).alias("age_aids_rate"),
    ])

    if dist_available and live_census_dist:
        dist_rows = [{"age": age, "dist_weight": w} for age, w in live_census_dist.items()]
        dist_df = pl.DataFrame(dist_rows, schema={"age": pl.Int64, "dist_weight": pl.Float64})
        rates = rates.join(dist_df, on="age", how="left").with_columns(
            pl.col("dist_weight").fill_null(0.0)
        )
    else:
        dist_df = assumed_dist.select(["age", "assumed_age_dist_weight"]).rename(
            {"assumed_age_dist_weight": "dist_weight"}
        )
        rates = rates.join(dist_df, on="age", how="left").with_columns(
            pl.col("dist_weight").fill_null(0.0)
        )

    rates = rates.with_columns(
        ((pl.col("age_basic_rate") + pl.col("age_aids_rate")) * pl.col("dist_weight"))
        .alias("weighted_risk_rate")
    )

    return rates.select(["age", "weighted_risk_rate", "dist_weight"])


def _aggregate_ext_fc_age_bands(
    age_rate_table: pl.DataFrame,
    bands: list[dict],
) -> list[dict]:
    results = []
    for i, band in enumerate(bands):
        start_age = band["start_age"]
        end_age = band["end_age"]
        cover = band["cover"]

        sub = age_rate_table.filter(
            (pl.col("age") >= start_age) & (pl.col("age") <= end_age)
        )
        sum_weighted = float(sub["weighted_risk_rate"].sum()) if sub.height else 0.0
        sum_dist = float(sub["dist_weight"].sum()) if sub.height else 0.0

        if sum_dist > 0:
            band_rate = (sum_weighted / sum_dist) * cover / 1000.0 / 12.0
        else:
            band_rate = 0.0

        results.append({
            "band_index": i,
            "start_age": start_age,
            "end_age": end_age,
            "cover": cover,
            "risk_premium_per_life_pm": band_rate,
        })
    return results


def build_ext_fc_rate_card(scheme, ctr, basis_tables: dict) -> pl.DataFrame:
    active_cats = ctr.active_categories.get("ext_fc", ())
    if not active_cats:
        return pl.DataFrame(schema={
            "category_number": pl.Int64, "relative_type": pl.Utf8,
            "band_index": pl.Int64, "start_age": pl.Float64, "end_age": pl.Float64,
            "cover": pl.Float64, "risk_premium_per_life_pm": pl.Float64,
        })

    ext_fc_extended_family_selected = getattr(scheme, "ext_fc_extended_family_selected", {}) or {}
    ext_fc_parents_selected = getattr(scheme, "ext_fc_parents_selected", {}) or {}
    ext_fc_pct_male = getattr(scheme, "ext_fc_pct_male", {}) or {}
    ext_fc_avg_salary = getattr(scheme, "ext_fc_avg_salary", {}) or {}
    ext_fc_parent_dist_available = getattr(scheme, "ext_fc_parent_dist_available", {}) or {}
    ext_fc_family_dist_available = getattr(scheme, "ext_fc_family_dist_available", {}) or {}
    ext_fc_age_bands = getattr(scheme, "ext_fc_age_bands", {}) or {}

    ext_fc_members = ctr.members.get("ext_fc_sheet")
    live_dist_by_cat: dict[int, dict[str, dict[int, float]]] = {}
    if ext_fc_members is not None and ext_fc_members.height > 0:
        pass

    rows = []
    for cat_num in active_cats:
        pct_male = ext_fc_pct_male.get(cat_num, 0.0)
        avg_salary = ext_fc_avg_salary.get(cat_num, 0.0)
        bands_for_cat = ext_fc_age_bands.get(cat_num, {})

        relative_type_flags = {
            "parents": (ext_fc_parents_selected.get(cat_num, "No") or "No").strip().lower() == "yes",
            "extended_family": (ext_fc_extended_family_selected.get(cat_num, "No") or "No").strip().lower() == "yes",
        }
        dist_available_flags = {
            "parents": (ext_fc_parent_dist_available.get(cat_num, "No") or "No").strip().lower() == "yes",
            "extended_family": (ext_fc_family_dist_available.get(cat_num, "No") or "No").strip().lower() == "yes",
        }

        for relative_type in ("parents", "extended_family"):
            if not relative_type_flags[relative_type]:
                continue
            bands = bands_for_cat.get(relative_type, [])
            if not bands:
                continue

            dist_available = dist_available_flags[relative_type]
            live_dist = (live_dist_by_cat.get(cat_num, {}) or {}).get(relative_type)

            age_rate_table = _build_ext_fc_age_rate_table(
                category_number=cat_num,
                relative_type=relative_type,
                pct_male=pct_male,
                avg_salary=avg_salary,
                dist_available=dist_available,
                live_census_dist=live_dist,
                basis_tables=basis_tables,
            )
            band_results = _aggregate_ext_fc_age_bands(age_rate_table, bands)
            for br in band_results:
                rows.append({
                    "category_number": int(cat_num),
                    "relative_type": relative_type,
                    **br,
                })

    if not rows:
        return pl.DataFrame(schema={
            "category_number": pl.Int64, "relative_type": pl.Utf8,
            "band_index": pl.Int64, "start_age": pl.Float64, "end_age": pl.Float64,
            "cover": pl.Float64, "risk_premium_per_life_pm": pl.Float64,
        })

    return pl.DataFrame(rows)


def apply_post_rate_adjust(df, adj, assump_tables, basis_tables=None, ctx=None):
    kind = adj["kind"]

    if kind == "db_definition_multiplicative":
        feats = assump_tables.get("lsdb_features")
        if feats is None:
            return df
        db_defs = feats.filter(pl.col("feature") == "db_definitions")\
                       .filter(pl.col("cost_basis") == "basic")\
                       .select(["key", "value"])\
                       .with_columns(pl.col("value").cast(pl.Float64, strict=False))
        df = df.with_columns(
            pl.col("definition_of_disability").cast(pl.Utf8)
              .str.to_lowercase()
              .str.replace_all(" ", "_")
              .str.replace_all("[^a-z_]", "")
              .alias("__db_key")
        )
        df = df.join(
            db_defs.rename({"key": "__db_key", "value": "__db_factor"}),
            on="__db_key", how="left"
        )
        df = df.with_columns(
            (pl.col("basic_technical_premium") *
             (1 + pl.col("__db_factor").fill_null(0.0))
            ).alias("basic_technical_premium")
        ).drop(["__db_key", "__db_factor"])
        return df

    if kind == "spouse_factor_multiplicative":
        sf = assump_tables.get("spouse_factors")
        if sf is None:
            return df
        rows = sf.filter(
            (pl.col("factor_type") == "basic_spouses_factor") &
            (pl.col("cost_basis") == "basic")
        ).select(["gender", "value"])\
         .with_columns(pl.col("value").cast(pl.Float64, strict=False))

        m_val = 0.0
        f_val = 0.0
        m_row = rows.filter(pl.col("gender") == "M")
        f_row = rows.filter(pl.col("gender") == "F")
        if m_row.height: m_val = float(m_row["value"][0] or 0.0)
        if f_row.height: f_val = float(f_row["value"][0] or 0.0)

        df = df.with_columns(
            pl.when(pl.col("gender") == "M").then(pl.lit(m_val))
              .when(pl.col("gender") == "F").then(pl.lit(f_val))
              .otherwise(0.0)
              .alias("__spouse_factor")
        )
        df = df.with_columns(
            (pl.col("basic_technical_premium") *
             (1 + pl.col("__spouse_factor"))
            ).alias("basic_technical_premium")
        ).drop("__spouse_factor")
        return df

    if kind == "spouse_db_definition_multiplicative":
        sf = assump_tables.get("spouse_factors")
        if sf is None:
            return df
        row = sf.filter(
            (pl.col("factor_type") == "db_definition_factor") &
            (pl.col("cost_basis") == "basic")
        )
        val = 0.0
        if row.height:
            v = row["value"][0]
            if v is not None:
                val = float(v)
        return df.with_columns(
            (pl.col("basic_technical_premium") * (1 + pl.lit(val)))
                .alias("basic_technical_premium")
        )

    if kind == "fc_demographics_join":
        return _fc_demographics_join(df, basis_tables)

    if kind == "fc_family_composition_join":
        return _fc_family_composition_join(df, basis_tables, ctx)

    if kind == "fc_second_spouse_loading":
        return _fc_second_spouse_loading(df, assump_tables, ctx)

    if kind == "fc_db_loading":
        return _fc_db_loading(df, adj, assump_tables, ctx)

    raise ValueError(f"Unknown post_rate_adjust: {kind}")
