from __future__ import annotations

import polars as pl

from .helpers_common import (
    _gip_asof,
    _gip_key_part,
    _gip_wp_months,
    _gipf,
    _vec_asof_by_gender,
    _vec_asof_cascade,
)

__all__ = [
    "_apply_tip_composite",
    "_apply_tip_composite_legacy",
    "_tip_annuity_fs",
    "_tip_build_lookup_tables",
    "_tip_compute_member_rows",
    "_tip_esc_str",
    "_tip_finalize_dataframe",
]


def _apply_tip_composite(df: pl.DataFrame,
                          spec: dict,
                          basis_tables: dict,
                          assump_tables: dict,
                          ctx) -> pl.DataFrame:
    LT = _tip_build_lookup_tables(basis_tables, assump_tables, ctx)
    g = LT["g"]
    size_t = LT["size_t"]
    pear = LT["pear"]
    mort_map = LT["mort_map"]
    sb_by_g = LT["sb_by_g"]
    BJ_val = LT["BJ_val"]
    BK_val = LT["BK_val"]
    rr_rows = LT["rr_rows"]
    wp_rows = LT["wp_rows"]
    asb_rows = LT["asb_rows"]
    ip_mm = LT["ip_mm"]
    tip_db = LT["tip_db"]
    age_map = LT["age_map"]
    pp_map = LT["pp_map"]
    ppl_map = LT["ppl_map"]
    esc_map = LT["esc_map"]
    wpann = LT["wpann"]
    bands = LT["bands"]
    rebate = LT["rebate"]
    waiver_incl = LT["waiver_incl"]

    cat_ids = sorted({c for c in df["category_number"].drop_nulls().unique().to_list()})
    cat_rows = []
    for cat in cat_ids:
        cp = g.tip_core_benefit_perc.get(cat, [0, 0, 0, 0])
        cl = g.tip_core_benefit_layer.get(cat, [0, 0, 0, 0])
        wp_str_cat = g.tip_waiting_period.get(cat)
        wpm_raw_cat = _gip_wp_months(wp_str_cat)
        cat_rows.append({
            "category_number": cat,
            "cp0": _gipf(cp[0]), "cp1": _gipf(cp[1]), "cp2": _gipf(cp[2]), "cp3": _gipf(cp[3]),
            "cl0": _gipf(cl[0]), "cl1": _gipf(cl[1]), "cl2": _gipf(cl[2]), "cl3": _gipf(cl[3]),
            "add_p": _gipf(g.tip_core_benefit_perc_additional.get(cat)),
            "add_l": _gipf(g.tip_core_benefit_layer_additional.get(cat)),
            "AB": _gipf(g.tip_core_overall_maximum.get(cat)),
            "emp_w": _gipf(g.tip_employer_waiver.get(cat)),
            "wspec": g.tip_waiver_pct_specified.get(cat, "N/A"),
            "val3_2": waiver_incl.get(cat, "No"),
            "core_def": g.tip_core_disability_definition.get(cat),
            "init_pd": g.tip_initial_period.get(cat),
            "wp_str": wp_str_cat,
            "__wpm_raw": wpm_raw_cat,
            "esc_pct": _gipf(g.tip_escalation_pct.get(cat)),
            "tax_opt": g.tip_tax_options.get(cat),
            "pp_label": g.tip_payment_period.get(cat),
        })
    cat_table = pl.DataFrame(cat_rows) if cat_rows else pl.DataFrame({
        "category_number": [], "cp0": [], "cp1": [], "cp2": [], "cp3": [],
        "cl0": [], "cl1": [], "cl2": [], "cl3": [], "add_p": [], "add_l": [],
        "AB": [], "emp_w": [], "wspec": [], "val3_2": [], "core_def": [],
        "init_pd": [], "wp_str": [], "__wpm_raw": [], "esc_pct": [], "tax_opt": [], "pp_label": [],
    })

    out = df.join(cat_table, on="category_number", how="left")

    out = out.with_columns([
        pl.col("__wpm_raw").fill_null(0.0).cast(pl.Float64).alias("__g_wpm"),
        pl.col("age").cast(pl.Float64).alias("__g_age"),
        pl.when(pl.col("ceasing_age").is_not_null())
          .then(pl.col("ceasing_age"))
          .otherwise(pl.col("nra_final"))
          .cast(pl.Float64)
          .alias("__g_cease"),
    ])
    out = out.with_columns(
        (pl.col("__g_age") > pl.col("__g_cease")).alias("__g_over_cease")
    )
    out = out.with_columns(
        (
            pl.col("category_number").is_not_null()
            & pl.col("product_active")
            & (pl.col("__g_age") >= 16)
            & (pl.col("__g_age") <= pl.col("__g_cease"))
            & ((pl.col("__g_cease") - pl.col("__g_age")) * 12.0 >= pl.col("__g_wpm"))
        ).alias("__included")
    )
    out = out.with_columns(
        (pl.col("category_number").is_null() | ~pl.col("product_active")).alias("__zero_branch")
    )
    out = out.with_columns(
        (~pl.col("__zero_branch") & ~pl.col("__included")).alias("__tip_excluded_flag")
    )
    out = out.with_columns(
        pl.when(pl.col("__zero_branch")).then(pl.lit(False)).otherwise(pl.col("__g_over_cease"))
          .alias("__tip_reporting_over_cease_final")
    )

    has_age_next = "age_next" in df.columns
    has_monthly_salary = "monthly_salary" in df.columns

    out = out.with_columns([
        (pl.col("age_next").cast(pl.Int64) if has_age_next else pl.col("age_nb").cast(pl.Float64).cast(pl.Int64)).alias("__E"),
        pl.col("gender").alias("__K"),
        pl.col("annual_salary").fill_null(0.0).cast(pl.Float64).alias("__I"),
        pl.when(pl.col("nra_final").is_not_null())
          .then(pl.col("nra_final"))
          .otherwise(pl.col("nra"))
          .round(0).cast(pl.Int64).alias("__M"),
        pl.col("waiver").fill_null(0.0).cast(pl.Float64).alias("__L"),
    ])
    if has_monthly_salary:
        out = out.with_columns(
            pl.when(pl.col("monthly_salary").is_not_null() & (pl.col("monthly_salary") != 0.0))
              .then(pl.col("monthly_salary").cast(pl.Float64))
              .otherwise(pl.col("__I") / 12.0)
              .alias("__J")
        )
    else:
        out = out.with_columns((pl.col("__I") / 12.0).alias("__J"))

    def _res_expr(base_col):
        return (
            pl.when(pl.col("wspec") == "N/A").then(pl.col(base_col))
              .when(pl.col("wspec") == "As Per Data")
              .then(pl.col(base_col) + pl.when(pl.col("val3_2") == "No").then(pl.col("__L")).otherwise(0.0))
              .when(pl.col("wspec") == "Same For All")
              .then(pl.col(base_col) + pl.when(pl.col("val3_2") == "No").then(pl.col("emp_w")).otherwise(0.0))
              .otherwise(pl.col(base_col) + 0.0)
        )

    out = out.with_columns([
        _res_expr("cp0").alias("__R"),
        _res_expr("cp1").alias("__T"),
        _res_expr("cp2").alias("__Vp"),
        _res_expr("cp3").alias("__Xp"),
    ])
    out = out.with_columns(
        pl.when((pl.col("val3_2") == "No") & (pl.col("wspec") == "As Per Data"))
          .then(pl.col("__L") * pl.col("__J"))
          .otherwise(pl.col("emp_w") * pl.col("__J"))
          .alias("__BAw")
    )
    out = out.with_columns(
        (
            pl.col("__R") * pl.min_horizontal(["__J", "cl0"])
            + pl.col("__T") * pl.max_horizontal([pl.lit(0.0), pl.min_horizontal(["cl1", pl.col("__J") - pl.col("cl0")])])
            + pl.col("__Vp") * pl.max_horizontal([pl.lit(0.0), pl.min_horizontal(["cl2", pl.col("__J") - pl.col("cl0") - pl.col("cl1")])])
            + pl.col("__Xp") * pl.max_horizontal([pl.lit(0.0), pl.min_horizontal(["cl3", pl.col("__J") - pl.col("cl0") - pl.col("cl1") - pl.col("cl2")])])
        ).alias("__TH")
    )
    out = out.with_columns(
        pl.max_horizontal([pl.lit(0.0), pl.col("add_p") * pl.min_horizontal(["__J", "add_l"])]).alias("__TI")
    )
    out = out.with_columns(
        pl.min_horizontal([pl.col("__TH") + pl.col("__TI"), pl.col("AB")]).alias("__TJ")
    )

    def _band_lookup_expr(x_col_expr):
        tax_expr = pl.lit(0.0)
        for b in bands:
            cond = pl.lit(b["band_from"]) <= (x_col_expr * 12)
            val = pl.max_horizontal([
                pl.lit(b["basic_amount"]) + (x_col_expr * 12 - pl.lit(b["band_from"])) * pl.lit(b["margin"]) - pl.lit(rebate),
                pl.lit(0.0),
            ])
            tax_expr = pl.when(cond).then(val).otherwise(tax_expr)
        return tax_expr

    out = out.with_columns((pl.col("__J") / pear).alias("__TM"))
    out = out.with_columns((pl.col("__TM") - pl.col("__BAw")).alias("__TN"))
    out = out.with_columns(_band_lookup_expr(pl.col("__TN")).alias("__tax"))
    out = out.with_columns(((pl.col("__TM") * 12 - pl.col("__tax")) / 12).alias("__TO"))

    out = out.with_columns((pl.col("__TH") - pl.col("__BAw")).alias("___TB"))
    out = out.with_columns(_band_lookup_expr(pl.col("___TB")).alias("___tax_tb"))
    out = out.with_columns(((pl.col("__TH") * 12 - pl.col("___tax_tb")) / 12).alias("___TD"))

    out = out.with_columns(
        pl.when(pl.col("tax_opt") == "Apply net-of-tax maximum")
          .then(pl.min_horizontal(["__TJ", "__TO"]))
          .when(pl.col("tax_opt") == "Tax Replicator")
          .then(pl.min_horizontal(["___TD", "AB"]))
          .otherwise(pl.col("__TJ"))
          .alias("__AX")
    )

    mort_table = pl.DataFrame(
        [{"__E": k[0], "__K": k[1], "__BD": v} for k, v in mort_map.items()]
    ) if mort_map else pl.DataFrame({"__E": [], "__K": [], "__BD": []})
    out = out.join(mort_table, on=["__E", "__K"], how="left").with_columns(
        pl.col("__BD").fill_null(0.0)
    )

    out = out.with_columns(pl.col("__wpm_raw").cast(pl.Int64, strict=False).alias("__BF"))

    wp_table_full = pl.DataFrame(wp_rows) if wp_rows else pl.DataFrame(
        {"age_band_from": [], "wp_months": [], "factor": []}
    )
    member_bf = out.select(["member_no", "__E", "__BF"])
    bg_candidates = (
        member_bf.join(wp_table_full, left_on="__BF", right_on="wp_months", how="left")
        .filter(pl.col("age_band_from") <= pl.col("__E"))
    )
    bg_agg = (
        bg_candidates.sort(["member_no", "age_band_from"])
        .group_by("member_no", maintain_order=True)
        .agg(pl.col("factor").last().alias("__BG"))
    )
    out = out.join(bg_agg, on="member_no", how="left")

    out = out.with_columns([
        _vec_asof_by_gender(sb_by_g, "inc_adj", pl.col("__I"), pl.col("__K")).alias("__BH"),
        _vec_asof_by_gender(sb_by_g, "acaf_adj", pl.col("__I"), pl.col("__K")).alias("__BI"),
    ])

    out = out.with_columns(
        pl.when(pl.col("__J") > 0).then(pl.col("__AX") / pl.col("__J")).otherwise(0.0).alias("__BL")
    )
    out = out.with_columns(
        _vec_asof_cascade(rr_rows, "rr_from", "loading", pl.col("__BL")).alias("__BM")
    )

    db_table = pl.DataFrame({"core_def": list(tip_db.keys()), "__BW": list(tip_db.values())}) if tip_db else \
        pl.DataFrame({"core_def": [], "__BW": []})
    out = out.join(db_table, on="core_def", how="left")

    out = out.with_columns(
        (
            (pl.col("__BD") / 1000.0)
            * pl.col("__BG").fill_null(0.0)
            * pl.col("__BH").fill_null(0.0)
            * pl.col("__BI").fill_null(0.0)
            * BJ_val
            * BK_val
            * pl.col("__BM").fill_null(0.0)
            * pl.col("__BW").fill_null(0.0)
        ).alias("__TS")
    )

    ip_table = pl.DataFrame({"init_pd": list(ip_mm.keys()), "__BO": list(ip_mm.values())}) if ip_mm else \
        pl.DataFrame({"init_pd": [], "__BO": []})
    out = out.join(ip_table, on="init_pd", how="left")

    out = out.with_columns(
        _vec_asof_cascade(asb_rows, "salary_from", "sal_tier", pl.col("__I")).cast(pl.Int64).alias("__BP")
    )
    sal_col_map = {1: "sal_1", 2: "sal_2", 3: "sal_3"}
    sal_col_table = pl.DataFrame({"__BP": list(sal_col_map.keys()), "__sal_col": list(sal_col_map.values())})
    out = out.join(sal_col_table, on="__BP", how="left")

    wpann_table = pl.DataFrame({"__BF": list(wpann.keys()), "__WC": list(wpann.values())}) if wpann else \
        pl.DataFrame({"__BF": [], "__WC": []})
    out = out.join(wpann_table, on="__BF", how="left")

    ppl_table = pl.DataFrame({"pp_label": list(ppl_map.keys()), "__WA": list(ppl_map.values())}) if ppl_map else \
        pl.DataFrame({"pp_label": [], "__WA": []})
    out = out.join(ppl_table, on="pp_label", how="left")

    out = out.with_columns(
        pl.when(pl.col("__WA").is_not_null())
          .then(pl.max_horizontal([
              pl.lit(0.0),
              pl.min_horizontal([
                  pl.col("__WA"),
                  (pl.col("__M") - pl.col("__E")).cast(pl.Float64) * 12.0 - pl.col("__BF") + 1,
              ])
          ]))
          .otherwise(0.0)
          .alias("__WB")
    )
    out = out.with_columns(pl.col("__WB").cast(pl.Int64).alias("__WB_i"))

    age_table = pl.DataFrame([
        {"__E": k, "__WD": v["closest_age"], "__WE_val": v["interp_age"], "__WF": _gipf(v["age_factor"])}
        for k, v in age_map.items()
    ]) if age_map else pl.DataFrame({"__E": [], "__WD": [], "__WE_val": [], "__WF": []})
    out = out.join(age_table, on="__E", how="left")

    pp_table = pl.DataFrame([
        {"__pp_key_match": k, "__WL": v["closest_pp"], "__WM_val": v["interp_pp"], "__WN": _gipf(v["pp_factor"])}
        for k, v in pp_map.items()
    ]) if pp_map else pl.DataFrame({"__pp_key_match": [], "__WL": [], "__WM_val": [], "__WN": []})
    out = out.join(pp_table, left_on="__WB_i", right_on="__pp_key_match", how="left")

    esc_table = pl.DataFrame([
        {"__esc_pct_key": k, "__WT": _tip_esc_str(v["map_lower"]), "__WU": _tip_esc_str(v["map_upper"]),
         "__WV": _gipf(v["interp_adj"]), "__WW": _gipf(v["progression_loading"])}
        for k, v in esc_map.items() if isinstance(k, (int, float))
    ]) if esc_map else pl.DataFrame({"__esc_pct_key": [], "__WT": [], "__WU": [], "__WV": [], "__WW": []})
    out = out.with_columns(pl.col("esc_pct").round(2).alias("__esc_pct_key"))
    out = out.join(esc_table, on="__esc_pct_key", how="left")

    out = out.with_columns([
        pl.col("__WD").cast(pl.Int64).alias("__WD_i"),
        pl.col("__WE_val").cast(pl.Int64).alias("__WE_i"),
        pl.col("__WL").cast(pl.Int64).alias("__WL_i"),
        pl.col("__WM_val").cast(pl.Int64).alias("__WM_i"),
    ])

    esc_row0_src = esc_map.get(0.0)
    WT0 = _tip_esc_str(esc_row0_src["map_lower"]) if esc_row0_src else "0"
    WU0 = _tip_esc_str(esc_row0_src["map_upper"]) if esc_row0_src else "0"
    WV0 = _gipf(esc_row0_src["interp_adj"]) if esc_row0_src else 0.0
    WW0 = _gipf(esc_row0_src["progression_loading"]) if esc_row0_src else 0.0

    af_tbl = basis_tables["tip_annuity_factors"].filter(pl.col("product") == "tip")
    join_cols = ["age", "gender", "wp", "ip", "esc_key", "scheme_size", "max_pp"]
    dedup_tbl = af_tbl.unique(subset=join_cols, keep="last", maintain_order=True)

    def _sal_pick(res):
        return (
            pl.when(pl.col("__sal_col") == "sal_1").then(pl.col("sal_1"))
              .when(pl.col("__sal_col") == "sal_2").then(pl.col("sal_2"))
              .when(pl.col("__sal_col") == "sal_3").then(pl.col("sal_3"))
              .otherwise(None)
        )

    def _lookup(keys_df, age_col, esc_col, maxpp_col, is_literal_esc=False):
        cols = ["member_no", "__sal_col", age_col, "__K", "__WC", "__BO", maxpp_col]
        if not is_literal_esc:
            cols.append(esc_col)
        tmp = keys_df.select(cols).rename({
            age_col: "age", "__K": "gender", "__WC": "wp", "__BO": "ip", maxpp_col: "max_pp",
            **({} if is_literal_esc else {esc_col: "esc_key"}),
        })
        if is_literal_esc:
            tmp = tmp.with_columns(pl.lit(esc_col).alias("esc_key"))
        tmp = tmp.with_columns(pl.lit(size_t).alias("scheme_size"))
        res = tmp.join(dedup_tbl, on=["age", "gender", "wp", "ip", "esc_key", "scheme_size", "max_pp"], how="left")
        res = res.with_columns(_sal_pick(res).alias("__val"))
        return res.select(["member_no", "__val"])

    base_keys = out.select([
        "member_no", "__K", "__WC", "__BO", "__WT", "__WU",
        "__WD_i", "__WE_i", "__WL_i", "__WM_i", "__WB_i", "__sal_col",
    ])

    L1 = _lookup(base_keys, "__WD_i", "__WT", "__WL_i").rename({"__val": "L1"})
    L2 = _lookup(base_keys, "__WE_i", "__WT", "__WL_i").rename({"__val": "L2"})
    L3 = _lookup(base_keys, "__WD_i", "__WT", "__WM_i").rename({"__val": "L3"})
    L4 = _lookup(base_keys, "__WD_i", "__WU", "__WL_i").rename({"__val": "L4"})
    annuity_joined = L1.join(L2, on="member_no", how="left") \
                        .join(L3, on="member_no", how="left") \
                        .join(L4, on="member_no", how="left")

    L1_0 = _lookup(base_keys, "__WD_i", WT0, "__WL_i", is_literal_esc=True).rename({"__val": "L1_0"})
    L2_0 = _lookup(base_keys, "__WE_i", WT0, "__WL_i", is_literal_esc=True).rename({"__val": "L2_0"})
    L3_0 = _lookup(base_keys, "__WD_i", WT0, "__WM_i", is_literal_esc=True).rename({"__val": "L3_0"})
    L4_0 = _lookup(base_keys, "__WD_i", WU0, "__WL_i", is_literal_esc=True).rename({"__val": "L4_0"})
    annuity0_joined = L1_0.join(L2_0, on="member_no", how="left") \
                           .join(L3_0, on="member_no", how="left") \
                           .join(L4_0, on="member_no", how="left")

    xf_keys = base_keys.select(["member_no", "__sal_col", "__WU", "__WB_i"]).with_columns([
        pl.lit(40).alias("age"), pl.lit("F").alias("gender"), pl.lit(3).alias("wp"),
        pl.lit(12).alias("ip"), pl.col("__WU").alias("esc_key"), pl.lit("Small").alias("scheme_size"),
        pl.col("__WB_i").alias("max_pp"),
    ])
    xf_joined = xf_keys.join(dedup_tbl, on=["age", "gender", "wp", "ip", "esc_key", "scheme_size", "max_pp"], how="left")
    xf_joined = xf_joined.with_columns(_sal_pick(xf_joined).fill_null(0.0).alias("__xf")).select(["member_no", "__xf"])

    xf0_keys = base_keys.select(["member_no", "__sal_col", "__WB_i"]).with_columns([
        pl.lit(40).alias("age"), pl.lit("F").alias("gender"), pl.lit(3).alias("wp"),
        pl.lit(12).alias("ip"), pl.lit(WU0).alias("esc_key"), pl.lit("Small").alias("scheme_size"),
        pl.col("__WB_i").alias("max_pp"),
    ])
    xf0_joined = xf0_keys.join(dedup_tbl, on=["age", "gender", "wp", "ip", "esc_key", "scheme_size", "max_pp"], how="left")
    xf0_joined = xf0_joined.with_columns(_sal_pick(xf0_joined).fill_null(0.0).alias("__xf0")).select(["member_no", "__xf0"])

    tu_calc = (
        base_keys.select("member_no")
        .join(annuity_joined, on="member_no", how="left")
        .join(xf_joined, on="member_no", how="left")
        .join(out.select(["member_no", "__WF", "__WN", "__WV", "__WW", "__WB"]), on="member_no", how="left")
    )
    tu_calc = tu_calc.with_columns(
        pl.when(pl.col("__WB") == 0)
          .then(pl.lit(0.0))
          .when(pl.col("L1").is_null() | pl.col("L2").is_null() | pl.col("L3").is_null() | pl.col("L4").is_null())
          .then(pl.col("__xf"))
          .otherwise(
              (pl.col("L1") + (pl.col("L2") - pl.col("L1")) * pl.col("__WF")
               + (pl.col("L3") - pl.col("L1")) * pl.col("__WN")
               + (pl.col("L4") - pl.col("L1")) * pl.col("__WV")) * (1 + pl.col("__WW"))
          )
          .alias("__TU")
    )

    tu0_calc = (
        base_keys.select("member_no")
        .join(annuity0_joined, on="member_no", how="left")
        .join(xf0_joined, on="member_no", how="left")
        .join(out.select(["member_no", "__WF", "__WN", "__WB"]), on="member_no", how="left")
    )
    tu0_calc = tu0_calc.with_columns(
        pl.when(pl.col("__WB") == 0)
          .then(pl.lit(0.0))
          .when(pl.col("L1_0").is_null() | pl.col("L2_0").is_null() | pl.col("L3_0").is_null() | pl.col("L4_0").is_null())
          .then(pl.col("__xf0"))
          .otherwise(
              (pl.col("L1_0") + (pl.col("L2_0") - pl.col("L1_0")) * pl.col("__WF")
               + (pl.col("L3_0") - pl.col("L1_0")) * pl.col("__WN")
               + (pl.col("L4_0") - pl.col("L1_0")) * WV0) * (1 + WW0)
          )
          .alias("__TU0")
    )

    out = out.join(tu_calc.select(["member_no", "__TU"]), on="member_no", how="left")
    out = out.join(tu0_calc.select(["member_no", "__TU0"]), on="member_no", how="left")

    out = out.with_columns([
        (pl.col("__TS") * pl.col("__AX") * pl.col("__TU").fill_null(0.0)).alias("__TV"),
        (pl.col("__TS") * pl.col("__AX") * pl.col("__TU0").fill_null(0.0)).alias("__TV0"),
    ])

    out = out.with_columns([
        pl.when(pl.col("__included")).then(pl.col("__AX")).otherwise(0.0).fill_null(0.0).alias("__c"),
        pl.when(pl.col("__included")).then(pl.col("__TV")).otherwise(0.0).fill_null(0.0).alias("__b"),
        pl.when(pl.col("__included")).then(pl.col("__TV0")).otherwise(0.0).fill_null(0.0).alias("__b0"),
        pl.when(pl.col("__included")).then(pl.col("__TS")).otherwise(0.0).fill_null(0.0).alias("__ri"),
    ])
    out = out.with_columns(
        (pl.col("__ri") * pl.col("__c")).alias("__rixc")
    )

    _tip_excluded_members = out.filter(pl.col("__tip_excluded_flag"))["member_no"]

    result_cols = out.select([
        "member_no", "__c", "__b", "__b0", "__ri", "__rixc",
        "__tip_reporting_over_cease_final",
    ]).rename({
        "__c": "__c_final", "__b": "__b_final", "__b0": "__b0_final",
        "__ri": "__tip_reporting_incidence",
        "__rixc": "__tip_reporting_incidence_x_cover",
        "__tip_reporting_over_cease_final": "__tip_reporting_over_cease",
    })

    df2 = df.join(result_cols, on="member_no", how="left")
    if _tip_excluded_members.len() > 0:
        excluded_set = _tip_excluded_members.to_list()
        df2 = df2.with_columns(
            pl.when(pl.col("member_no").is_in(excluded_set))
              .then(pl.lit(False)).otherwise(pl.col("product_active"))
              .alias("product_active")
        )

    df2 = df2.with_columns([
        pl.col("__c_final").fill_null(0.0).alias("total_cover"),
        pl.col("__b_final").fill_null(0.0).alias("basic_technical_premium"),
        pl.lit(0.0).alias("aids_technical_premium"),
        pl.lit(0.0).alias("co_premium"),
        pl.lit(0.0).alias("waiver_premium"),
        pl.col("__b0_final").fill_null(0.0).alias("basic_excl_esc"),
        pl.col("__tip_reporting_incidence").fill_null(0.0).alias("reporting_incidence"),
        pl.col("__tip_reporting_incidence_x_cover").fill_null(0.0).alias("reporting_incidence_x_cover"),
        pl.col("__tip_reporting_over_cease").fill_null(False).alias("reporting_over_cease"),
    ])
    df2 = df2.with_columns(
        pl.when(pl.col("total_cover") > 0)
          .then(pl.col("basic_technical_premium") / pl.col("total_cover") * 1000)
          .otherwise(0.0).alias("rate_basic")
    )
    df2 = df2.with_columns(pl.lit(0.0).alias("rate_aids"))

    return df2.drop([
        "__c_final", "__b_final", "__b0_final",
        "__tip_reporting_incidence", "__tip_reporting_incidence_x_cover", "__tip_reporting_over_cease",
    ])


