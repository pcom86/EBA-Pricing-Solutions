from __future__ import annotations

import logging

import polars as pl

from .constants import (
    CB_PRODUCTS,
    DEFAULT_COMMISSION_PCT,
    DEFAULT_VAT_PCT,
    N_CATEGORIES,
    PO_PRODUCTS,
    PO_TECH_ADDONS,
)
from .demographics import _tt_active, compute_eoh_for_scheme

logger = logging.getLogger(__name__)


_PKEY_TO_MARGIN_PRODUCT = {
    "gla":                 "gla",
    "lsdb":                "lsdb",
    "ci":                  "ci",
    "spouse_gla":          "gla",
    "spouse_lsdb":         "lsdb",
    "terminal":            "ti",
    "accident_death":      "adb",
    "accident_disability": "accdb",
    "dismemberment":       "dismem",
}


def _profit_rate_for(assump, pkey: str) -> float:
    tbl = assump.tables.get("profit_margins")
    if tbl is None:
        return 0.0
    mkey = _PKEY_TO_MARGIN_PRODUCT.get(pkey)
    if mkey is None:
        return 0.0
    row = tbl.filter(
        (pl.col("product") == mkey) & (pl.col("margin_type") == "standard")
    )
    if row.height == 0:
        return 0.0
    try:
        return float(row["margin"][0])
    except (TypeError, ValueError):
        return 0.0


def _admin_rate_for(assump, pkey: str, scheme) -> float:
    tbl = assump.tables.get("admin_fees")
    if tbl is None:
        return 0.0

    rating = getattr(scheme, "rating", {}) or {}
    if pkey == "lsdb":
        r = str(rating.get("lsdb", "")).strip().lower()
        variant = "standalone" if r == "standalone" else "accelerator"
    elif pkey == "ci":
        r = str(rating.get("ci", "")).strip().lower()
        variant = "standalone" if r == "standalone" else "accelerator"
    else:
        variant = "default"

    row = tbl.filter(
        (pl.col("product") == pkey) & (pl.col("variant") == variant)
    )
    if row.height == 0:
        row = tbl.filter(
            (pl.col("product") == pkey) & (pl.col("variant") == "default")
        )
    if row.height == 0:
        return 0.0
    try:
        return float(row["admin"][0])
    except (TypeError, ValueError):
        return 0.0


def _rate_basis_for(scheme, pkey: str) -> float:
    rb_map = getattr(scheme, "rate_basis_by_product", {}) or {}
    try:
        v = rb_map.get(pkey, 1.0)
        return float(v) if v is not None else 1.0
    except (TypeError, ValueError):
        return 1.0


def _freq_divisor_for(scheme) -> float:
    freq = str(getattr(scheme, "premium_frequency", "Monthly")).strip().lower()
    return 12.0 if freq == "monthly" else 1.0


_BROKER_COMMISSION_SCALES = {
    "South Africa": [(0.075, 200_000), (0.050, 100_000), (0.030, 300_000),
                     (0.020, 1_400_000), (0.010, None)],
    "Namibia":      [(0.075, 126_500), (0.050,  91_500), (0.030, 253_000),
                     (0.020,   909_000), (0.010, None)],
    "eSwatini":     [(0.075,  90_000), (0.050,  70_000), (0.030, 160_000),
                     (0.020,   660_000), (0.010, None)],
}

_REP_COMMISSION_SCALES = {
    "South Africa": [(0.060, 110_000), (0.040, 80_000), (0.023, 220_000),
                     (0.015, 790_000), (0.007, None)],
    "Namibia":      [(0.060, 110_000), (0.040, 80_000), (0.023, 220_000),
                     (0.015, 790_000), (0.007, None)],
    "eSwatini":     [(0.060,  90_000), (0.040, 70_000), (0.023, 160_000),
                     (0.015, 660_000), (0.007, None)],
}


def _vat_on_commission_for(scheme) -> float:
    raw = getattr(scheme, "include_vat_commission", "Yes")
    if str(raw).strip().lower() in ("yes", "y", "true", "1"):
        return 0.15
    return 0.0


def _commission_scale_for(scheme):
    country = str(getattr(scheme, "country_scale", None) or "South Africa").strip()
    intermediary = str(getattr(scheme, "intermediary", "") or "").strip().lower()
    is_rep = intermediary in ("rep", "representative", "direct")
    scales = _REP_COMMISSION_SCALES if is_rep else _BROKER_COMMISSION_SCALES
    return scales.get(country, scales["South Africa"])


def _scheme_size_threshold(assump) -> int:
    try:
        return int(float(assump.globals.get("scheme_size_large_threshold", 400)))
    except (TypeError, ValueError, AttributeError):
        return 400


def _resolve_commission_rate(
    all_results,
    scheme,
    assump,
    subtotal_provider,
) -> float:
    gla_df = all_results.get("gla")
    if gla_df is not None and "product_active" in gla_df.columns:
        member_count = gla_df.filter(pl.col("product_active")).height
    elif gla_df is not None:
        member_count = gla_df.height
    else:
        member_count = 0

    threshold = _scheme_size_threshold(assump)
    is_large = member_count >= threshold

    scale = _commission_scale_for(scheme)
    vat_pct = _vat_on_commission_for(scheme)

    if is_large:
        total_net = 0.0
        for pkey in CB_PRODUCTS:
            try:
                total_net += float(subtotal_provider(pkey) or 0.0)
            except Exception as e:  # noqa: BLE001 - per-product subtotal is best-effort
                logger.warning(f"[commission] subtotal failed for {pkey}: {e}")

        if total_net <= 0:
            return 0.0
        return _tiered_commission_on_net(total_net, scale) / total_net

    first_tier_rate = scale[0][0]
    denom = 1.0 - first_tier_rate * (1.0 + vat_pct)
    return first_tier_rate / denom if denom > 0 else 0.0


def _vat_loaded_bucket_rate(net_base: float, scale, vat_pct: float) -> float:
    if net_base <= 0:
        return 0.0
    L  = [r * (1.0 + vat_pct) for (r, _b) in scale]
    bd = [b for (_r, b) in scale]
    Qn = [x / (1.0 - x) if x < 1.0 else 0.0 for x in L]
    Sn = [(b * (1.0 - x) if b is not None else None) for b, x in zip(bd, L)]
    tot, cum = 0.0, 0.0
    for i in range(len(Qn)):
        sni = Sn[i]
        if sni is None:
            band = max(0.0, net_base - cum)
        else:
            band = max(0.0, min(net_base - cum, sni)); cum += sni
        tot += band * Qn[i]
    return (tot / (1.0 + vat_pct)) / net_base


def _fc_commission_bucket_rate(all_results, scheme, assump, ctr) -> float:
    fc = all_results.get("fc")
    if fc is None or fc.height == 0:
        return 0.0
    act = fc.filter(pl.col("product_active")) if "product_active" in fc.columns else fc
    if act.height == 0:
        return 0.0
    def _s(c):
        return _sum_col(act, c)
    rb = _ctr_rate_basis(ctr, "fc")
    net_risk = (_s("basic_technical_premium") + _s("aids_technical_premium")) * rb
    profit_rate = _profit_rate_for(assump, "gla")
    admin_rate  = _admin_rate_for(assump, "gla", scheme)
    m11 = net_risk * (1.0 + profit_rate + admin_rate)
    fc_net = m11 + _s("waiver_premium") * rb + _s("prb_premium") * rb + _s("ctc_premium") * rb
    scale, _country, _payee = _resolve_commission_scale(ctr)
    return _vat_loaded_bucket_rate(fc_net, scale, _vat_on_commission_for(scheme))


def _cb_gip(all_results, scheme, assump, ctr, split_mode: str = "Sum Assured"):
    a = all_results.get("gip")
    if a is None or a.height == 0:
        return None
    a = a.filter(pl.col("product_active"))
    if a.height == 0:
        return None
    RB = _rate_basis_for(scheme, "gip")
    PRO = float(scheme.profit_refund_loading_by_product.get("gip", 0.03))
    plg = {r["param_name"]: r["param_value"] for r in
           assump.tables["product_limits"].filter(pl.col("product") == "gip").iter_rows(named=True)}
    CFLO = float(plg["cash_for_loved_ones"])
    PROFIT = float(assump.tables["profit_margins"].filter(
        (pl.col("product") == "gip") & (pl.col("margin_type") == "standard"))["margin"][0])
    ADMIN = 0.10
    L = 1 + PRO + CFLO + PROFIT

    def S(col, c=None):
        d = a if c is None else a.filter(pl.col("category_number") == c)
        return float(d[col].sum()) if col in d.columns else 0.0

    def SW(num, wt, c=None):
        d = a if c is None else a.filter(pl.col("category_number") == c)
        w = float(d[wt].sum())
        return float((d[num] * d[wt]).sum() / w) if w else 0.0

    C160 = S("total_cover") * 12
    C159 = S("basic_excl_esc") * RB * L
    C82 = (S("basic_technical_premium") + S("waiver_premium")) * RB
    C104 = ADMIN * C82
    scale, _c, _p = _resolve_commission_scale(ctr)
    vatpct = _vat_on_commission_for(scheme)

    def build(c):
        cov = S("total_cover", c) * 12
        if split_mode == "Risk Profile":
            basic = S("basic_excl_esc", c) * RB * L
        else:
            basic = cov / C160 * C159 if C160 else 0.0
        esc = (S("basic_technical_premium", c) - S("basic_excl_esc", c)) * RB * L
        hcc = S("waiver_premium", c) * RB * L
        conv = S("co_premium", c) * RB
        sub = basic + esc + hcc + conv
        n = a.filter(pl.col("category_number") == c).height if c else a.height
        males = (a.filter((pl.col("category_number") == c) & (pl.col("gender") == "M")).height
                 if c else a.filter(pl.col("gender") == "M").height)
        return {"basic_premium": basic, "escalation": esc, "waiver_premium": hcc,
                "co_premium": conv, "sub_total": sub,
                "total_cover": S("total_cover", c), "total_salary_annual": S("annual_salary", c),
                "n_members": n, "salary_weighted_age": SW("age", "annual_salary", c),
                "cover_weighted_age": SW("age", "total_cover", c),
                "pct_males": float(males) / max(n, 1),
                "policy_fee": 0.0, "commission": 0.0, "vat": 0.0, "total_premium": 0.0}

    cats = {c: build(c) for c in range(1, 7) if S("basic_excl_esc", c) > 0 or S("total_cover", c) > 0}
    scheme_d = build(None)
    TR103 = sum(m2["sub_total"] for m2 in cats.values())
    for m2 in cats.values():
        m2["policy_fee"] = C104 / TR103 * m2["sub_total"] if TR103 else 0.0
        m2["_r106"] = m2["sub_total"] + m2["policy_fee"]
    TR106 = sum(m2["_r106"] for m2 in cats.values())
    rate = _vat_loaded_bucket_rate(TR106, scale, vatpct)
    COMM = rate * TR106
    VATc = COMM * vatpct
    for m2 in cats.values():
        m2["commission"] = COMM / TR106 * m2["_r106"] if TR106 else 0.0
        m2["vat"] = VATc / TR106 * m2["_r106"] if TR106 else 0.0
        m2["total_premium"] = m2["_r106"] + m2["commission"] + m2["vat"]
        m2.pop("_r106")
    scheme_d["sub_total"] = sum(m2["sub_total"] for m2 in cats.values())
    for k in ("policy_fee", "commission", "vat", "total_premium"):
        scheme_d[k] = sum(m2[k] for m2 in cats.values())
    return {"scheme": scheme_d, "cats": cats, "active": {c: True for c in cats},
            "express_over_gla": False, "freq_divisor": 12.0, "_pkey": "gip"}


