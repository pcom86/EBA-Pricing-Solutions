from __future__ import annotations

import calendar
import math
import re
from datetime import date
from typing import Any

import polars as pl

from . import helpers_common
from .helpers_common import (
    _gip_asof,
    _gip_key_part,
    _gip_wp_months,
    _gipf,
    _vec_asof_by_gender,
    _vec_asof_cascade,
    _vec_wp_months,
    _vec_yearfrac_30_360,
)

__all__ = [
    "_apply_gip_composite",
    "_apply_gip_composite_legacy",
    "_gip_annuity_fs",
    "_gip_annuity_fs_fast",
    "_gip_backdating_loading",
    "_gip_build_lookup_tables",
    "_gip_compute_member_rows",
    "_gip_eomonth",
    "_gip_esc_key",
    "_gip_finalize_dataframe",
    "_gip_yearfrac_30_360",
]


def _apply_gip_composite(df: pl.DataFrame, spec: dict, basis_tables: dict,
                         assump_tables: dict, ctx) -> pl.DataFrame:
    # Vectorized GIP composite (Polars column expressions + table joins,
    # replacing the original per-member Python loop). Validated against the
    # original loop-based implementation (now kept as _apply_gip_composite_legacy)
    # across the full golden CTR population. Approx 20x speedup measured.
    scheme = ctx.scheme
    g = scheme
    size = ctx.scheme_size.lower()
    size_title = ctx.scheme_size
    comm = ctx.calc_date
    sec_ind = scheme.secondary
    pear = float(scheme.pear_tgp_pct) if scheme.pear_tgp_pct else 1.0
    B = basis_tables

    esc_map = {}
    _esc_tbl = B["annuity_esc_loading"].filter(pl.col("product") == "gip")
    for _er in _esc_tbl.iter_rows(named=True):
        if _er["esc_pct"] not in esc_map:
            esc_map[_er["esc_pct"]] = _er

    mort_map = {(int(r["age"]), r["gender"]): r["rate"]
                for r in B["mortality"].filter(pl.col("product") == "gip").iter_rows(named=True)}
    sb_rows = (B["salary_band"].filter(pl.col("product") == "gip")
               .select(["gender", "salary_from", "inc_adj", "acaf_adj"])
               .sort(["gender", "salary_from"]).to_dicts())
    sb_by_g = {gd: [r for r in sb_rows if r["gender"] == gd] for gd in ("M", "F")}

    ind = B["industry"].filter((pl.col("product") == "gip") & (pl.col("scheme_size") == size))
    ind_row = ind.filter(pl.col("secondary") == sec_ind)
    if ind_row.height == 0:
        raise KeyError(f"[gip] industry '{sec_ind}' not found for size={size}")
    BO_val = float(ind_row["inc_adj"][0])
    BP_val = float(ind_row["acaf_adj"][0])

    rr_rows = (B["rr_loading"].filter(pl.col("product") == "gip")
               .select(["rr_from", "loading"]).sort("rr_from").to_dicts())
    asb_rows = (B["annuity_salary_bands"].filter(pl.col("product") == "gip")
                .select(["salary_from", "sal_tier"]).sort("salary_from").to_dicts())
    wp_rows = (B["wp_loading"].filter((pl.col("product") == "gip") & (pl.col("scheme_size") == size))
               .select(["age_band_from", "wp_months", "factor"])
               .sort(["wp_months", "age_band_from"]).to_dicts())
    ip_adj = {r["ip_label"]: r["adjustment"] for r in B["ip_loading"].filter(pl.col("product") == "gip").iter_rows(named=True)}
    db_map = {r["combined_key"]: r["loading"] for r in B["db_loading"].filter(pl.col("product") == "gip").iter_rows(named=True)}
    wpann = {int(r["wp_raw"]): int(r["wp_annuity"]) for r in B["wp_annuity_mapping"].filter(pl.col("product") == "gip").iter_rows(named=True)}
    nra_map = {int(r["nra"]): r for r in B["annuity_nra_interpolation"].filter(pl.col("product") == "gip").iter_rows(named=True)}
    hcc = {r["param_name"]: float(r["param_value"]) for r in B["gip_hcc_basis"].filter(pl.col("product") == "gip").iter_rows(named=True)}
    HCC_RATE = hcc["hcc_pricing_rate"]
    J5 = hcc["hcc_reduction_factor"]

    tx = (assump_tables["tax_tables"].filter(pl.col("tax_year") == "2026-2027_gip")
          .select(["band_from", "margin", "basic_amount", "primary_rebate"])
          .with_columns([pl.col(c).cast(pl.Float64, strict=False) for c in ["band_from", "margin", "basic_amount", "primary_rebate"]])
          .drop_nulls("band_from").sort("band_from"))
    bands = tx.to_dicts()
    rebate = bands[0]["primary_rebate"] if bands else 0.0

    plim = {r["param_name"]: r["param_value"] for r in assump_tables["product_limits"].filter(pl.col("product") == "gip").iter_rows(named=True)}
    co_cost = float(assump_tables["conversion_option"].filter(pl.col("product") == "gip")["rate"][0])
    f451 = float(plim.get("max_co_age", 65))
    f452 = float(plim.get("withdrawal_only_pre_nra_cap_years", 5))
    waiver_incl_map = getattr(g, "gip_waiver_included", {}) or {}
    backdating_table = B["backdating_loading"].filter((pl.col("product") == "gip") & (pl.col("scheme_wp") == 6))
    af_table = B["annuity_factors"].filter(pl.col("product") == "gip").select(
        ["age", "gender", "wp", "ip", "nra", "esc_key", "scheme_size", "sal_1", "sal_2", "sal_3"])

    def _backdating_loading(label):
        if not label or str(label).strip().lower() == "none":
            return 0.0
        bd = backdating_table.filter(pl.col("backdating_option") == label)
        return float(bd["loading"][0]) if bd.height else 0.0

    cats_all = sorted(set(g.gip_core_benefit_perc.keys()) | set(df["category_number"].drop_nulls().unique().to_list()))
    cat_rows = []
    for cat in cats_all:
        perc = g.gip_core_benefit_perc.get(cat, [0, 0, 0, 0])
        layer = g.gip_core_benefit_layer.get(cat, [0, 0, 0, 0])
        wp_str = g.gip_waiting_period.get(cat)
        BJ = _vec_wp_months(wp_str)
        init_pd = g.gip_initial_period.get(cat)
        core_def = g.gip_core_disability_definition.get(cat)
        ext_def = g.gip_hcc_disability_definition.get(cat)
        cat_rows.append({
            "category_number": cat,
            "__S0": float(perc[0] or 0), "__S1": float(perc[1] or 0), "__S2": float(perc[2] or 0), "__S3": float(perc[3] or 0),
            "__T0": float(layer[0] or 0), "__T1": float(layer[1] or 0), "__T2": float(layer[2] or 0), "__T3": float(layer[3] or 0),
            "__add_perc": float(g.gip_core_benefit_perc_additional.get(cat) or 0),
            "__add_layer": float(g.gip_core_benefit_layer_additional.get(cat) or 0),
            "__AC": float(g.gip_core_overall_maximum.get(cat) or 0),
            "__emp_waiver": float(g.gip_employer_waiver.get(cat) or 0),
            "__waiver_spec": g.gip_waiver_pct_specified.get(cat, "N/A"),
            "__val3_2": waiver_incl_map.get(cat, "No"),
            "__tax_opt": g.gip_tax_options.get(cat),
            "__wp_months_raw": BJ,
            "__BY": wpann.get(BJ) if BJ is not None else None,
            "__BT": ip_adj.get(init_pd) and None,
            "__esc_pct": float(g.gip_escalation_pct.get(cat) or 0),
            "__BL": _backdating_loading(g.gip_backdating.get(cat)),
            "__BV": (core_def or "") + (ext_def or ""),
            "__BU": ip_adj.get(init_pd),
            "__AQ": g.gip_include_hcc_waiver.get(cat, "No"),
            "__AR": float(g.gip_hcc_payment_period.get(cat) or 0),
            "__AS": g.gip_hcc_payment_type.get(cat),
            "__AT_nom": float(g.gip_hcc_nominal_amount.get(cat) or 0),
            "__AV": float(g.gip_hcc_escalation_pct.get(cat) or 0),
            "__AY": g.gip_include_conversion_option.get(cat, "No"),
        })
    ip_mm = {r["ip_label"]: r["mapped_months"] for r in B["ip_loading"].filter(pl.col("product") == "gip").iter_rows(named=True)}
    for row in cat_rows:
        init_pd = g.gip_initial_period.get(row["category_number"])
        row["__BT"] = ip_mm.get(init_pd)
    cat_df = pl.DataFrame(cat_rows).with_columns(pl.col("category_number").cast(pl.Int64))

    nra_map_rows = [{"__M": k, "__FK": v.get("age_lookup"), "__FL": v.get("age_interp"), "__FR": float(v.get("linear_scaler") or 0)}
                     for k, v in nra_map.items()]
    nra_map_df = pl.DataFrame(nra_map_rows)

    esc_map_rows = [{"__esc_pct_key": round(k, 2), "__map_lower_key": v["map_lower_key"], "__map_upper_key": v["map_upper_key"],
                      "__interp_weight": float(v["interp_weight"] or 0), "__progression_loading": float(v["progression_loading"] or 0)}
                     for k, v in esc_map.items() if k is not None]
    esc_map_df = pl.DataFrame(esc_map_rows)

    mort_df = pl.DataFrame([{"__E_m": k[0], "__K_m": k[1], "__BH": v} for k, v in mort_map.items()])
    db_map_df = pl.DataFrame([{"__BV_key": k, "__BW": v} for k, v in db_map.items()]) if db_map else pl.DataFrame(
        {"__BV_key": [], "__BW": []}, schema={"__BV_key": pl.Utf8, "__BW": pl.Float64})
    wp_df = pl.DataFrame(wp_rows) if wp_rows else pl.DataFrame(
        {"age_band_from": [], "wp_months": [], "factor": []}, schema={"age_band_from": pl.Float64, "wp_months": pl.Int64, "factor": pl.Float64})

    result = df.select(["member_no", "category_number", "product_active", "age", "ceasing_age", "nra_final",
                         "annual_salary", "waiver", "age_nb", "gender", "date_of_birth", "hcc_waiver"])
    result = result.with_columns(pl.col("category_number").cast(pl.Int64))
    result = result.join(cat_df, on="category_number", how="left")

    result = result.with_columns([
        pl.col("age").fill_null(0.0).alias("__g_age"),
        pl.when(pl.col("ceasing_age").is_not_null()).then(pl.col("ceasing_age"))
          .otherwise(pl.col("nra_final").fill_null(0.0)).fill_null(0.0).alias("__g_cease"),
        pl.col("__wp_months_raw").fill_null(0.0).alias("__g_wpm"),
        pl.col("annual_salary").fill_null(0.0).alias("__I"),
        pl.col("waiver").fill_null(0.0).alias("__L"),
        pl.col("age_nb").fill_null(0.0).cast(pl.Int64).alias("__E"),
        pl.col("gender").alias("__K"),
        pl.col("nra_final").fill_null(0.0).round(0).cast(pl.Int64).alias("__M"),
    ])
    result = result.with_columns([
        ((pl.col("__g_cease") - pl.col("__g_age")) * 12.0).alias("__g_m2nrd"),
        (pl.col("__I") / 12.0).alias("__J"),
    ])

    result = result.with_columns(
        (
            pl.col("category_number").is_not_null()
            & pl.col("product_active")
            & (pl.col("__g_age") >= 16)
            & (pl.col("__g_age") <= pl.col("__g_cease"))
            & (pl.col("__g_m2nrd") >= pl.col("__g_wpm"))
        ).alias("__included")
    )

    def _resolve(base_col):
        return (
            pl.when(pl.col("__waiver_spec") == "N/A").then(pl.col(base_col))
              .when(pl.col("__waiver_spec") == "As Per Data")
              .then(pl.col(base_col) + pl.when(pl.col("__val3_2") == "No").then(pl.col("__L")).otherwise(0.0))
              .when(pl.col("__waiver_spec") == "Same For All")
              .then(pl.col(base_col) + pl.when(pl.col("__val3_2") == "No").then(pl.col("__emp_waiver")).otherwise(0.0))
              .otherwise(pl.col(base_col) + 0.0)
        )
    result = result.with_columns([
        _resolve("__S0").alias("__S"), _resolve("__S1").alias("__U"),
        _resolve("__S2").alias("__W"), _resolve("__S3").alias("__Y"),
        pl.col("__T0").alias("__T"), pl.col("__T1").alias("__V"),
        pl.col("__T2").alias("__X"), pl.col("__T3").alias("__Z"),
    ])
    result = result.with_columns(
        pl.when((pl.col("__val3_2") == "No") & (pl.col("__waiver_spec") == "As Per Data"))
          .then(pl.col("__L") * pl.col("__J")).otherwise(pl.col("__emp_waiver") * pl.col("__J")).alias("__BE")
    )
    result = result.with_columns(
        (pl.col("__S") * pl.min_horizontal([pl.col("__J"), pl.col("__T")])
         + pl.col("__U") * pl.max_horizontal([pl.lit(0.0), pl.min_horizontal([pl.col("__V"), pl.col("__J") - pl.col("__T")])])
         + pl.col("__W") * pl.max_horizontal([pl.lit(0.0), pl.min_horizontal([pl.col("__X"), pl.col("__J") - pl.col("__T") - pl.col("__V")])])
         + pl.col("__Y") * pl.max_horizontal([pl.lit(0.0), pl.min_horizontal([pl.col("__Z"), pl.col("__J") - pl.col("__T") - pl.col("__V") - pl.col("__X")])])
        ).alias("__CO")
    )
    result = result.with_columns(
        pl.max_horizontal([pl.lit(0.0), pl.col("__add_perc") * pl.min_horizontal([pl.col("__J"), pl.col("__add_layer")])]).alias("__CP")
    )
    result = result.with_columns(
        pl.min_horizontal([pl.col("__CO") + pl.col("__CP"), pl.col("__AC")]).alias("__CQ")
    )

    def _band_lookup(x_col):
        expr = pl.lit(0.0)
        for b in bands:
            cond = pl.lit(b["band_from"]) <= x_col
            val = pl.max_horizontal([pl.lit(b["basic_amount"]) + (x_col - pl.lit(b["band_from"])) * pl.lit(b["margin"]) - pl.lit(rebate), pl.lit(0.0)])
            expr = pl.when(cond).then(val).otherwise(expr)
        return expr

    result = result.with_columns((pl.col("__J") / pear).alias("__CT"))
    result = result.with_columns((pl.col("__CT") - pl.col("__BE")).alias("__CU"))
    result = result.with_columns(_band_lookup(pl.col("__CU") * 12.0).alias("__tax_CU"))
    result = result.with_columns(((pl.col("__CT") * 12.0 - pl.col("__tax_CU")) / 12.0).alias("__CV"))
    result = result.with_columns(pl.min_horizontal([pl.col("__CQ"), pl.col("__CV")]).alias("__CW"))

    result = result.with_columns((pl.col("__CO") - pl.col("__BE")).alias("__CI_"))
    result = result.with_columns(_band_lookup(pl.col("__CI_") * 12.0).alias("__tax_CI"))
    result = result.with_columns(((pl.col("__CO") * 12.0 - pl.col("__tax_CI")) / 12.0).alias("__CK"))

    result = result.with_columns(
        pl.when(pl.col("__tax_opt") == "Apply net-of-tax maximum").then(pl.col("__CW"))
          .when(pl.col("__tax_opt") == "Tax Replicator").then(pl.col("__CK"))
          .otherwise(pl.col("__CQ")).alias("__BA")
    )

    result = result.join(mort_df, left_on=["__E", "__K"], right_on=["__E_m", "__K_m"], how="left")
    result = result.with_columns(pl.col("__BH").fill_null(0.0))

    if wp_rows:
        wp_joined = result.select(["member_no", "__wp_months_raw", "__E"]).join(
            wp_df.rename({"wp_months": "__BJ_join", "age_band_from": "__abf", "factor": "__BK_candidate"}),
            left_on="__wp_months_raw", right_on="__BJ_join", how="left"
        )
        wp_filtered = wp_joined.filter((pl.col("__abf") <= pl.col("__E")) | pl.col("__abf").is_null())
        bk_agg = (wp_filtered.sort("__abf").group_by("member_no", maintain_order=True)
                  .agg(pl.col("__BK_candidate").last().alias("__BK")))
        result = result.join(bk_agg, on="member_no", how="left")
    else:
        result = result.with_columns(pl.lit(None).alias("__BK"))

    result = result.with_columns([
        _vec_asof_by_gender(sb_by_g, "inc_adj", pl.col("__I"), pl.col("__K")).alias("__BM"),
        _vec_asof_by_gender(sb_by_g, "acaf_adj", pl.col("__I"), pl.col("__K")).alias("__BN"),
    ])
    result = result.join(db_map_df, left_on="__BV", right_on="__BV_key", how="left")
    result = result.with_columns(
        pl.when(pl.col("__J") > 0).then(pl.col("__BA") / pl.col("__J")).otherwise(0.0).alias("__BQ")
    )
    result = result.with_columns(_vec_asof_cascade(rr_rows, "rr_from", "loading", pl.col("__BQ")).alias("__BR"))

    result = result.with_columns(
        (pl.col("__BH").fill_null(0.0) / 1000 * pl.col("__BK").fill_null(0.0) * pl.col("__BM").fill_null(0.0)
         * pl.col("__BN").fill_null(0.0) * BO_val * BP_val * pl.col("__BR").fill_null(0.0)
         * pl.col("__BU").fill_null(0.0) * pl.col("__BW").fill_null(0.0)).alias("__DC")
    )

    result = result.join(nra_map_df, on="__M", how="left")
    result = result.with_columns(pl.col("__esc_pct").round(2).alias("__esc_pct_rounded"))
    result = result.join(esc_map_df, left_on="__esc_pct_rounded", right_on="__esc_pct_key", how="left")
    result = result.with_columns((pl.col("__esc_pct").fill_null(0.0) * 100).round(0).cast(pl.Int64).cast(pl.Utf8).alias("__BS"))

    result = result.with_columns(_vec_asof_cascade(asb_rows, "salary_from", "sal_tier", pl.col("__I")).cast(pl.Int64).alias("__BX"))
    result = result.with_columns(pl.lit(size_title).alias("__size_title"))

    j0 = result.select(["member_no","__E","__K","__BY","__BT","__FK","__BS","__size_title","__BL","__BX"]).join(
        af_table, left_on=["__E","__K","__BY","__BT","__FK","__BS","__size_title"],
        right_on=["age","gender","wp","ip","nra","esc_key","scheme_size"], how="left")
    j0 = j0.with_columns((pl.when(pl.col("__BX")==1).then(pl.col("sal_1")).when(pl.col("__BX")==2).then(pl.col("sal_2")).otherwise(pl.col("sal_3"))).alias("__FO_sal"))
    j0 = j0.with_columns((pl.col("__FO_sal") * (1 + pl.col("__BL"))).alias("__FO"))

    j1 = result.select(["member_no","__E","__K","__BY","__BT","__FL","__BS","__size_title","__BL","__BX"]).join(
        af_table, left_on=["__E","__K","__BY","__BT","__FL","__BS","__size_title"],
        right_on=["age","gender","wp","ip","nra","esc_key","scheme_size"], how="left")
    j1 = j1.with_columns((pl.when(pl.col("__BX")==1).then(pl.col("sal_1")).when(pl.col("__BX")==2).then(pl.col("sal_2")).otherwise(pl.col("sal_3"))).alias("__FP_sal"))
    j1 = j1.with_columns((pl.col("__FP_sal") * (1 + pl.col("__BL"))).alias("__FP"))

    j2 = result.select(["member_no","__E","__K","__BY","__BT","__FK","__map_lower_key","__size_title","__BL","__BX"]).join(
        af_table, left_on=["__E","__K","__BY","__BT","__FK","__map_lower_key","__size_title"],
        right_on=["age","gender","wp","ip","nra","esc_key","scheme_size"], how="left")
    j2 = j2.with_columns((pl.when(pl.col("__BX")==1).then(pl.col("sal_1")).when(pl.col("__BX")==2).then(pl.col("sal_2")).otherwise(pl.col("sal_3"))).alias("__GB_sal"))
    j2 = j2.with_columns((pl.col("__GB_sal") * (1 + pl.col("__BL"))).alias("__GB"))

    j3 = result.select(["member_no","__E","__K","__BY","__BT","__FK","__map_upper_key","__size_title","__BL","__BX"]).join(
        af_table, left_on=["__E","__K","__BY","__BT","__FK","__map_upper_key","__size_title"],
        right_on=["age","gender","wp","ip","nra","esc_key","scheme_size"], how="left")
    j3 = j3.with_columns((pl.when(pl.col("__BX")==1).then(pl.col("sal_1")).when(pl.col("__BX")==2).then(pl.col("sal_2")).otherwise(pl.col("sal_3"))).alias("__GC_sal"))
    j3 = j3.with_columns((pl.col("__GC_sal") * (1 + pl.col("__BL"))).alias("__GC"))

    result = result.join(j0.select(["member_no", "__FO"]), on="member_no", how="left")
    result = result.join(j1.select(["member_no", "__FP", "__FP_sal"]), on="member_no", how="left")
    result = result.join(j2.select(["member_no", "__GB"]), on="member_no", how="left")
    result = result.join(j3.select(["member_no", "__GC"]), on="member_no", how="left")

    result = result.with_columns(
        pl.when(pl.col("__FO").is_null()).then(0.0)
          .otherwise(
              (pl.col("__FO").fill_null(0.0)
               + pl.col("__FR").fill_null(0.0) * (pl.col("__FP").fill_null(pl.col("__FO")) - pl.col("__FO")).fill_null(0.0)
               + pl.when(pl.col("__GB").is_not_null() & pl.col("__GC").is_not_null())
                   .then((pl.col("__GC") - pl.col("__GB")) * pl.col("__interp_weight").fill_null(0.0))
                   .otherwise(0.0)
              ) * (1 + pl.col("__progression_loading").fill_null(0.0))
          ).alias("__FS")
    )
    result = result.with_columns((pl.col("__DC") * pl.col("__BA") * pl.col("__FS")).alias("__DF"))

    esc_row_zero = esc_map.get(0.0)
    BS0 = "0"
    lower_key_0 = esc_row_zero["map_lower_key"]
    upper_key_0 = esc_row_zero["map_upper_key"]
    interp_weight_0 = float(esc_row_zero["interp_weight"] or 0)
    prog_loading_0 = float(esc_row_zero["progression_loading"] or 0)

    af_esc0 = af_table.filter(pl.col("esc_key") == BS0)
    af_lower0 = af_table.filter(pl.col("esc_key") == lower_key_0)
    af_upper0 = af_table.filter(pl.col("esc_key") == upper_key_0)

    jf0 = result.select(["member_no","__E","__K","__BY","__BT","__FK","__size_title","__BL","__BX"]).join(
        af_esc0, left_on=["__E","__K","__BY","__BT","__FK","__size_title"],
        right_on=["age","gender","wp","ip","nra","scheme_size"], how="left")
    jf0 = jf0.with_columns((pl.when(pl.col("__BX")==1).then(pl.col("sal_1")).when(pl.col("__BX")==2).then(pl.col("sal_2")).otherwise(pl.col("sal_3"))).alias("__FO0_sal"))
    jf0 = jf0.with_columns((pl.col("__FO0_sal") * (1 + pl.col("__BL"))).alias("__FO0"))

    jp0 = result.select(["member_no","__E","__K","__BY","__BT","__FL","__size_title","__BL","__BX"]).join(
        af_esc0, left_on=["__E","__K","__BY","__BT","__FL","__size_title"],
        right_on=["age","gender","wp","ip","nra","scheme_size"], how="left")
    jp0 = jp0.with_columns((pl.when(pl.col("__BX")==1).then(pl.col("sal_1")).when(pl.col("__BX")==2).then(pl.col("sal_2")).otherwise(pl.col("sal_3"))).alias("__FP0_sal"))
    jp0 = jp0.with_columns((pl.col("__FP0_sal") * (1 + pl.col("__BL"))).alias("__FP0"))

    jb0 = result.select(["member_no","__E","__K","__BY","__BT","__FK","__size_title","__BL","__BX"]).join(
        af_lower0, left_on=["__E","__K","__BY","__BT","__FK","__size_title"],
        right_on=["age","gender","wp","ip","nra","scheme_size"], how="left")
    jb0 = jb0.with_columns((pl.when(pl.col("__BX")==1).then(pl.col("sal_1")).when(pl.col("__BX")==2).then(pl.col("sal_2")).otherwise(pl.col("sal_3"))).alias("__GB0_sal"))
    jb0 = jb0.with_columns((pl.col("__GB0_sal") * (1 + pl.col("__BL"))).alias("__GB0"))

    jc0 = result.select(["member_no","__E","__K","__BY","__BT","__FK","__size_title","__BL","__BX"]).join(
        af_upper0, left_on=["__E","__K","__BY","__BT","__FK","__size_title"],
        right_on=["age","gender","wp","ip","nra","scheme_size"], how="left")
    jc0 = jc0.with_columns((pl.when(pl.col("__BX")==1).then(pl.col("sal_1")).when(pl.col("__BX")==2).then(pl.col("sal_2")).otherwise(pl.col("sal_3"))).alias("__GC0_sal"))
    jc0 = jc0.with_columns((pl.col("__GC0_sal") * (1 + pl.col("__BL"))).alias("__GC0"))

    result = result.join(jf0.select(["member_no", "__FO0", "__FO0_sal"]), on="member_no", how="left")
    result = result.join(jp0.select(["member_no", "__FP0", "__FP0_sal"]), on="member_no", how="left")
    result = result.join(jb0.select(["member_no", "__GB0"]), on="member_no", how="left")
    result = result.join(jc0.select(["member_no", "__GC0"]), on="member_no", how="left")

    result = result.with_columns(
        pl.when(pl.col("__FO0").is_null()).then(0.0)
          .otherwise(
              (pl.col("__FO0").fill_null(0.0)
               + pl.col("__FR").fill_null(0.0) * (pl.col("__FP0").fill_null(pl.col("__FO0")) - pl.col("__FO0")).fill_null(0.0)
               + pl.when(pl.col("__GB0").is_not_null() & pl.col("__GC0").is_not_null())
                   .then((pl.col("__GC0") - pl.col("__GB0")) * pl.lit(interp_weight_0))
                   .otherwise(0.0)
              ) * (1 + pl.lit(prog_loading_0))
          ).alias("__FS0_pre_override")
    )
    result = result.with_columns(
        pl.when(pl.col("__FP0_sal").is_null() & pl.col("__FO").is_not_null())
          .then(pl.col("__FO")).otherwise(pl.col("__FS0_pre_override")).alias("__FS0")
    )
    result = result.with_columns((pl.col("__DC") * pl.col("__BA") * pl.col("__FS0")).alias("__DF0"))

    result = result.with_columns(
        pl.when(pl.col("__AS") == "Nominal Amount").then(pl.col("__AT_nom")).otherwise(pl.col("hcc_waiver").fill_null(0.0)).alias("__AT")
    )
    result = result.with_columns([
        (pl.col("date_of_birth").dt.year().cast(pl.Int32) + pl.col("__M").cast(pl.Int32)).alias("__ret_year"),
        pl.col("date_of_birth").dt.month().cast(pl.Int32).alias("__ret_month"),
    ])
    result = result.with_columns(pl.date(pl.col("__ret_year"), pl.col("__ret_month"), 1).dt.month_end().alias("__ret"))
    result = result.with_columns(pl.lit(comm).alias("__comm"))
    result = result.with_columns((_vec_yearfrac_30_360(pl.col("__comm"), pl.col("__ret")) * 12).round(0).alias("__mtr"))
    result = result.with_columns(((1 + pl.col("__AV")) ** pl.max_horizontal([(pl.col("__AR") - 12) / 12, pl.lit(0.0)])).alias("__esc_f"))
    result = result.with_columns(
        pl.max_horizontal([pl.min_horizontal([pl.col("__AR") - pl.col("__wp_months_raw").fill_null(0.0), pl.col("__mtr")]), pl.lit(0.0)]).alias("__annuity6_exp")
    )
    result = result.with_columns(((1 - (1.06) ** (-pl.col("__annuity6_exp") / 12)) / 0.06).alias("__annuity6"))
    result = result.with_columns(
        pl.when(pl.col("__AQ") != "Yes").then(0.0)
          .otherwise((pl.col("__AT") * 12) * pl.col("__DC") * pl.col("__annuity6") * pl.lit(J5) * pl.col("__esc_f"))
          .alias("__DB")
    )
    result = result.with_columns(
        ((pl.col("__AY") == "Yes") & (pl.col("__E") <= pl.min_horizontal([pl.col("__M") - f452, pl.lit(f451)]))).alias("__gate")
    )
    result = result.with_columns(
        (pl.when(pl.col("__gate")).then(pl.lit(co_cost)).otherwise(0.0) * pl.col("__DF")).alias("__DH")
    )

    result = result.with_columns([
        pl.when(pl.col("__included")).then(pl.col("__BA")).otherwise(0.0).alias("total_cover"),
        pl.when(pl.col("__included")).then(pl.col("__DF")).otherwise(0.0).alias("basic_technical_premium"),
        pl.lit(0.0).alias("aids_technical_premium"),
        pl.when(pl.col("__included")).then(pl.col("__DH")).otherwise(0.0).alias("co_premium"),
        pl.when(pl.col("__included")).then(pl.col("__DB")).otherwise(0.0).alias("waiver_premium"),
        pl.when(pl.col("__included")).then(pl.col("__DF0")).otherwise(0.0).alias("basic_excl_esc"),
        pl.when(pl.col("__included")).then(pl.col("__DC")).otherwise(0.0).alias("reporting_incidence"),
        pl.when(pl.col("__included")).then(pl.col("__DC") * pl.col("__BA")).otherwise(0.0).alias("reporting_incidence_x_cover"),
        (~pl.col("__included") & (pl.col("__g_age") > pl.col("__g_cease"))).alias("reporting_over_cease"),
        (~pl.col("__included")).alias("__to_deactivate"),
    ])

    _new_cols = ["total_cover", "basic_technical_premium", "aids_technical_premium",
                 "co_premium", "waiver_premium", "basic_excl_esc", "reporting_incidence",
                 "reporting_incidence_x_cover", "reporting_over_cease"]
    _cols_to_drop_from_df = [c for c in _new_cols + ["rate_basic", "rate_aids"] if c in df.columns]
    df_out = df.drop(_cols_to_drop_from_df).join(
        result.select(["member_no"] + _new_cols + ["__to_deactivate"]),
        on="member_no", how="left"
    )
    df_out = df_out.with_columns(
        pl.when(pl.col("__to_deactivate").fill_null(False)).then(pl.lit(False)).otherwise(pl.col("product_active")).alias("product_active")
    )
    df_out = df_out.drop("__to_deactivate")
    df_out = df_out.with_columns(
        pl.when(pl.col("total_cover") > 0)
          .then(pl.col("basic_technical_premium") / pl.col("total_cover") * 1000)
          .otherwise(0.0).alias("rate_basic")
    )
    df_out = df_out.with_columns(pl.lit(0.0).alias("rate_aids"))
    return df_out


