from __future__ import annotations
import math
from datetime import date
from typing import Any
import polars as pl


def _tt_active(df: pl.DataFrame) -> pl.DataFrame:
    return df.filter(
        pl.col("product_active")
        & (pl.col("age") <= pl.col("ceasing_age"))
    )


def _over_nra_count(df: pl.DataFrame) -> int:
    return df.filter(
        pl.col("product_active")
        & (pl.col("age") > pl.col("ceasing_age"))
    ).height


_DB_RELEVANT_PRODUCTS = frozenset({
    "gla", "lsdb", "ci",
    "accident_death", "accident_disability", "dismemberment",
})


def _disabled_count(df: pl.DataFrame, product_key: str) -> int:
    if product_key not in _DB_RELEVANT_PRODUCTS:
        return 0
    return df.filter(pl.col("status") == "DB").height


_EOH_PRODUCTS = frozenset({"gla", "lsdb", "ci"})


def _eoh_ladder(n_members: int, total_cover: float,
                base: float, layer1: float, layer2: float,
                perc1: float, perc2: float, perc3: float,
                layer1_cap: float | None = None,
                layer2_cap: float | None = None) -> float:
    if n_members <= 10:
        return 0.0

    if n_members > 200:
        return 0.75 * math.sqrt(n_members) * total_cover / n_members

    if total_cover >= layer1:
        l1 = layer1 * perc1
    else:
        l1 = total_cover * perc1
    if layer1_cap is not None:
        l1 = min(l1, layer1_cap)

    if total_cover >= layer1 + layer2:
        l2 = layer2 * perc2
    else:
        l2 = max(0.0, total_cover - layer1) * perc2
    if layer2_cap is not None:
        l2 = min(l2, layer2_cap)

    l3 = max(0.0, total_cover - layer1 - layer2) * perc3

    return base + l1 + l2 + l3


def _get_limit(assump, product: str, param_name: str) -> float | None:
    plims = assump.tables.get("product_limits")
    if plims is None:
        return None
    row = plims.filter(
        (pl.col("product") == product) & (pl.col("param_name") == param_name)
    )
    if row.height == 0:
        return None
    raw = row["param_value"][0]
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _resolve_ci_variant_type(ctr, assump) -> str:
    ci_cats = ctr.categories.filter(
        (pl.col("product") == "ci") & (pl.col("field") == "product_option")
    )
    if ci_cats.height == 0:
        return "New"
    product_option = str(ci_cats["value"][0])

    mapping = assump.tables.get("ci_table_mapping")
    if mapping is None:
        return "New"
    row = mapping.filter(pl.col("product_option") == product_option)
    if row.height == 0:
        return "New"
    return str(row["variant_type"][0])


_EOH_REQUIRED_COLS = {"product_active", "age", "ceasing_age", "total_cover"}


def _eoh_agg_active(df):
    if df is None or df.height == 0 or not _EOH_REQUIRED_COLS.issubset(set(df.columns)):
        return {"n": 0, "total": 0.0, "highest": 0.0}
    active = df.filter(
        pl.col("product_active") & (pl.col("age") <= pl.col("ceasing_age"))
    )
    return {
        "n":       active.height,
        "total":   float(active["total_cover"].sum()),
        "highest": float(active["total_cover"].max()) if active.height else 0.0,
    }


def _eoh_get_result_df(all_results, key):
    df = all_results.get(key)
    return df if df is not None else pl.DataFrame()


def _compute_eoh_constants(assump):
    g = assump.globals
    C = {
        "gla_amount":     g["eoh_amount"],
        "gla_layer1":     g["eoh_layer1"],
        "gla_layer2":     g["eoh_layer2"],
        "gla_perc1":      g["eoh_perc1"],
        "gla_perc2":      g["eoh_perc2"],
        "gla_perc3":      g["eoh_perc3"],
        "gla_layer1_cap": g["eoh_layer1_cap"],
        "gla_layer2_cap": g["eoh_layer2_cap"],

        "ci_amount":      g["eoh_amount_ci"],
        "ci_layer1":      g["eoh_layer1_ci"],
        "ci_layer2":      g["eoh_layer2_ci"],
        "ci_perc1":       g["eoh_perc1_ci"],
        "ci_perc2":       g["eoh_perc2_ci"],
        "ci_perc3":       g["eoh_perc3_ci"],
        "ci_layer1_cap":  g["eoh_layer1_cap_ci"],
        "ci_layer2_cap":  g["eoh_layer2_cap_ci"],
    }
    return C, g