def _apply_tip_composite_legacy(df, spec, basis_tables, assump_tables, ctx):
    LT = _tip_build_lookup_tables(basis_tables, assump_tables, ctx)
    results, _tip_excluded = _tip_compute_member_rows(df, LT)
    df = _tip_finalize_dataframe(df, results, _tip_excluded)
    return df


def _tip_annuity_fs(af_map, sal_col, E, K, WC, BO, size,
                    age_row, pp_row, esc_row, WB):
    if WB == 0:
        return 0.0
    WD = age_row["closest_age"]; WE = age_row["interp_age"]; WF = _gipf(age_row["age_factor"])
    WL = pp_row["closest_pp"];   WM = pp_row["interp_pp"];   WN = _gipf(pp_row["pp_factor"])
    WT = _tip_esc_str(esc_row["map_lower"]); WU = _tip_esc_str(esc_row["map_upper"])
    WV = _gipf(esc_row["interp_adj"]);        WW = _gipf(esc_row["progression_loading"])

    def ann(age, esc, maxpp):
        key = "/".join([_gip_key_part(age), K, _gip_key_part(WC),
                        _gip_key_part(BO), esc, size, _gip_key_part(maxpp)])
        r = af_map.get(key.lower())
        return float(r[sal_col]) if r else None

    def _xf():
        fk = "/".join(["40", "F", "3", "12", WU, "Small", _gip_key_part(WB)])
        r = af_map.get(fk.lower())
        return float(r[sal_col]) if r is not None else 0.0

    WI = ann(WD, WT, WL); WJ = ann(WE, WT, WL)
    if WI is None or WJ is None:
        return _xf()
    WK = WI + (WJ - WI) * WF

    WQ = ann(WD, WT, WL); WR = ann(WD, WT, WM)
    if WQ is None or WR is None:
        return _xf()
    WS = (WR - WQ) * WN

    WZ = ann(WD, WT, WL); XA = ann(WD, WU, WL)
    if WZ is None or XA is None:
        return _xf()
    XB = (XA - WZ) * WV

    return (WK + WS + XB) * (1 + WW)