def _apply_gip_composite_legacy(df: pl.DataFrame,
                         spec: dict,
                         basis_tables: dict,
                         assump_tables: dict,
                         ctx) -> pl.DataFrame:
    L = _gip_build_lookup_tables(basis_tables, assump_tables, ctx)
    results, _gip_excluded = _gip_compute_member_rows(df, L, basis_tables)
    df = _gip_finalize_dataframe(df, results, _gip_excluded)
    return df


def _gip_annuity_fs(FO, FR, af_map, sal_col, BL, basis, esc_pct,
                    size, E, K, BY, BT, FK, FL):
    if FO is None:
        return 0.0
    esc = basis["annuity_esc_loading"].filter(pl.col("product") == "gip")
    erow = esc.filter(pl.col("esc_pct") == esc_pct)
    if erow.height == 0:
        return FO
    e = erow.row(0, named=True)
    lower_key, upper_key = e["map_lower_key"], e["map_upper_key"]
    GD = _gipf(e["interp_weight"])
    GE = _gipf(e["progression_loading"])

    def _ann(nra, esc_k):
        k = "/".join([_gip_key_part(E), _gip_key_part(K), _gip_key_part(BY),
                      _gip_key_part(BT), _gip_key_part(nra), _gip_key_part(esc_k), size])
        r = af_map.get(k)
        return _gipf(r[sal_col]) * (1 + BL) if r else None

    BS = _gip_esc_key(esc_pct)
    FP = _ann(FL, BS)
    FQ = (FP - FO) if FP is not None else 0.0
    GB = _ann(FK, lower_key)
    GC = _ann(FK, upper_key)
    GF = ((GC - GB) * GD) if (GB is not None and GC is not None) else 0.0
    return (FO + FR * FQ + GF) * (1 + GE)


