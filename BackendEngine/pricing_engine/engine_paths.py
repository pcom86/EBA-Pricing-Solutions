from __future__ import annotations

import logging

from ctr_loader.engine_paths import (
    CTR_LOADER,
    CTR_LOADER_DIR,
    ENGINE_ROOT,
    snapshot_dir,
)

logger = logging.getLogger(__name__)

__all__ = ["CTR_LOADER", "CTR_LOADER_DIR", "ENGINE_ROOT", "snapshot_dir"]

if __name__ == "__main__":
    logger.info("ENGINE_ROOT     : %s", ENGINE_ROOT)
    logger.info("newest basis    : %s", snapshot_dir("basis"))
    logger.info("newest assump   : %s", snapshot_dir("assumption"))
