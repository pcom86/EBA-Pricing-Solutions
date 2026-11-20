from __future__ import annotations

import json
import logging
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType

import polars as pl
from gap_config.storage import local_directory

from .products_registry import PRODUCT_REGISTRY
from .snapshot_bundle_utils import (
    BUNDLE_NAME,
    _bundle_is_fresh,
    _read_from_bundle,
    _read_threaded,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BasisBundle:
    version: str
    built_at: str
    source_sha256: str
    tables: dict[str, pl.DataFrame]

    def for_product(self, product_key: str) -> dict[str, pl.DataFrame]:
        spec = PRODUCT_REGISTRY[product_key]
        out: dict[str, pl.DataFrame] = {}

        def _filter(sheet: str, alias: tuple[str, ...]) -> pl.DataFrame | None:
            if not alias or sheet not in self.tables:
                return None
            df = self.tables[sheet]
            if "product" not in df.columns:
                return None
            sub = df.filter(pl.col("product").is_in(list(alias)))
            return sub if sub.height > 0 else None

        for sheet, alias in [
            ("mortality",         spec.basis_mortality),
            ("salary_band",       spec.basis_salary_band),
            ("industry",          spec.basis_industry),
            ("regional_factors",  spec.basis_regional),
            ("trend",             spec.basis_trend),
            ("waiting_periods",   spec.basis_waiting_periods),
            ("product_type_adj",  spec.basis_product_type_adj),
            ("education_guardian_cover",         spec.basis_education_guardian_cover),
            ("education_guardian_salary_bands",  spec.basis_education_guardian_salary_bands),
        ]:
            sub = _filter(sheet, alias)
            if sub is not None:
                out[sheet] = sub

        if spec.uses_ti_factors and "ti_factors" in self.tables:
            out["ti_factors"] = self.tables["ti_factors"]

        for flag, sheet in [
            (spec.uses_fc_rates,               "fc_rates"),
            (spec.uses_fc_demographics,        "fc_demographics"),
            (spec.uses_fc_family_composition,  "fc_family_composition"),
            (spec.uses_ext_fc_base_rates,        "ext_fc_base_rates"),
            (spec.uses_ext_fc_salary_factors,    "ext_fc_salary_factors"),
            (spec.uses_ext_fc_basis_params,      "ext_fc_basis_params"),
            (spec.uses_ext_fc_assumed_age_dist,  "ext_fc_assumed_age_dist"),
            (spec.uses_gip_wp_loading,           "wp_loading"),
            (spec.uses_gip_annuity_factors,      "annuity_factors"),
            (spec.uses_gip_esc_loading,          "annuity_esc_loading"),
            (spec.uses_gip_nra_interp,           "annuity_nra_interpolation"),
            (spec.uses_gip_wp_annuity_mapping,   "wp_annuity_mapping"),
            (spec.uses_gip_wp_base_inceptions,   "wp_base_inceptions"),
            (spec.uses_gip_backdating_loading,   "backdating_loading"),
            (spec.uses_gip_rr_loading,           "rr_loading"),
            (spec.uses_gip_ip_loading,           "ip_loading"),
            (spec.uses_gip_db_loading,           "db_loading"),
            (spec.uses_gip_annuity_salary_bands, "annuity_salary_bands"),
            (spec.uses_gip_annuity_age_bands,    "annuity_age_bands"),
            (spec.uses_gip_hcc_basis,            "gip_hcc_basis"),
            (spec.uses_tip_annuity_factors,    "tip_annuity_factors"),
            (spec.uses_tip_payment_period_map, "tip_payment_period_map"),
            (spec.uses_tip_age_interp,         "tip_age_interp"),
            (spec.uses_tip_esc_loading,        "tip_esc_loading"),
            (spec.uses_tip_wp_ann_map,         "tip_wp_ann_map"),
            (spec.uses_tip_db_loading,         "tip_db_loading"),
        ]:
            if flag and sheet in self.tables:
                out[sheet] = self.tables[sheet]
        return out


def _read_meta(folder: Path) -> dict:
    meta = {"version": folder.name, "built_at": "", "source_sha256": ""}
    mpath = folder / "manifest.json"
    if mpath.exists():
        m = json.loads(mpath.read_text())
        meta["version"]       = m.get("version", meta["version"])
        meta["built_at"]      = m.get("built_at", "")
        meta["source_sha256"] = m.get("source_sha256", "")
        return meta
    log = folder / "build_log.csv"
    if log.exists():
        try:
            df = pl.read_csv(log)
            last = df.tail(1)
            for col_meta, col_log in [
                ("built_at", "built_at"),
                ("source_sha256", "source_sha256"),
                ("version", "version"),
            ]:
                if col_log in last.columns:
                    val = last[col_log][0]
                    if val is not None:
                        meta[col_meta] = str(val)
        except Exception:
            logger.warning("Could not read basis build metadata from %s", log, exc_info=True)
    return meta


def load_basis(folder: str | Path, max_workers: int = 32,
               use_bundle: bool = True) -> BasisBundle:
    folder = local_directory(folder)
    if not folder.exists():
        raise FileNotFoundError(f"Basis snapshot folder not found: {folder}")

    files = sorted(folder.glob("*.parquet"))
    if not files:
        raise FileNotFoundError(f"No .parquet files found in {folder}")

    zpath = folder / BUNDLE_NAME
    if use_bundle and _bundle_is_fresh(zpath, files):
        tables = _read_from_bundle(zpath)
    else:
        tables = _read_threaded(files, max_workers)

    meta = _read_meta(folder)
    return BasisBundle(
        version=meta["version"],
        built_at=meta["built_at"],
        source_sha256=meta["source_sha256"],
        tables=tables,
    )


@lru_cache(maxsize=8)
def _get_basis_cached(folder: str, max_workers: int, use_bundle: bool) -> BasisBundle:
    b = load_basis(folder, max_workers=max_workers, use_bundle=use_bundle)
    return replace(b, tables=MappingProxyType(dict(b.tables)))


def get_basis(folder, max_workers: int = 32, use_bundle: bool = True) -> BasisBundle:
    return _get_basis_cached(str(folder), int(max_workers), bool(use_bundle))


get_basis.cache_clear = _get_basis_cached.cache_clear
get_basis.cache_info = _get_basis_cached.cache_info
