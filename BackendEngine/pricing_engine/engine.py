from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterable
import polars as pl

from .helpers import (
    compute_age, pivot_categories_wide, attach_category,
    mark_product_active, resolve_nra, compute_ceasing_age,
    apply_factor, compute_cover, apply_cover_caps, apply_income_taper,
    apply_ctr_max_cover,
    tax_gross_up, finalise_cover,
    co_premium_gla, co_premium_lsdb, co_premium_spouse, co_premium_ci, zero_addon,
    apply_post_rate_adjust, compute_qx, apply_profit_margin,
    apply_special_premium, attach_gla_cover_for_ti,
    zero_if_status,
    deactivate_when_status,
    apply_prb_premium,
)
from .recipes import RECIPES
from ctr_loader.products_registry import PRODUCT_REGISTRY


@dataclass
class PricingContext:
    scheme: Any
    globals: dict[str, Any]
    calc_date: date
    member_count: int
    scheme_size: str
    secondary_industry: str = ""
    primary_industry: str = ""
    region_selected: str = ""
    assump_limits: dict[str, dict[str, Any]] = field(default_factory=dict)
    prior_results: dict[str, pl.DataFrame] = field(default_factory=dict)

    def limits_for(self, product_key: str) -> dict[str, Any]:
        return self.assump_limits.get(product_key, {})


def apply_cease_override(df, product_key, ctx):
    gla = ctx.prior_results.get("gla")
    if gla is None:
        return df
    eog = (getattr(ctx.scheme, "express_over_gla", {}) or {})
    if product_key != "gla" and eog.get(product_key, False):
        col = "ceasing_age"
    elif product_key == "spouse_gla":
        col = "ceasing_age"
    elif product_key == "spouse_lsdb":
        col = "nra_final"
    else:
        return df
    ref = gla.select(["member_no", col]).rename({col: "__cease_ref"})
    return (df.join(ref, on="member_no", how="left")
              .with_columns(pl.coalesce(["__cease_ref", "ceasing_age"]).alias("ceasing_age"))
              .drop("__cease_ref"))


def build_context(ctr, assump) -> PricingContext:
    scheme = ctr.scheme
    member_count = ctr.members["gla_sheet"].height
    threshold = assump.globals.get("scheme_size_large_threshold", 400)
    scheme_size = "Large" if member_count >= threshold else "Small"

    limits = {}
    plims = assump.tables.get("product_limits")
    if plims is not None:
        for row in plims.iter_rows(named=True):
            p = row["product"]
            limits.setdefault(p, {})[row["param_name"]] = _coerce_limit(
                row["param_value"], row.get("param_type")
            )

    return PricingContext(
        scheme=scheme,
        globals=assump.globals,
        calc_date=scheme.commencement_date,
        member_count=member_count,
        scheme_size=scheme_size,
        secondary_industry=getattr(scheme, "secondary", "") or "",
        primary_industry=getattr(scheme, "primary", "") or "",
        region_selected=scheme.region or "",
        assump_limits=limits,
    )


def _coerce_limit(val, kind):
    if val is None:
        return None
    if kind == "int":
        return int(float(val))
    if kind == "float":
        return float(val)
    if kind == "bool":
        return str(val).lower() in ("true", "1", "yes")
    return str(val)


_SPOUSE_PRODUCTS: frozenset[str] = frozenset({"spouse_gla", "spouse_lsdb"})


def _resolve_member_role(product_key: str, spec, scheme) -> str:
    if product_key in _SPOUSE_PRODUCTS:
        return "sp_sheet" if scheme.spouse_use_sp_sheet else "gla_sheet"
    if product_key == "fc":
        return "fc_sheet"
    if product_key == "gip":
        return "gip_sheet"
    if product_key == "tip":
        return "tip_sheet"
    return "gla_sheet"

def _resolve_rate_factors(recipe: dict, scheme) -> list:
    if "rate_factors" in recipe:
        return recipe["rate_factors"]
    if scheme.spouse_use_sp_sheet:
        return recipe["rate_factors_sp_source"]
    return recipe["rate_factors_gla_source"]


def _require_columns(df: pl.DataFrame, needed: Iterable[str], where: str) -> None:
    missing = [c for c in needed if c not in df.columns]
    if missing:
        raise KeyError(
            f"[engine:{where}] missing required columns {missing}. "
            f"Available columns: {df.columns}"
        )


_CO_ADDON_HANDLERS = {
    "co_premium_gla":    co_premium_gla,
    "co_premium_lsdb":   co_premium_lsdb,
    "co_premium_spouse": co_premium_spouse,
    "co_premium_ci":     co_premium_ci,
  
}


