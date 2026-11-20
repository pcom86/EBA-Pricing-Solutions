from __future__ import annotations

import logging
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

from concurrent.futures import ThreadPoolExecutor as _ThreadPoolExecutor

import polars as pl
from gap_config import get_settings
from gap_config.storage import Storage, is_s3, join_location, local_file

_db_executor: _ThreadPoolExecutor | None = None
_db_reachable: bool | None = None


def _is_db_reachable() -> bool:
    global _db_reachable
    if not get_settings().db_enabled:
        return False
    if _db_reachable is not None:
        return _db_reachable

    try:
        import pyodbc
        conn = pyodbc.connect(get_settings().db_connection_string, timeout=2)
        conn.close()
        _db_reachable = True
    except Exception as e:  # noqa: BLE001 - any driver/connection error means unreachable
        logger.warning(f"[db_loader] Local database not reachable on this machine "
              f"({type(e).__name__}) -- DB load will be skipped for this "
              f"session. Quote pricing and reports are unaffected.")
        _db_reachable = False

    return _db_reachable


def _get_db_executor() -> _ThreadPoolExecutor:
    global _db_executor
    if _db_executor is None:
        _db_executor = _ThreadPoolExecutor(max_workers=get_settings().database_workers, thread_name_prefix="gap-batch-db")
    return _db_executor


_report_executor: _ThreadPoolExecutor | None = None


def _get_report_executor() -> _ThreadPoolExecutor:
    global _report_executor
    if _report_executor is None:
        _report_executor = _ThreadPoolExecutor(max_workers=get_settings().report_workers, thread_name_prefix="gap-batch-report")
    return _report_executor


def load_shared_snapshots(basis_folder: str | Path,
                          assump_folder: str | Path) -> tuple[Any, Any]:
    from ctr_loader.assumption_loader import get_assumptions
    from ctr_loader.basis_loader import get_basis

    basis  = get_basis(basis_folder)
    assump = get_assumptions(assump_folder)
    return basis, assump


@dataclass
class QuoteResult:
    ctr_name: str
    ctr_path: str
    scheme_name: str | None
    quote_number: int | None
    output_path: str | None
    n_products: int
    runtime_s: float
    status: str
    error: str | None
    html_path: str | None = None

    def as_row(self) -> dict:
        return {
            "ctr_name":     self.ctr_name,
            "scheme":       self.scheme_name or "",
            "quote_no":     self.quote_number or 0,
            "n_products":   self.n_products,
            "runtime_s":    round(self.runtime_s, 3),
            "output":       Path(self.output_path).name if self.output_path else "",
            "html":         Path(self.html_path).name if self.html_path else "",
            "status":       self.status,
            "error":        (self.error or "")[:200],
        }


