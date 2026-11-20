
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import polars as pl

ENGINE_VERSION = "v2026.07 (Rules 19-27 applied)"


def build_meta_rows(
    ctr,
    assump,
    all_results: dict[str, pl.DataFrame],
    runtime_s: float | None = None,
    engine_version: str = ENGINE_VERSION,
    basis_snapshot: str | None = None,
    assumption_snapshot: str | None = None,
    ctr_path: str | Path | None = None,
    warnings: list[str] | None = None,
) -> list[tuple[str, object]]:
    scheme_name = getattr(ctr.scheme, "scheme_name", None) or getattr(ctr, "scheme_name", None)
    quote_no    = getattr(ctr.scheme, "quote_number", None) or getattr(ctr, "quote_number", None)
    calc_date   = getattr(ctr.scheme, "calculation_date", None) or getattr(ctr, "calculation_date", None)

    if basis_snapshot is None:
        basis_snapshot = (
            getattr(assump, "basis_snapshot_name", None)
            or getattr(assump, "basis_snapshot", None)
            or ""
        )
    if assumption_snapshot is None:
        assumption_snapshot = (
            getattr(assump, "snapshot_name", None)
            or getattr(assump, "snapshot", None)
            or ""
        )

    active_products = ", ".join(
        pkey.upper() for pkey in all_results
        if all_results[pkey] is not None and all_results[pkey].height > 0
    )

    generated_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    ctr_path_str = str(ctr_path) if ctr_path is not None else ""

    rows = [
        ("Field",                "Value"),
        ("Scheme Name",          scheme_name or ""),
        ("Quote Number",         quote_no or ""),
        ("Calculation Date",     _fmt_date(calc_date)),
        ("CTR File",             ctr_path_str),
        ("Engine Version",       engine_version),
        ("Basis Snapshot",       basis_snapshot),
        ("Assumption Snapshot",  assumption_snapshot),
        ("Generated At",         generated_at),
        ("Total Runtime (s)",    round(runtime_s, 2) if runtime_s is not None else ""),
        ("Active Products",      active_products),
    ]

    if warnings:
        rows.append(("", ""))
        rows.append(("Warnings", f"{len(warnings)} warning(s)"))
        for i, w in enumerate(warnings, start=1):
            rows.append((f"  #{i}", str(w)))

    return rows

def _fmt_date(v) -> str:
    if v is None:
        return ""
    if isinstance(v, datetime):
        return v.date().isoformat()
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return str(v)


def build_engine_frame(all_results: dict[str, pl.DataFrame]) -> pl.DataFrame:
    if not all_results:
        return pl.DataFrame({"product": pl.Series([], dtype=pl.String)})

    ordered_cols: list[str] = []
    seen: set[str] = set()
    for pkey, df in all_results.items():
        if df is None:
            continue
        for c in df.columns:
            if c not in seen:
                ordered_cols.append(c)
                seen.add(c)

    if not ordered_cols:
        return pl.DataFrame({"product": pl.Series([], dtype=pl.String)})

    frames: list[pl.DataFrame] = []
    for pkey, df in all_results.items():
        if df is None or df.height == 0:
            continue

        cols_to_add = [c for c in ordered_cols if c not in df.columns]
        if cols_to_add:
            df = df.with_columns([pl.lit(None).alias(c) for c in cols_to_add])

        df = df.select(ordered_cols)
        df = df.with_columns(pl.lit(pkey).alias("product")).select(
            ["product", *ordered_cols]
        )
        frames.append(df)

    if not frames:
        return pl.DataFrame({"product": pl.Series([], dtype=pl.String)})

    return pl.concat(frames, how="diagonal_relaxed")


from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.worksheet import Worksheet

_HEADER_FONT = Font(bold=True, color="FFFFFF")
_HEADER_FILL = PatternFill("solid", fgColor="305496")
_LABEL_FONT  = Font(bold=True)