def _compute_gla_eoh(all_results, C):
    gla = _eoh_agg_active(_eoh_get_result_df(all_results, "gla"))
    if gla["n"] == 0:
        return {"tech_eoh": None, "custom_eoh": None, "eoh_limit": None}
    highest_x_12 = 0.0 if gla["n"] < 10 else 1.2 * gla["highest"]
    tech_gla = _eoh_ladder(
        n_members=gla["n"], total_cover=gla["total"],
        base=C["gla_amount"], layer1=C["gla_layer1"], layer2=C["gla_layer2"],
        perc1=C["gla_perc1"], perc2=C["gla_perc2"], perc3=C["gla_perc3"],
        layer1_cap=C["gla_layer1_cap"],
        layer2_cap=C["gla_layer2_cap"],
    )
    tolerance_gla = 0.0
    final_gla = min(highest_x_12, tech_gla + tolerance_gla) if tech_gla else 0.0
    return {
        "tech_eoh":   final_gla,
        "custom_eoh": final_gla,
        "eoh_limit":  final_gla,
    }


def _compute_lsdb_eoh(all_results, C, lsdb_rating, lsdb_max_cover):
    if lsdb_rating != "Standalone":
        return {"tech_eoh": None, "custom_eoh": None, "eoh_limit": None}
    lsdb = _eoh_agg_active(_eoh_get_result_df(all_results, "lsdb"))
    if lsdb["n"] == 0:
        return {"tech_eoh": None, "custom_eoh": None, "eoh_limit": None}
    highest_x_12_lsdb = 0.0 if lsdb["n"] < 10 else 1.2 * lsdb["highest"]
    tech_lsdb_ladder = _eoh_ladder(
        n_members=lsdb["n"], total_cover=lsdb["total"],
        base=C["gla_amount"], layer1=C["gla_layer1"], layer2=C["gla_layer2"],
        perc1=C["gla_perc1"], perc2=C["gla_perc2"], perc3=C["gla_perc3"],
        layer1_cap=C["gla_layer1_cap"],
        layer2_cap=C["gla_layer2_cap"],
    )
    if lsdb_max_cover and tech_lsdb_ladder / lsdb_max_cover >= 0.95:
        tolerance_lsdb = max(0.0, lsdb_max_cover - tech_lsdb_ladder)
    else:
        tolerance_lsdb = 0.0
    eoh_lsdb_formula = min(highest_x_12_lsdb, tech_lsdb_ladder + tolerance_lsdb)
    final_lsdb = min(eoh_lsdb_formula, lsdb_max_cover) if lsdb_max_cover else eoh_lsdb_formula
    return {
        "tech_eoh":   final_lsdb,
        "custom_eoh": final_lsdb,
        "eoh_limit":  final_lsdb,
    }


