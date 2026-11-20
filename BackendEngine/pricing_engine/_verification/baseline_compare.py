import openpyxl
import polars as pl


def read_xlsx_values(path):
    wb = openpyxl.load_workbook(path, data_only=True)
    values = {}
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                if cell.value is not None:
                    values[f"{ws.title}!{cell.coordinate}"] = cell.value
    return values


def compare_to_baseline_v2(new_xlsx_path, new_parquet_path, baseline_dir, rel_tol=1e-9):
    baseline_xlsx = next(iter(baseline_dir.glob("*.xlsx")))
    baseline_parquet = next(iter(baseline_dir.glob("*_calc.parquet")))
    b_vals = read_xlsx_values(baseline_xlsx)
    n_vals = read_xlsx_values(new_xlsx_path)
    all_keys = set(b_vals) | set(n_vals)
    xlsx_diffs = []
    for k in sorted(all_keys):
        if k == "Meta!B9":
            continue
        b, n = b_vals.get(k), n_vals.get(k)
        if b == n:
            continue
        if (isinstance(b, (int, float)) and isinstance(n, (int, float))
                and b != 0 and abs(n - b) / abs(b) <= rel_tol):
            continue
        xlsx_diffs.append((k, b, n))

    b_df = pl.read_parquet(baseline_parquet)
    n_df = pl.read_parquet(new_parquet_path)
    parquet_mismatches = 0
    parquet_details = []
    if b_df.shape != n_df.shape:
        parquet_details.append(f"SHAPE MISMATCH: baseline={b_df.shape} new={n_df.shape}")
    else:
        for col in b_df.columns:
            if col not in n_df.columns:
                parquet_details.append(f"MISSING COLUMN: {col}")
                continue
            o, n = b_df[col], n_df[col]
            raw_mismatch = (o != n) & ~(o.is_null() & n.is_null())
            if o.dtype in (pl.Float32, pl.Float64) and n.dtype in (pl.Float32, pl.Float64):
                o_f = o.cast(pl.Float64)
                n_f = n.cast(pl.Float64)
                rel_ok = (o_f == 0) | ((n_f - o_f).abs() / o_f.abs() <= rel_tol)
                real_mismatch = raw_mismatch & ~rel_ok
            else:
                real_mismatch = raw_mismatch
            cnt = real_mismatch.sum()
            if cnt > 0:
                parquet_mismatches += cnt
                idx = real_mismatch.arg_true().to_list()[:2]
                for i in idx:
                    parquet_details.append(f"{col}[row {i}]: baseline={o[i]!r} new={n[i]!r}")

    print(f"xlsx cells compared: {len(all_keys)}  |  real differences (float noise + Meta!B9 excluded): {len(xlsx_diffs)}")
    for k, b, n in xlsx_diffs[:20]:
        print(f"  {k}: baseline={b!r}  new={n!r}")
    print(f"\nparquet mismatched cells (beyond {rel_tol} relative tolerance): {parquet_mismatches}")
    for d in parquet_details[:20]:
        print(f"  {d}")

    is_clean = (len(xlsx_diffs) == 0) and (parquet_mismatches == 0)
    print(f"\n{'CLEAN' if is_clean else 'DIFFERENCES FOUND'}")
    return is_clean