def write_calc_companion_parquet(
    engine_df: pl.DataFrame,
    main_workbook_path: str | Path,
    suffix: str = "_calc",
) -> Path:
    main_path = Path(main_workbook_path)
    companion = main_path.with_name(f"{main_path.stem}{suffix}.parquet")

    safe_df = engine_df
    for col, dtype in engine_df.schema.items():
        if dtype == pl.Object or str(dtype).startswith("List"):
            safe_df = safe_df.with_columns(pl.col(col).cast(pl.Utf8, strict=False))

    safe_df.write_parquet(
        companion,
        compression="zstd",
        compression_level=3,
    )
    return companion


def write_meta_sheet(
    wb: Workbook,
    meta_rows: list[tuple[str, object]],
    sheet_name: str = "Meta",
) -> Worksheet:
    if sheet_name in wb.sheetnames:
        del wb[sheet_name]
    ws = wb.create_sheet(sheet_name)

    if not meta_rows:
        return ws

    _LEFT = Alignment(horizontal="left", vertical="center")

    header_field, header_value = meta_rows[0]
    ws.append([header_field, header_value])
    for col in (1, 2):
        c = ws.cell(row=1, column=col)
        c.font = _HEADER_FONT
        c.fill = _HEADER_FILL
        c.alignment = _LEFT

    for field, value in meta_rows[1:]:
        ws.append([field, value])
        r = ws.max_row
        ws.cell(row=r, column=1).font = _LABEL_FONT
        ws.cell(row=r, column=1).alignment = _LEFT
        ws.cell(row=r, column=2).alignment = _LEFT

    ws.column_dimensions["A"].width = 22
    ws.column_dimensions["B"].width = 90

    ws.freeze_panes = "A2"
    return ws


_ENGINE_FMT_MONEY   = "#,##0.00"
_ENGINE_FMT_PCT8    = "0.00000000"
_ENGINE_FMT_INT     = "#,##0"
_ENGINE_FMT_DATE    = "yyyy-mm-dd"

_MONEY_HINTS = (
    "salary", "cover", "premium", "amount", "bill", "fee",
)
_PCT_HINTS = (
    "qx", "rate", "margin", "commission_rate", "pct",
)
_INT_HINTS = (
    "member_no", "quote", "count", "n_", "age_lb", "age_nb",
    "category_number", "category_flag",
)
_DATE_HINTS = (
    "date_of_", "birth", "start_date", "end_date", "calculation_date",
)


def _guess_format(col_name: str) -> str | None:
    low = col_name.lower()
    if any(h in low for h in _DATE_HINTS):
        return _ENGINE_FMT_DATE
    if any(h in low for h in _MONEY_HINTS):
        return _ENGINE_FMT_MONEY
    if any(h in low for h in _PCT_HINTS):
        return _ENGINE_FMT_PCT8
    if any(h in low for h in _INT_HINTS):
        return _ENGINE_FMT_INT
    return None


def write_engine_sheet(
    wb: Workbook,
    engine_df,
    sheet_name: str = "Calc - All Products",
) -> Worksheet:
    if sheet_name in wb.sheetnames:
        del wb[sheet_name]
    ws = wb.create_sheet(sheet_name)

    if engine_df is None or engine_df.height == 0:
        ws.append(["(no data)"])
        return ws

    columns = list(engine_df.columns)

    ws.append(columns)
    for col_idx in range(1, len(columns) + 1):
        c = ws.cell(row=1, column=col_idx)
        c.font = _HEADER_FONT
        c.fill = _HEADER_FILL
        c.alignment = Alignment(horizontal="left", vertical="center")

    for row in engine_df.iter_rows():
        ws.append(row)

    from openpyxl.utils import get_column_letter

    for idx, name in enumerate(columns, start=1):
        fmt = _guess_format(name)
        if fmt is None:
            continue
        letter = get_column_letter(idx)
        col = ws.column_dimensions[letter]
        col.number_format = fmt

    for idx, name in enumerate(columns, start=1):
        letter = get_column_letter(idx)
        ws.column_dimensions[letter].width = max(12, min(30, len(str(name)) + 2))

    ws.column_dimensions["A"].width = 14

    ws.freeze_panes = "B2"
    ws.auto_filter.ref = ws.dimensions

    return ws