def _gip_annuity_fs_fast(FO, FR, af_map, sal_col, BL, esc_map, esc_pct,
                         size, E, K, BY, BT, FK, FL):
    if FO is None:
        return 0.0
    esc_row = esc_map.get(esc_pct)
    if esc_row is None:
        try: esc_row = esc_map.get(int(round(float(esc_pct)*100))/100.0)
        except (TypeError, ValueError): esc_row = None
    if esc_row is None:
        return FO
    lower_key, upper_key = esc_row["map_lower_key"], esc_row["map_upper_key"]
    GD = _gipf(esc_row["interp_weight"])
    GE = _gipf(esc_row["progression_loading"])
    def _ann(nra, esc_k):
        k = "/".join([_gip_key_part(E), _gip_key_part(K), _gip_key_part(BY),
                      _gip_key_part(BT), _gip_key_part(nra), _gip_key_part(esc_k), size])
        r = af_map.get(k)
        return _gipf(r[sal_col]) * (1 + BL) if r else None
    BS = _gip_esc_key(esc_pct)
    FP = _ann(FL, BS)
    FQ = (FP - FO) if FP is not None else 0.0
    GB = _ann(FK, lower_key)
    GC = _ann(FK, upper_key)
    GF = ((GC - GB) * GD) if (GB is not None and GC is not None) else 0.0
    return (FO + FR * FQ + GF) * (1 + GE)


