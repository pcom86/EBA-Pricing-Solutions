from __future__ import annotations

import importlib
import json
import logging
import sys
import threading
import time
import zipfile
from pathlib import Path

from gap_config import get_settings

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(message)s")

HERE = Path(__file__).resolve().parent
_ROOT = HERE.parent
BUNDLE_NAME = "_engine_bundle.zip"
META_NAME = "_bundle_meta.json"
BUNDLE_PATH = get_settings().cache_dir / BUNDLE_NAME

SEARCH_DIRS = [
    HERE,
    _ROOT / "ctr_loader",
    _ROOT / "pricing_engine",
    _ROOT / "reporting",
    _ROOT / "gap_config",
]

def _in_search_dirs(p: Path) -> bool:
    pr = p.resolve()
    for d in SEARCH_DIRS:
        if not d.exists():
            continue
        if pr.parent == d.resolve():
            return True
    return False


def discover_sources() -> dict[str, Path]:
    srcs: dict[str, Path] = {}
    for directory in SEARCH_DIRS:
        for path in directory.glob("*.py"):
            srcs[path.relative_to(_ROOT).as_posix()] = path
    config = get_settings().config_file
    srcs["config.json"] = config
    return srcs


def _meta_for(srcs: dict[str, Path]) -> dict:
    files = {}
    for n, p in srcs.items():
        st = p.stat()
        try:
            rel = str(p.resolve().relative_to(_ROOT.resolve()))
        except Exception:
            rel = str(p)
        files[n] = [rel, st.st_size, st.st_mtime_ns]
    return {"files": files}


def _embedded_meta(zpath: Path) -> dict | None:
    try:
        with zipfile.ZipFile(zpath, "r") as z:
            return json.loads(z.read(META_NAME).decode("utf-8"))
    except Exception:
        return None


def is_fresh(zpath: Path) -> bool:
    if not zpath.exists():
        return False
    emb = _embedded_meta(zpath)
    if not emb or "files" not in emb:
        return False
    for n, (rel, sz, mt) in emb["files"].items():
        p = (_ROOT / rel)
        if not p.exists():
            return False
        st = p.stat()
        if st.st_size != sz or st.st_mtime_ns != mt:
            return False
    return True


def rezip(srcs: dict[str, Path] | None = None) -> Path:
    import os
    if srcs is None:
        srcs = discover_sources()
    if not srcs:
        raise RuntimeError("engine_bundle: no source modules discovered")

    tmp = BUNDLE_PATH.with_suffix(".zip.tmp")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    meta = _meta_for(srcs)
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_STORED) as z:
        for n, p in srcs.items():
            z.write(p, arcname=n)
        z.writestr(META_NAME, json.dumps(meta, indent=0))
    os.replace(tmp, BUNDLE_PATH)
    return BUNDLE_PATH


def activate(verbose: bool = True) -> dict:
    status = {"used_zip": False, "rebuilt": False,
              "reason": "Package imports are authoritative; bundles are export-only", "seconds": 0.0}
    if verbose:
        logger.info("[engine_bundle] %s", status["reason"])
    return status


_warm_thread = None


def warm_async(modules=("reporting",), verbose: bool = True, delay_s: float = 0.0):
    global _warm_thread
    if _warm_thread is not None and _warm_thread.is_alive():
        return _warm_thread

    def _worker():
        if delay_s > 0:
            time.sleep(delay_s)
        for m in modules:
            t0 = time.perf_counter()
            try:
                importlib.import_module(m)
                if verbose:
                    logger.info(f"[engine_bundle] warmed {m} in "
                          f"{time.perf_counter()-t0:.3f}s (background)")
            except Exception as e:
                if verbose:
                    logger.info(f"[engine_bundle] warm {m} skipped: "
                          f"{type(e).__name__}: {e}")

    _warm_thread = threading.Thread(target=_worker, name="engine-warm",
                                    daemon=True)
    _warm_thread.start()
    return _warm_thread


def _selftest(keep: bool) -> int:
    logger.info("=" * 64)
    logger.info(" engine_bundle self-test (auto-discover + build + parity verify)")
    logger.info("=" * 64)

    srcs = discover_sources()
    if not srcs:
        logger.info(" FAIL: no sources discovered.")
        return 1
    for n in sorted(srcs):
        logger.info(f"  discovered  {n:<20} -> {srcs[n]}")
    logger.info(f"\n  {len(srcs)} flat engine modules in the closure")

    z = rezip(srcs)
    logger.info(f"  built {z.name} ({z.stat().st_size:,} bytes)")
    logger.info(f"  fresh check: {is_fresh(z)}  (must be True)")

    ok = True
    with zipfile.ZipFile(z, "r") as zf:
        names = set(zf.namelist())
        for n, p in srcs.items():
            same = (n in names) and (zf.read(n) == p.read_bytes())
            ok = ok and same
            logger.info(f"  parity {n:<20} {'OK' if same else 'MISMATCH'}")

    with zipfile.ZipFile(z) as archive:
        ok = ok and archive.testzip() is None

    if not keep:
        try:
            z.unlink()
            logger.info(f"  cleaned up {z.name} (self-test only; use --keep to retain)")
        except Exception as e:
            logger.info(f"  [warn] could not delete {z.name}: {e}")

    logger.info("-" * 64)
    logger.info(f" RESULT: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(_selftest(keep="--keep" in sys.argv))