def _cb_tip(all_results, scheme, assump, ctr, split_mode: str = "Sum Assured"):
    a = all_results.get("tip")
    if a is None or a.height == 0:
        return None
    a = a.filter(pl.col("product_active"))
    if a.height == 0:
        return None
    RB = _rate_basis_for(scheme, "tip")
    PRO = float(scheme.profit_refund_loading_by_product.get("tip", 0.03))
    plg = {r["param_name"]: r["param_value"] for r in
           assump.tables["product_limits"].filter(pl.col("product") == "gip").iter_rows(named=True)}
    CFLO = float(plg["cash_for_loved_ones"])
    PROFIT = float(assump.tables["profit_margins"].filter(
        (pl.col("product") == "gip") & (pl.col("margin_type") == "standard"))["margin"][0])
    ADMIN = 0.10
    L = 1 + PRO + CFLO + PROFIT

    def S(col, c=None):
        d = a if c is None else a.filter(pl.col("category_number") == c)
        return float(d[col].sum()) if col in d.columns else 0.0

    def SW(num, wt, c=None):
        d = a if c is None else a.filter(pl.col("category_number") == c)
        w = float(d[wt].sum())
        return float((d[num] * d[wt]).sum() / w) if w else 0.0

    C160 = S("total_cover") * 12
    C159 = S("basic_excl_esc") * RB * L
    C82 = S("basic_technical_premium") * RB
    C104 = ADMIN * C82
    scale, _c, _p = _resolve_commission_scale(ctr)
    vatpct = _vat_on_commission_for(scheme)

    def build(c):
        cov = S("total_cover", c) * 12
        if split_mode == "Risk Profile":
            basic = S("basic_excl_esc", c) * RB * L
        else:
            basic = cov / C160 * C159 if C160 else 0.0
        esc = (S("basic_technical_premium", c) - S("basic_excl_esc", c)) * RB * L
        sub = basic + esc
        n = a.filter(pl.col("category_number") == c).height if c else a.height
        males = (a.filter((pl.col("category_number") == c) & (pl.col("gender") == "M")).height
                 if c else a.filter(pl.col("gender") == "M").height)
        return {"basic_premium": basic, "escalation": esc, "sub_total": sub,
                "total_cover": S("total_cover", c), "total_salary_annual": S("annual_salary", c),
                "n_members": n, "salary_weighted_age": SW("age", "annual_salary", c),
                "cover_weighted_age": SW("age", "total_cover", c),
                "pct_males": float(males) / max(n, 1),
                "policy_fee": 0.0, "commission": 0.0, "vat": 0.0, "total_premium": 0.0}

    cats = {c: build(c) for c in range(1, 7) if S("total_cover", c) > 0}
    scheme_d = build(None)
    TR103 = sum(m2["sub_total"] for m2 in cats.values())
    for m2 in cats.values():
        m2["policy_fee"] = C104 / TR103 * m2["sub_total"] if TR103 else 0.0
        m2["_r106"] = m2["sub_total"] + m2["policy_fee"]
    TR106 = sum(m2["_r106"] for m2 in cats.values())
    rate = _vat_loaded_bucket_rate(TR106, scale, vatpct)
    COMM = rate * TR106; VATc = COMM * vatpct
    for m2 in cats.values():
        m2["commission"] = COMM / TR106 * m2["_r106"] if TR106 else 0.0
        m2["vat"] = VATc / TR106 * m2["_r106"] if TR106 else 0.0
        m2["total_premium"] = m2["_r106"] + m2["commission"] + m2["vat"]
        m2.pop("_r106")
    scheme_d["sub_total"] = sum(m2["sub_total"] for m2 in cats.values())
    for k in ("policy_fee", "commission", "vat", "total_premium"):
        scheme_d[k] = sum(m2[k] for m2 in cats.values())
    return {"scheme": scheme_d, "cats": cats, "active": {c: True for c in cats},
            "express_over_gla": False, "freq_divisor": 12.0, "_pkey": "tip"}


def _po_profile_tip(tip_df):
    a = tip_df.filter(pl.col("product_active")) if "product_active" in tip_df.columns else tip_df
    def S(c): return float(a[c].sum()) if c in a.columns else 0.0
    def SW(num, wt):
        w = S(wt); return float((a[num]*a[wt]).sum()/w) if w else 0.0
    n = int(a.height); salary = S("annual_salary"); cover = S("total_cover")*12
    over_nra = int(tip_df.filter(pl.col("age_nb") > pl.col("ceasing_age")).height) \
        if {"age_nb", "ceasing_age"} <= set(tip_df.columns) else 0
    avg_cover = cover / n if n else 0.0
    if "reporting_incidence" in a.columns and "reporting_incidence_x_cover" in a.columns:
        inc_sum = S("reporting_incidence")
        inc_x_cover_sum = S("reporting_incidence_x_cover")
        expected_claims = inc_sum
        incidence_rate = inc_sum * 1000.0 / n if n else 0.0
        expected_claim_size = (inc_x_cover_sum / inc_sum * 12.0) if inc_sum else 0.0
        claim_cover_ratio = (expected_claim_size / avg_cover) if avg_cover else 0.0
    else:
        expected_claims = 0.0; incidence_rate = 0.0
        expected_claim_size = 0.0; claim_cover_ratio = 0.0
    return {"membership": n, "over_nra": over_nra, "disabled": 0,
            "avg_age": float(a["age"].mean()) if n else 0.0,
            "pct_males": float(a.filter(pl.col("gender")=="M").height)/max(n,1),
            "salary_bill": salary, "avg_salary": salary/n if n else 0.0,
            "age_wtd_by_salary": SW("age","annual_salary"),
            "total_cover": cover, "avg_multiple": cover/salary if salary else 0.0,
            "avg_cover": avg_cover, "age_wtd_by_cover": SW("age","total_cover"),
            "incidence_rate": incidence_rate, "expected_claims": expected_claims,
            "expected_claim_size": expected_claim_size, "claim_cover_ratio": claim_cover_ratio}


def _po_margins_tip(tip_df, assump, ctr):
    scheme = getattr(ctr, "scheme", None)
    PRO = float(scheme.profit_refund_loading_by_product.get("tip", 0.03))
    plg = {r["param_name"]: r["param_value"] for r in
           assump.tables["product_limits"].filter(pl.col("product")=="gip").iter_rows(named=True)}
    CFLO = float(plg["cash_for_loved_ones"])
    PROFIT = float(assump.tables["profit_margins"].filter(
        (pl.col("product")=="gip")&(pl.col("margin_type")=="standard"))["margin"][0])
    return {
        "conversion_option": 0.0,
        "cover_to_continue": 0.0,
        "cash_for_loved_ones": _resolved_priced_rate(ctr, "tip", "cash_for_loved_ones", CFLO),
        "pro_loading": _resolved_priced_rate(ctr, "tip", "pro_loading", PRO),
        "profit_margin": _resolved_priced_rate(ctr, "tip", "profit_margin", PROFIT),
        "admin": _resolved_priced_rate(ctr, "tip", "admin", 0.10),
        "business_decision": _resolved_priced_rate(ctr, "tip", "business_decision", 0.0),
        "phasing":           _resolved_priced_rate(ctr, "tip", "phasing", 0.0),
        "reins_profit":      _resolved_priced_rate(ctr, "tip", "reins_profit", 0.0),
        "reins_arb":         _resolved_priced_rate(ctr, "tip", "reins_arb", 0.0),
        "rate_basis": 1.0,
    }


def _po_buildup_tip(tip_df, assump, ctr, vat_pct=DEFAULT_VAT_PCT):
    scheme = getattr(ctr, "scheme", None)
    a = tip_df.filter(pl.col("product_active")) if "product_active" in tip_df.columns else tip_df
    def S(c): return float(a[c].sum()) if c in a.columns else 0.0
    PRO = float(scheme.profit_refund_loading_by_product.get("tip", 0.03))
    plg = {r["param_name"]: r["param_value"] for r in
           assump.tables["product_limits"].filter(pl.col("product")=="gip").iter_rows(named=True)}
    CFLO = float(plg["cash_for_loved_ones"])
    PROFIT = float(assump.tables["profit_margins"].filter(
        (pl.col("product")=="gip")&(pl.col("margin_type")=="standard"))["margin"][0])
    ADMIN = 0.10
    NR_tech = S("basic_technical_premium")
    PRO    = _resolved_priced_rate(ctr, "tip", "pro_loading",        PRO)
    CFLO   = _resolved_priced_rate(ctr, "tip", "cash_for_loved_ones", CFLO)
    PROFIT = _resolved_priced_rate(ctr, "tip", "profit_margin",      PROFIT)
    ADMIN  = _resolved_priced_rate(ctr, "tip", "admin",              ADMIN)
    BDM    = _resolved_priced_rate(ctr, "tip", "business_decision", 0.0)
    PHASE  = _resolved_priced_rate(ctr, "tip", "phasing",           0.0)
    RPROF  = _resolved_priced_rate(ctr, "tip", "reins_profit",      0.0)
    RARB   = _resolved_priced_rate(ctr, "tip", "reins_arb",         0.0)
    PROFIT_DISPLAY = PROFIT + BDM + PHASE + RPROF + RARB

    rate_basis = _ctr_rate_basis(ctr, "tip")
    NR_basis = NR_tech * rate_basis

    PROn_t = NR_tech*PRO; CFLOn_t = NR_tech*CFLO; PROFITn_t = NR_tech*PROFIT

    PROn = NR_basis*PRO; CFLOn = NR_basis*CFLO; PROFITn = NR_basis*PROFIT_DISPLAY; ADMINn = NR_basis*ADMIN
    F61 = NR_basis + PROn + CFLOn + PROFITn + ADMINn
    scale, _c, _p = _resolve_commission_scale(ctr)
    rate = _vat_loaded_bucket_rate(F61, scale, vat_pct)
    COMM = rate*F61; VATc = COMM*vat_pct; GROSS = F61 + COMM + VATc
    salary = S("annual_salary"); cover = S("total_cover")*12
    basis = [("Net Risk Premium", NR_basis, None), ("PRO Premium ", PROn, PRO),
             ("Cash for Loved Ones", CFLOn, CFLO), ("Profit ", PROFITn, PROFIT_DISPLAY),
             ("Admin & Expenses ", ADMINn, None), ("Normal Commission", COMM, None),
             ("Initial Commission", 0.0, None), ("VAT on Commission", VATc, None),
             ("Gross Risk Prem ", GROSS, None)]
    tech = [("Net Risk Premium", NR_tech, None), ("PRO Premium ", PROn_t, PRO),
            ("Cash for Loved Ones", CFLOn_t, CFLO), ("Profit ", PROFITn_t, PROFIT)]
    return {"tech": tech, "basis": basis, "salary": salary, "cover": cover, "rate_basis": rate_basis}


_CB_SHEET_FAMILY_FOR_PKEY = {
    "gla": "gla", "lsdb": "gla", "ci": "gla",
    "spouse_gla": "gla", "spouse_lsdb": "gla", "terminal": "gla",
    "accident_death": "gla", "accident_disability": "gla", "dismemberment": "gla",
    "fc": "fc", "gip": "gip", "tip": "tip",
    "educate_a_child": "gla", "education_guardian": "gla",
}


def _resolve_split_mode(scheme, pkey: str) -> str:
    sheet_family = _CB_SHEET_FAMILY_FOR_PKEY.get(pkey)
    if sheet_family is None:
        return "Sum Assured"
    cat_split = getattr(scheme, "category_split", {}) or {}
    mode = cat_split.get(sheet_family, "")
    return mode if mode in ("Sum Assured", "Risk Profile", "Flat Rand per Mem") else "Sum Assured"


def _cb_resolve_commission_and_vat(all_results, scheme, assump, freq_divisor):
    def _subtotal_provider(pkey: str) -> float:
        df = all_results.get(pkey)
        if df is None or df.height == 0:
            return 0.0

        active = (
            df.filter(pl.col("product_active"))
            if "product_active" in df.columns else df
        )
        if active.height == 0:
            return 0.0

        def _s(col):
            return _sum_col(active, col)

        tech_net_annual = _s("basic_technical_premium") + _s("aids_technical_premium")
        rate_basis = _rate_basis_for(scheme, pkey)
        basis_net_annual = tech_net_annual * rate_basis

        profit_rate = _profit_rate_for(assump, pkey)
        admin_rate  = _admin_rate_for(assump, pkey, scheme)

        basic_periodic  = basis_net_annual * (1.0 + profit_rate + admin_rate) / freq_divisor
        co_periodic     = _s("co_premium")            / freq_divisor
        ctc_periodic    = _s("ctc_premium")           / freq_divisor
        waiver_periodic = _s("waiver_premium")        / freq_divisor
        prb_periodic    = _s("prb_premium")           / freq_divisor
        reins_periodic  = _s("reinstatement_premium") / freq_divisor

        return (
            basic_periodic
            + co_periodic
            + ctc_periodic
            + waiver_periodic
            + prb_periodic
            + reins_periodic
        )

    resolved_commission_pct = _resolve_commission_rate(
        all_results, scheme, assump, _subtotal_provider,
    )
    resolved_vat_pct = _vat_on_commission_for(scheme)
    return resolved_commission_pct, resolved_vat_pct


def _cb_compute_gla_bill(all_results):
    gla_df = all_results.get("gla")
    gla_salary_bill = 0.0
    gla_cover_bill  = 0.0
    if gla_df is not None:
        gla_active = (
            gla_df.filter(pl.col("product_active"))
            if "product_active" in gla_df.columns else gla_df
        )
        if "annual_salary" in gla_active.columns:
            gla_salary_bill = float(gla_active["annual_salary"].sum())
        if "total_cover" in gla_active.columns:
            gla_cover_bill = float(gla_active["total_cover"].sum())
    return gla_salary_bill, gla_cover_bill


