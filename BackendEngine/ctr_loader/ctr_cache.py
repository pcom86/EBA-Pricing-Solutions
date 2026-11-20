from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import threading
from pathlib import Path

from gap_config import get_settings
from gap_config.storage import local_file

logger = logging.getLogger(__name__)

_locks_guard = threading.Lock()
_locks: dict = {}
_NETWORK_FILESYSTEMS = {
    "9p", "ceph", "cifs", "davfs", "fuse.sshfs", "glusterfs", "nfs", "nfs4",
    "smb3", "sshfs",
}


def _lock_for(key: str) -> threading.Lock:
    with _locks_guard:
        lk = _locks.get(key)
        if lk is None:
            lk = threading.Lock()
            _locks[key] = lk
        return lk


def _fingerprint(src: Path) -> str:
    st = src.stat()
    key = f"{src.name}|{st.st_mtime_ns}|{st.st_size}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def _cache_target(src: Path, cache_dir: Path) -> Path:
    return cache_dir / f"{src.stem}__{_fingerprint(src)}{src.suffix}"


def _unescape_mount_field(value: str) -> str:
    return re.sub(r"\\([0-7]{3})", lambda match: chr(int(match.group(1), 8)), value)


def _is_linux_network_mount(path: Path, mountinfo_path: str | Path = "/proc/self/mountinfo") -> bool:
    try:
        resolved = path.resolve()
        mounts = []
        for line in Path(mountinfo_path).read_text(encoding="utf-8").splitlines():
            before, separator, after = line.partition(" - ")
            if not separator:
                continue
            fields = before.split()
            filesystem = after.split()
            if len(fields) < 5 or not filesystem:
                continue
            mountpoint = Path(_unescape_mount_field(fields[4])).resolve()
            try:
                resolved.relative_to(mountpoint)
            except ValueError:
                continue
            mounts.append((len(mountpoint.parts), filesystem[0]))
        return bool(mounts) and max(mounts)[1] in _NETWORK_FILESYSTEMS
    except (OSError, RuntimeError, ValueError):
        return False


def _should_cache_ctr(path: Path, platform: str | None = None) -> bool:
    platform = os.name if platform is None else platform
    if str(path).startswith("\\\\"):
        return True
    return platform == "posix" and _is_linux_network_mount(path)


def local_ctr_path(src, cache_dir=None, on_log=None) -> Path:
    src = local_file(src)
    cache_dir = Path(cache_dir) if cache_dir else get_settings().cache_dir
    log = on_log or (lambda m: None)

    if not _should_cache_ctr(src):
        return src

    cache_dir.mkdir(parents=True, exist_ok=True)
    target = _cache_target(src, cache_dir)

    if target.exists() and target.stat().st_size == src.stat().st_size:
        log(f"[ctr_cache] HIT  {target.name}")
        return target

    with _lock_for(str(target)):
        if target.exists() and target.stat().st_size == src.stat().st_size:
            log(f"[ctr_cache] HIT (waited on prefetch)  {target.name}")
            return target

        for old in cache_dir.glob(f"{src.stem}__*{src.suffix}"):
            if old != target:
                try:
                    old.unlink()
                except OSError:
                    pass

        log(f"[ctr_cache] MISS -> copying {src.name} ({src.stat().st_size/1e6:.1f} MB) ...")
        tmp = target.with_suffix(target.suffix + f".part.{os.getpid()}.{threading.get_ident()}")
        try:
            shutil.copy2(src, tmp)
            os.replace(tmp, target)
        finally:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass
        log(f"[ctr_cache] cached -> {target}")
        return target


def prefetch_async(src, cache_dir=None, on_log=None) -> threading.Thread:
    def _run():
        try:
            local_ctr_path(src, cache_dir, on_log)
        except Exception:
            logger.warning("CTR prefetch failed for %s; it will be loaded on demand", src, exc_info=True)
    t = threading.Thread(target=_run, daemon=True, name="ctr_prefetch")
    t.start()
    return t