def _apply_addons(df, recipe, cats_wide, assump_slice, ctx) -> pl.DataFrame:
    ineligible = recipe.get("ineligible_status", ())

    for addon_name in recipe.get("add_ons", []):
        handler = _CO_ADDON_HANDLERS.get(addon_name)
        if handler is None:
            df = zero_addon(df, ctx, target=addon_name.replace("_premium", "") + "_premium")
            continue

        if addon_name == "co_premium_lsdb":
            df = handler(df, cats_wide, assump_slice, ctx,
                         target="co_premium",
                         ineligible_statuses=ineligible)
        else:
            df = handler(df, cats_wide, assump_slice, ctx, target="co_premium")

    return df

def _load_and_index_members(product_key: str, ctr, spec, ctx: PricingContext) -> pl.DataFrame:
    member_role = _resolve_member_role(product_key, spec, ctx.scheme)
    raw_df = ctr.members.get(member_role)

    if raw_df is None:
        raise RuntimeError(
            f"[engine:{product_key}] ctr.members has no '{member_role}' key. "
            f"Available keys: {list(ctr.members.keys())}"
        )
    if raw_df.height == 0:
        raise RuntimeError(
            f"[engine:{product_key}] members sheet '{member_role}' is empty (0 rows)."
        )
    if len(raw_df.columns) == 0:
        raise RuntimeError(
            f"[engine:{product_key}] members sheet '{member_role}' has no columns."
        )

    df = raw_df.with_row_index("member_no", offset=1)
    return df


def _attach_categories_and_eligibility(df: pl.DataFrame, product_key: str, recipe, ctr, ctx: PricingContext):
    cats_wide = pivot_categories_wide(ctr.categories, product_key)
    df = attach_category(df, cats_wide)
    df = compute_age(df, ctx.calc_date)

    active_cats = ctr.active_categories.get(product_key, ())
    _EDUCATION_PKEYS_ENGINE = ("educate_a_child", "education_guardian")
    if product_key in _EDUCATION_PKEYS_ENGINE:
        education_rating = "Yes" if ctx.scheme.education_product == product_key else "No"
        df = mark_product_active(df, education_rating, active_cats)
    else:
        df = mark_product_active(df, ctx.scheme.rating.get(product_key), active_cats)
    df = deactivate_when_status(df, recipe.get("ineligible_status", ()))
    _EDUCATION_PKEYS_NRA_ATTACH = ("educate_a_child", "education_guardian")
    if product_key in _EDUCATION_PKEYS_NRA_ATTACH and "nra_cat" not in df.columns:
        edu_nra = getattr(ctx.scheme, "education_nra", {}) or {}
        edu_deferred = getattr(ctx.scheme, "education_deferred", {}) or {}
        edu_deferred_age = getattr(ctx.scheme, "education_deferred_age", {}) or {}
        all_cats = set(edu_nra) | set(edu_deferred) | set(edu_deferred_age)
        if all_cats:
            edu_cat_rows = [{
                "category_number": int(cat),
                "nra_cat": edu_nra.get(cat),
                "deferred_retirals": edu_deferred.get(cat),
                "deferred_retirement_age": edu_deferred_age.get(cat),
            } for cat in all_cats]
            edu_cat_df = pl.DataFrame(edu_cat_rows)
            df = df.join(edu_cat_df, on="category_number", how="left")

    df = resolve_nra(df, category_col="nra_cat") if "nra_cat" in df.columns else resolve_nra(df)
    if product_key == "fc" and "deferred_retirals" not in df.columns:
        _gla = ctx.prior_results.get("gla")
        if _gla is not None and "deferred_retirals" in _gla.columns:
            _defmap = (_gla.select(["category_number", "deferred_retirals", "deferred_retirement_age"])
                           .unique(subset=["category_number"]))
            df = df.join(_defmap, on="category_number", how="left")

    df = compute_ceasing_age(df, recipe["ceasing_source"])
    df = apply_cease_override(df, product_key, ctx)
    return df, cats_wide


def _apply_rate_factors(df: pl.DataFrame, product_key: str, recipe, basis, assump, ctx: PricingContext):
    df = df.with_columns(pl.lit(1.0).alias("rate_basic"))
    basis_slice = basis.for_product(product_key)
    assump_slice = assump.for_product(product_key)

    if product_key == "ci":
        basis_slice["mortality"] = basis.tables["mortality"]

    rate_factors = _resolve_rate_factors(recipe, ctx.scheme)
    for factor in rate_factors:
        df = apply_factor(df, factor, basis_slice, assump_slice, ctx, target="rate_basic")

    aids_factors = recipe.get("aids_factors", [])
    if aids_factors:
        df = df.with_columns(pl.lit(1.0).alias("rate_aids"))
        for factor in aids_factors:
            df = apply_factor(df, factor, basis_slice, assump_slice, ctx, target="rate_aids")
    else:
        df = df.with_columns(pl.lit(0.0).alias("rate_aids"))

    return df, basis_slice, assump_slice


