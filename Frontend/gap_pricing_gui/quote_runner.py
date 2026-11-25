from __future__ import annotations

import datetime
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime as _datetime
from pathlib import Path
from typing import Any

import polars as pl
from ctr_loader.assumption_loader import get_assumptions
from ctr_loader.basis_loader import get_basis
from ctr_loader.ctr_loader_v2 import load_ctr
from gap_config import get_settings
from gap_config.storage import Storage, is_s3, join_location
from pricing_engine.engine import build_context, price_all_products

from .config import snapshot_folders
from .file_access import input_file, output_directory

_PRODUCT_DISPLAY = {
    "gla":                 "GLA",
    "lsdb":                "LSDB",
    "ci":                  "CI",
    "spouse_gla":          "Spouses - GLA",
    "spouse_lsdb":         "Spouses - LSDB",
    "terminal":            "Terminal Illness Cover",
    "accident_death":      "Accidental Death",
    "accident_disability": "Accidental Disability",
    "dismemberment":       "Dismemberment",
    "fc":                  "FC",
    "gip":                 "GIP",
    "tip":                 "TIP",
}


import logging

logger = logging.getLogger(__name__)


_run_count = {"n": 0}


def _log_compute(scheme: str, secs: float) -> None:
    _run_count["n"] += 1
    tag = "COLD" if _run_count["n"] == 1 else "WARM"
    line = (f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S}  "
            f"{tag}  price_quote={secs:.3f}s  scheme={scheme}")
    logger.info(line)


@dataclass
class QuoteResult:
    ctr_path: str
    scheme_name: str
    quote_number: str
    calculation_date: str
    active_products: tuple
    per_product_results: dict[str, pl.DataFrame]
    aggregates: dict[str, float]

    ctr_bundle:    Any = None
    assump_bundle: Any = None

    scheme_params: dict             = field(default_factory=dict)
    timings:       dict[str, float] = field(default_factory=dict)
    warnings:      list[str]        = field(default_factory=list)

    total_runtime_s: float | None = None

    xlsx_path:    str | Path | None = None
    parquet_path: str | Path | None = None
    html_path:    str | Path | None = None
    _db_load_started: bool = False

    def summary_text(self) -> str:
        lines: list[str] = [
            f"Scheme:     {self.scheme_name}",
            f"Quote #:    {self.quote_number}",
            f"Calc date:  {self.calculation_date}",
            f"CTR:        {Path(self.ctr_path).name}",
            "",
            "─" * 60,
            f"{'Product':<28} {'Members':>10} {'Total Premium':>18}",
            "─" * 60,
        ]

        for pkey in self.active_products:
            display = _PRODUCT_DISPLAY.get(pkey, pkey)
            df = self.per_product_results.get(pkey)
            if df is None:
                continue
            active = (
                df.filter(pl.col("product_active"))
                if "product_active" in df.columns else df
            )
            n = active.height
            total = (
                float(active["total_annual_premium"].sum())
                if "total_annual_premium" in active.columns else 0.0
            )
            lines.append(f"{display:<28} {n:>10,} {total:>18,.2f}")

        lines.append("─" * 60)
        lines.append(
            f"{'TOTAL':<28} {'':>10} "
            f"{self.aggregates.get('total_annual_premium', 0):>18,.2f}"
        )
        lines.append("")
        lines.append(
            f"Priced in {sum(self.timings.values()):.2f}s across "
            f"{len(self.active_products)} products."
        )
        lines.append("")
        lines.append("Report will include:")
        lines.append("  • Category Breakdown, Pricing Outputs, Meta   (xlsx)")
        lines.append("  • Full per-member audit dump                  (parquet)")

        if self.warnings:
            lines.append("")
            lines.append("Warnings:")
            for w in self.warnings:
                lines.append(f"  ⚠  {w}")

        return "\n".join(lines)


def price_quote(
    ctr_path: str,
    progress_cb: Callable[[str], None] | None = None,
    scheme_params_override: dict | None = None,
) -> QuoteResult:
    ctr_path = str(input_file(ctr_path))
    BASIS_SNAP, ASSUMP_SNAP = snapshot_folders()
    def _log(msg: str) -> None:
        if progress_cb:
            progress_cb(msg)

    _compute_t0 = time.perf_counter()

    t: dict[str, float] = {}

    _log("Loading basis snapshots…")
    t0 = time.time()
    basis = get_basis(BASIS_SNAP)
    t["basis"] = time.time() - t0

    _log("Loading assumption snapshots…")
    t0 = time.time()
    assump = get_assumptions(ASSUMP_SNAP)
    t["assump"] = time.time() - t0

    _log(f"Parsing CTR: {Path(ctr_path).name}")
    t0 = time.time()
    ctr = load_ctr(ctr_path)
    t["ctr"] = time.time() - t0

    return _price_loaded_ctr(ctr, basis, assump, t, _compute_t0, _log,
                              ctr_path_label=ctr_path)