def _cb_build_product_dict(all_results, scheme, assump, ctr, resolved_commission_pct,
        resolved_vat_pct, freq_divisor, gla_salary_bill, gla_cover_bill,
        express_over_gla, _cb_bucket_rates):
    out: dict[str, dict] = {}
    for pkey in CB_PRODUCTS:
        df = all_results.get(pkey)

        profit_rate = _profit_rate_for(assump, pkey)
        admin_rate  = _admin_rate_for(assump, pkey, scheme)
        rate_basis  = _rate_basis_for(scheme, pkey)
        pup_rate    = _pup_rate_for(assump, scheme, pkey)
        skip_waiver = False
        if pkey == "fc":
            profit_rate = _profit_rate_for(assump, "gla")
            admin_rate  = _admin_rate_for(assump, "gla", scheme)
            skip_waiver = True

        if df is None or df.height == 0:
            prod = _empty_product()
        else:
            prod = _aggregate_product(
                df,
                resolved_commission_pct,
                resolved_vat_pct,
                profit_rate=profit_rate,
                admin_rate=admin_rate,
                rate_basis=rate_basis,
                freq_divisor=freq_divisor,
                pup_rate=pup_rate,
                skip_waiver=skip_waiver,
            )

        express = bool(express_over_gla.get(pkey, False))
        prod["express_over_gla"]   = express
        prod["denominator_salary"] = (
            gla_salary_bill if express
            else prod["scheme"].get("total_salary_annual", 0.0)
        )
        prod["denominator_cover"]  = (
            gla_cover_bill if express
            else prod["scheme"].get("total_cover", 0.0)
        )
        prod["freq_divisor"] = freq_divisor
        prod["cb_bucket_rate"] = _cb_bucket_rates.get(pkey, 0.0)
        if pkey == "fc" and ctr is not None and prod["cb_bucket_rate"] == 0.0:
            prod["cb_bucket_rate"] = _fc_commission_bucket_rate(
                all_results, scheme, assump, ctr)
        prod["_pkey"] = pkey
        _reallocate_cb_basic_pooled(prod, split_mode=_resolve_split_mode(scheme, pkey), raw_df=df)
        out[pkey] = prod
    return out


def _cb_apply_gla_crossproduct_fixup(out, scheme):
    if "gla" in out:
        lsdb_val = str(scheme.rating.get("lsdb", "")).strip().lower()
        ci_val = str(scheme.rating.get("ci", "")).strip().lower()
        _source_pkey = None
        if lsdb_val == "standalone":
            _source_pkey = "lsdb"
        elif ci_val == "standalone":
            _source_pkey = "ci"
        if _source_pkey and _source_pkey in out:
            out["gla"]["scheme"]["total_salary_annual"] = out[_source_pkey]["scheme"].get("total_salary_annual", 0.0)
            out["gla"]["scheme"]["n_members"] = out[_source_pkey]["scheme"].get("n_members", 0)
    return out


def _cb_apply_special_products(out, all_results, scheme, assump, ctr, commission_pct, vat_pct):
    _gip_cb = _cb_gip(all_results, scheme, assump, ctr, split_mode=_resolve_split_mode(scheme, "gip")) if ctr is not None else None
    if _gip_cb is not None:
        out['gip'] = _gip_cb
    _tip_cb = _cb_tip(all_results, scheme, assump, ctr, split_mode=_resolve_split_mode(scheme, "tip")) if ctr is not None else None
    if _tip_cb is not None:
        out['tip'] = _tip_cb

    _edu_cb = _cb_education(all_results, scheme, assump, ctr,
                             commission_pct=commission_pct, vat_pct=vat_pct)
    if _edu_cb is not None:
        out['education'] = _edu_cb

    _ext_fc_cb = _cb_ext_fc(all_results, scheme, assump, ctr)
    if _ext_fc_cb is not None:
        out['ext_fc'] = _ext_fc_cb
    return out


def gather_cb_data(
    all_results: dict[str, pl.DataFrame],
    scheme,
    assump,
    commission_pct: float = DEFAULT_COMMISSION_PCT,
    vat_pct: float = DEFAULT_VAT_PCT,
    ctr=None,
) -> dict[str, dict]:
    express_over_gla = getattr(scheme, "express_over_gla", {}) or {}
    _cb_bucket_rates = (_compute_scheme_commission_rate(all_results, assump, ctr)
                        if ctr is not None else {})
    freq_divisor = _freq_divisor_for(scheme)

    resolved_commission_pct, resolved_vat_pct = _cb_resolve_commission_and_vat(
        all_results, scheme, assump, freq_divisor)

    gla_salary_bill, gla_cover_bill = _cb_compute_gla_bill(all_results)

    out = _cb_build_product_dict(all_results, scheme, assump, ctr, resolved_commission_pct,
        resolved_vat_pct, freq_divisor, gla_salary_bill, gla_cover_bill,
        express_over_gla, _cb_bucket_rates)

    out = _cb_apply_gla_crossproduct_fixup(out, scheme)

    out = _cb_apply_special_products(out, all_results, scheme, assump, ctr, commission_pct, vat_pct)

    return out


_FC_FAMCOV_COLS = ["primary_insured_person","insured_spouse","child_age_14",
                   "child_age_6_13","child_age_2_5","child_under_age_2","child_stillborn"]
def _famcov_sum(frame):
    if not any(c in frame.columns for c in _FC_FAMCOV_COLS):
        return 0.0
    f = frame
    if "ceasing_age" in f.columns and "age" in f.columns:
        f = f.filter(pl.col("age") <= pl.col("ceasing_age"))
    expr = None
    for c in _FC_FAMCOV_COLS:
        if c in f.columns:
            term = (pl.col(c).cast(pl.Utf8).str.replace_all(",","")
                      .cast(pl.Float64, strict=False).fill_null(0.0))
            expr = term if expr is None else expr + term
    if expr is None:
        return 0.0
    return float(f.select(expr.alias("_f"))["_f"].sum())

def _aggregate_product(
    df: pl.DataFrame,
    commission_pct: float,
    vat_pct: float,
    *,
    profit_rate: float,
    admin_rate: float,
    rate_basis: float,
    freq_divisor: float,
    pup_rate: float = 0.0,
    skip_waiver: bool = False,
) -> dict:
    if "product_active" in df.columns and "age" in df.columns and "ceasing_age" in df.columns:
        active_df = df.filter(
            pl.col("product_active").fill_null(False)
            & (pl.col("age") <= pl.col("ceasing_age"))
        )
    elif "product_active" in df.columns:
        active_df = df.filter(pl.col("product_active"))
    else:
        active_df = df
    scheme = _compute_metrics(
        active_df, commission_pct, vat_pct,
        profit_rate=profit_rate,
        admin_rate=admin_rate,
        rate_basis=rate_basis,
        freq_divisor=freq_divisor,
        pup_rate=pup_rate,
        skip_waiver=skip_waiver,
    )
    scheme["famcov"] = _famcov_sum(active_df)
    cats = {}
    active_flags = {}
    for cat_num in range(1, N_CATEGORIES + 1):
        if "category_number" in active_df.columns:
            sub = active_df.filter(pl.col("category_number") == cat_num)
        else:
            sub = active_df.head(0)
        if sub.height > 0:
            cats[cat_num] = _compute_metrics(
                sub, commission_pct, vat_pct,
                profit_rate=profit_rate,
                admin_rate=admin_rate,
                rate_basis=rate_basis,
                freq_divisor=freq_divisor,
                pup_rate=pup_rate,
                skip_waiver=skip_waiver,
            )
            cats[cat_num]["famcov"] = _famcov_sum(sub)
            active_flags[cat_num] = True
        else:
            cats[cat_num] = _zero_metrics()
            active_flags[cat_num] = False
    return {"scheme": scheme, "cats": cats, "active": active_flags}

def _compute_metrics(
    df: pl.DataFrame,
    commission_pct: float,
    vat_pct: float,
    *,
    profit_rate: float,
    admin_rate: float,
    rate_basis: float,
    freq_divisor: float,
    pup_rate: float = 0.0,
    skip_waiver: bool = False,
) -> dict:
    if df.height == 0:
        return _zero_metrics()

    def _sum(col: str) -> float:
        return float(df[col].sum()) if col in df.columns else 0.0

    tech_net_annual  = _sum("basic_technical_premium") + _sum("aids_technical_premium")
    basis_net_annual = tech_net_annual * rate_basis
    basic_periodic   = basis_net_annual * (1.0 + profit_rate + admin_rate) / freq_divisor

    co_periodic     = _sum("co_premium")            / freq_divisor
    ctc_periodic    = _sum("ctc_premium")           / freq_divisor
    waiver_periodic = _sum("waiver_premium")        / freq_divisor
    prb_periodic    = _sum("prb_premium")           / freq_divisor
    reins_periodic  = _sum("reinstatement_premium") / freq_divisor

    pup_periodic = basis_net_annual * pup_rate / freq_divisor

    waiver_in_sub = 0.0 if skip_waiver else waiver_periodic
    sub_total_periodic  = (basic_periodic + co_periodic + ctc_periodic
                           + waiver_in_sub + prb_periodic + reins_periodic
                           + pup_periodic)
    commission_periodic = sub_total_periodic * commission_pct
    vat_periodic        = commission_periodic * vat_pct
    total_periodic      = sub_total_periodic + commission_periodic + vat_periodic

    return {
        "basic_premium":         basic_periodic,
        "co_premium":            co_periodic,
        "ctc_premium":           ctc_periodic,
        "waiver_premium":        waiver_periodic,
        "prb_premium":           prb_periodic,
        "reinstatement_premium": reins_periodic,
        "pup":                   pup_periodic,
        "sub_total":             sub_total_periodic,
        "commission":            commission_periodic,
        "vat":                   vat_periodic,
        "total_premium":         total_periodic,
        "total_salary_annual":   (float(df.filter(pl.col("total_cover") > 0)["annual_salary"].sum())
                                   if ("annual_salary" in df.columns and "total_cover" in df.columns)
                                   else _sum("annual_salary")),
        "total_cover":           _sum("total_cover"),
        "n_members":             (df.filter(pl.col("total_cover") > 0).height
                                   if "total_cover" in df.columns else df.height),
        "salary_weighted_age":   _weighted(df, "age", "annual_salary"),
        "cover_weighted_age":    _weighted(df, "age", "total_cover"),
        "pct_males":             _pct(df, "gender", "M"),
    }

def _pup_rate_for(assump, scheme, pkey: str) -> float:
    if pkey != "fc":
        return 0.0
    plims = assump.tables.get("product_limits")
    if plims is None:
        return 0.0
    def _any_yes(m):
        return any(str(v).strip().lower() == "yes" for v in (m or {}).values())
    if not _any_yes(getattr(scheme, "fc_paid_up_benefits_active", {})):
        return 0.0
    if _any_yes(getattr(scheme, "fc_pup_death_lsdb", {})):
        param = "pup_cost_death_lsdb"
    elif _any_yes(getattr(scheme, "fc_pup_lsdb", {})):
        param = "pup_cost_lsdb"
    elif _any_yes(getattr(scheme, "fc_pup_death", {})):
        param = "pup_cost_death"
    else:
        return 0.0
    row = plims.filter((pl.col("product") == "fc") & (pl.col("param_name") == param))
    if row.height == 0:
        return 0.0
    try:
        return float(row["param_value"][0])
    except (TypeError, ValueError):
        return 0.0


def _po_profile_fc(fc_df):
    act = _tt_active(fc_df)
    def _cs(c):
        if c not in act.columns: return 0.0
        return float(act.select(pl.col(c).cast(pl.Utf8).str.replace_all(",", "")
                     .cast(pl.Float64, strict=False).fill_null(0.0).sum()).item())
    def _s(c):
        if c not in act.columns: return 0.0
        try: return float(act[c].sum())
        except Exception: return _cs(c)  # noqa: BLE001 - fall back to string-column sum on any dtype error
    n = int(act.height)
    salary = _s("annual_salary"); cover = _s("total_cover")
    main = _cs("primary_insured_person")
    over_nra = 0
    if {"product_active", "age", "ceasing_age"} <= set(fc_df.columns):
        over_nra = fc_df.filter(pl.col("product_active")
                                & (pl.col("age") > pl.col("ceasing_age"))).height
    if "qx" in act.columns and "maximum_no_of_spouses" in act.columns and "pct_married" in act.columns:
        no_of_spouses = act["maximum_no_of_spouses"].cast(pl.Float64, strict=False)
        total_assumed_members = (
            act["pct_married"] * no_of_spouses
            + act.get_column("n_children_0_13")
            + act.get_column("n_children_14_plus")
            + 1.0
        )
        weighted_qx = act["qx"] * total_assumed_members
        expected_claims = float(weighted_qx.sum())
        incidence_rate = expected_claims * 1000.0 / n if n else 0.0
        ecs = (float((act["qx"] * act["total_cover"]).sum()) / expected_claims) if expected_claims else 0.0
    else:
        expected_claims = 0.0
        incidence_rate = 0.0
        ecs = 0.0
    avg_cover_main = main / n if n else 0.0
    disabled = int(act.filter(pl.col("status").str.to_uppercase() == "DB").height) if "status" in act.columns else 0
    return {
        "membership": n, "over_nra": over_nra, "disabled": disabled,
        "avg_age": float(act["age"].mean()) if n else 0.0,
        "pct_males": (act.filter(pl.col("gender") == "M").height / n if n else 0.0),
        "salary_bill": salary, "avg_salary": salary / n if n else 0.0,
        "age_wtd_by_salary": float((act["age"] * act["annual_salary"]).sum() / salary) if salary else 0.0,
        "total_cover": cover, "total_cover_main": main,
        "avg_multiple": cover / salary if salary else 0.0,
        "avg_cover": cover / n if n else 0.0, "avg_cover_main": avg_cover_main,
        "age_wtd_by_cover": float((act["age"] * act["total_cover"]).sum() / cover) if cover else 0.0,
        "incidence_rate": incidence_rate,
        "expected_claims": expected_claims, "expected_claim_size": ecs,
        "claim_cover_ratio": (ecs / avg_cover_main) if avg_cover_main else 0.0,
    }