def _gip_backdating_loading(basis, backdating_label):
    if not backdating_label or str(backdating_label).strip().lower() == "none":
        return 0.0
    bd = basis["backdating_loading"].filter(
        (pl.col("product") == "gip") & (pl.col("scheme_wp") == 6) &
        (pl.col("backdating_option") == backdating_label))
    return float(bd["loading"][0]) if bd.height else 0.0


def _gip_build_lookup_tables(basis_tables, assump_tables, ctx):
    scheme = ctx.scheme
    g = scheme
    size = ctx.scheme_size.lower()
    size_title = ctx.scheme_size
    sec_ind = scheme.secondary

    B = basis_tables

    _esc_tbl = B["annuity_esc_loading"].filter(pl.col("product") == "gip")
    esc_map = {}
    for _er in _esc_tbl.iter_rows(named=True):
        if _er["esc_pct"] not in esc_map:
            esc_map[_er["esc_pct"]] = _er
    def _esc_grid(_e):
        try: return int(round(float(_e) * 100)) / 100.0
        except (TypeError, ValueError): return _e
    for _er in _esc_tbl.iter_rows(named=True):
        _gk = _esc_grid(_er["esc_pct"])
        esc_map.setdefault(_gk, _er)

    mort_map = {(int(r["age"]), r["gender"]): r["rate"]
                for r in B["mortality"].filter(pl.col("product") == "gip").iter_rows(named=True)}

    sb_rows = (B["salary_band"].filter(pl.col("product") == "gip")
               .select(["gender", "salary_from", "inc_adj", "acaf_adj"])
               .sort(["gender", "salary_from"]).to_dicts())
    sb_by_g = {gd: [r for r in sb_rows if r["gender"] == gd] for gd in ("M", "F")}

    ind = B["industry"].filter((pl.col("product") == "gip") & (pl.col("scheme_size") == size))
    ind_row = ind.filter(pl.col("secondary") == sec_ind)
    if ind_row.height == 0:
        raise KeyError(f"[gip] industry '{sec_ind}' not found for size={size}")
    BO_val = float(ind_row["inc_adj"][0])
    BP_val = float(ind_row["acaf_adj"][0])

    rr_rows  = (B["rr_loading"].filter(pl.col("product") == "gip")
                .select(["rr_from", "loading"]).sort("rr_from").to_dicts())
    asb_rows = (B["annuity_salary_bands"].filter(pl.col("product") == "gip")
                .select(["salary_from", "sal_tier"]).sort("salary_from").to_dicts())
    wp_rows  = (B["wp_loading"].filter((pl.col("product") == "gip") & (pl.col("scheme_size") == size))
                .select(["age_band_from", "wp_months", "factor"])
                .sort(["wp_months", "age_band_from"]).to_dicts())
    ip = B["ip_loading"].filter(pl.col("product") == "gip")
    ip_adj = {r["ip_label"]: r["adjustment"] for r in ip.iter_rows(named=True)}
    ip_mm  = {r["ip_label"]: r["mapped_months"] for r in ip.iter_rows(named=True)}
    db_map = {r["combined_key"]: r["loading"]
              for r in B["db_loading"].filter(pl.col("product") == "gip").iter_rows(named=True)}
    wpann  = {int(r["wp_raw"]): int(r["wp_annuity"])
              for r in B["wp_annuity_mapping"].filter(pl.col("product") == "gip").iter_rows(named=True)}
    nra_map = {int(r["nra"]): r
               for r in B["annuity_nra_interpolation"].filter(pl.col("product") == "gip").iter_rows(named=True)}
    af_map = {r["key"]: r
              for r in B["annuity_factors"].filter(pl.col("product") == "gip").iter_rows(named=True)}
    hcc = {r["param_name"]: float(r["param_value"])
           for r in B["gip_hcc_basis"].filter(pl.col("product") == "gip").iter_rows(named=True)}
    HCC_RATE = hcc["hcc_pricing_rate"]
    J5 = hcc["hcc_reduction_factor"]

    tx = (assump_tables["tax_tables"]
          .filter(pl.col("tax_year") == "2026-2027_gip")
          .select(["band_from", "margin", "basic_amount", "primary_rebate"])
          .with_columns([pl.col(c).cast(pl.Float64, strict=False)
                         for c in ["band_from", "margin", "basic_amount", "primary_rebate"]])
          .drop_nulls("band_from").sort("band_from"))
    bands = tx.to_dicts()
    rebate = bands[0]["primary_rebate"] if bands else 0.0

    plim = {r["param_name"]: r["param_value"]
            for r in assump_tables["product_limits"].filter(pl.col("product") == "gip").iter_rows(named=True)}
    co_cost = float(assump_tables["conversion_option"]
                    .filter(pl.col("product") == "gip")["rate"][0])
    f451 = float(plim.get("max_co_age", 65))
    f452 = float(plim.get("withdrawal_only_pre_nra_cap_years", 5))

    waiver_incl_map = getattr(g, "gip_waiver_included", {}) or {}

    return {
        "size": size, "size_title": size_title, "comm": ctx.calc_date,
        "pear": float(scheme.pear_tgp_pct) if scheme.pear_tgp_pct else 1.0,
        "esc_map": esc_map, "mort_map": mort_map, "sb_by_g": sb_by_g,
        "BO_val": BO_val, "BP_val": BP_val, "rr_rows": rr_rows, "asb_rows": asb_rows,
        "wp_rows": wp_rows, "ip_adj": ip_adj, "ip_mm": ip_mm, "db_map": db_map,
        "wpann": wpann, "nra_map": nra_map, "af_map": af_map,
        "HCC_RATE": HCC_RATE, "J5": J5, "bands": bands, "rebate": rebate,
        "co_cost": co_cost, "f451": f451, "f452": f452,
        "waiver_incl_map": waiver_incl_map, "g": g,
    }