def _compute_ci_eoh(all_results, C, ci_rating, ci_f323):
    if ci_rating != "Standalone":
        return {"tech_eoh": None, "custom_eoh": None, "eoh_limit": None}
    ci = _eoh_agg_active(_eoh_get_result_df(all_results, "ci"))
    if ci["n"] == 0:
        return {"tech_eoh": None, "custom_eoh": None, "eoh_limit": None}
    highest_x_12_ci = 0.0 if ci["n"] < 10 else 1.2 * ci["highest"]
    if ci["n"] <= 10:
        tech_ci_ladder = 0.0
    else:
        if ci["total"] <= C["ci_layer1"]:
            l1 = ci["total"] * C["ci_perc1"]
        else:
            l1 = C["ci_layer1"] * C["ci_perc1"]
        if C["ci_layer1_cap"] is not None:
            l1 = min(l1, C["ci_layer1_cap"])

        if ci["total"] >= C["ci_layer1"] + C["ci_layer2"]:
            l2 = C["ci_layer2"] * C["ci_perc2"]
        else:
            l2 = max(0.0, ci["total"] - C["ci_layer1"]) * C["ci_perc2"]
        if C["ci_layer2_cap"] is not None:
            l2 = min(l2, C["ci_layer2_cap"])

        l3 = max(0.0, (ci["total"] - C["ci_layer1"] - C["ci_layer2"]) * C["ci_perc3"])

        tech_ci_ladder = C["ci_amount"] + l1 + l2 + l3

    tolerance_ci = 0.0
    candidates = [highest_x_12_ci, tech_ci_ladder + tolerance_ci]
    if ci_f323 is not None:
        candidates.append(ci_f323)
    final_ci = min(candidates) if tech_ci_ladder else 0.0
    return {
        "tech_eoh":   final_ci,
        "custom_eoh": final_ci,
        "eoh_limit":  final_ci,
    }


def _compute_gip_eoh(all_results, g):
    gip_df = all_results.get("gip")
    if gip_df is None or gip_df.height == 0 or "total_cover" not in gip_df.columns:
        return {"tech_eoh": None, "custom_eoh": None, "eoh_limit": None,
                "members_over": 0, "cover_over": 0.0, "max_cover": 0.0}
    gip_member_count = gip_df.height
    gip_active = gip_df.filter(pl.col("product_active")) if "product_active" in gip_df.columns else gip_df
    gip_total_cover_annual = float(gip_active["total_cover"].sum()) * 12.0
    gip_max_cover = float(gip_df["total_cover"].max())
    eoh_max_gip = g.get("eoh_max_gip", 145000.0)

    if gip_member_count >= 10:
        gip_term = (0.4 * (gip_member_count ** 0.5) * gip_total_cover_annual
                    / gip_member_count) / 12.0
        gip_tail = min(0.1 * gip_term, 5000.0)
        gip_calc1 = gip_term + gip_tail
    else:
        gip_calc1 = 0.0
    gip_calc2 = min(eoh_max_gip, gip_max_cover * 1.2)
    gip_eoh_limit = min(gip_calc1, gip_calc2)

    gip_over = (gip_df["total_cover"] - gip_eoh_limit).clip(lower_bound=0.0)
    gip_members_over = int((gip_over > 0).sum())
    gip_cover_over = float(gip_over.sum())

    return {
        "tech_eoh":     gip_eoh_limit,
        "custom_eoh":   gip_eoh_limit,
        "eoh_limit":    gip_eoh_limit,
        "members_over": gip_members_over,
        "cover_over":   gip_cover_over,
        "max_cover":    gip_max_cover,
    }


def _compute_tip_eoh(all_results, g):
    tip_df = all_results.get("tip")
    if tip_df is None or tip_df.height == 0 or "total_cover" not in tip_df.columns:
        return {"tech_eoh": None, "custom_eoh": None, "eoh_limit": None,
                "members_over": 0, "cover_over": 0.0, "max_cover": 0.0}
    tip_member_count = tip_df.height
    tip_active = tip_df.filter(pl.col("product_active")) if "product_active" in tip_df.columns else tip_df
    tip_total_cover_annual = float(tip_active["total_cover"].sum()) * 12.0
    tip_max_cover = float(tip_df["total_cover"].max())
    eoh_max_tip = g.get("eoh_max_tip", 145000.0)

    if tip_member_count >= 10:
        tip_term = (0.4 * (tip_member_count ** 0.5) * tip_total_cover_annual
                    / tip_member_count) / 12.0
        tip_tail = min(0.1 * tip_term, 5000.0)
        tip_calc1 = tip_term + tip_tail
    else:
        tip_calc1 = 0.0
    tip_calc2 = min(eoh_max_tip, tip_max_cover * 1.2)
    tip_eoh_limit = min(tip_calc1, tip_calc2)

    tip_over = (tip_df["total_cover"] - tip_eoh_limit).clip(lower_bound=0.0)
    tip_members_over = int((tip_over > 0).sum())
    tip_cover_over = float(tip_over.sum())

    return {
        "tech_eoh":     tip_eoh_limit,
        "custom_eoh":   tip_eoh_limit,
        "eoh_limit":    tip_eoh_limit,
        "members_over": tip_members_over,
        "cover_over":   tip_cover_over,
        "max_cover":    tip_max_cover,
    }