def _compute_cover_and_tax(df: pl.DataFrame, product_key: str, recipe, ctx: PricingContext, assump_slice):
    if recipe["cover_rule"] == "multiple_or_data_or_gla":
        df = attach_gla_cover_for_ti(df, ctx.prior_results)

    df = compute_cover(df, recipe)
    if product_key == "lsdb":
        df = apply_income_taper(df)
    df = apply_ctr_max_cover(
        df, ctx, product_key,
        assump_fallback_limits=ctx.limits_for(product_key),
        assump_fallback_key="max_cover",
    )

    if recipe.get("cover_caps"):
        df = apply_cover_caps(df, recipe, ctx, assump_slice, ctx.prior_results)

    if recipe.get("apply_tax_gross_up") and "tax_tables" in assump_slice:
        df = tax_gross_up(df, assump_slice["tax_tables"])
    else:
        df = df.with_columns(pl.lit(0.0).alias("tax_on_cover"))

    df = finalise_cover(df, recipe)
    return df


def _compute_premiums(df: pl.DataFrame, recipe, basis_slice, assump_slice, ctx: PricingContext, cats_wide):
    if recipe.get("special_premium"):
        df = apply_special_premium(df, recipe["special_premium"], basis_slice, ctx.prior_results,
                            assump_tables=assump_slice, ctx=ctx)
    else:
        df = df.with_columns([
            (pl.col("rate_basic") / 1000 * pl.col("total_cover")).alias("basic_technical_premium"),
            (pl.col("rate_aids")  / 1000 * pl.col("total_cover")).alias("aids_technical_premium"),
        ])
        for adj in recipe.get("post_rate_adjust", []):
            df = apply_post_rate_adjust(df, adj, assump_slice, basis_tables=basis_slice, ctx=ctx)

    df = _apply_addons(df, recipe, cats_wide, assump_slice, ctx)

    for col in ["co_premium", "waiver_premium", "prb_premium",
                "ctc_premium", "reinstatement_premium"]:
        if col not in df.columns:
            df = df.with_columns(pl.lit(0.0).alias(col))

    df = df.with_columns(
        (pl.col("basic_technical_premium") +
         pl.col("aids_technical_premium") +
         pl.col("co_premium") +
         pl.col("waiver_premium") +
         pl.col("prb_premium") +
         pl.col("ctc_premium") +
         pl.col("reinstatement_premium")
        ).alias("total_annual_premium")
    )
    return df


def _finalise_pricing(df: pl.DataFrame, recipe, ctx: PricingContext, product_key: str, assump_slice):
    zero_statuses = recipe.get("zero_when_status_in", ())
    if zero_statuses:
        df = zero_if_status(df, zero_statuses)

    df = compute_qx(df)
    df = apply_profit_margin(df, recipe, assump_slice)
    if not recipe.get("skip_prb_premium", False):
        df = apply_prb_premium(df, ctx, product_key)
    return df.sort("member_no")


def price(product_key: str, ctr, basis, assump, ctx: PricingContext) -> pl.DataFrame:
    recipe = RECIPES[product_key]
    spec = PRODUCT_REGISTRY[product_key]

    df = _load_and_index_members(product_key, ctr, spec, ctx)
    df, cats_wide = _attach_categories_and_eligibility(df, product_key, recipe, ctr, ctx)
    df, basis_slice, assump_slice = _apply_rate_factors(df, product_key, recipe, basis, assump, ctx)
    df = _compute_cover_and_tax(df, product_key, recipe, ctx, assump_slice)
    df = _compute_premiums(df, recipe, basis_slice, assump_slice, ctx, cats_wide)
    df = _finalise_pricing(df, recipe, ctx, product_key, assump_slice)
    return df


def price_all_products(ctr, basis, assump, ctx, *, on_product_priced=None) -> dict[str, pl.DataFrame]:
    import time as _time
    from .helpers import build_ext_fc_rate_card
    all_results: dict[str, pl.DataFrame] = {}
    for pkey in ctr.active_products:
        _t0 = _time.time()
        if pkey == "ext_fc":
            df = build_ext_fc_rate_card(ctr.scheme, ctr, basis.for_product("ext_fc"))
        else:
            df = price(pkey, ctr, basis, assump, ctx)
            ctx.prior_results[pkey] = df
        all_results[pkey] = df
        if on_product_priced is not None:
            on_product_priced(pkey, df, _time.time() - _t0)
    return all_results