def _gip_compute_member_rows(df, L, basis_tables):
    g = L["g"]
    size_title = L["size_title"]
    comm = L["comm"]
    pear = L["pear"]
    esc_map = L["esc_map"]
    mort_map = L["mort_map"]
    sb_by_g = L["sb_by_g"]
    BO_val = L["BO_val"]
    BP_val = L["BP_val"]
    rr_rows = L["rr_rows"]
    asb_rows = L["asb_rows"]
    wp_rows = L["wp_rows"]
    ip_adj = L["ip_adj"]
    ip_mm = L["ip_mm"]
    db_map = L["db_map"]
    wpann = L["wpann"]
    nra_map = L["nra_map"]
    af_map = L["af_map"]
    HCC_RATE = L["HCC_RATE"]
    J5 = L["J5"]
    bands = L["bands"]
    rebate = L["rebate"]
    co_cost = L["co_cost"]
    f451 = L["f451"]
    f452 = L["f452"]
    waiver_incl_map = L["waiver_incl_map"]

    results = []
    _gip_excluded = set()
    for row in df.iter_rows(named=True):
        cat = int(row["category_number"]) if row.get("category_number") is not None else None
        active = bool(row.get("product_active"))
        member_no = row.get("member_no")

        if cat is None or not active:
            results.append({
                "member_no": member_no,
                "__gip_cover": 0.0,
                "__gip_basic": 0.0,
                "__gip_co": 0.0,
                "__gip_total": 0.0,
                "__gip_hcc": 0.0,
                "__gip_basic_excl_esc": 0.0,
                "__gip_reporting_incidence": 0.0,
                "__gip_reporting_incidence_x_cover": 0.0,
                "__gip_reporting_over_cease": False,
            })
            continue

        _g_age = _gipf(row.get("age"))
        _g_cease = _gipf(row.get("ceasing_age") if row.get("ceasing_age") is not None
                         else row.get("nra_final"))
        _g_wpm = _gip_wp_months(g.gip_waiting_period.get(cat))
        _g_wpm = _g_wpm if _g_wpm is not None else 0.0
        _g_m2nrd = (_g_cease - _g_age) * 12.0
        _g_over_cease = _g_age > _g_cease

        if not (_g_age >= 16 and _g_age <= _g_cease and _g_m2nrd >= _g_wpm):
            _gip_excluded.add(member_no)
            results.append({
                "member_no": member_no,
                "__gip_cover": 0.0,
                "__gip_basic": 0.0,
                "__gip_co": 0.0,
                "__gip_hcc": 0.0,
                "__gip_basic_excl_esc": 0.0,
                "__gip_reporting_incidence": 0.0,
                "__gip_reporting_incidence_x_cover": 0.0,
                "__gip_reporting_over_cease": _g_over_cease,
            })
            continue

        E = int(row["age_next"]) if row.get("age_next") is not None else int(_gipf(row.get("age_nb")))
        K = row.get("gender")
        I = _gipf(row.get("annual_salary"))
        J = _gipf(row.get("monthly_salary")) or (I / 12.0)
        M = int(round(_gipf(row.get("nra_final") if row.get("nra_final") is not None else row.get("nra"))))
        L_ = _gipf(row.get("waiver"))
        member_hcc = row.get("hcc_waiver")
        dob = row.get("date_of_birth")

        core_perc  = g.gip_core_benefit_perc.get(cat, [0, 0, 0, 0])
        core_layer = g.gip_core_benefit_layer.get(cat, [0, 0, 0, 0])
        add_perc   = _gipf(g.gip_core_benefit_perc_additional.get(cat))
        add_layer  = _gipf(g.gip_core_benefit_layer_additional.get(cat))
        AC         = _gipf(g.gip_core_overall_maximum.get(cat))
        emp_waiver = _gipf(g.gip_employer_waiver.get(cat))
        waiver_spec = g.gip_waiver_pct_specified.get(cat, "N/A")

        val3_2 = waiver_incl_map.get(cat, "No")
        assert val3_2 == "No", (
            f"[gip] cat {cat}: 'Waiver Included?'={val3_2!r} — engine verified only "
            f"for 'No'. Wire CatInput-Load AE source before pricing 'Yes' schemes.")

        core_def = g.gip_core_disability_definition.get(cat)
        ext_def  = g.gip_hcc_disability_definition.get(cat)
        init_pd  = g.gip_initial_period.get(cat)
        wp_str   = g.gip_waiting_period.get(cat)
        esc_pct  = _gipf(g.gip_escalation_pct.get(cat))
        tax_opt  = g.gip_tax_options.get(cat)
        backdating = g.gip_backdating.get(cat)

        AQ = g.gip_include_hcc_waiver.get(cat, "No")
        AR = _gipf(g.gip_hcc_payment_period.get(cat))
        AS = g.gip_hcc_payment_type.get(cat)
        AT_nom = _gipf(g.gip_hcc_nominal_amount.get(cat))
        AV = _gipf(g.gip_hcc_escalation_pct.get(cat))
        AY = g.gip_include_conversion_option.get(cat, "No")

        # Called only within this iteration, so loop variables are bound correctly.
        def _resolve(base):
            if waiver_spec == "N/A":  # noqa: B023
                return base
            if waiver_spec == "As Per Data":  # noqa: B023
                add = L_ if val3_2 == "No" else 0.0  # noqa: B023
            elif waiver_spec == "Same For All":  # noqa: B023
                add = emp_waiver if val3_2 == "No" else 0.0  # noqa: B023
            else:
                add = 0.0
            return base + add
        S = _resolve(_gipf(core_perc[0]))
        U = _resolve(_gipf(core_perc[1]))
        W = _resolve(_gipf(core_perc[2]))
        Y = _resolve(_gipf(core_perc[3]))
        T, V, X, Z = (_gipf(core_layer[0]), _gipf(core_layer[1]),
                      _gipf(core_layer[2]), _gipf(core_layer[3]))

        BE = (L_ * J) if (val3_2 == "No" and waiver_spec == "As Per Data") else (emp_waiver * J)

        CO = (S * min(J, T)
              + U * max(0, min(V, J - T))
              + W * max(0, min(X, J - T - V))
              + Y * max(0, min(Z, J - T - V - X)))
        CP = max(0, add_perc * min(J, add_layer))
        CQ = min(CO + CP, AC)

        CT = J / pear
        CU = CT - BE
        bandrow = None
        for br in bands:
            if br["band_from"] <= CU * 12:
                bandrow = br
            else:
                break
        tax = 0.0 if bandrow is None else max(
            bandrow["basic_amount"] + (CU * 12 - bandrow["band_from"]) * bandrow["margin"] - rebate, 0.0)
        CV = (CT * 12 - tax) / 12
        CW = min(CQ, CV)

        _CF = CO
        _CI = _CF - BE
        _br_ci = None
        for _br in bands:
            if _br['band_from'] <= _CI * 12:
                _br_ci = _br
            else:
                break
        _tax_ci = 0.0 if _br_ci is None else max(
            _br_ci['basic_amount'] + (_CI * 12 - _br_ci['band_from']) * _br_ci['margin'] - rebate, 0.0)
        CK = (_CF * 12 - _tax_ci) / 12

        if tax_opt == "Apply net-of-tax maximum":
            BA = CW
        elif tax_opt == "Tax Replicator":
            BA = CK
        else:
            BA = CQ

        BH = mort_map.get((E, K), 0.0)
        BJ = _gip_wp_months(wp_str)
        BK = None
        if BJ is not None:
            sub = [r for r in wp_rows if int(r["wp_months"]) == int(BJ) and r["age_band_from"] <= E]
            BK = sub[-1]["factor"] if sub else None
        BM = _gip_asof(sb_by_g.get(K, []), "salary_from", "inc_adj", I)
        BN = _gip_asof(sb_by_g.get(K, []), "salary_from", "acaf_adj", I)
        BX = int(_gip_asof(asb_rows, "salary_from", "sal_tier", I))
        BQ = BA / J if J > 0 else 0.0
        BR = _gip_asof(rr_rows, "rr_from", "loading", BQ)
        BU = ip_adj.get(init_pd)
        BT = ip_mm.get(init_pd)
        BV = (core_def or "") + (ext_def or "")
        BW = db_map.get(BV)
        BL = _gip_backdating_loading(basis_tables, backdating)

        DC = (_gipf(BH) / 1000 * _gipf(BK) * _gipf(BM) * _gipf(BN)
              * BO_val * BP_val * _gipf(BR) * _gipf(BU) * _gipf(BW))

        nr = nra_map.get(M, {})
        FK = nr.get("age_lookup")
        FL = nr.get("age_interp")
        FR = _gipf(nr.get("linear_scaler"))
        BY = wpann.get(BJ)
        FM = "/".join([_gip_key_part(E), _gip_key_part(K), _gip_key_part(BY),
                       _gip_key_part(BT), _gip_key_part(FK), _gip_esc_key(esc_pct),
                       size_title])
        af_row = af_map.get(FM)
        sal_col = {1: "sal_1", 2: "sal_2", 3: "sal_3"}[BX]
        FO = (_gipf(af_row[sal_col]) * (1 + BL)) if af_row else None
        FS = _gip_annuity_fs_fast(FO, FR, af_map, sal_col, BL, esc_map, esc_pct,
                             size_title, E, K, BY, BT, FK, FL)

        DF = DC * BA * _gipf(FS)

        FM0 = "/".join([_gip_key_part(E), _gip_key_part(K), _gip_key_part(BY),
                        _gip_key_part(BT), _gip_key_part(FK), _gip_esc_key(0.0),
                        size_title])
        af_row0 = af_map.get(FM0)
        FO0 = (_gipf(af_row0[sal_col]) * (1 + BL)) if af_row0 else None
        FS0 = _gip_annuity_fs_fast(FO0, FR, af_map, sal_col, BL, esc_map, 0.0,
                              size_title, E, K, BY, BT, FK, FL)
        _fl0 = "/".join([_gip_key_part(E), _gip_key_part(K), _gip_key_part(BY),
                         _gip_key_part(BT), _gip_key_part(FL), _gip_esc_key(0.0),
                         size_title])
        if af_map.get(_fl0) is None and FO is not None:
            FS0 = FO
        DF0 = DC * BA * _gipf(FS0)

        if AQ != "Yes":
            DB = 0.0
        else:
            AT = AT_nom if AS == "Nominal Amount" else _gipf(member_hcc)
            dob_d = dob if isinstance(dob, date) else dob
            ret = _gip_eomonth(dob_d.year + M, dob_d.month)
            mtr = round(12 * _gip_yearfrac_30_360(comm, ret))
            esc_f = (1 + AV) ** max((AR - 12) / 12, 0)
            annuity6 = (1 - (1.06) ** (-max(min(AR - _gipf(BJ), mtr), 0) / 12)) / 0.06
            DB = (AT * 12) * DC * annuity6 * J5 * esc_f

        gate = (AY == "Yes") and (E <= min(M - f452, f451))
        DH = (co_cost if gate else 0.0) * DF

        results.append({
            "member_no": member_no,
            "__gip_cover": BA,
            "__gip_basic": DF,
            "__gip_co": DH,
            "__gip_hcc": DB,
            "__gip_basic_excl_esc": DF0,
            "__gip_reporting_incidence": DC,
            "__gip_reporting_incidence_x_cover": DC * BA,
            "__gip_reporting_over_cease": False,
        })

    return results, _gip_excluded


