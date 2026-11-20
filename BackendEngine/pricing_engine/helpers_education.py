from __future__ import annotations

import polars as pl

__all__ = [
    "_apply_educate_a_child_composite",
    "_apply_education_guardian_composite",
    "_edu_guardian_salary_tier",
]


def _apply_education_guardian_composite(df: pl.DataFrame,
                                         spec: dict,
                                         basis_tables: dict[str, pl.DataFrame],
                                         assump_tables: dict | None,
                                         ctx) -> pl.DataFrame:

    male_offset = 0.0
    if assump_tables is not None and "product_limits" in assump_tables:
        row = assump_tables["product_limits"].filter(
            (pl.col("product") == "education_guardian") &
            (pl.col("param_name") == "male_age_offset")
        )
        if row.height:
            try:
                male_offset = float(row["param_value"][0])
            except (TypeError, ValueError):
                male_offset = 0.0

    sal_bands_df = basis_tables.get("education_guardian_salary_bands") if basis_tables else None
    if sal_bands_df is None or sal_bands_df.height == 0:
        return df.with_columns([
            pl.lit(0.0).alias("total_cover"),
            pl.lit(0.0).alias("basic_technical_premium"),
            pl.lit(0.0).alias("aids_technical_premium"),
        ])
    bands_sorted = sorted(
        zip(sal_bands_df["salary_from"].to_list(), sal_bands_df["sal_tier"].to_list())
    )
    sal_col_map = {1: "sal_1", 2: "sal_2", 3: "sal_3", 4: "sal_4", 5: "sal_5"}

    cover_df = basis_tables.get("education_guardian_cover") if basis_tables else None
    if cover_df is None or cover_df.height == 0:
        return df.with_columns([
            pl.lit(0.0).alias("total_cover"),
            pl.lit(0.0).alias("basic_technical_premium"),
            pl.lit(0.0).alias("aids_technical_premium"),
        ])
    cover_by_age: dict[int, dict[int, float]] = {}
    for row in cover_df.iter_rows(named=True):
        age_key = int(row["age"])
        cover_by_age[age_key] = {
            tier: float(row[col]) for tier, col in sal_col_map.items() if col in row
        }
    grid_ages_sorted = sorted(cover_by_age.keys())
    min_age, _max_age = grid_ages_sorted[0], grid_ages_sorted[-1]

    def _asof_age(target_age: int) -> int | None:
        if target_age < min_age:
            return None
        hit = min_age
        for a in grid_ages_sorted:
            if a <= target_age:
                hit = a
            else:
                break
        return hit

    genders = df["gender"].to_list()
    ages_raw = df["age"].to_list()
    salaries = df["annual_salary"].to_list()

    covers = []
    for gender, age_val, salary in zip(genders, ages_raw, salaries):
        if age_val is None or salary is None:
            covers.append(0.0)
            continue
        offset = male_offset if str(gender).strip().upper() == "M" else 0.0
        lookup_age = int(age_val - offset)
        matched_age = _asof_age(lookup_age)
        if matched_age is None:
            covers.append(0.0)
            continue
        tier = _edu_guardian_salary_tier(float(salary), bands_sorted)
        cover_val = cover_by_age.get(matched_age, {}).get(tier, 0.0)
        covers.append(cover_val)

    df = df.with_columns(pl.Series("__edu_guardian_ag_cover", covers))

    df = df.with_columns(
        pl.when(~pl.col("product_active")).then(0.0)
          .when(pl.col("status") == "P").then(0.0)
          .when(pl.col("age") > pl.col("ceasing_age")).then(0.0)
          .otherwise(pl.col("__edu_guardian_ag_cover"))
          .alias("total_cover")
    )

    df = df.with_columns([
        (pl.col("rate_basic").fill_null(0.0) / 1000.0 * pl.col("total_cover"))
            .alias("basic_technical_premium"),
        (pl.col("rate_aids").fill_null(0.0) / 1000.0 * pl.col("total_cover"))
            .alias("aids_technical_premium"),
    ])

    return df.drop(["__edu_guardian_ag_cover"])


def _apply_educate_a_child_composite(df: pl.DataFrame,
                                      spec: dict,
                                      basis_tables: dict[str, pl.DataFrame],
                                      assump_tables: dict | None,
                                      ctx) -> pl.DataFrame:
    scheme = ctx.scheme if ctx is not None else None
    edu_multiple = getattr(scheme, "education_multiple", {}) or {}
    edu_max_cover = getattr(scheme, "education_max_cover", {}) or {}

    if "category_number" not in df.columns:
        return df.with_columns([
            pl.lit(0.0).alias("total_cover"),
            pl.lit(0.0).alias("basic_technical_premium"),
            pl.lit(0.0).alias("aids_technical_premium"),
        ])

    cat_numbers = df["category_number"].to_list()
    salaries = df["annual_salary"].to_list()

    covers = []
    for cat, salary in zip(cat_numbers, salaries):
        if cat is None or salary is None:
            covers.append(0.0)
            continue
        multiple = float(edu_multiple.get(int(cat), 0.0) or 0.0)
        raw_cover = multiple * float(salary)
        cap = edu_max_cover.get(int(cat))
        if cap is not None:
            raw_cover = min(raw_cover, float(cap))
        covers.append(raw_cover)

    df = df.with_columns(pl.Series("__eac_ah_cover", covers))

    df = df.with_columns(
        pl.when(~pl.col("product_active")).then(0.0)
          .when(pl.col("status") == "P").then(0.0)
          .when(pl.col("age") > pl.col("ceasing_age")).then(0.0)
          .otherwise(pl.col("__eac_ah_cover"))
          .alias("total_cover")
    )

    df = df.with_columns([
        (pl.col("rate_basic").fill_null(0.0) / 1000.0 * pl.col("total_cover"))
            .alias("basic_technical_premium"),
        (pl.col("rate_aids").fill_null(0.0) / 1000.0 * pl.col("total_cover"))
            .alias("aids_technical_premium"),
    ])

    return df.drop(["__eac_ah_cover"])


def _edu_guardian_salary_tier(salary: float, bands: list[tuple[float, int]]) -> int:
    tier = bands[0][1]
    for salary_from, t in bands:
        if salary_from <= salary:
            tier = t
        else:
            break
    return tier