def _fill_remaining_none_products(result):
    for pkey in ("spouse_gla", "spouse_lsdb", "terminal",
                 "accident_death", "accident_disability", "dismemberment"):
        result[pkey] = {"tech_eoh": None, "custom_eoh": None, "eoh_limit": None}
    return result


def compute_eoh_for_scheme(all_results: dict[str, pl.DataFrame],
                           ctr, assump) -> dict[str, dict]:
    C, g = _compute_eoh_constants(assump)

    lsdb_max_cover = _get_limit(assump, "lsdb", "max_cover")
    ci_variant_type = _resolve_ci_variant_type(ctr, assump)
    ci_f323 = _get_limit(assump, f"ci_{ci_variant_type.lower()}", "max_cover")

    lsdb_rating = (ctr.scheme.rating or {}).get("lsdb", "")
    ci_rating = (ctr.scheme.rating or {}).get("ci", "")

    result: dict[str, dict] = {}

    result["gla"] = _compute_gla_eoh(all_results, C)
    result["lsdb"] = _compute_lsdb_eoh(all_results, C, lsdb_rating, lsdb_max_cover)
    result["ci"] = _compute_ci_eoh(all_results, C, ci_rating, ci_f323)
    result["gip"] = _compute_gip_eoh(all_results, g)
    result["tip"] = _compute_tip_eoh(all_results, g)

    result = _fill_remaining_none_products(result)

    return result


PRODUCT_COLUMNS = [
    ("gla",                 "GLA"),
    ("lsdb",                "LSDB Accelerator"),
    ("ci",                  "CI Accelerator"),
    ("spouse_gla",          "Spouses - GLA"),
    ("spouse_lsdb",         "Spouses - LSDB"),
    ("terminal",            "Terminal Illness Cover"),
    ("accident_death",      "Accidental Death"),
    ("accident_disability", "Accidental Disability"),
    ("dismemberment",       "Dismemberment"),
    ("sc_income",           "S&C Income Cover"),
    ("education",           "Educate-a-Child"),
]

ENGINE_PRODUCTS = {
    "gla", "lsdb", "ci",
    "spouse_gla", "spouse_lsdb",
    "terminal",
    "accident_death", "accident_disability", "dismemberment",
}