def _po_margins_fc(fc_df, assump, ctr):
    act = _tt_active(fc_df)
    def _s(c):
        return _sum_col(act, c, safe=True)
    rb = _ctr_rate_basis(ctr, "fc")
    net_risk = (_s("basic_technical_premium") + _s("aids_technical_premium")) * rb
    pup = _s("waiver_premium") * rb
    scheme = getattr(ctr, "scheme", None)
    pup_cost_default = (pup / net_risk if net_risk else 0.0)
    admin_default = _admin_rate_for(assump, "gla", scheme)
    return {
        "conversion_option": 0.0,
        "cover_to_continue": _addon_pct_of_net_risk(act, "ctc_premium"),
        "pup_cost": _resolved_priced_rate(ctr, "fc", "pup_cost", pup_cost_default),
        "pro_loading": _ctr_prb_loading(ctr, "fc"),
        "profit_margin": _profit_rate_for(assump, "gla"),
        "admin": _resolved_priced_rate(ctr, "fc", "admin", admin_default),
        "business_decision": 0.0, "phasing": 0.0,
        "reins_profit": 0.0, "reins_arb": 0.0, "rate_basis": 1.0,
    }


def _po_buildup_fc(fc_df, assump, ctr, commission_rate=0.0, vat_pct=DEFAULT_VAT_PCT):
    act = _tt_active(fc_df)
    def _s(c):
        return _sum_col(act, c, safe=True)
    rb = _ctr_rate_basis(ctr, "fc")
    scheme = getattr(ctr, "scheme", None)
    net_risk_tech = _s("basic_technical_premium") + _s("aids_technical_premium")
    net_risk = net_risk_tech * rb
    profit_r = _profit_rate_for(assump, "gla")
    admin_default = _admin_rate_for(assump, "gla", scheme)
    admin_r  = _resolved_priced_rate(ctr, "fc", "admin", admin_default)
    ctc_t, pup_t, pro_t = _s("ctc_premium"), _s("waiver_premium"), _s("prb_premium")
    pup_cost_default = (pup_t / net_risk_tech if net_risk_tech else 0.0)
    pup_rate = _resolved_priced_rate(ctr, "fc", "pup_cost", pup_cost_default)
    ctc, pro = ctc_t * rb, pro_t * rb
    pup = net_risk * pup_rate
    profit = net_risk * profit_r
    admin  = net_risk * admin_r
    _den = act.filter(pl.col("total_cover") > 0)
    salary = float(_den["annual_salary"].sum()) if "annual_salary" in _den.columns else 0.0
    cover  = float(_den["total_cover"].sum()) if "total_cover" in _den.columns else 0.0
    tech = [
        ("Net Risk Premium", net_risk_tech, None),
        ("Cover to Continue", ctc_t, None),
        ("Paid-Up Cover", pup_t, None),
        ("PRO Premium ", pro_t, None),
        ("Profit ", _s("profit_margin_amount"), profit_r),
    ]
    subtotal = net_risk + ctc + pup + pro + profit + admin
    commission = subtotal * commission_rate
    vat = commission * vat_pct
    gross = subtotal + commission + vat
    basis = [
        ("Net Risk Premium", net_risk, None),
        ("Cover to Continue", ctc, None),
        ("Paid-Up Cover", pup, None),
        ("PRO Premium ", pro, None),
        ("Profit ", profit, profit_r),
        ("Admin & Expenses ", admin, None),
        ("Normal Commission", commission, None),
        ("Initial Commission", 0.0, None),
        ("VAT on Commission", vat, None),
        ("Gross Risk Prem ", gross, None),
    ]
    return {"tech": tech, "basis": basis, "salary": salary, "cover": cover, "rate_basis": rb}


def _po_profile_gip(gip_df):
    a = gip_df.filter(pl.col("product_active")) if "product_active" in gip_df.columns else gip_df
    def S(c): return float(a[c].sum()) if c in a.columns else 0.0
    def SW(num, wt):
        w = S(wt); return float((a[num]*a[wt]).sum()/w) if w else 0.0
    n = int(a.height); salary = S("annual_salary"); cover = S("total_cover")*12
    over_nra = int(gip_df.filter(pl.col("age_nb") > pl.col("ceasing_age")).height) \
        if {"age_nb", "ceasing_age"} <= set(gip_df.columns) else 0
    avg_cover = cover / n if n else 0.0
    if "reporting_incidence" in a.columns and "reporting_incidence_x_cover" in a.columns:
        inc_sum = S("reporting_incidence")
        inc_x_cover_sum = S("reporting_incidence_x_cover")
        expected_claims = inc_sum
        incidence_rate = inc_sum * 1000.0 / n if n else 0.0
        expected_claim_size = (inc_x_cover_sum / inc_sum * 12.0) if inc_sum else 0.0
        claim_cover_ratio = (expected_claim_size / avg_cover) if avg_cover else 0.0
    else:
        expected_claims = 0.0; incidence_rate = 0.0
        expected_claim_size = 0.0; claim_cover_ratio = 0.0
    return {"membership": n, "over_nra": over_nra, "disabled": 0,
            "avg_age": float(a["age"].mean()) if n else 0.0,
            "pct_males": float(a.filter(pl.col("gender")=="M").height)/max(n,1),
            "salary_bill": salary, "avg_salary": salary/n if n else 0.0,
            "age_wtd_by_salary": SW("age","annual_salary"),
            "total_cover": cover, "avg_multiple": cover/salary if salary else 0.0,
            "avg_cover": avg_cover, "age_wtd_by_cover": SW("age","total_cover"),
            "incidence_rate": incidence_rate, "expected_claims": expected_claims,
            "expected_claim_size": expected_claim_size, "claim_cover_ratio": claim_cover_ratio}


def _po_margins_gip(gip_df, assump, ctr):
    scheme = getattr(ctr, "scheme", None)
    a = gip_df.filter(pl.col("product_active")) if "product_active" in gip_df.columns else gip_df
    def S(c): return float(a[c].sum()) if c in a.columns else 0.0
    PRO = float(scheme.profit_refund_loading_by_product.get("gip", 0.03))
    plg = {r["param_name"]: r["param_value"] for r in
           assump.tables["product_limits"].filter(pl.col("product")=="gip").iter_rows(named=True)}
    CFLO = float(plg["cash_for_loved_ones"])
    PROFIT = float(assump.tables["profit_margins"].filter(
        (pl.col("product")=="gip")&(pl.col("margin_type")=="standard"))["margin"][0])
    nr = S("basic_technical_premium")
    CO_default = (S("co_premium")/nr if nr else 0.0)
    return {
        "conversion_option": _resolved_priced_rate(ctr, "gip", "conversion_option", CO_default),
        "cover_to_continue": 0.0,
        "cash_for_loved_ones": _resolved_priced_rate(ctr, "gip", "cash_for_loved_ones", CFLO),
        "pro_loading": _resolved_priced_rate(ctr, "gip", "pro_loading", PRO),
        "profit_margin": _resolved_priced_rate(ctr, "gip", "profit_margin", PROFIT),
        "admin": _resolved_priced_rate(ctr, "gip", "admin", 0.10),
        "business_decision": _resolved_priced_rate(ctr, "gip", "business_decision", 0.0),
        "phasing":           _resolved_priced_rate(ctr, "gip", "phasing", 0.0),
        "reins_profit":      _resolved_priced_rate(ctr, "gip", "reins_profit", 0.0),
        "reins_arb":         _resolved_priced_rate(ctr, "gip", "reins_arb", 0.0),
        "rate_basis": 1.0,
    }


def _po_buildup_gip(gip_df, assump, ctr, vat_pct=DEFAULT_VAT_PCT):
    scheme = getattr(ctr, "scheme", None)
    a = gip_df.filter(pl.col("product_active")) if "product_active" in gip_df.columns else gip_df
    def S(c): return float(a[c].sum()) if c in a.columns else 0.0
    PRO = float(scheme.profit_refund_loading_by_product.get("gip", 0.03))
    plg = {r["param_name"]: r["param_value"] for r in
           assump.tables["product_limits"].filter(pl.col("product")=="gip").iter_rows(named=True)}
    CFLO = float(plg["cash_for_loved_ones"])
    PROFIT = float(assump.tables["profit_margins"].filter(
        (pl.col("product")=="gip")&(pl.col("margin_type")=="standard"))["margin"][0])
    ADMIN = 0.10
    NR_tech = S("basic_technical_premium") + S("waiver_premium")
    CO_default = (S("co_premium")/NR_tech if NR_tech else 0.0)
    CO_rate = _resolved_priced_rate(ctr, "gip", "conversion_option",   CO_default)
    PRO     = _resolved_priced_rate(ctr, "gip", "pro_loading",         PRO)
    CFLO    = _resolved_priced_rate(ctr, "gip", "cash_for_loved_ones", CFLO)
    PROFIT  = _resolved_priced_rate(ctr, "gip", "profit_margin",       PROFIT)
    ADMIN   = _resolved_priced_rate(ctr, "gip", "admin",               ADMIN)
    BDM    = _resolved_priced_rate(ctr, "gip", "business_decision", 0.0)
    PHASE  = _resolved_priced_rate(ctr, "gip", "phasing",           0.0)
    RPROF  = _resolved_priced_rate(ctr, "gip", "reins_profit",      0.0)
    RARB   = _resolved_priced_rate(ctr, "gip", "reins_arb",         0.0)
    PROFIT_DISPLAY = PROFIT + BDM + PHASE + RPROF + RARB

    rate_basis = _ctr_rate_basis(ctr, "gip")
    NR_basis = NR_tech * rate_basis

    CO_t = NR_tech * CO_rate; PROn_t = NR_tech*PRO; CFLOn_t = NR_tech*CFLO; PROFITn_t = NR_tech*PROFIT

    CO = NR_basis * CO_rate; PROn = NR_basis*PRO; CFLOn = NR_basis*CFLO
    PROFITn = NR_basis*PROFIT_DISPLAY; ADMINn = NR_basis*ADMIN
    F61 = NR_basis + CO + PROn + CFLOn + PROFITn + ADMINn
    scale, _c, _p = _resolve_commission_scale(ctr)
    rate = _vat_loaded_bucket_rate(F61, scale, vat_pct)
    COMM = rate*F61; VATc = COMM*vat_pct; GROSS = F61 + COMM + VATc
    salary = S("annual_salary"); cover = S("total_cover")*12
    basis = [("Net Risk Premium", NR_basis, None), ("Conversion Option ", CO, None),
             ("PRO Premium ", PROn, PRO), ("Cash for Loved Ones", CFLOn, CFLO),
             ("Profit ", PROFITn, PROFIT_DISPLAY), ("Admin & Expenses ", ADMINn, None),
             ("Normal Commission", COMM, None), ("Initial Commission", 0.0, None),
             ("VAT on Commission", VATc, None), ("Gross Risk Prem ", GROSS, None)]
    tech = [("Net Risk Premium", NR_tech, None), ("Conversion Option ", CO_t, None),
            ("PRO Premium ", PROn_t, PRO), ("Cash for Loved Ones", CFLOn_t, CFLO),
            ("Profit ", PROFITn_t, PROFIT)]
    return {"tech": tech, "basis": basis, "salary": salary, "cover": cover, "rate_basis": rate_basis}


def _po_profile_gla(gla_df, rate_basis: float = 1.0) -> dict:
    if gla_df is None or gla_df.height == 0:
        return _zero_profile()

    required = {
        "product_active",
        "age",
        "ceasing_age",
    }

    missing = sorted(required - set(gla_df.columns))

    if missing:
        raise KeyError(
            "GLA Pricing Outputs profile missing required columns: "
            + ", ".join(missing)
        )

    product_active = gla_df.filter(
        pl.col("product_active").fill_null(False)
    )

    tt_active = product_active.filter(
        pl.col("age") <= pl.col("ceasing_age")
    )

    over_nra = product_active.filter(
        pl.col("age") > pl.col("ceasing_age")
    ).height

    profile = _po_profile(
        tt_active,
        rate_basis=rate_basis,
    )

    profile["over_nra"] = int(over_nra)

    return profile