def _tip_build_lookup_tables(basis_tables, assump_tables, ctx):
    scheme = ctx.scheme
    g = scheme
    size  = ctx.scheme_size.lower()
    size_t = ctx.scheme_size
    comm  = ctx.calc_date
    sec_ind = scheme.secondary
    pear = float(scheme.pear_tgp_pct) if scheme.pear_tgp_pct else 1.0
    B = basis_tables

    mort_map = {(int(r["age"]), r["gender"]): r["rate"]
                for r in B["mortality"].filter(pl.col("product") == "gip").iter_rows(named=True)}
    sb_rows = (B["salary_band"].filter(pl.col("product") == "gip")
               .select(["gender", "salary_from", "inc_adj", "acaf_adj"])
               .sort(["gender", "salary_from"]).to_dicts())
    sb_by_g = {gd: [r for r in sb_rows if r["gender"] == gd] for gd in ("M", "F")}
    ind = B["industry"].filter((pl.col("product") == "gip") & (pl.col("scheme_size") == size))
    ind_row = ind.filter(pl.col("secondary") == sec_ind)
    if ind_row.height == 0:
        raise KeyError(f"[tip] industry '{sec_ind}' not found size={size}")
    BJ_val = float(ind_row["inc_adj"][0]); BK_val = float(ind_row["acaf_adj"][0])
    rr_rows = (B["rr_loading"].filter(pl.col("product") == "gip")
               .select(["rr_from", "loading"]).sort("rr_from").to_dicts())
    wp_rows = (B["wp_loading"].filter((pl.col("product") == "gip") & (pl.col("scheme_size") == size))
               .select(["age_band_from", "wp_months", "factor"])
               .sort(["wp_months", "age_band_from"]).to_dicts())
    asb_rows = (B["annuity_salary_bands"].filter(pl.col("product") == "gip")
                .select(["salary_from", "sal_tier"]).sort("salary_from").to_dicts())
    ip = B["ip_loading"].filter(pl.col("product") == "gip")
    ip_mm = {r["ip_label"]: r["mapped_months"] for r in ip.iter_rows(named=True)}

    tip_db = {r["db_def"]: r["loading"]
              for r in B["tip_db_loading"].filter(pl.col("product") == "tip").iter_rows(named=True)}
    af_map = {r["key"].lower(): r
              for r in B["tip_annuity_factors"].filter(pl.col("product") == "tip").iter_rows(named=True)}    
    age_map = {int(r["age"]): r
               for r in B["tip_age_interp"].filter(pl.col("product") == "tip").iter_rows(named=True)}
    pp_map = {int(r["pp_key"]): r
              for r in B["tip_payment_period_map"].filter(pl.col("product") == "tip").iter_rows(named=True)}
    ppl_map = {r["pp_label"]: int(r["pp_months"])
               for r in B["tip_payment_period_map"].filter(pl.col("product") == "tip").iter_rows(named=True)}
    esc_rows = B["tip_esc_loading"].filter(pl.col("product") == "tip")
    esc_map = {}
    for r in esc_rows.iter_rows(named=True):
        k = "Uncapped" if r["esc_pct"] is None else round(float(r["esc_pct"]), 2)
        esc_map[k] = r
    wpann = {int(r["wp_raw"]): int(r["wp_annuity"])
             for r in B["tip_wp_ann_map"].filter(pl.col("product") == "tip").iter_rows(named=True)}

    tx = (assump_tables["tax_tables"].filter(pl.col("tax_year") == "2026-2027_gip")
          .select(["band_from", "margin", "basic_amount", "primary_rebate"])
          .with_columns([pl.col(c).cast(pl.Float64, strict=False)
                         for c in ["band_from", "margin", "basic_amount", "primary_rebate"]])
          .drop_nulls("band_from").sort("band_from"))
    bands = tx.to_dicts()
    rebate = bands[0]["primary_rebate"] if bands else 0.0

    waiver_incl = getattr(g, "tip_waiver_included", {}) or {}

    return {
        "g": g, "size_t": size_t, "comm": comm, "pear": pear,
        "mort_map": mort_map, "sb_by_g": sb_by_g,
        "BJ_val": BJ_val, "BK_val": BK_val, "rr_rows": rr_rows, "wp_rows": wp_rows,
        "asb_rows": asb_rows, "ip_mm": ip_mm, "tip_db": tip_db, "af_map": af_map,
        "age_map": age_map, "pp_map": pp_map, "ppl_map": ppl_map, "esc_map": esc_map,
        "wpann": wpann, "bands": bands, "rebate": rebate, "waiver_incl": waiver_incl,
    }