def product_aggregates(df: pl.DataFrame, scheme_params: dict,
                       product_key: str = "") -> dict[str, Any]:
    _active_all = _tt_active(df)
    active = (_active_all.filter(pl.col("total_cover") > 0)
              if product_key == "lsdb" else _active_all)

    n_members = active.height
    n_over_nra = _over_nra_count(df)
    n_disabled = _disabled_count(df, product_key)
    avg_age = float(_active_all["age"].mean()) if _active_all.height > 0 else 0.0
    n_males = active.filter(pl.col("gender") == "M").height
    pct_males = (n_males / n_members) if n_members > 0 else 0.0
    salary_bill = float(active["annual_salary"].sum())
    avg_salary = (salary_bill / n_members) if n_members > 0 else 0.0
    aw_salary = (float((_active_all["age"] * _active_all["annual_salary"]).sum() / salary_bill)
                 if salary_bill > 0 else 0.0)
    total_cover = float(active["total_cover"].sum())
    avg_multiple = (total_cover / salary_bill) if salary_bill > 0 else 0.0
    avg_cover = (total_cover / n_members) if n_members > 0 else 0.0
    aw_cover = (float((active["age"] * active["total_cover"]).sum() / total_cover)
                if total_cover > 0 else 0.0)

    qx_weighted_sum = float(
        (active["basic_technical_premium"] + active["aids_technical_premium"]).sum()
    )
    basis_key = _basis_key_for_product(df, scheme_params)
    basis_multiplier = scheme_params.get("basis", {}).get(basis_key, 1.0)
    incidence_rate = (qx_weighted_sum / n_members * 1000 * basis_multiplier
                      if n_members > 0 else 0.0)
    expected_claims = incidence_rate / 1000 * n_members

    qx_sum = float((active["basic_technical_premium"] / active["total_cover"].clip(1)
                    + active["aids_technical_premium"] / active["total_cover"].clip(1)).sum())
    expected_claim_size = (qx_weighted_sum / qx_sum) if qx_sum > 0 else 0.0
    ave_claim_over_ave_cover = (expected_claim_size / avg_cover) if avg_cover > 0 else 0.0

    return {
        "n_members":                n_members,
        "n_over_nra":               n_over_nra,
        "n_disabled":               n_disabled,
        "avg_age":                  avg_age,
        "pct_males":                pct_males,
        "salary_bill":              salary_bill,
        "avg_salary":               avg_salary,
        "age_weighted_by_salary":   aw_salary,
        "total_cover":              total_cover,
        "avg_multiple":             avg_multiple,
        "avg_cover":                avg_cover,
        "age_weighted_by_cover":    aw_cover,
        "incidence_rate":           incidence_rate,
        "expected_claims":          expected_claims,
        "expected_claim_size":      expected_claim_size,
        "ave_claim_over_ave_cover": ave_claim_over_ave_cover,
    }


def _basis_key_for_product(df: pl.DataFrame, scheme_params: dict) -> str:
    return "gla"


def compute_eoh_row(product_key: str,
                    aggregates: dict,
                    df: pl.DataFrame,
                    scheme_params: dict,
                    eoh_by_product: dict[str, dict] | None = None) -> dict[str, Any]:
    active = _tt_active(df)
    max_cover = _max_cover(active)

    eoh_vals = (eoh_by_product or {}).get(product_key, {})
    tech_eoh = eoh_vals.get("tech_eoh")
    custom_eoh = eoh_vals.get("custom_eoh")
    eoh_limit = eoh_vals.get("eoh_limit")

    if product_key not in _EOH_PRODUCTS or eoh_limit is None:
        return {
            "tech_eoh":         None,
            "custom_eoh":       None,
            "eoh_limit":        None,
            "members_over_eoh": None,
            "cover_over_eoh":   None,
            "max_cover":        max_cover,
        }

    limit_f = float(eoh_limit)
    over = active.filter(pl.col("total_cover") > limit_f)
    members_over_eoh = over.height
    cover_over_eoh = (float((over["total_cover"] - limit_f).sum())
                     if over.height else 0.0)

    return {
        "tech_eoh":         tech_eoh,
        "custom_eoh":       custom_eoh,
        "eoh_limit":        eoh_limit,
        "members_over_eoh": members_over_eoh,
        "cover_over_eoh":   cover_over_eoh,
        "max_cover":        max_cover,
    }


def _max_cover(active: pl.DataFrame) -> float:
    if active.height == 0:
        return 0.0
    return float(active["total_cover"].max())


