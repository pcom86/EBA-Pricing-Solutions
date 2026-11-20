import logging
import threading

logger = logging.getLogger(__name__)

_db_writes_lock = threading.Lock()
_db_writes_in_flight = 0


def db_write_started() -> None:
    global _db_writes_in_flight
    with _db_writes_lock:
        _db_writes_in_flight += 1


def db_write_finished() -> None:
    global _db_writes_in_flight
    with _db_writes_lock:
        _db_writes_in_flight = max(0, _db_writes_in_flight - 1)


def db_writes_pending() -> int:
    with _db_writes_lock:
        return _db_writes_in_flight


_file_sync_lock = threading.Lock()
_file_syncs_in_flight = 0


def file_sync_started() -> None:
    global _file_syncs_in_flight
    with _file_sync_lock:
        _file_syncs_in_flight += 1


def file_sync_finished() -> None:
    global _file_syncs_in_flight
    with _file_sync_lock:
        _file_syncs_in_flight = max(0, _file_syncs_in_flight - 1)


def file_syncs_pending() -> int:
    with _file_sync_lock:
        return _file_syncs_in_flight


_dir_cleanup_lock = threading.Lock()
_dir_pending_consumers: dict[str, int] = {}


def register_local_dir(path: str, consumers: int) -> None:
    if consumers <= 0:
        with _dir_cleanup_lock:
            _dir_pending_consumers.pop(path, None)
        _delete_dir(path)
        return
    with _dir_cleanup_lock:
        _dir_pending_consumers[path] = consumers


def release_local_dir(path: str) -> None:
    should_delete = False
    with _dir_cleanup_lock:
        if path in _dir_pending_consumers:
            _dir_pending_consumers[path] -= 1
            if _dir_pending_consumers[path] <= 0:
                del _dir_pending_consumers[path]
                should_delete = True
    if should_delete:
        _delete_dir(path)


def _delete_dir(path: str) -> None:
    import shutil
    try:
        shutil.rmtree(path, ignore_errors=True)
    except Exception as e:
        logger.warning(f"[cache_cleanup] could not remove {path}: {e}")


def sweep_stale_staging(staging_root: str, max_age_hours: float = 24.0) -> int:
    import os
    import shutil
    import time
    if not os.path.isdir(staging_root):
        return 0
    cutoff = time.time() - (max_age_hours * 3600)
    removed = 0
    for name in os.listdir(staging_root):
        p = os.path.join(staging_root, name)
        try:
            if os.path.isdir(p) and os.path.getmtime(p) < cutoff:
                shutil.rmtree(p, ignore_errors=True)
                removed += 1
        except Exception:
            logger.warning("Could not sweep stale staging directory %s", p, exc_info=True)
            continue
    return removed


_report_writes_lock = threading.Lock()
_report_writes_in_flight = 0


def report_write_started() -> None:
    global _report_writes_in_flight
    with _report_writes_lock:
        _report_writes_in_flight += 1


def report_write_finished() -> None:
    global _report_writes_in_flight
    with _report_writes_lock:
        _report_writes_in_flight = max(0, _report_writes_in_flight - 1)


def report_writes_pending() -> int:
    with _report_writes_lock:
        return _report_writes_in_flight
