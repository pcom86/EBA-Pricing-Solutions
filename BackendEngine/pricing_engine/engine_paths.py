from __future__ import annotations
import logging

from ctr_loader.engine_paths import ENGINE_ROOT, CTR_LOADER_DIR, CTR_LOADER, snapshot_dir

logger = logging.getLogger(__name__)

__all__ = ["ENGINE_ROOT", "CTR_LOADER_DIR", "CTR_LOADER", "snapshot_dir"]

if __name__ == "__main__":
    logger.info("ENGINE_ROOT     : %s", ENGINE_ROOT)
    logger.info("newest basis    : %s", snapshot_dir("basis"))
    logger.info("newest assump   : %s", snapshot_dir("assumption"))