def build_membership_block(all_results: dict[str, pl.DataFrame],
                           scheme_params: dict,
                           ctr=None, assump=None) -> pl.DataFrame:
    PRODUCT_TO_BASIS = {
        "gla":                 "gla",
        "lsdb":                "lsdb",
        "ci":                  "ci",
        "spouse_gla":          "spouse_gla",
        "spouse_lsdb":         "spouse_lsdb",
        "terminal":            "ti",
        "accident_death":      "accdth",
        "accident_disability": "accdb",
        "dismemberment":       "dismem",
        "sc_income":           "sc_income",
        "education":           "education",
    }

    product_labels = _resolve_product_labels(scheme_params)

    if ctr is not None and assump is not None:
        eoh_by_product = compute_eoh_for_scheme(all_results, ctr, assump)
    else:
        eoh_by_product = None

    col_data = {}
    for pkey, default_label in PRODUCT_COLUMNS:
        label = product_labels.get(pkey, default_label)
        df = all_results.get(pkey)
        if df is None:
            col_data[label] = _empty_column()
            col_data[label]["_pkey"] = pkey
            continue

        basis_key = PRODUCT_TO_BASIS[pkey]
        aggs = _compute_aggregates_for_product(df, scheme_params, basis_key, pkey)
        eoh = compute_eoh_row(pkey, aggs, df, scheme_params, eoh_by_product)

        col = {**aggs, **eoh, "_pkey": pkey}
        col_data[label] = col

    METRIC_ROWS = [
        ("Membership",                     "n_members",                "int"),
        ("-Over NRA",                      "n_over_nra",               "int"),
        ("-Disabled",                      "n_disabled",               "int"),
        ("Average Age",                    "avg_age",                  "float2"),
        ("% Males",                        "pct_males",                "pct2"),
        ("",                               None,                       None),
        ("Salary Bill",                    "salary_bill",              "money"),
        ("Average Salary",                 "avg_salary",               "money"),
        ("Age Weighted by Salary",         "age_weighted_by_salary",   "float2"),
        ("",                               None,                       None),
        ("Total Cover",                    "total_cover",              "money"),
        ("Average Multiple",               "avg_multiple",             "float2"),
        ("Average Cover",                  "avg_cover",                "money"),
        ("Age Weighted by Cover",          "age_weighted_by_cover",    "float2"),
        ("",                               None,                       None),
        ("Incidence Rate",                 "incidence_rate",           "float2"),
        ("Expected # Claims",              "expected_claims",          "float2"),
        ("Average Cover",                  "avg_cover",                "money"),
        ("Expected Claim Size",            "expected_claim_size",      "money"),
        ("Ave Claim/Ave Cover",            "ave_claim_over_ave_cover", "pct2"),
        ("",                               None,                       None),
        ("Tech EOH",                       "tech_eoh",                 "money_or_na"),
        ("Enter Custom EOH",               "custom_eoh",               "money_or_na"),
        ("EOH Limit",                      "eoh_limit",                "money_or_na"),
        ("Members over EOH",               "members_over_eoh",         "int_or_na"),
        ("Cover over EOH",                 "cover_over_eoh",           "money_or_na"),
        ("Max Cover",                      "max_cover",                "money"),
    ]

    rows = []
    for metric_label, metric_key, fmt in METRIC_ROWS:
        row = {"Metric": metric_label}
        for pkey, default_label in PRODUCT_COLUMNS:
            label = product_labels.get(pkey, default_label)
            if metric_key is None:
                row[label] = ""
            else:
                val = col_data[label].get(metric_key)
                row[label] = _format_val(val, fmt)
        rows.append(row)

    return pl.DataFrame(rows)