def _price_loaded_ctr(ctr, basis, assump, t: dict, _compute_t0: float,
                       _log, ctr_path_label: str) -> QuoteResult:
    ctx = build_context(ctr, assump)

    def _on_priced(pkey, df, elapsed_s):
        _log(f"Pricing {_PRODUCT_DISPLAY.get(pkey, pkey)}…")
        t[pkey] = elapsed_s

    all_results = price_all_products(ctr, basis, assump, ctx, on_product_priced=_on_priced)

    total_annual_premium = 0.0
    total_cover          = 0.0
    for df in all_results.values():
        active = (
            df.filter(pl.col("product_active"))
            if "product_active" in df.columns else df
        )
        if "total_annual_premium" in active.columns:
            total_annual_premium += float(active["total_annual_premium"].sum())
        if "total_cover" in active.columns:
            total_cover += float(active["total_cover"].sum())

    aggregates = {
        "total_annual_premium":     total_annual_premium,
        "total_subtotal_premium":   total_annual_premium,
        "total_gross_risk_premium": total_annual_premium * 1.075,
        "total_cover":              total_cover,
    }

    result = QuoteResult(
        ctr_path            = ctr_path_label,
        scheme_name         = str(getattr(ctr.scheme, "scheme_name", "")
                                  or Path(ctr_path_label).stem),
        quote_number        = str(getattr(ctr.scheme, "quote_number", "") or ""),
        calculation_date    = str(getattr(ctr.scheme, "calculation_date", "") or ""),
        active_products     = ctr.active_products,
        per_product_results = all_results,
        aggregates          = aggregates,
        ctr_bundle          = ctr,
        assump_bundle       = assump,
        timings             = t,
        warnings            = list(getattr(ctr, "warnings", []) or []),
    )

    _total_runtime = time.perf_counter() - _compute_t0
    _log_compute(result.scheme_name, _total_runtime)

    result.total_runtime_s = _total_runtime

    return result


