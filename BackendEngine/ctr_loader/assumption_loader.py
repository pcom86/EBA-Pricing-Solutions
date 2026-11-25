from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import Any

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
class AssumptionsBundle:
    built_at: str
    source_sha256: str
    tables: Mapping[str, pl.DataFrame]
    globals: Mapping[str, Any]

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
            ("product_limits",    spec.assumption_limits),
            ("profit_margins",    spec.assumption_margins),
            ("conversion_option", spec.assumption_co),
        ]:
            sub = _filter(sheet, alias)
            if sub is not None:
                out[sheet] = sub

        for flag, sheet in [
            (spec.uses_lsdb_features,    "lsdb_features"),
            (spec.uses_spouse_factors,   "spouse_factors"),
            (spec.uses_ci_table_mapping, "ci_table_mapping"),
            (spec.uses_ci_reinstatement, "ci_reinstatement"),
            (spec.uses_ctc_options,      "ctc_options"),
            (spec.uses_tax_tables,       "tax_tables"),
            (spec.uses_fc_db_loading,    "fc_db_loading"),
            (spec.uses_gip_fee_table, "gip_fee_table"),
            (spec.uses_wp_categories, "wp_categories"),
        ]:
            if flag and sheet in self.tables:
                out[sheet] = self.tables[sheet]
        return out


def _coerce_global(param_value, param_type):
    if param_value is None:
        return None
    if param_type == "float":
        try: return float(param_value)
        except (ValueError, TypeError): return None
    if param_type == "int":
        try: return int(float(param_value))
        except (ValueError, TypeError): return None
    if param_type == "bool":
        return str(param_value).strip().lower() in ("true", "1", "yes", "y")
    return str(param_value)


def _read_meta(folder: Path) -> dict:
    meta = {"built_at": "", "source_sha256": ""}
    mpath = folder / "manifest.json"
    if mpath.exists():
        m = json.loads(mpath.read_text())
        meta["built_at"]      = m.get("built_at", "")
        meta["source_sha256"] = m.get("source_sha256", "")
        return meta
    log = folder / "build_log.csv"
    if log.exists():
        try:
            df = pl.read_csv(log)
            last = df.tail(1)
            for k in ("built_at", "source_sha256"):
                if k in last.columns and last[k][0] is not None:
                    meta[k] = str(last[k][0])
        except Exception:
            logger.warning("Could not read assumption build metadata from %s", log, exc_info=True)
    return meta


def load_assumptions(folder: str | Path, max_workers: int = 32,
                     use_bundle: bool = True) -> AssumptionsBundle:
    folder = local_directory(folder)
    if not folder.exists():
        raise FileNotFoundError(f"Assumption snapshot folder not found: {folder}")

    files = sorted(folder.glob("*.parquet"))
    if not files:
        raise FileNotFoundError(f"No .parquet files found in {folder}")

    zpath = folder / BUNDLE_NAME
    if use_bundle and _bundle_is_fresh(zpath, files):
        tables = _read_from_bundle(zpath)
    else:
        tables = _read_threaded(files, max_workers)

    globals_dict: dict[str, Any] = {}
    if "globals" in tables:
        g = tables["globals"]
        if {"param_name", "param_value", "param_type"}.issubset(g.columns):
            for row in g.iter_rows(named=True):
                globals_dict[row["param_name"]] = _coerce_global(
                    row["param_value"], row.get("param_type")
                )

    meta = _read_meta(folder)
    return AssumptionsBundle(
        built_at=meta["built_at"],
        source_sha256=meta["source_sha256"],
        tables=tables,
        globals=globals_dict,
    )


@lru_cache(maxsize=8)
def _get_assumptions_cached(folder: str, max_workers: int, use_bundle: bool) -> AssumptionsBundle:
    a = load_assumptions(folder, max_workers=max_workers, use_bundle=use_bundle)
    return replace(a,
                   tables=MappingProxyType(dict(a.tables)),
                   globals=MappingProxyType(dict(a.globals)))


def get_assumptions(folder, max_workers: int = 32, use_bundle: bool = True) -> AssumptionsBundle:
    return _get_assumptions_cached(str(folder), int(max_workers), bool(use_bundle))


get_assumptions.cache_clear = _get_assumptions_cached.cache_clear  # type: ignore[attr-defined]
get_assumptions.cache_info = _get_assumptions_cached.cache_info  # type: ignore[attr-defined]