def _tip_compute_member_rows(df, LT):
    g = LT["g"]
    size_t = LT["size_t"]
    LT["comm"]
    pear = LT["pear"]
    mort_map = LT["mort_map"]
    sb_by_g = LT["sb_by_g"]
    BJ_val = LT["BJ_val"]
    BK_val = LT["BK_val"]
    rr_rows = LT["rr_rows"]
    wp_rows = LT["wp_rows"]
    asb_rows = LT["asb_rows"]
    ip_mm = LT["ip_mm"]
    tip_db = LT["tip_db"]
    af_map = LT["af_map"]
    age_map = LT["age_map"]
    pp_map = LT["pp_map"]
    ppl_map = LT["ppl_map"]
    esc_map = LT["esc_map"]
    wpann = LT["wpann"]
    bands = LT["bands"]
    rebate = LT["rebate"]
    waiver_incl = LT["waiver_incl"]

    results = []
    _tip_excluded = set()
    for row in df.iter_rows(named=True):
        cat = int(row["category_number"]) if row.get("category_number") is not None else None
        mno = row.get("member_no")
        if cat is None or not bool(row.get("product_active")):
            results.append({
                "member_no": mno,
                "__c": 0.0,
                "__b": 0.0,
                "__b0": 0.0,
                "__tip_reporting_incidence": 0.0,
                "__tip_reporting_incidence_x_cover": 0.0,
                "__tip_reporting_over_cease": False,
            })
            continue

        _g_age = _gipf(row.get("age"))
        _g_cease = _gipf(row.get("ceasing_age") if row.get("ceasing_age") is not None else row.get("nra_final"))
        _g_wpm = _gip_wp_months(g.tip_waiting_period.get(cat))
        _g_wpm = _g_wpm if _g_wpm is not None else 0.0
        _g_over_cease = _g_age > _g_cease

        if not (_g_age >= 16 and _g_age <= _g_cease and (_g_cease - _g_age) * 12.0 >= _g_wpm):
            _tip_excluded.add(mno)
            results.append({
                "member_no": mno,
                "__c": 0.0,
                "__b": 0.0,
                "__b0": 0.0,
                "__tip_reporting_incidence": 0.0,
                "__tip_reporting_incidence_x_cover": 0.0,
                "__tip_reporting_over_cease": _g_over_cease,
            })
            continue

        E = int(row["age_next"]) if row.get("age_next") is not None else int(_gipf(row.get("age_nb")))
        K = row.get("gender")
        I = _gipf(row.get("annual_salary"))
        J = _gipf(row.get("monthly_salary")) or (I / 12.0)
        M = round(_gipf(row.get("nra_final") if row.get("nra_final") is not None else row.get("nra")))
        L = _gipf(row.get("waiver"))

        cp = g.tip_core_benefit_perc.get(cat, [0, 0, 0, 0])
        cl = g.tip_core_benefit_layer.get(cat, [0, 0, 0, 0])
        add_p = _gipf(g.tip_core_benefit_perc_additional.get(cat))
        add_l = _gipf(g.tip_core_benefit_layer_additional.get(cat))
        AB = _gipf(g.tip_core_overall_maximum.get(cat))
        emp_w = _gipf(g.tip_employer_waiver.get(cat))
        wspec = g.tip_waiver_pct_specified.get(cat, "N/A")
        val3_2 = waiver_incl.get(cat, "No")
        assert val3_2 == "No", (f"[tip] cat {cat}: 'Waiver Included?'={val3_2!r} — "
                                f"engine verified only for 'No'.")
        core_def = g.tip_core_disability_definition.get(cat)
        init_pd = g.tip_initial_period.get(cat)
        wp_str = g.tip_waiting_period.get(cat)
        esc_pct = _gipf(g.tip_escalation_pct.get(cat))
        tax_opt = g.tip_tax_options.get(cat)
        pp_label = g.tip_payment_period.get(cat)

        # Called only within this iteration, so loop variables are bound correctly.
        def _res(base):
            if wspec == "N/A":  # noqa: B023
                return base
            if wspec == "As Per Data":  # noqa: B023
                add = L if val3_2 == "No" else 0.0  # noqa: B023
            elif wspec == "Same For All":  # noqa: B023
                add = emp_w if val3_2 == "No" else 0.0  # noqa: B023
            else:
                add = 0.0
            return base + add
        R = _res(_gipf(cp[0])); T = _res(_gipf(cp[1])); Vp = _res(_gipf(cp[2])); Xp = _res(_gipf(cp[3]))
        S, U, W, Yl = _gipf(cl[0]), _gipf(cl[1]), _gipf(cl[2]), _gipf(cl[3])
        BAw = (L * J) if (val3_2 == "No" and wspec == "As Per Data") else (emp_w * J)

        TH = (R*min(J, S) + T*max(0, min(U, J-S)) + Vp*max(0, min(W, J-S-U))
              + Xp*max(0, min(Yl, J-S-U-W)))
        TI = max(0, add_p*min(J, add_l))
        TJ = min(TH + TI, AB)
        TM = J / pear
        TN = TM - BAw
        br = None
        for b in bands:
            if b["band_from"] <= TN*12: br = b
            else: break
        tax = 0.0 if br is None else max(br["basic_amount"] + (TN*12-br["band_from"])*br["margin"] - rebate, 0.0)
        TO = (TM*12 - tax) / 12
        if tax_opt == "Apply net-of-tax maximum":
            AX = min(TJ, TO)
        elif tax_opt == "Tax Replicator":
            _SY = TH
            _TB = _SY - BAw
            _br_tb = None
            for _b in bands:
                if _b['band_from'] <= _TB * 12:
                    _br_tb = _b
                else:
                    break
            _tax_tb = 0.0 if _br_tb is None else max(
                _br_tb['basic_amount'] + (_TB * 12 - _br_tb['band_from']) * _br_tb['margin'] - rebate, 0.0)
            _TD = (_SY * 12 - _tax_tb) / 12
            AX = min(_TD, AB)
        else:
            AX = TJ

        BD = mort_map.get((E, K), 0.0)
        BF = _gip_wp_months(wp_str)
        BG = None
        if BF is not None:
            sub = [r for r in wp_rows if int(r["wp_months"]) == int(BF) and r["age_band_from"] <= E]
            BG = sub[-1]["factor"] if sub else None
        BH = _gip_asof(sb_by_g.get(K, []), "salary_from", "inc_adj", I)
        BI = _gip_asof(sb_by_g.get(K, []), "salary_from", "acaf_adj", I)
        BL = AX / J if J > 0 else 0.0
        BM = _gip_asof(rr_rows, "rr_from", "loading", BL)
        BW = tip_db.get(core_def)
        TS = (_gipf(BD)/1000 * _gipf(BG) * _gipf(BH) * _gipf(BI)
              * BJ_val * BK_val * _gipf(BM) * _gipf(BW))

        BO = ip_mm.get(init_pd)
        BP = int(_gip_asof(asb_rows, "salary_from", "sal_tier", I))
        sal_col = {1: "sal_1", 2: "sal_2", 3: "sal_3"}[BP]
        WC = wpann.get(BF)
        WA = ppl_map.get(pp_label)
        WB = max(0, min(WA, (M - E)*12 - BF + 1)) if WA is not None else 0
        age_row = age_map.get(E)
        pp_row  = pp_map.get(int(WB)) if WB in pp_map else pp_map.get(WB)
        esc_row = esc_map.get(round(esc_pct, 2))
        if age_row is None or pp_row is None or esc_row is None:
            TU = 0.0
        else:
            TU = _tip_annuity_fs(af_map, sal_col, E, K, WC, BO, size_t,
                                 age_row, pp_row, esc_row, WB)

        esc_row0 = esc_map.get(0.0)
        if age_row is None or pp_row is None or esc_row0 is None:
            TU0 = 0.0
        else:
            TU0 = _tip_annuity_fs(af_map, sal_col, E, K, WC, BO, size_t,
                                  age_row, pp_row, esc_row0, WB)
        TV0 = TS * AX * _gipf(TU0)

        TV = TS * AX * _gipf(TU)
        results.append({
            "member_no": mno,
            "__c": AX,
            "__b": TV,
            "__b0": TV0,
            "__tip_reporting_incidence": TS,
            "__tip_reporting_incidence_x_cover": TS * AX,
            "__tip_reporting_over_cease": False,
        })

    return results, _tip_excluded


