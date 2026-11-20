from __future__ import annotations

import io
import json
import zipfile
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path

import polars as pl

BUNDLE_NAME = "_snapshot_bundle.zip"
_META_NAME = "_bundle_meta.json"
_read_parquet = partial(pl.read_parquet, rechunk=False)


def _live_stat_map(files: list[Path]) -> dict[str, list[int]]:
    out: dict[str, list[int]] = {}
    for p in files:
        st = p.stat()
        out[p.name] = [st.st_size, st.st_mtime_ns]
    return out


def _bundle_is_fresh(zpath: Path, files: list[Path]) -> bool:
    if not zpath.exists():
        return False
    try:
        with zipfile.ZipFile(zpath) as zf:
            if _META_NAME not in zf.namelist():
                return False
            recorded = json.loads(zf.read(_META_NAME))
    except (zipfile.BadZipFile, json.JSONDecodeError, KeyError):
        return False
    return recorded == _live_stat_map(files)


def _read_from_bundle(zpath: Path) -> dict[str, pl.DataFrame]:
    raw = zpath.read_bytes()
    tables: dict[str, pl.DataFrame] = {}
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        for nm in zf.namelist():
            if nm.endswith(".parquet"):
                tables[Path(nm).stem] = _read_parquet(io.BytesIO(zf.read(nm)))
    return tables


def _read_threaded(files: list[Path], max_workers: int) -> dict[str, pl.DataFrame]:
    workers = max(1, min(max_workers, len(files)))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        frames = list(ex.map(_read_parquet, files))
    return {p.stem: df for p, df in zip(files, frames)}


def rezip_snapshot(folder: str | Path) -> Path:
    folder = Path(folder)
    files = sorted(folder.glob("*.parquet"))
    if not files:
        raise FileNotFoundError(f"No .parquet files to bundle in {folder}")
    meta = _live_stat_map(files)
    zpath = folder / BUNDLE_NAME
    tmp = folder / f"{BUNDLE_NAME}.tmp"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_STORED) as zf:
        for p in files:
            zf.write(p, p.name)
        zf.writestr(_META_NAME, json.dumps(meta, sort_keys=True))
    tmp.replace(zpath)
    return zpath