def _compute_aggregates_for_product(df: pl.DataFrame,
                                    scheme_params: dict,
                                    basis_key: str,
                                    product_key: str) -> dict:
    _active_all = _tt_active(df)
    active = (_active_all.filter(pl.col("total_cover") > 0)
              if product_key == "lsdb" else _active_all)
    n_members = active.height
    n_over_nra = _over_nra_count(df)
    n_disabled = _disabled_count(df, product_key)
    avg_age = float(_active_all["age"].mean()) if _active_all.height > 0 else 0.0
    n_males = active.filter(pl.col("gender") == "M").height
    pct_males = (n_males / n_members) if n_members > 0 else 0.0
    salary_bill = float(active["annual_salary"].sum())
    avg_salary = (salary_bill / n_members) if n_members > 0 else 0.0
    aw_salary = (float((_active_all["age"] * _active_all["annual_salary"]).sum() / salary_bill)
                 if salary_bill > 0 else 0.0)
    total_cover = float(active["total_cover"].sum())
    avg_multiple = (total_cover / salary_bill) if salary_bill > 0 else 0.0
    avg_cover = (total_cover / n_members) if n_members > 0 else 0.0
    aw_cover = (float((active["age"] * active["total_cover"]).sum() / total_cover)
                if total_cover > 0 else 0.0)

    basis_multiplier = scheme_params.get("basis", {}).get(basis_key, 1.0)

    if n_members > 0:
        active_with_prob = active.with_columns(
            pl.when(pl.col("total_cover") > 0)
              .then((pl.col("basic_technical_premium") + pl.col("aids_technical_premium"))
                    / pl.col("total_cover"))
              .otherwise(0.0)
              .alias("__prob_claim")
        )
        expected_claims = float(active_with_prob["__prob_claim"].sum()) * basis_multiplier
        incidence_rate = expected_claims / n_members * 1000
    else:
        expected_claims = 0.0
        incidence_rate = 0.0

    total_expected_payout = float(
        (active["basic_technical_premium"] + active["aids_technical_premium"]).sum()
    )
    prob_sum = expected_claims / basis_multiplier if basis_multiplier > 0 else 0.0
    expected_claim_size = (total_expected_payout / prob_sum) if prob_sum > 0 else 0.0
    ave_claim_over_ave_cover = (expected_claim_size / avg_cover) if avg_cover > 0 else 0.0

    return {
        "n_members": n_members,
        "n_over_nra": n_over_nra,
        "n_disabled": n_disabled,
        "avg_age": avg_age,
        "pct_males": pct_males,
        "salary_bill": salary_bill,
        "avg_salary": avg_salary,
        "age_weighted_by_salary": aw_salary,
        "total_cover": total_cover,
        "avg_multiple": avg_multiple,
        "avg_cover": avg_cover,
        "age_weighted_by_cover": aw_cover,
        "incidence_rate": incidence_rate,
        "expected_claims": expected_claims,
        "expected_claim_size": expected_claim_size,
        "ave_claim_over_ave_cover": ave_claim_over_ave_cover,
    }


def _empty_column() -> dict:
    return {
        "n_members": 0, "n_over_nra": 0, "n_disabled": 0,
        "avg_age": 0.0, "pct_males": 0.0,
        "salary_bill": 0.0, "avg_salary": 0.0, "age_weighted_by_salary": 0.0,
        "total_cover": 0.0, "avg_multiple": 0.0, "avg_cover": 0.0, "age_weighted_by_cover": 0.0,
        "incidence_rate": 0.0, "expected_claims": 0.0,
        "expected_claim_size": 0.0, "ave_claim_over_ave_cover": 0.0,
        "tech_eoh": None, "custom_eoh": None, "eoh_limit": None,
        "members_over_eoh": None, "cover_over_eoh": None, "max_cover": 0.0,
    }


def _resolve_product_labels(scheme_params: dict) -> dict[str, str]:
    lsdb_variant = scheme_params.get("lsdb_product", "Accelerator")
    ci_variant = scheme_params.get("ci_product", "Accelerator")
    return {
        "gla":                 "GLA",
        "lsdb":                f"LSDB {lsdb_variant}",
        "ci":                  f"CI {ci_variant}",
        "spouse_gla":          "Spouses - GLA",
        "spouse_lsdb":         "Spouses - LSDB",
        "terminal":            "Terminal Illness Cover",
        "accident_death":      "Accidental Death",
        "accident_disability": "Accidental Disability",
        "dismemberment":       "Dismemberment",
        "sc_income":           "S&C Income Cover",
        "education":           "Educate-a-Child",
    }