def _tip_esc_str(v):
    if v is None:
        return "0"
    if isinstance(v, str) and v.strip().lower() == "uncapped":
        return "Uncapped"
    try:
        return str(int(float(v)))
    except (TypeError, ValueError):
        return str(v)


def _tip_finalize_dataframe(df, results, _tip_excluded):
    res = pl.DataFrame(results)
    df = df.join(res, on="member_no", how="left")
    if _tip_excluded:
        df = df.with_columns(
            pl.when(pl.col("member_no").is_in(list(_tip_excluded)))
              .then(pl.lit(False)).otherwise(pl.col("product_active"))
              .alias("product_active"))
    df = df.with_columns([
        pl.col("__c").fill_null(0.0).alias("total_cover"),
        pl.col("__b").fill_null(0.0).alias("basic_technical_premium"),
        pl.lit(0.0).alias("aids_technical_premium"),
        pl.lit(0.0).alias("co_premium"),
        pl.lit(0.0).alias("waiver_premium"),
        pl.col("__b0").fill_null(0.0).alias("basic_excl_esc"),
        pl.col("__tip_reporting_incidence")
          .fill_null(0.0)
          .alias("reporting_incidence"),
        pl.col("__tip_reporting_incidence_x_cover")
          .fill_null(0.0)
          .alias("reporting_incidence_x_cover"),
        pl.col("__tip_reporting_over_cease")
          .fill_null(False)
          .alias("reporting_over_cease"),
    ])
    df = df.with_columns(
        pl.when(pl.col("total_cover") > 0)
          .then(pl.col("basic_technical_premium") / pl.col("total_cover") * 1000)
          .otherwise(0.0).alias("rate_basic"))
    df = df.with_columns(pl.lit(0.0).alias("rate_aids"))
    return df.drop([
        "__c",
        "__b",
        "__b0",
        "__tip_reporting_incidence",
        "__tip_reporting_incidence_x_cover",
        "__tip_reporting_over_cease",
    ])
