from __future__ import annotations

from functools import lru_cache

from ctr_loader.engine_paths import snapshot_dir
from gap_config import get_settings


@lru_cache(maxsize=1)
def snapshot_folders() -> tuple[str, str]:
    return str(snapshot_dir("basis")), str(snapshot_dir("assumption"))


def __getattr__(name: str):
    if name == "DEFAULT_CTR_DIR":
        return get_settings().input_dir
    if name == "DEFAULT_BATCH_OUT":
        return get_settings().output_dir
    raise AttributeError(name)