def build_normal_margins_block(all_results: dict[str, pl.DataFrame],
                               scheme_params: dict) -> pl.DataFrame:
    product_labels = _resolve_product_labels(scheme_params)

    PKEY_TO_PARAM = {
        "gla": "gla", "lsdb": "lsdb", "ci": "ci",
        "spouse_gla": "spouse_gla", "spouse_lsdb": "spouse_lsdb",
        "terminal": "ti",
        "accident_death": "accdth", "accident_disability": "accdb",
        "dismemberment": "dismem",
        "sc_income": "sc_income", "education": "education",
    }

    profit_margins = scheme_params.get("profit_margin", {})
    admin_fees = scheme_params.get("admin_fee", {})
    bdm = scheme_params.get("business_decision_margin", {})
    phasing = scheme_params.get("phasing_margin", {})
    reins_profit = scheme_params.get("reinsurance_profit", {})
    reins_cost = scheme_params.get("reinsurance_cost", {})
    rate_basis = scheme_params.get("rate_basis", {})
    prb_active = scheme_params.get("prb_active", {})
    prb_pct = scheme_params.get("prb_percentage", 0.0)

    co_pct = {}
    ctc_pct = {}
    for pkey in PKEY_TO_PARAM.keys():
        df = all_results.get(pkey)
        if df is None:
            co_pct[pkey] = 0.0
            ctc_pct[pkey] = 0.0
            continue
        active = _tt_active(df)
        net = float(active["basic_technical_premium"].sum()
                    + active["aids_technical_premium"].sum())
        co = float(active["co_premium"].sum()) if "co_premium" in active.columns else 0.0
        ctc = float(active["ctc_premium"].sum()) if "ctc_premium" in active.columns else 0.0
        co_pct[pkey] = (co / net) if net > 0 else 0.0
        ctc_pct[pkey] = (ctc / net) if net > 0 else 0.0

    def _prb_row(pkey):
        return prb_pct if prb_active.get(pkey, False) else 0.0

    def _pct_str(v):
        return f"{v * 100:.2f}%"

    rows = []
    ROW_DEFS = [
        ("Conversion Option",                 lambda pkey: co_pct.get(pkey, 0.0)),
        ("Cover To Continue",                 lambda pkey: ctc_pct.get(pkey, 0.0)),
        ("Profit Refund Option Loading",      lambda pkey: _prb_row(pkey)),
        ("Profit Margin",                     lambda pkey: profit_margins.get(PKEY_TO_PARAM[pkey], 0.0)),
        ("Admin",                             lambda pkey: admin_fees.get(PKEY_TO_PARAM[pkey], 0.0)),
        ("Business Decision Margin",          lambda pkey: bdm.get(PKEY_TO_PARAM[pkey], 0.0)),
        ("Target Unchanged/Phasing Margin",   lambda pkey: phasing.get(PKEY_TO_PARAM[pkey], 0.0)),
        ("Reinsurance Profit",                lambda pkey: reins_profit.get(PKEY_TO_PARAM[pkey], 0.0)),
        ("Reinsurance Arbitrage/Cost Margin", lambda pkey: reins_cost.get(PKEY_TO_PARAM[pkey], 0.0)),
        ("Rate Basis",                        lambda pkey: rate_basis.get(PKEY_TO_PARAM[pkey], 1.0)),
    ]
    for label, fn in ROW_DEFS:
        row = {"Metric": label}
        for pkey, default_label in PRODUCT_COLUMNS:
            display_label = product_labels.get(pkey, default_label)
            row[display_label] = _pct_str(fn(pkey))
        rows.append(row)

    return pl.DataFrame(rows)


def _format_val(val, fmt) -> str:
    if val is None:
        return "N/A"
    if fmt == "int":
        return f"{int(val):,}"
    if fmt == "int_or_na":
        return f"{int(val):,}" if val is not None else "N/A"
    if fmt == "float2":
        return f"{val:,.2f}"
    if fmt == "money":
        return f"{val:,.2f}"
    if fmt == "money_or_na":
        return f"{val:,.2f}" if val is not None else "N/A"
    if fmt == "pct2":
        return f"{val*100:.2f}%"
    return str(val)