def gather_po_data(
    all_results: dict[str, pl.DataFrame],
    scheme,
    assump=None,
    ctr=None,
    commission_pct: float = DEFAULT_COMMISSION_PCT,
    vat_pct: float = DEFAULT_VAT_PCT,
) -> dict:
    ratings = dict(getattr(scheme, "rating", {}) or {})

    industry = getattr(scheme, "secondary", None) or getattr(scheme, "primary", None) or ""
    region   = getattr(scheme, "region", "") or ""
    express  = dict(getattr(scheme, "express_over_gla", {}) or {})
    _edu_pkey_for_express = getattr(scheme, "education_product", None)
    if _edu_pkey_for_express is not None:
        express["education"] = express.get(_edu_pkey_for_express, False)

    eoh_by_product = (
        compute_eoh_for_scheme(all_results, ctr, assump)
        if ctr is not None and assump is not None
        else None
    )

    gla_active = _active_slice(all_results.get("gla"))
    if gla_active is not None and gla_active.height:
        _gla_den   = gla_active.filter(pl.col("total_cover") > 0)
        gla_salary = float(_gla_den["annual_salary"].sum())
        gla_cover  = float(_gla_den["total_cover"].sum())
    else:
        gla_salary = 0.0
        gla_cover  = 0.0

    scheme_commission_rate = _compute_scheme_commission_rate(all_results, assump, ctr)
    apply_vat              = _ctr_vat_flag(ctr)
    vat_effective          = vat_pct if apply_vat else 0.0

    profile:        dict = {}
    eoh:            dict = {}
    margins:        dict = {}
    priced_margins: dict = {}
    buildup:        dict = {}

    for pkey in PO_PRODUCTS:
        df = all_results.get(pkey)
        active = _active_slice(df)

        rate_basis = _ctr_rate_basis(ctr, pkey)

        if pkey == "gla":
            profile[pkey] = _po_profile_gla(
                df,
                rate_basis=rate_basis,
            )
        elif pkey == "lsdb":
            profile[pkey] = _po_profile_lsdb(
                df,
                rate_basis=rate_basis,
            )
        elif pkey in _TT_ACTIVE_PRODUCTS:
            profile_active = _tt_active_slice(df)
            over_nra_count = int(
                active.filter(
                    pl.col("age") > pl.col("ceasing_age")
                ).height
            ) if active is not None and active.height and "age" in active.columns and "ceasing_age" in active.columns else 0

            profile[pkey] = _po_profile(
                profile_active,
                rate_basis=rate_basis,
            )
            profile[pkey]["over_nra"] = over_nra_count
            if pkey in _DISABLED_HARDCODE_ZERO_PRODUCTS:
                profile[pkey]["disabled"] = 0
        else:
            profile[pkey] = _po_profile(
                active,
                rate_basis=rate_basis,
            )
        eoh[pkey]            = _po_eoh(active, pkey, eoh_by_product)
        margins[pkey]        = _po_margins(assump, pkey, df=active, ctr=ctr)
        priced_margins[pkey] = _po_priced_margins(assump, pkey, df=active, ctr=ctr)
        buildup[pkey]        = _po_buildup(
            active, pkey, assump, ctr,
            commission_rate=scheme_commission_rate.get(pkey, 0.0),
            vat_pct=vat_effective,
            gla_salary=gla_salary, gla_cover=gla_cover,
        )

    products_out = list(PO_PRODUCTS)
    fc_df = all_results.get("fc")
    if fc_df is not None and fc_df.height:
        fc_comm = _fc_commission_bucket_rate(all_results, scheme, assump, ctr)
        profile["fc"]        = _po_profile_fc(fc_df)
        eoh["fc"]            = {"tech_eoh": None, "custom_eoh": None, "eoh_limit": None,
                                "members_over": 0, "cover_over": 0.0, "max_cover": 0.0}
        margins["fc"]        = _po_margins_fc(fc_df, assump, ctr)
        priced_margins["fc"] = {**margins["fc"], "rate_basis": _ctr_rate_basis(ctr, "fc")}
        buildup["fc"]        = _po_buildup_fc(fc_df, assump, ctr,
                                              commission_rate=fc_comm, vat_pct=vat_effective)
        products_out = list(products_out) + ["fc"]
    gip_df = all_results.get("gip")
    if gip_df is not None and gip_df.height:
        profile["gip"]        = _po_profile_gip(gip_df)
        gip_eoh = (eoh_by_product or {}).get("gip")
        eoh["gip"] = gip_eoh if gip_eoh is not None else {
            "tech_eoh": None, "custom_eoh": None, "eoh_limit": None,
            "members_over": 0, "cover_over": 0.0, "max_cover": 0.0,
        }
        margins["gip"]        = _po_margins_gip(gip_df, assump, ctr)
        priced_margins["gip"] = {**margins["gip"], "rate_basis": _ctr_rate_basis(ctr, "gip")}
        buildup["gip"]        = _po_buildup_gip(gip_df, assump, ctr, vat_pct=vat_effective)
        products_out = list(products_out) + ["gip"]

    tip_df = all_results.get("tip")
    if tip_df is not None and tip_df.height:
        profile["tip"]        = _po_profile_tip(tip_df)
        tip_eoh = (eoh_by_product or {}).get("tip")
        eoh["tip"] = tip_eoh if tip_eoh is not None else {
            "tech_eoh": None, "custom_eoh": None, "eoh_limit": None,
            "members_over": 0, "cover_over": 0.0, "max_cover": 0.0,
        }
        margins["tip"]        = _po_margins_tip(tip_df, assump, ctr)
        priced_margins["tip"] = {**margins["tip"], "rate_basis": _ctr_rate_basis(ctr, "tip")}
        buildup["tip"]        = _po_buildup_tip(tip_df, assump, ctr, vat_pct=vat_effective)
        products_out = list(products_out) + ["tip"]

    active_edu_pkey = getattr(scheme, "education_product", None)
    edu_df = all_results.get(active_edu_pkey) if active_edu_pkey else None
    if edu_df is not None and edu_df.height:
        assert active_edu_pkey is not None  # edu_df is only set when a pkey exists
        edu_active_raw = _active_slice(edu_df)
        edu_active = _tt_active_slice(edu_df)
        edu_over_nra = int(
            edu_active_raw.filter(
                pl.col("age") > pl.col("ceasing_age")
            ).height
        ) if edu_active_raw is not None and edu_active_raw.height and "age" in edu_active_raw.columns and "ceasing_age" in edu_active_raw.columns else 0
        profile["education"] = _po_profile(edu_active, rate_basis=_ctr_rate_basis(ctr, active_edu_pkey))
        profile["education"]["over_nra"] = edu_over_nra
        eoh["education"] = {
            "tech_eoh": None, "custom_eoh": None, "eoh_limit": None,
            "members_over": 0, "cover_over": 0.0, "max_cover": 0.0,
        }
        margins["education"] = _po_margins_education(assump, active_edu_pkey, ctr)
        priced_margins["education"] = {**margins["education"],
                                        "rate_basis": _ctr_rate_basis(ctr, active_edu_pkey)}
        edu_commission_rate = 0.0
        if ctr is not None:
            _s_edu = lambda c: float(edu_active[c].sum()) if edu_active is not None and c in edu_active.columns else 0.0
            _rb_edu = _ctr_rate_basis(ctr, active_edu_pkey)
            _tech_net_edu = (_s_edu("basic_technical_premium") + _s_edu("aids_technical_premium")) * _rb_edu
            _profit_edu = _profit_rate_for_education(assump, active_edu_pkey)
            _admin_edu = _admin_rate_for_education(assump, active_edu_pkey)
            _net_base_edu = _tech_net_edu * (1.0 + _profit_edu + _admin_edu)
            _scale_edu, _c, _p = _resolve_commission_scale(ctr)
            edu_commission_rate = _vat_loaded_bucket_rate(_net_base_edu, _scale_edu, vat_effective)
        edu_express = bool(express.get("education", False))
        buildup["education"] = _po_buildup_education(
            edu_active, active_edu_pkey, assump, ctr,
            commission_rate=edu_commission_rate, vat_pct=vat_effective,
            express_over_gla=edu_express, gla_salary=gla_salary, gla_cover=gla_cover,
        )
        try:
            from ctr_loader.products_registry import PRODUCT_REGISTRY
            profile["education"]["_display_title"] = PRODUCT_REGISTRY[active_edu_pkey].display_name
        except (ImportError, KeyError):
            pass
        products_out = list(products_out) + ["education"]

    ext_fc_data = _cb_ext_fc(all_results, scheme, assump, ctr)

    return {
        "scheme":         {"industry": industry, "region": region, "run_type": "Yes"},
        "products":       products_out,
        "profile":        profile,
        "eoh":            eoh,
        "margins":        margins,
        "priced_margins": priced_margins,
        "buildup":        buildup,
        "express":        express,
        "commission_rate": scheme_commission_rate,
        "vat_applied":     apply_vat,
        "ratings":         ratings,
        "ext_fc_data":     ext_fc_data,
    }


def _active_slice(df: pl.DataFrame | None) -> pl.DataFrame | None:
    if df is None:
        return None
    if "product_active" not in df.columns:
        return df
    return df.filter(pl.col("product_active"))


def _tt_active_slice(df: pl.DataFrame | None) -> pl.DataFrame | None:
    if df is None:
        return None
    if "product_active" not in df.columns or "age" not in df.columns or "ceasing_age" not in df.columns:
        return _active_slice(df)
    return df.filter(
        pl.col("product_active").fill_null(False)
        & (pl.col("age") <= pl.col("ceasing_age"))
    )


_TT_ACTIVE_PRODUCTS = frozenset({
    "ci", "spouse_gla", "spouse_lsdb", "terminal",
    "accident_death", "accident_disability", "dismemberment",
})

_DISABLED_HARDCODE_ZERO_PRODUCTS = frozenset({"spouse_gla", "spouse_lsdb"})


def _po_profile_lsdb(df, rate_basis: float = 1.0) -> dict:
    if df is None or df.height == 0:
        return _zero_profile()

    required = {
        "product_active", "status", "age", "ceasing_age",
        "gender", "annual_salary", "total_cover", "qx",
    }

    missing = sorted(required.difference(df.columns))

    if missing:
        raise KeyError(
            "LSDB Pricing Outputs profile missing columns: "
            + ", ".join(missing)
        )

    work = df.with_columns(
        pl.col("product_active")
        .cast(pl.Boolean, strict=False)
        .fill_null(False)
        .alias("_lsdb_active"),

        (
            pl.col("status")
            .cast(pl.String)
            .str.to_uppercase()
            != "A"
        ).alias("_lsdb_disabled"),

        (
            pl.col("age").cast(pl.Float64, strict=False)
            >
            pl.col("ceasing_age").cast(pl.Float64, strict=False)
        ).alias("_lsdb_over_nra"),
    )

    base_filter = (
        pl.col("_lsdb_active")
        & ~pl.col("_lsdb_disabled")
        & ~pl.col("_lsdb_over_nra")
    )

    covered_filter = (
        base_filter
        & (
            pl.col("total_cover")
            .cast(pl.Float64, strict=False)
            > 0
        )
    )

    base = work.filter(base_filter)
    covered = work.filter(covered_filter)

    membership = int(covered.height)

    over_nra = int(
        work.filter(
            pl.col("_lsdb_active")
            & ~pl.col("_lsdb_disabled")
            & pl.col("_lsdb_over_nra")
        ).height
    )

    disabled = int(
        work.filter(
            pl.col("_lsdb_disabled")
        ).height
    )

    males = int(
        covered.filter(
            pl.col("gender")
            .cast(pl.String)
            .str.to_uppercase()
            == "M"
        ).height
    )

    def sum_col(frame, column):
        value = frame.select(
            pl.col(column)
            .cast(pl.Float64, strict=False)
            .sum()
        ).item()

        return float(value or 0.0)

    salary_bill = sum_col(covered, "annual_salary")
    total_cover = sum_col(base, "total_cover")
    incidence_raw = sum_col(base, "qx")

    avg_age = (
        float(
            base.select(
                pl.col("age")
                .cast(pl.Float64, strict=False)
                .mean()
            ).item()
        )
        if base.height
        else 0.0
    )

    age_x_salary = float(
        base.select(
            (
                pl.col("age").cast(pl.Float64, strict=False)
                *
                pl.col("annual_salary").cast(pl.Float64, strict=False)
            ).sum()
        ).item()
        or 0.0
    )

    age_x_cover = float(
        base.select(
            (
                pl.col("age").cast(pl.Float64, strict=False)
                *
                pl.col("total_cover").cast(pl.Float64, strict=False)
            ).sum()
        ).item()
        or 0.0
    )

    qx_x_cover = float(
        base.select(
            (
                pl.col("qx").cast(pl.Float64, strict=False)
                *
                pl.col("total_cover").cast(pl.Float64, strict=False)
            ).sum()
        ).item()
        or 0.0
    )

    expected_claims = incidence_raw * float(rate_basis)

    expected_claim_size = (
        qx_x_cover / incidence_raw
        if incidence_raw
        else 0.0
    )

    avg_cover = (
        total_cover / membership
        if membership
        else 0.0
    )

    return {
        "membership": membership,
        "over_nra": over_nra,
        "disabled": disabled,
        "avg_age": avg_age,
        "pct_males": males / membership if membership else 0.0,
        "salary_bill": salary_bill,
        "avg_salary": salary_bill / membership if membership else 0.0,
        "age_wtd_by_salary": (
            age_x_salary / salary_bill if salary_bill else 0.0
        ),
        "total_cover": total_cover,
        "avg_multiple": (
            total_cover / salary_bill if salary_bill else 0.0
        ),
        "avg_cover": avg_cover,
        "age_wtd_by_cover": (
            age_x_cover / total_cover if total_cover else 0.0
        ),
        "incidence_rate": (
            expected_claims * 1000.0 / membership
            if membership else 0.0
        ),
        "expected_claims": expected_claims,
        "expected_claim_size": expected_claim_size,
        "claim_cover_ratio": (
            expected_claim_size / avg_cover
            if avg_cover else 0.0
        ),
    }