def run_single_quote(ctr_path: str | Path,
                     basis: Any,
                     assump: Any,
                     out_dir: str | Path,
                     basis_snapshot: str = "",
                     assump_snapshot: str = "",
                     overwrite: bool = True,
                     write_report: bool | str = True,
                     result_sink=None,
                     case_id: str | None = None,
                     quote_seq: int | None = None,
                     wait_for_reports: bool = False,
                     basis_folder: str | None = None,
                     assump_folder: str | None = None) -> QuoteResult:
    from ctr_loader.ctr_loader_v2 import load_ctr

    from .engine import build_context, price_all_products

    output_root = str(out_dir)
    out_dir = Path(".") if is_s3(output_root) else Path(output_root)
    if not is_s3(output_root):
        out_dir.mkdir(parents=True, exist_ok=True)
    source_location = str(ctr_path)
    ctr_path = Path(ctr_path)

    scheme_name  = None
    quote_number = None
    output_path  = None
    html_path    = None
    n_products   = 0

    _write_xlsx = write_report is True
    _write_html = write_report == "html"

    t0 = time.perf_counter()
    from datetime import datetime as _dt_start
    logger.info(f"[QUOTE START] {_dt_start.now().strftime('%H:%M:%S.%f')[:-3]} {ctr_path.name} -- worker picked up this CTR")
    try:
        ctr_path = local_file(source_location)
        ctr = load_ctr(str(ctr_path))
        scheme_name  = getattr(ctr.scheme, "scheme_name", None)
        quote_number = getattr(ctr.scheme, "quote_number", None)

        ctx = build_context(ctr, assump)

        all_results = price_all_products(ctr, basis, assump, ctx)
        n_products = len(all_results)
        from datetime import datetime as _dt_priced
        logger.info(f"[PRICING DONE] {_dt_priced.now().strftime('%H:%M:%S.%f')[:-3]} "
              f"{scheme_name or ctr_path.stem} -- all {n_products} products priced, "
              f"pure pricing time: {time.perf_counter()-t0:.2f}s")

        safe_scheme = _slug(scheme_name or ctr_path.stem)
        stamp       = time.strftime("%Y%m%d_%H%M%S")
        _disambig   = uuid.uuid4().hex[:6]
        out_path        = out_dir / f"{safe_scheme}_Q{quote_number or 'X'}_{stamp}_{_disambig}.xlsx"
        final_parquet   = out_path.with_name(f"{out_path.stem}_calc.parquet")
        final_html      = out_path.with_suffix(".html")

        output_path = join_location(output_root, out_path.name if _write_xlsx else final_parquet.name)
        if _write_html:
            html_path = join_location(output_root, final_html.name)

        from datetime import datetime as _dt_marker
        logger.info(f"[PRICED] {_dt_marker.now().strftime('%H:%M:%S.%f')[:-3]} "
              f"{scheme_name or ctr_path.stem} -- report job submitted, run_single_quote returning now")

        job = ReportWriteJob(
                ctr_path        = ctr_path,
                scheme_name     = scheme_name,
                all_results     = all_results,
                ctr             = ctr,
                assump          = assump,
                basis_snapshot  = basis_snapshot,
                assump_snapshot = assump_snapshot,
                out_path        = out_path,
                final_parquet   = final_parquet,
                final_html      = final_html,
                write_xlsx      = _write_xlsx,
                write_html      = _write_html,
                case_id         = case_id,
                quote_seq       = quote_seq,
                output_root     = output_root,
                wait_for_reports = wait_for_reports,
                basis_folder    = basis_folder,
                assump_folder   = assump_folder,
        )
        if wait_for_reports:
            _write_report_worker(job)
        else:
            _get_report_executor().submit(_write_report_worker, job)

        if result_sink is not None:
            try:
                result_sink(str(ctr_path), ctr, assump, all_results)
            except Exception as e:  # noqa: BLE001 - result_sink is best-effort; never fail the quote
                logger.warning(f"[result_sink] callback failed for {ctr_path}: {e}")

        return QuoteResult(
            ctr_name     = ctr_path.name,
            ctr_path     = source_location,
            scheme_name  = scheme_name,
            quote_number = quote_number,
            output_path  = output_path,
            n_products   = n_products,
            runtime_s    = time.perf_counter() - t0,
            status       = "OK",
            error        = None,
            html_path    = html_path,
        )

    except Exception:  # noqa: BLE001 - per-quote isolation: any failure becomes an error QuoteResult
        return QuoteResult(
            ctr_name     = ctr_path.name,
            ctr_path     = source_location,
            scheme_name  = scheme_name,
            quote_number = quote_number,
            output_path  = output_path,
            n_products   = n_products,
            runtime_s    = time.perf_counter() - t0,
            status       = "ERROR",
            error        = traceback.format_exc(),
            html_path    = html_path,
        )


@dataclass
class ReportWriteJob:
    ctr_path: Path
    scheme_name: str | None
    all_results: Any
    ctr: Any
    assump: Any
    basis_snapshot: str
    assump_snapshot: str
    out_path: Path
    final_parquet: Path
    final_html: Path
    write_xlsx: bool
    write_html: bool
    case_id: str | None = None
    quote_seq: int | None = None
    output_root: str | None = None
    wait_for_reports: bool = False
    basis_folder: str | None = None
    assump_folder: str | None = None


