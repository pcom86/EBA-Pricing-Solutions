from __future__ import annotations

import logging
from pathlib import Path
from time import perf_counter

import openpyxl

from .category_breakdown import write_category_breakdown
from .html_adapter import build_full_report_html
from .inputs import gather_cb_data, gather_po_data
from .meta_engine import (
    ENGINE_VERSION,
    build_engine_frame,
    build_meta_rows,
    write_calc_companion_parquet,
    write_meta_sheet,
)
from .pricing_outputs import write_pricing_outputs

logger = logging.getLogger(__name__)


_SHEET_ORDER = [
    "Meta",
    "Category Breakdown",
    "Pricing Outputs",
    "Calc - All Products",
]


def write_pricing_report(
    workbook_path: str | Path,
    all_results,
    ctr,
    assump=None,
    *,
    ctr_path: str | Path | None = None,
    basis_snapshot: str | None = None,
    assumption_snapshot: str | None = None,
    engine_version: str = ENGINE_VERSION,
    warnings: list[str] | None = None,
    runtime_s: float | None = None,
    write_xlsx: bool = True,
    write_parquet: bool = True,
    write_html: bool = False,
) -> Path:
    workbook_path = Path(workbook_path)
    workbook_path.parent.mkdir(parents=True, exist_ok=True)

    t_all = perf_counter()

    if not write_xlsx:
        engine_df = build_engine_frame(all_results)
        companion = None
        if write_parquet:
            companion = write_calc_companion_parquet(engine_df, workbook_path)

        if write_html:
            t_html = perf_counter()
            cb_data = gather_cb_data(all_results, ctr.scheme, assump, ctr=ctr)
            po_data = gather_po_data(all_results, ctr.scheme, assump=assump, ctr=ctr)
            scheme_name = getattr(ctr.scheme, "scheme_name", None) or workbook_path.stem
            html_str = build_full_report_html(cb_data, po_data, scheme_name=scheme_name)
            html_path = workbook_path.with_suffix(".html")
            html_path.write_text(html_str, encoding="utf-8")
            logger.info(f"    html report: {(perf_counter() - t_html)*1000:6.1f} ms -> {html_path.name}")

        return companion if companion is not None else workbook_path

    t = perf_counter()
    cb_data = gather_cb_data(all_results, ctr.scheme, assump, ctr=ctr)
    po_data = gather_po_data(all_results, ctr.scheme, assump=assump, ctr=ctr)
    engine_df = build_engine_frame(all_results)
    meta_rows = build_meta_rows(
        ctr, assump, all_results,
        runtime_s          = runtime_s,
        engine_version     = engine_version,
        basis_snapshot     = basis_snapshot,
        assumption_snapshot= assumption_snapshot,
        ctr_path           = ctr_path,
        warnings           = warnings,
    )
    t_gather = perf_counter() - t

    t = perf_counter()
    wb = openpyxl.Workbook()
    wb.remove(wb.active)

    write_category_breakdown(wb, cb_data)
    write_pricing_outputs(wb, po_data)
    write_meta_sheet(wb, meta_rows)

    _reorder_sheets(wb, ["Meta", "Category Breakdown", "Pricing Outputs"])
    wb.save(workbook_path)
    t_main = perf_counter() - t

    t = perf_counter()
    companion = write_calc_companion_parquet(engine_df, workbook_path)
    t_calc = perf_counter() - t

    t_total = perf_counter() - t_all
    logger.info(f"    gather:       {t_gather*1000:6.1f} ms")
    logger.info(f"    main wb save: {t_main*1000:6.1f} ms")
    logger.info(f"    calc (parquet):{t_calc*1000:6.1f} ms  -> {companion.name}")
    logger.info(f"    total:        {t_total*1000:6.1f} ms")

    return workbook_path

def _reorder_sheets(wb: openpyxl.Workbook, desired_order: list[str]) -> None:
    known  = [name for name in desired_order  if name in wb.sheetnames]
    others = [name for name in wb.sheetnames if name not in known]
    wb._sheets = [wb[name] for name in known + others]
