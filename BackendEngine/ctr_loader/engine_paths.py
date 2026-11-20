from __future__ import annotations

import logging
from pathlib import Path

from gap_config import get_settings
from gap_config.settings import APPLICATION_ROOT
from gap_config.storage import select_snapshot

logger = logging.getLogger(__name__)

ENGINE_ROOT = APPLICATION_ROOT
CTR_LOADER_DIR = ENGINE_ROOT / "ctr_loader"
CTR_LOADER = CTR_LOADER_DIR


def snapshot_dir(kind: str, name: str | None = None) -> Path:
    kind = kind.lower()
    settings = get_settings()
    if kind == "basis":
        root, configured = settings.basis_root, settings.basis_snapshot
    elif kind == "assumption":
        root, configured = settings.assumption_root, settings.assumption_snapshot
    else:
        raise ValueError(f"kind must be 'basis' or 'assumption', got {kind!r}")
    return select_snapshot(root, name if name is not None else configured)


if __name__ == "__main__":
    logger.info("ENGINE_ROOT     : %s", ENGINE_ROOT)
    logger.info("ctr_loader      : %s exists=%s", CTR_LOADER_DIR, CTR_LOADER_DIR.exists())
    logger.info("newest basis    : %s", snapshot_dir("basis"))
    logger.info("newest assump   : %s", snapshot_dir("assumption"))