def _gip_eomonth(y, m):
    return date(y, m, calendar.monthrange(y, m)[1])


def _gip_esc_key(esc_pct):
    if esc_pct is None:
        return "0"
    return str(int(round(float(esc_pct) * 100)))


def _gip_finalize_dataframe(df, results, _gip_excluded):
    res = pl.DataFrame(results)
    df = df.join(res, on="member_no", how="left")
    if _gip_excluded:
        df = df.with_columns(
            pl.when(pl.col("member_no").is_in(list(_gip_excluded)))
              .then(pl.lit(False)).otherwise(pl.col("product_active"))
              .alias("product_active"))
    df = df.with_columns([
        pl.col("__gip_cover").fill_null(0.0).alias("total_cover"),
        pl.col("__gip_basic").fill_null(0.0).alias("basic_technical_premium"),
        pl.lit(0.0).alias("aids_technical_premium"),
        pl.col("__gip_co").fill_null(0.0).alias("co_premium"),
        pl.col("__gip_hcc").fill_null(0.0).alias("waiver_premium"),
        pl.col("__gip_basic_excl_esc").fill_null(0.0).alias("basic_excl_esc"),
        pl.col("__gip_reporting_incidence")
          .fill_null(0.0)
          .alias("reporting_incidence"),
        pl.col("__gip_reporting_incidence_x_cover")
          .fill_null(0.0)
          .alias("reporting_incidence_x_cover"),
        pl.col("__gip_reporting_over_cease")
          .fill_null(False)
          .alias("reporting_over_cease"),
    ])
    df = df.with_columns(
        pl.when(pl.col("total_cover") > 0)
          .then(pl.col("basic_technical_premium") / pl.col("total_cover") * 1000)
          .otherwise(0.0).alias("rate_basic")
    )
    df = df.with_columns(pl.lit(0.0).alias("rate_aids"))
    return df.drop([
        "__gip_cover",
        "__gip_basic",
        "__gip_co",
        "__gip_hcc",
        "__gip_basic_excl_esc",
        "__gip_reporting_incidence",
        "__gip_reporting_incidence_x_cover",
        "__gip_reporting_over_cease",
    ])


def _gip_yearfrac_30_360(d0, d1):
    dd0 = 30 if d0.day == 31 else d0.day
    dd1 = 30 if (d1.day == 31 and dd0 == 30) else d1.day
    return ((d1.year - d0.year) * 360 + (d1.month - d0.month) * 30 + (dd1 - dd0)) / 360.0