def _po_profile(df, rate_basis: float = 1.0) -> dict:
    if df is None or df.height == 0:
        return _zero_profile()

    def _s(col):
        return _sum_col(df, col)

    n = int(df.height)
    salary_bill = _s("annual_salary")
    total_cover = _s("total_cover")

    incidence_raw = _s("qx") if "qx" in df.columns else 0.0
    expected_claims = incidence_raw * rate_basis

    if incidence_raw > 0 and "qx" in df.columns and "total_cover" in df.columns:
        exp_claim_size = float((df["qx"] * df["total_cover"]).sum()) / incidence_raw
    else:
        exp_claim_size = 0.0

    return {
        "membership":          n,
        "over_nra":            _count_flag(df, "over_nra"),
        "disabled":            _count_flag(df, "status", target="DB"),
        "avg_age":             _avg(df, "age"),
        "pct_males":           _pct(df, "gender", "M"),
        "salary_bill":         salary_bill,
        "avg_salary":          salary_bill / n if n else 0.0,
        "age_wtd_by_salary":   _weighted(df, "age", "annual_salary"),
        "total_cover":         total_cover,
        "avg_multiple":        total_cover / salary_bill if salary_bill else 0.0,
        "avg_cover":           total_cover / n if n else 0.0,
        "age_wtd_by_cover":    _weighted(df, "age", "total_cover"),
        "incidence_rate":      expected_claims * 1000.0 / n if n else 0.0,
        "expected_claims":     expected_claims,
        "expected_claim_size": exp_claim_size,
        "claim_cover_ratio":   (exp_claim_size / (total_cover / n)) if n and total_cover else 0.0,
    }


def _po_eoh(df, pkey: str, eoh_by_product: dict | None) -> dict:
    max_cover = 0.0
    if df is not None and "total_cover" in df.columns and df.height:
        max_cover = float(df["total_cover"].max())

    eoh_vals = (eoh_by_product or {}).get(pkey, {})
    tech_eoh   = eoh_vals.get("tech_eoh")
    custom_eoh = eoh_vals.get("custom_eoh")
    eoh_limit  = eoh_vals.get("eoh_limit")

    members_over = 0
    cover_over   = 0.0

    if (
        pkey == "lsdb"
        and df is not None
        and df.height
        and "total_cover" in df.columns
    ):
        cover_over = float(
            df.select(
                pl.col("total_cover")
                .cast(pl.Float64, strict=False)
                .sum()
            ).item()
            or 0.0
        )

    elif eoh_limit is not None and df is not None and "total_cover" in df.columns:
        limit_f = float(eoh_limit)
        over = df.filter(pl.col("total_cover") > limit_f)
        members_over = over.height
        cover_over = (
            float((over["total_cover"] - limit_f).sum())
            if over.height else 0.0
        )

    return {
        "tech_eoh":     tech_eoh,
        "custom_eoh":   custom_eoh,
        "eoh_limit":    eoh_limit,
        "members_over": members_over,
        "cover_over":   cover_over,
        "max_cover":    max_cover,
    }


_PKEY_TO_PROFIT_MARGIN_PRODUCT = {
    "gla":                 "gla",
    "lsdb":                "lsdb",
    "ci":                  "ci",
    "spouse_gla":          "gla",
    "spouse_lsdb":         "lsdb",
    "terminal":            "ti",
    "accident_death":      "adb",
    "accident_disability": "accdb",
    "dismemberment":       "dismem",
}


def _resolved_priced_rate(ctr, pkey: str, field_key: str, default_rate: float) -> float:
    scheme = getattr(ctr, "scheme", None) if ctr is not None else None
    overrides = getattr(scheme, "priced_margin_overrides", {}) or {}
    pkey_overrides = overrides.get(pkey, {}) or {}
    val = pkey_overrides.get(field_key)
    if val is None or val == "":
        return default_rate
    try:
        return float(val)
    except (TypeError, ValueError):
        return default_rate


def _po_margins(assump, pkey: str, df=None, ctr=None) -> dict:
    return {
        "conversion_option":  _addon_pct_of_net_risk(df, "co_premium"),
        "cover_to_continue":  _addon_pct_of_net_risk(df, "ctc_premium"),
        "pro_loading":        _ctr_prb_loading(ctr, pkey),
        "profit_margin":      _assump_profit_margin(assump, pkey),
        "admin":              _assump_admin(assump, ctr, pkey),
        "business_decision":  0.0,
        "phasing":            0.0,
        "reins_profit":       0.0,
        "reins_arb":          0.0,
        "rate_basis":         1.0,
    }


def _po_priced_margins(assump, pkey: str, df=None, ctr=None) -> dict:
    m = _po_margins(assump, pkey, df=df, ctr=ctr)
    for field_key in ("profit_margin", "admin", "business_decision", "phasing", "reins_profit", "reins_arb",
                       "conversion_option", "cover_to_continue", "pro_loading"):
        m[field_key] = _resolved_priced_rate(ctr, pkey, field_key, m[field_key])
    m["rate_basis"] = _ctr_rate_basis(ctr, pkey)
    return m


def _addon_pct_of_net_risk(df, addon_col: str) -> float:
    if df is None or df.height == 0 or addon_col not in df.columns:
        return 0.0

    def _s(col):
        return _sum_col(df, col)

    basic = _s("basic_technical_premium")
    aids  = _s("aids_technical_premium")
    net   = basic + aids

    return _s(addon_col) / net if net > 0 else 0.0