def _write_report_worker(job: ReportWriteJob) -> None:
    ctr_path = job.ctr_path
    scheme_name = job.scheme_name
    all_results = job.all_results
    ctr = job.ctr
    assump = job.assump
    basis_snapshot = job.basis_snapshot
    assump_snapshot = job.assump_snapshot
    out_path = job.out_path
    final_parquet = job.final_parquet
    final_html = job.final_html
    write_xlsx = job.write_xlsx
    write_html = job.write_html
    case_id = job.case_id
    quote_seq = job.quote_seq
    store = Storage(job.output_root or out_path.parent)
    from reporting import write_pricing_report

    from . import async_state

    async_state.report_write_started()
    from datetime import datetime as _dt_marker2
    logger.info(f"[REPORT START] {_dt_marker2.now().strftime('%H:%M:%S.%f')[:-3]} "
          f"{scheme_name or ctr_path.stem} -- report worker actually begins now")
    local_dir = None
    try:
        local_dir = get_settings().staging_dir / \
            f"{_slug(scheme_name or ctr_path.stem)}_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        local_dir.mkdir(parents=True, exist_ok=True)
        local_xlsx    = local_dir / out_path.name
        local_parquet = local_dir / final_parquet.name
        local_html    = local_xlsx.with_suffix(".html")

        write_pricing_report(
            workbook_path       = local_xlsx,
            all_results         = all_results,
            ctr                 = ctr,
            assump              = assump,
            ctr_path            = str(ctr_path),
            basis_snapshot      = basis_snapshot,
            assumption_snapshot = assump_snapshot,
            write_xlsx          = write_xlsx,
            write_html          = write_html,
        )

        local_xlsx_ok    = write_xlsx and local_xlsx.exists()
        local_parquet_ok = local_parquet.exists()
        local_html_ok    = write_html and local_html.exists()
        if job.wait_for_reports and (not local_parquet_ok or (write_xlsx and not local_xlsx_ok)
                                     or (write_html and not local_html_ok)):
            raise RuntimeError("Report writer did not produce all requested artifacts")

        _sync_pairs = []
        if local_xlsx_ok:
            _sync_pairs.append((local_xlsx, out_path))
        if local_parquet_ok:
            _sync_pairs.append((local_parquet, final_parquet))
        if local_html_ok:
            _sync_pairs.append((local_html, final_html))

        _will_db_load = get_settings().db_enabled and local_parquet_ok
        if _will_db_load and not _is_db_reachable():
            if job.wait_for_reports:
                raise RuntimeError("Database loading was enabled but the configured database is unreachable")
            _will_db_load = False
        _consumer_count = (1 if _sync_pairs else 0) + (1 if _will_db_load else 0)
        async_state.register_local_dir(str(local_dir), _consumer_count)

        if _sync_pairs:
            def _sync_worker(pairs=_sync_pairs, ldir=str(local_dir)):
                async_state.file_sync_started()
                try:
                    for src, dst in pairs:
                        store.put_file(src.name, src)
                except Exception as e:
                    logger.warning(f"[file_sync] batch: failed to sync {ctr_path.name} report(s) to network: {e}")
                    if job.wait_for_reports:
                        raise
                finally:
                    async_state.file_sync_finished()
                    async_state.release_local_dir(ldir)

            if job.wait_for_reports:
                _sync_worker()
            else:
                threading.Thread(target=_sync_worker, name="gap-batch-sync", daemon=True).start()

        if _will_db_load:
            def _db_worker(parquet_path=local_parquet, ctr_bundle=ctr, scheme=scheme_name,
                            ldir=str(local_dir), basis_snap=basis_snapshot, assump_snap=assump_snapshot,
                            case_id=case_id, quote_seq=quote_seq):
                async_state.db_write_started()
                try:
                    import pandas as pd
                    from ctr_loader.engine_paths import snapshot_dir
                    from gap_config.storage import Storage

                    from .db_loader import load_run_to_db

                    df_result = pd.DataFrame(pl.read_parquet(parquet_path).to_dicts())
                    s3 = Storage(get_settings().artifact_root)
                    basis_folder = job.basis_folder or (str(snapshot_dir("basis", name=basis_snap)) if basis_snap else None)
                    assump_folder = job.assump_folder or (str(snapshot_dir("assumption", name=assump_snap)) if assump_snap else None)
                    db_summary = load_run_to_db(
                        get_settings().db_connection_string, s3, ctr_bundle, df_result, parquet_path,
                        basis_snapshot=basis_snap, assumption_snapshot=assump_snap,
                        basis_snapshot_folder=basis_folder,
                        assumption_snapshot_folder=assump_folder,
                        case_id=case_id, quote_seq=quote_seq,
                    )
                    if db_summary["status"] == "success":
                        _t = db_summary.get("timing", {})
                        _breakdown = " | ".join(f"{k}={v:.2f}s" for k, v in _t.items())
                        logger.info(f"[db_loader] batch run {db_summary['run_id']} "
                              f"({scheme}) loaded ({db_summary.get('rows', 0)} rows) -- {_breakdown}")
                    else:
                        logger.warning(f"[db_loader] batch load failed for {scheme}: {db_summary['error']}")
                        if job.wait_for_reports:
                            raise RuntimeError("Configured database load failed; see database diagnostics")
                except Exception as e:
                    logger.warning(f"[db_loader] batch: unexpected error for {scheme} (quote save unaffected): {e}")
                    if job.wait_for_reports:
                        raise
                finally:
                    async_state.db_write_finished()
                    async_state.release_local_dir(ldir)

            if job.wait_for_reports:
                _db_worker()
            else:
                _get_db_executor().submit(_db_worker)
    except Exception as e:
        logger.warning(f"[report_writer] batch: failed to write report for {scheme_name}: {e}")
        if job.wait_for_reports:
            raise
    finally:
        from datetime import datetime as _dt_marker3
        logger.info(f"[REPORT DONE] {_dt_marker3.now().strftime('%H:%M:%S.%f')[:-3]} "
              f"{scheme_name or ctr_path.stem} -- report worker finished")
        async_state.report_write_finished()
        if job.wait_for_reports and local_dir is not None:
            async_state.register_local_dir(str(local_dir), 0)