def list_db_schemes(status_filter: str = "success") -> pl.DataFrame:
    settings = get_settings()
    if not settings.db_enabled:
        raise ValueError("Database access is disabled; configure GAP_DB_ENABLED and GAP_DB_CONNECTION_STRING")
    import pyodbc
    conn = pyodbc.connect(settings.db_connection_string)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT
            r.run_id, s.scheme_id, s.scheme_name, s.internal_gla_code AS scheme_code,
            s.quote_number, s.broker_name, s.doer, s.checker,
            s.commencement_date, r.started_at, r.status, r.rerun_number,
            r.row_count, s.scheme_business_key
        FROM run r
        JOIN scheme s ON s.scheme_id = r.scheme_id
        WHERE (? = '' OR r.status = ?)
        ORDER BY s.scheme_name, r.started_at DESC
        """,
        status_filter, status_filter,
    )
    cols = [c[0] for c in cur.description]
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return pl.DataFrame([dict(zip(cols, r)) for r in rows]) if rows else pl.DataFrame(schema={c: pl.Utf8 for c in cols})


def price_quote_from_db(
    run_id: str,
    progress_cb: Callable[[str], None] | None = None,
) -> QuoteResult:
    settings = get_settings()
    if not settings.db_enabled:
        raise ValueError("Database access is disabled; configure GAP_DB_ENABLED and GAP_DB_CONNECTION_STRING")
    BASIS_SNAP, ASSUMP_SNAP = snapshot_folders()
    def _log(msg: str) -> None:
        if progress_cb:
            progress_cb(msg)

    _compute_t0 = time.perf_counter()
    t: dict[str, float] = {}

    _log("Loading basis snapshots…")
    t0 = time.time()
    basis = get_basis(BASIS_SNAP)
    t["basis"] = time.time() - t0

    _log("Loading assumption snapshots…")
    t0 = time.time()
    assump = get_assumptions(ASSUMP_SNAP)
    t["assump"] = time.time() - t0

    _log(f"Reconstructing CTR from database (run_id={run_id})…")
    t0 = time.time()
    from pricing_engine.db_loader import reconstruct_ctr_bundle
    s3 = Storage(settings.artifact_root)
    ctr = reconstruct_ctr_bundle(settings.db_connection_string, s3, run_id)
    t["ctr"] = time.time() - t0

    ctr_path_label = f"[DB run {run_id}] {ctr.ctr_path or ''}".strip()
    return _price_loaded_ctr(ctr, basis, assump, t, _compute_t0, _log,
                              ctr_path_label=ctr_path_label)


def save_report(
    result: QuoteResult,
    out_dir: str | Path | None = None,
    *,
    write_xlsx: bool = True,
    write_parquet: bool = True,
    write_html: bool = False,
) -> Path | str:
    from reporting import write_pricing_report
    settings = get_settings()
    out_dir = output_directory(out_dir)
    BASIS_SNAP, ASSUMP_SNAP = snapshot_folders()
    store = Storage(out_dir)

    ts           = _datetime.now().strftime("%Y%m%d_%H%M%S")
    scheme_clean = _slug(result.scheme_name)
    quote_clean  = _slug(str(result.quote_number)) if result.quote_number else ""

    fname_parts: list[str] = [scheme_clean]
    if quote_clean:
        fname_parts.append(quote_clean)
    fname_parts.append(ts)

    filename = "_".join(fname_parts)
    xlsx_path: str | Path = join_location(out_dir, filename + ".xlsx")
    parquet_path: str | Path = join_location(out_dir, filename + "_calc.parquet")
    html_path: str | Path = join_location(out_dir, filename + ".html")
    if not is_s3(out_dir):
        xlsx_path, parquet_path, html_path = map(Path, (xlsx_path, parquet_path, html_path))

    import uuid as _uuid_local
    local_dir = settings.staging_dir / f"{scheme_clean}_{ts}_{_uuid_local.uuid4().hex[:8]}"
    local_dir.mkdir(parents=True, exist_ok=True)
    local_xlsx_path    = local_dir / (filename + ".xlsx")
    local_parquet_path = local_dir / (filename + "_calc.parquet")
    local_html_path    = local_xlsx_path.with_suffix(".html")

    from gap_pricing_gui import warm_state as _warm_state_cleanup
    try:
        write_pricing_report(
            workbook_path       = local_xlsx_path,
            all_results         = result.per_product_results,
            ctr                 = result.ctr_bundle,
            assump              = result.assump_bundle,
            ctr_path            = result.ctr_path,
            basis_snapshot      = Path(BASIS_SNAP).name,
            assumption_snapshot = Path(ASSUMP_SNAP).name,
            warnings            = result.warnings,
            runtime_s           = result.total_runtime_s,
            write_xlsx          = write_xlsx,
            write_parquet       = write_parquet,
            write_html          = write_html,
        )
        for requested, source in ((write_xlsx, local_xlsx_path),
                                  (write_parquet, local_parquet_path),
                                  (write_html, local_html_path)):
            if requested:
                if not source.is_file():
                    raise RuntimeError(f"Report writer did not produce: {source.name}")
                store.put_file(source.name, source)
    except Exception:
        _warm_state_cleanup.register_local_dir(str(local_dir), 0)
        raise

    local_xlsx_ok    = write_xlsx and local_xlsx_path.exists()
    local_parquet_ok = write_parquet and local_parquet_path.exists()
    local_html_ok    = write_html and local_html_path.exists()

    if write_xlsx:
        result.xlsx_path = xlsx_path if local_xlsx_ok else None
    if write_parquet:
        result.parquet_path = parquet_path if local_parquet_ok else None
    if write_html:
        result.html_path = html_path if local_html_ok else None

    _will_db_load = settings.db_enabled and local_parquet_ok and not getattr(result, "_db_load_started", False)
    _consumer_count = 1 if _will_db_load else 0
    _warm_state_cleanup.register_local_dir(str(local_dir), _consumer_count)

    if settings.db_enabled and local_parquet_ok and not getattr(result, "_db_load_started", False):
        result._db_load_started = True
        import threading

        def _db_worker(parquet_path=local_parquet_path,
                        ctr_bundle=result.ctr_bundle,
                        scheme_name=result.scheme_name):
            from gap_pricing_gui import warm_state
            warm_state.db_write_started()
            try:
                import pandas as pd
                from pricing_engine.db_loader import load_run_to_db

                df_result = pd.DataFrame(pl.read_parquet(parquet_path).to_dicts())
                s3 = Storage(settings.artifact_root)
                db_summary = load_run_to_db(
                    settings.db_connection_string, s3, ctr_bundle, df_result, parquet_path,
                    basis_snapshot=Path(BASIS_SNAP).name,
                    assumption_snapshot=Path(ASSUMP_SNAP).name,
                    basis_snapshot_folder=BASIS_SNAP,
                    assumption_snapshot_folder=ASSUMP_SNAP,
                )
                if db_summary["status"] == "success":
                    _t = db_summary.get("timing", {})
                    _breakdown = " | ".join(f"{k}={v:.2f}s" for k, v in _t.items())
                    logger.info(f"[db_loader] run {db_summary['run_id']} loaded "
                          f"({db_summary.get('rows', 0)} rows) -- {_breakdown}")
                else:
                    logger.info(f"[db_loader] load failed for {scheme_name}: {db_summary['error']}")
            except Exception as e:  # noqa: BLE001 - DB load is best-effort; quote save must not fail
                logger.warning(f"[db_loader] unexpected error (quote save unaffected): {e}")
            finally:
                warm_state.db_write_finished()
                warm_state.release_local_dir(str(local_dir))

        try:
            threading.Thread(target=_db_worker, name="gap-db-load", daemon=True).start()
        except Exception:
            result._db_load_started = False
            _warm_state_cleanup.register_local_dir(str(local_dir), 0)
            raise

    if write_xlsx:
        return xlsx_path
    if write_html:
        return html_path
    return parquet_path


def _slug(s: str) -> str:
    if not s:
        return "unknown"
    out: list[str] = []
    for ch in str(s):
        if ch.isalnum() or ch in ("-", "_"):
            out.append(ch)
        elif ch in (" ", "/", "\\"):
            out.append("_")
    return "".join(out).strip("_") or "unknown"