def _ctr_prb_loading(ctr, pkey: str) -> float:
    if ctr is None:
        return 0.0
    scheme = getattr(ctr, "scheme", None)
    if scheme is None:
        return 0.0
    prb = getattr(scheme, "profit_refund_loading_by_product", {}) or {}
    try:
        return float(prb.get(pkey, 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _ctr_rate_basis(ctr, pkey: str) -> float:
    if ctr is None:
        return 1.0
    scheme = getattr(ctr, "scheme", None)
    if scheme is None:
        return 1.0
    rb = getattr(scheme, "rate_basis_by_product", {}) or {}
    try:
        val = rb.get(pkey, 1.0)
        return float(val) if val is not None else 1.0
    except (TypeError, ValueError):
        return 1.0


def _ctr_vat_flag(ctr) -> bool:
    if ctr is None:
        return True
    scheme = getattr(ctr, "scheme", None)
    if scheme is None:
        return True
    raw = getattr(scheme, "include_vat_commission", "Yes")
    return str(raw).strip().lower() in ("yes", "y", "true", "1")


def _resolve_commission_scale(ctr):
    scheme = getattr(ctr, "scheme", None) if ctr is not None else None

    country = str(getattr(scheme, "country_scale", None) or "South Africa").strip()
    intermediary = str(getattr(scheme, "intermediary", "") or "").strip().lower()
    is_rep = intermediary in ("rep", "representative", "direct")

    scales = _REP_COMMISSION_SCALES if is_rep else _BROKER_COMMISSION_SCALES
    scale  = scales.get(country, scales["South Africa"])
    payee  = "Representative" if is_rep else "Broker"
    return scale, country, payee


def _sum_col(df, col, safe=False):
    if col not in df.columns:
        return 0.0
    if safe:
        try:
            return float(df[col].sum())
        except Exception:  # noqa: BLE001 - safe mode returns 0.0 on any dtype/compute error
            return 0.0
    return float(df[col].sum())


def _tiered_commission_on_net(total_net_premium: float, scale) -> float:
    if total_net_premium <= 0:
        return 0.0

    remaining = total_net_premium
    commission = 0.0
    for rate, bound_gross in scale:
        rate_on_net = rate / (1.0 - rate) if rate < 1.0 else 0.0
        if bound_gross is None:
            commission += remaining * rate_on_net
            return commission
        bound_on_net = bound_gross * (1.0 - rate)
        chunk = min(remaining, bound_on_net)
        commission += chunk * rate_on_net
        remaining -= chunk
        if remaining <= 0:
            return commission
    return commission


def _product_basis_subtotal(df, pkey: str, assump, ctr) -> float:
    if df is None or df.height == 0:
        return 0.0

    def _s(col):
        return _sum_col(df, col)

    rb = _ctr_rate_basis(ctr, pkey)
    net_risk = (_s("basic_technical_premium") + _s("aids_technical_premium")) * rb

    addons = 0.0
    for _, engine_col in PO_TECH_ADDONS.get(pkey, []):
        addons += _s(engine_col) * rb

    margins = _po_margins(assump, pkey, df=df, ctr=ctr)
    profit_rate = (
        margins["profit_margin"]
        + margins["business_decision"]
        + margins["phasing"]
        + margins["reins_profit"]
        + margins["reins_arb"]
    )
    profit = net_risk * profit_rate
    admin  = net_risk * margins["admin"]

    return net_risk + addons + profit + admin


def _compute_scheme_commission_rate(all_results, assump, ctr) -> dict:
    base = {}
    for pkey in PO_PRODUCTS:
        df = all_results.get(pkey)
        if df is None:
            base[pkey] = 0.0
            continue
        active = df.filter(pl.col("product_active")) if "product_active" in df.columns else df
        base[pkey] = _product_basis_subtotal(active, pkey, assump, ctr) if active.height else 0.0

    total_net = sum(base.values())
    if total_net <= 0:
        return {pkey: 0.0 for pkey in PO_PRODUCTS}

    gla_df = all_results.get("gla")
    if gla_df is not None and "product_active" in gla_df.columns:
        member_count = gla_df.filter(pl.col("product_active")).height
    elif gla_df is not None:
        member_count = gla_df.height
    else:
        member_count = 0

    threshold = _scheme_size_threshold(assump)
    is_large = member_count >= threshold

    scale, _country, _payee = _resolve_commission_scale(ctr)
    apply_vat = _ctr_vat_flag(ctr)
    vat_pct = 0.15 if apply_vat else 0.0

    if not is_large:
        first_tier_rate = scale[0][0]
        denom = 1.0 - first_tier_rate * (1.0 + vat_pct)
        flat = first_tier_rate / denom if denom > 0 else 0.0
        return {pkey: flat for pkey in PO_PRODUCTS}

    L  = [r * (1.0 + vat_pct) for (r, _b) in scale]
    bd = [b for (_r, b) in scale]
    Qn = [x / (1.0 - x) if x < 1.0 else 0.0 for x in L]
    Sn = [(b * (1.0 - x) if b is not None else None) for b, x in zip(bd, L)]

    def _gross_comm(X):
        total, cum = 0.0, 0.0
        for i in range(len(Qn)):
            if Sn[i] is None:
                band = max(0.0, X - cum)
            else:
                band = max(0.0, min(X - cum, Sn[i])); cum += Sn[i]
            total += band * Qn[i]
        return total

    def _bucket_rate(net_base):
        if net_base <= 0:
            return 0.0
        return (_gross_comm(net_base) / (1.0 + vat_pct)) / net_base

    rating = getattr(getattr(ctr, "scheme", None), "rating", {}) or {}
    lsdb_accel = str(rating.get("lsdb", "")).strip().lower() == "accelerator"
    ci_accel   = str(rating.get("ci", "")).strip().lower() == "accelerator"

    def _b(pkey):
        return base.get(pkey, 0.0)

    gla_bucket  = (_b("gla") + _b("terminal") + _b("accident_death")
                   + _b("accident_disability") + _b("dismemberment")
                   + (_b("lsdb") if lsdb_accel else 0.0)
                   + (_b("ci")   if ci_accel   else 0.0))
    lsdb_bucket = _b("lsdb") if not lsdb_accel else 0.0
    ci_bucket   = _b("ci")   if not ci_accel   else 0.0
    sp_bucket   = _b("spouse_gla") + _b("spouse_lsdb")

    r_gla  = _bucket_rate(gla_bucket)
    r_lsdb = _bucket_rate(lsdb_bucket)
    r_ci   = _bucket_rate(ci_bucket)
    r_sp   = _bucket_rate(sp_bucket)

    rates = {
        "gla":                 r_gla,
        "lsdb":                r_gla if lsdb_accel else r_lsdb,
        "ci":                  r_gla if ci_accel   else r_ci,
        "spouse_gla":          r_sp,
        "spouse_lsdb":         r_gla,
        "terminal":            r_gla,
        "accident_death":      r_gla,
        "accident_disability": r_gla,
        "dismemberment":       r_gla,
    }
    return {pkey: rates.get(pkey, r_gla) for pkey in PO_PRODUCTS}


def _assump_profit_margin(assump, pkey: str) -> float:
    if assump is None:
        return 0.0
    tbl = getattr(assump, "tables", {}).get("profit_margins")
    if tbl is None:
        return 0.0
    mkey = _PKEY_TO_PROFIT_MARGIN_PRODUCT.get(pkey)
    if mkey is None:
        return 0.0
    row = tbl.filter(
        (pl.col("product") == mkey) & (pl.col("margin_type") == "standard")
    )
    if row.height == 0:
        return 0.0
    try:
        return float(row["margin"][0])
    except (TypeError, ValueError):
        return 0.0


def _assump_admin(assump, ctr, pkey: str) -> float:
    if assump is None:
        return 0.0
    tbl = getattr(assump, "tables", {}).get("admin_fees")
    if tbl is None:
        return 0.0

    variant = _admin_variant_for(ctr, pkey)

    row = tbl.filter(
        (pl.col("product") == pkey) & (pl.col("variant") == variant)
    )
    if row.height == 0:
        row = tbl.filter(
            (pl.col("product") == pkey) & (pl.col("variant") == "default")
        )
    if row.height == 0:
        return 0.0

    try:
        return float(row["admin"][0])
    except (TypeError, ValueError):
        return 0.0


def _admin_variant_for(ctr, pkey: str) -> str:
    scheme = getattr(ctr, "scheme", None) if ctr is not None else None
    rating = getattr(scheme, "rating", {}) or {}

    if pkey == "lsdb":
        r = str(rating.get("lsdb", "")).strip().lower()
        return "standalone" if r == "standalone" else "accelerator"

    if pkey == "ci":
        r = str(rating.get("ci", "")).strip().lower()
        return "standalone" if r == "standalone" else "accelerator"

    return "default"


def _po_buildup(
    df,
    pkey: str,
    assump,
    ctr,
    commission_rate: float = 0.0,
    vat_pct: float = DEFAULT_VAT_PCT,
    gla_salary: float = 0.0,
    gla_cover: float = 0.0,
) -> dict:
    if df is None or df.height == 0:
        return {"tech": [], "basis": [], "salary": 0.0, "cover": 0.0}

    def _s(col):
        return _sum_col(df, col)

    rate_basis = _ctr_rate_basis(ctr, pkey)

    net_risk_tech  = _s("basic_technical_premium") + _s("aids_technical_premium")
    net_risk_basis = net_risk_tech * rate_basis

    scheme = getattr(ctr, "scheme", None) if ctr is not None else None
    express_map = getattr(scheme, "express_over_gla", {}) or {}
    if express_map.get(pkey, False):
        salary = gla_salary
        cover  = gla_cover
    else:
        _den   = df.filter(pl.col("total_cover") > 0)
        salary = float(_den["annual_salary"].sum()) if "annual_salary" in _den.columns else 0.0
        cover  = float(_den["total_cover"].sum())   if "total_cover"   in _den.columns else 0.0

    margins = _po_margins(assump, pkey, df=df, ctr=ctr)

    profit_margin_r      = _resolved_priced_rate(ctr, pkey, "profit_margin",     margins["profit_margin"])
    business_decision_r  = _resolved_priced_rate(ctr, pkey, "business_decision", margins["business_decision"])
    phasing_r            = _resolved_priced_rate(ctr, pkey, "phasing",           margins["phasing"])
    reins_profit_r       = _resolved_priced_rate(ctr, pkey, "reins_profit",      margins["reins_profit"])
    reins_arb_r          = _resolved_priced_rate(ctr, pkey, "reins_arb",         margins["reins_arb"])
    admin_r              = _resolved_priced_rate(ctr, pkey, "admin",             margins["admin"])

    profit_rate_display = (
        profit_margin_r
        + business_decision_r
        + phasing_r
        + reins_profit_r
        + reins_arb_r
    )
    admin_rate = admin_r

    _ADDON_MARGIN_FIELD = {
        "co_premium":  "conversion_option",
        "ctc_premium": "cover_to_continue",
        "prb_premium": "pro_loading",
    }

    def _addon_resolved_rate(engine_col):
        field_key = _ADDON_MARGIN_FIELD.get(engine_col)
        if field_key is None:
            return None
        addon_actual = _s(engine_col)
        addon_default = (addon_actual / net_risk_tech if net_risk_tech else 0.0)
        return _resolved_priced_rate(ctr, pkey, field_key, addon_default)

    tech_rows: list[tuple[str, float, float | None]] = [
        ("Net Risk Premium", net_risk_tech, None)
    ]
    for label, engine_col in PO_TECH_ADDONS.get(pkey, []):
        addon_rate = _addon_resolved_rate(engine_col)
        if addon_rate is not None:
            tech_rows.append((label, net_risk_tech * addon_rate, None))
        else:
            tech_rows.append((label, _s(engine_col), None))
    tech_profit = _s("profit_margin_amount")
    tech_rows.append(("Profit ", tech_profit, profit_rate_display))

    basis_rows: list[tuple[str, float, float | None]] = [
        ("Net Risk Premium", net_risk_basis, None)
    ]
    for label, engine_col in PO_TECH_ADDONS.get(pkey, []):
        addon_rate = _addon_resolved_rate(engine_col)
        if addon_rate is not None:
            basis_rows.append((label, net_risk_basis * addon_rate, None))
        else:
            basis_rows.append((label, _s(engine_col) * rate_basis, None))

    basis_profit = net_risk_basis * profit_rate_display
    basis_rows.append(("Profit ", basis_profit, profit_rate_display))

    admin               = net_risk_basis * admin_rate
    subtotal_with_admin = sum(v for _, v, _ in basis_rows) + admin
    commission          = subtotal_with_admin * commission_rate
    vat                 = commission * vat_pct
    gross               = subtotal_with_admin + commission + vat

    basis_rows.extend([
        ("Admin & Expenses ",  admin,      None),
        ("Normal Commission",  commission, None),
        ("Initial Commission", 0.0,        None),
        ("VAT on Commission",  vat,        None),
        ("Gross Risk Prem ",   gross,      None),
    ])

    return {"tech": tech_rows, "basis": basis_rows, "salary": salary, "cover": cover, "rate_basis": rate_basis}


def _weighted(df: pl.DataFrame, val_col: str, weight_col: str) -> float:
    if val_col not in df.columns or weight_col not in df.columns:
        return 0.0
    w = float(df[weight_col].sum())
    if w == 0:
        return 0.0
    return float((df[val_col] * df[weight_col]).sum()) / w


def _pct(df: pl.DataFrame, col: str, target: str) -> float:
    if col not in df.columns or df.height == 0:
        return 0.0
    return df.filter(pl.col(col) == target).height / df.height


def _count_flag(df, col, target=None) -> int:
    if col not in df.columns:
        return 0
    if target is None:
        return int((df[col] == True).sum())
    return int((df[col] == target).sum())


def _avg(df, col) -> float:
    if col not in df.columns or df.height == 0:
        return 0.0
    return float(df[col].mean())


def _zero_metrics() -> dict:
    return {
        "basic_premium":         0.0,
        "co_premium":            0.0,
        "ctc_premium":           0.0,
        "waiver_premium":        0.0,
        "prb_premium":           0.0,
        "reinstatement_premium": 0.0,
        "pup":                   0.0,
        "sub_total":             0.0,
        "commission":            0.0,
        "vat":                   0.0,
        "total_premium":         0.0,
        "total_salary_annual":   0.0,
        "total_cover":           0.0,
        "n_members":             0,
        "salary_weighted_age":   0.0,
        "cover_weighted_age":    0.0,
        "pct_males":             0.0,
    }


def _empty_product() -> dict:
    return {
        "scheme": _zero_metrics(),
        "cats":   {c: _zero_metrics() for c in range(1, N_CATEGORIES + 1)},
        "active": {c: False for c in range(1, N_CATEGORIES + 1)},
    }


def _zero_profile() -> dict:
    return {
        "membership":          0,
        "over_nra":            0,
        "disabled":            0,
        "avg_age":             0.0,
        "pct_males":           0.0,
        "salary_bill":         0.0,
        "avg_salary":          0.0,
        "age_wtd_by_salary":   0.0,
        "total_cover":         0.0,
        "avg_multiple":        0.0,
        "avg_cover":           0.0,
        "age_wtd_by_cover":    0.0,
        "incidence_rate":      0.0,
        "expected_claims":     0.0,
        "expected_claim_size": 0.0,
        "claim_cover_ratio":   0.0,
    }

def _reallocate_cb_basic_pooled(prod: dict, split_mode: str = "Sum Assured", raw_df=None) -> None:
    scheme = prod.get("scheme") or {}
    cats = prod.get("cats") or {}
    scheme_basic = float(scheme.get("basic_premium", 0.0) or 0.0)
    scheme_cover = float(scheme.get("total_cover", 0.0) or 0.0)
    if scheme_cover <= 0.0 or not cats:
        return
    _pkey = prod.get("_pkey", "")
    if _pkey == "fc":
        scheme_pool = float(scheme.get("famcov", 0.0) or 0.0) or scheme_cover
        _cover_key = "famcov"
    else:
        scheme_pool = scheme_cover
        _cover_key = "total_cover"

    is_risk_profile = (split_mode == "Risk Profile")
    is_flat_rand_per_mem = (split_mode == "Flat Rand per Mem" and _pkey == "fc")

    pooled_rate = (scheme_basic / scheme_pool) if not is_risk_profile else 0.0

    cat_member_counts = {}
    if is_flat_rand_per_mem and raw_df is not None and \
       {"product_active", "age", "ceasing_age", "category_number"} <= set(raw_df.columns):
        tt_active_df = raw_df.filter(
            pl.col("product_active") & (pl.col("age") <= pl.col("ceasing_age"))
        )
        scheme_members = float(tt_active_df.height)
        for cat_num in cats:
            cat_member_counts[cat_num] = float(
                tt_active_df.filter(pl.col("category_number") == cat_num).height
            )
    else:
        scheme_members = float(scheme.get("n_members", 0.0) or 0.0)

    pooled_rate_per_member = (scheme_basic / scheme_members) if (is_flat_rand_per_mem and scheme_members > 0) else 0.0

    s_sub  = float(scheme.get("sub_total", 0.0) or 0.0)
    s_comm = float(scheme.get("commission", 0.0) or 0.0)
    s_vat  = float(scheme.get("vat", 0.0) or 0.0)
    comm_pct = (s_comm / s_sub) if s_sub else 0.0
    vat_pct  = (s_vat / s_comm) if s_comm else 0.0
    for m in cats.values():
        cat_cover = float(m.get(_cover_key, 0.0) or 0.0)
        old_basic = float(m.get("basic_premium", 0.0) or 0.0)
        old_sub   = float(m.get("sub_total", 0.0) or 0.0)

        if is_risk_profile:
            new_basic = old_basic
        elif is_flat_rand_per_mem:
            cat_key = [k for k, v in cats.items() if v is m]
            cat_members = (cat_member_counts.get(cat_key[0]) if cat_key and cat_member_counts
                           else float(m.get("n_members", 0.0) or 0.0)) or 0.0
            new_basic = pooled_rate_per_member * cat_members
        else:
            new_basic = pooled_rate * cat_cover

        addons    = old_sub - old_basic
        new_sub   = new_basic + addons
        _bucket = float(prod.get("cb_bucket_rate", 0.0) or 0.0)
        if _bucket and prod.get("express_over_gla"):
            new_comm = _bucket * s_sub * (cat_cover / scheme_cover)
        elif _bucket:
            new_comm = _bucket * new_sub
        else:
            new_comm = new_sub * comm_pct
        new_vat   = new_comm * vat_pct
        m["basic_premium"] = new_basic
        m["sub_total"]     = new_sub
        m["commission"]    = new_comm
        m["vat"]           = new_vat
        m["total_premium"] = new_sub + new_comm + new_vat

    if _pkey in ("gla", "lsdb", "ci", "spouse_gla", "spouse_lsdb", "terminal", "accident_death", "accident_disability", "dismemberment", "fc", "education"):
        scheme_commission = sum(
            float(m.get("commission", 0.0) or 0.0)
            for m in cats.values()
        )
        scheme_vat = sum(
            float(m.get("vat", 0.0) or 0.0)
            for m in cats.values()
        )

        scheme["commission"] = scheme_commission
        scheme["vat"] = scheme_vat
        scheme["total_premium"] = (
            float(scheme.get("sub_total", 0.0) or 0.0)
            + scheme_commission
            + scheme_vat
        )


def _ext_fc_row_calc(e, profit_rate, admin_rate, commission_pct, vat_pct):
    profit = e * profit_rate
    admin = e * admin_rate
    subtotal1 = e + profit + admin
    comm_gross_rate = commission_pct / (1.0 - commission_pct) if commission_pct < 1.0 else 0.0
    initial_commission = subtotal1 * comm_gross_rate
    subtotal2 = subtotal1 + initial_commission
    vat_gross_rate = vat_pct / (1.0 - vat_pct) if vat_pct < 1.0 else 0.0
    vat = subtotal2 * vat_gross_rate
    gross = subtotal2 + vat
    return {
        "risk_premium": e, "profit": profit, "admin": admin,
        "initial_commission": initial_commission, "normal_commission": None,
        "vat": vat, "gross": gross,
    }


def _ext_fc_age_band_label(start_age, end_age):
    s = round(start_age)
    e = round(end_age)
    return f"{s}+" if e >= 100 else f"{s} to {e}"


def _ext_fc_population(all_results, scheme):
    fc_df = all_results.get("fc")
    out = {}
    if fc_df is not None and fc_df.height > 0 and "category_number" in fc_df.columns:
        active = fc_df.filter(pl.col("product_active")) if "product_active" in fc_df.columns else fc_df
        agg_exprs = [pl.len().alias("n_members")]
        if "annual_salary" in active.columns:
            agg_exprs.append(pl.col("annual_salary").sum().alias("salary_bill"))
        grp = active.group_by("category_number").agg(agg_exprs)
        for row in grp.iter_rows(named=True):
            out[int(row["category_number"])] = {
                "n_members": int(row["n_members"]),
                "salary_bill": float(row.get("salary_bill", 0.0) or 0.0),
            }
    pct_male_map = getattr(scheme, "ext_fc_pct_male", {}) or {}
    avg_salary_map = getattr(scheme, "ext_fc_avg_salary", {}) or {}
    parents_sel = getattr(scheme, "ext_fc_parents_selected", {}) or {}
    family_sel = getattr(scheme, "ext_fc_extended_family_selected", {}) or {}
    for cat, pct_male in pct_male_map.items():
        cat = int(cat)
        entry = out.setdefault(cat, {"n_members": 0, "salary_bill": 0.0})
        entry["pct_male"] = float(pct_male) if pct_male is not None else None
        entry["avg_salary"] = float(avg_salary_map.get(cat)) if avg_salary_map.get(cat) is not None else None
        entry["parents_selected"] = str(parents_sel.get(cat, "No")).strip().lower() == "yes"
        entry["extended_family_selected"] = str(family_sel.get(cat, "No")).strip().lower() == "yes"
    return out


def _ext_fc_rate_rows(ext_fc_df, category_number, relative_type,
                       profit_rate, admin_rate, commission_pct, vat_pct):
    sub = ext_fc_df.filter(
        (pl.col("category_number") == category_number) &
        (pl.col("relative_type") == relative_type)
    ).sort("band_index")
    rows = []
    for r in sub.iter_rows(named=True):
        calc = _ext_fc_row_calc(
            float(r["risk_premium_per_life_pm"]), profit_rate, admin_rate,
            commission_pct, vat_pct,
        )
        rows.append({
            "age_band": _ext_fc_age_band_label(r["start_age"], r["end_age"]),
            "cover": float(r["cover"]),
            **calc,
        })
    return rows


def _cb_ext_fc(all_results, scheme, assump, ctr=None):
    ext_fc_df = all_results.get("ext_fc")
    if ext_fc_df is None or ext_fc_df.height == 0:
        return None

    ext_fc_params = (getattr(assump, "tables", {}) or {}).get("ext_fc_params")
    def _param(name, default):
        if ext_fc_params is None:
            return default
        row = ext_fc_params.filter(pl.col("param_name") == name)
        if row.height == 0:
            return default
        try:
            return float(row["param_value"][0])
        except (TypeError, ValueError):
            return default

    default_rate_basis     = _param("rate_basis", 1.0)
    default_commission_pct = _param("commission_pct", DEFAULT_COMMISSION_PCT)

    profit_std  = _profit_rate_for(assump, "gla")
    admin_std   = _admin_rate_for(assump, "ext_fc", scheme)
    vat_pct     = _vat_on_commission_for(scheme)
    comm_std    = default_commission_pct

    profit_priced      = _resolved_priced_rate(ctr, "ext_fc", "profit_margin", profit_std) if ctr is not None else profit_std
    admin_priced       = _resolved_priced_rate(ctr, "ext_fc", "admin", admin_std) if ctr is not None else admin_std
    rate_basis_priced  = _resolved_priced_rate(ctr, "ext_fc", "rate_basis", default_rate_basis) if ctr is not None else default_rate_basis
    commission_priced  = _resolved_priced_rate(ctr, "ext_fc", "commission_pct", comm_std) if ctr is not None else comm_std

    population = _ext_fc_population(all_results, scheme)
    categories = sorted(int(c) for c in ext_fc_df["category_number"].unique().to_list())

    cats_out = {}
    for cat in categories:
        pop = population.get(cat, {})
        entry = {
            "n_members":   pop.get("n_members", 0),
            "salary_bill": pop.get("salary_bill", 0.0),
            "pct_male":    pop.get("pct_male"),
            "avg_salary":  pop.get("avg_salary"),
            "parents_selected":          pop.get("parents_selected", False),
            "extended_family_selected":  pop.get("extended_family_selected", False),
            "parents_standard":  _ext_fc_rate_rows(ext_fc_df, cat, "parents", profit_std, admin_std, comm_std, vat_pct),
            "extended_family_standard": _ext_fc_rate_rows(ext_fc_df, cat, "extended_family", profit_std, admin_std, comm_std, vat_pct),
        }
        def _priced_rows(relative_type):
            base_rows = _ext_fc_rate_rows(ext_fc_df, cat, relative_type, 1.0, 0.0, 0.0, 0.0)  # noqa: B023 - called in this iteration
            out_rows = []
            for br in base_rows:
                e_priced = br["risk_premium"] * rate_basis_priced
                calc = _ext_fc_row_calc(e_priced, profit_priced, admin_priced, commission_priced, vat_pct)
                out_rows.append({"age_band": br["age_band"], "cover": br["cover"], **calc})
            return out_rows
        entry["parents_priced"] = _priced_rows("parents")
        entry["extended_family_priced"] = _priced_rows("extended_family")
        cats_out[cat] = entry

    return {
        "_pkey": "ext_fc",
        "_is_ext_fc": True,
        "_display_title": "Extended Family Cover",
        "categories": cats_out,
        "profit_rate_standard": profit_std, "admin_rate_standard": admin_std,
        "rate_basis_standard": default_rate_basis, "commission_pct_standard": comm_std,
        "profit_rate_priced": profit_priced, "admin_rate_priced": admin_priced,
        "rate_basis_priced": rate_basis_priced, "commission_pct_priced": commission_priced,
        "vat_pct": vat_pct,
    }


def _cb_education(all_results, scheme, assump, ctr=None,
                   commission_pct: float = DEFAULT_COMMISSION_PCT,
                   vat_pct: float = DEFAULT_VAT_PCT) -> dict | None:
    active_pkey = getattr(scheme, "education_product", None)
    if active_pkey is None:
        return None
    df = all_results.get(active_pkey)
    if df is None or df.height == 0:
        return None

    profit_rate = _profit_rate_for_education(assump, active_pkey)
    admin_rate  = _admin_rate_for_education(assump, active_pkey)
    rate_basis  = _rate_basis_for(scheme, active_pkey)
    freq_divisor = _freq_divisor_for(scheme)

    resolved_vat_pct = _vat_on_commission_for(scheme)

    active_df = df.filter(pl.col("product_active")) if "product_active" in df.columns else df
    def _s(c):
        return _sum_col(active_df, c)
    rb = rate_basis
    tech_net = (_s("basic_technical_premium") + _s("aids_technical_premium")) * rb
    education_net_base = tech_net * (1.0 + profit_rate + admin_rate)

    resolved_commission_pct = 0.0
    if ctr is not None:
        scale, _country, _payee = _resolve_commission_scale(ctr)
        resolved_commission_pct = _vat_loaded_bucket_rate(
            education_net_base, scale, resolved_vat_pct
        )
    else:
        resolved_commission_pct = commission_pct

    prod = _aggregate_product(
        df, resolved_commission_pct, resolved_vat_pct,
        profit_rate=profit_rate, admin_rate=admin_rate,
        rate_basis=rate_basis, freq_divisor=freq_divisor,
    )

    express = bool((getattr(scheme, "express_over_gla", {}) or {}).get(active_pkey, False))
    prod["express_over_gla"] = express
    prod["denominator_salary"] = prod["scheme"].get("total_salary_annual", 0.0)
    prod["denominator_cover"]  = prod["scheme"].get("total_cover", 0.0)
    prod["freq_divisor"] = freq_divisor
    prod["cb_bucket_rate"] = resolved_commission_pct
    prod["_pkey"] = "education"

    try:
        from ctr_loader.products_registry import PRODUCT_REGISTRY
        prod["_display_title"] = PRODUCT_REGISTRY[active_pkey].display_name
    except (ImportError, KeyError):
        prod["_display_title"] = None

    _reallocate_cb_basic_pooled(
        prod, split_mode=_resolve_split_mode(scheme, active_pkey), raw_df=df
    )
    return prod


def _profit_rate_for_education(assump, pkey: str) -> float:
    if assump is None:
        return 0.0
    tbl = getattr(assump, "tables", {}).get("product_limits")
    if tbl is None:
        return 0.0
    row = tbl.filter((pl.col("product") == pkey) & (pl.col("param_name") == "profit_margin"))
    if row.height == 0:
        return 0.0
    try:
        return float(row["param_value"][0])
    except (TypeError, ValueError):
        return 0.0


def _admin_rate_for_education(assump, pkey: str) -> float:
    if assump is None:
        return 0.0
    tbl = getattr(assump, "tables", {}).get("product_limits")
    if tbl is None:
        return 0.0
    row = tbl.filter((pl.col("product") == pkey) & (pl.col("param_name") == "admin_rate"))
    if row.height == 0:
        return 0.0
    try:
        return float(row["param_value"][0])
    except (TypeError, ValueError):
        return 0.0


def _po_margins_education(assump, pkey: str, ctr=None) -> dict:
    profit_default = _profit_rate_for_education(assump, pkey)
    admin_default  = _admin_rate_for_education(assump, pkey)
    return {
        "conversion_option":  0.0,
        "cover_to_continue":  0.0,
        "pro_loading":        0.0,
        "profit_margin":      _resolved_priced_rate(ctr, "education", "profit_margin", profit_default),
        "admin":              _resolved_priced_rate(ctr, "education", "admin", admin_default),
        "business_decision":  0.0,
        "phasing":            0.0,
        "reins_profit":       0.0,
        "reins_arb":          0.0,
        "rate_basis":         1.0,
    }


def _po_buildup_education(df, pkey: str, assump, ctr,
                           commission_rate: float = 0.0,
                           vat_pct: float = DEFAULT_VAT_PCT,
                           express_over_gla: bool = False,
                           gla_salary: float = 0.0,
                           gla_cover: float = 0.0) -> dict:
    if df is None or df.height == 0:
        return {"tech": [], "basis": [], "salary": 0.0, "cover": 0.0}

    def _s(col):
        return _sum_col(df, col)

    rate_basis = _ctr_rate_basis(ctr, pkey)
    net_risk_tech  = _s("basic_technical_premium") + _s("aids_technical_premium")
    net_risk_basis = net_risk_tech * rate_basis

    if express_over_gla:
        salary = gla_salary
        cover  = gla_cover
    else:
        _den = df.filter(pl.col("total_cover") > 0) if "total_cover" in df.columns else df
        salary = float(_den["annual_salary"].sum()) if "annual_salary" in _den.columns else 0.0
        cover  = float(_den["total_cover"].sum())   if "total_cover"   in _den.columns else 0.0

    profit_default = _profit_rate_for_education(assump, pkey)
    admin_default  = _admin_rate_for_education(assump, pkey)
    profit_rate = _resolved_priced_rate(ctr, "education", "profit_margin", profit_default)
    admin_rate  = _resolved_priced_rate(ctr, "education", "admin", admin_default)

    tech_rows: list[tuple[str, float, float | None]] = [("Net Risk Premium", net_risk_tech, None)]
    for label, engine_col in PO_TECH_ADDONS.get("education", []):
        tech_rows.append((label, _s(engine_col), None))
    tech_profit = net_risk_tech * profit_rate
    tech_rows.append(("Profit ", tech_profit, profit_rate))

    basis_rows: list[tuple[str, float, float | None]] = [("Net Risk Premium", net_risk_basis, None)]
    for label, engine_col in PO_TECH_ADDONS.get("education", []):
        basis_rows.append((label, _s(engine_col) * rate_basis, None))
    basis_profit = net_risk_basis * profit_rate
    basis_rows.append(("Profit ", basis_profit, profit_rate))

    admin = net_risk_basis * admin_rate
    subtotal_with_admin = sum(v for _, v, _ in basis_rows) + admin
    commission = subtotal_with_admin * commission_rate
    vat = commission * vat_pct
    gross = subtotal_with_admin + commission + vat

    basis_rows.extend([
        ("Admin & Expenses ",  admin,      None),
        ("Normal Commission",  commission, None),
        ("Initial Commission", 0.0,        None),
        ("VAT on Commission",  vat,        None),
        ("Gross Risk Prem ",   gross,      None),
    ])

    return {"tech": tech_rows, "basis": basis_rows, "salary": salary, "cover": cover, "rate_basis": rate_basis}