def run_quotes_concurrent(ctr_paths: list[str | Path],
                          basis: Any,
                          assump: Any,
                          out_dir: str | Path,
                          basis_snapshot: str = "",
                          assump_snapshot: str = "",
                          max_workers: int | None = None,
                          verbose: bool = True,
                          write_report: bool | str = True,
                          result_sink=None,
                          wait_for_reports: bool = False,
                          basis_folder: str | None = None,
                          assump_folder: str | None = None) -> pl.DataFrame:
    ctr_paths = [str(p) for p in ctr_paths]
    max_workers = max_workers if max_workers is not None else get_settings().pricing_workers

    if verbose:
        logger.info(f"⚡ Batch pricing {len(ctr_paths)} quotes with {max_workers} workers")
        logger.info(f"   Output: {out_dir}\n")

    try:
        from .db_loader import sql_seq_guid
        _batch_case_id = sql_seq_guid()
    except Exception as e:  # noqa: BLE001 - DB sequence is optional; fall back to uuid4
        if verbose:
            logger.warning(f"[db_loader] batch: sql_seq_guid unavailable ({type(e).__name__}: {e}) "
                  f"-- falling back to plain uuid4 for case_id (pricing unaffected)")
        _batch_case_id = str(uuid.uuid4())
    _quote_seq_by_path = {str(p): i + 1 for i, p in enumerate(ctr_paths)}

    batch_start = time.perf_counter()
    results: list[QuoteResult] = []

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(
                run_single_quote,
                ctr_path        = ctr_path,
                basis           = basis,
                assump          = assump,
                out_dir         = out_dir,
                basis_snapshot  = basis_snapshot,
                assump_snapshot = assump_snapshot,
                write_report    = write_report,
                result_sink     = result_sink,
                case_id         = _batch_case_id,
                quote_seq       = _quote_seq_by_path[str(ctr_path)],
                wait_for_reports = wait_for_reports,
                basis_folder    = basis_folder,
                assump_folder   = assump_folder,
            ): ctr_path
            for ctr_path in ctr_paths
        }

        for fut in as_completed(futures):
            r = fut.result()
            results.append(r)
            if verbose:
                icon = "✅" if r.status == "OK" else "❌"
                logger.info(f"  {icon} {r.ctr_name:<50s} {r.runtime_s:6.2f}s"
                      f"  ({r.n_products} products)")

    wall_time    = time.perf_counter() - batch_start
    sequential_s = sum(r.runtime_s for r in results)
    speedup      = sequential_s / wall_time if wall_time > 0 else 0.0

    if verbose:
        n_ok    = sum(1 for r in results if r.status == "OK")
        n_fail  = sum(1 for r in results if r.status == "ERROR")
        logger.info(f"\n{'-' * 68}")
        logger.info(f"  Wall time:              {wall_time:6.2f}s")
        logger.info(f"  Sequential would take:  {sequential_s:6.2f}s")
        logger.info(f"  Speedup:                {speedup:5.2f}x")
        logger.info(f"  Success / Failed:       {n_ok} / {n_fail}")
        logger.info(f"{'-' * 68}")

    from datetime import datetime as _dt_marker

    from . import async_state
    _pending_reports = async_state.report_writes_pending()
    _pending_db = async_state.db_writes_pending()
    logger.info(f"[BATCH READY] {_dt_marker.now().strftime('%H:%M:%S.%f')[:-3]} "
          f"-- pricing done, GUI table populating now. "
          f"Still in background: {_pending_reports} report write(s), {_pending_db} DB load(s) pending.")

    rows = [r.as_row() for r in results]
    summary = pl.DataFrame(rows) if rows else pl.DataFrame()

    if summary.height:
        order = {Path(p).name: i for i, p in enumerate(ctr_paths)}
        summary = summary.with_columns(
            pl.col("ctr_name").replace_strict(order, default=-1).alias("__ord")
        ).sort("__ord").drop("__ord")

    return summary


def _slug(s: str) -> str:
    keep = "-_ "
    out = "".join(c if c.isalnum() or c in keep else "_" for c in str(s))
    return out.strip().replace(" ", "_")[:60] or "quote"
