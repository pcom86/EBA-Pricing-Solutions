from __future__ import annotations

import tempfile
from pathlib import Path

from gap_config import ConfigurationError, Settings, get_settings
from gap_config.settings import resolve_location, validate_location
from gap_config.storage import is_s3


def _filesystem_path(location: str | Path, label: str) -> Path:
    value = validate_location(str(location).strip())
    if is_s3(value):
        raise ConfigurationError(
            f"{label} must be a mounted filesystem path for the GUI, not an S3 URI. "
            "Use a folder accessible inside the container."
        )
    return Path(resolve_location(value, get_settings().config_file.parent))


def _readable_directory(location: str | Path, label: str) -> Path:
    path = _filesystem_path(location, label)
    try:
        next(path.iterdir(), None)
    except OSError as exc:
        raise ConfigurationError(f"{label} is not an accessible directory: {path}") from exc
    return path


def input_directory(location: str | Path | None = None) -> Path:
    return _readable_directory(
        get_settings().input_dir if location is None else location, "CTR input"
    )


def input_file(location: str | Path) -> Path:
    path = _filesystem_path(location, "CTR input")
    try:
        with path.open("rb"):
            pass
    except OSError as exc:
        raise ConfigurationError(f"CTR input is not a readable file: {path}") from exc
    return path


def batch_ctr_files(location: str | Path) -> list[Path]:
    folder = input_directory(location)
    try:
        paths = sorted(
            (path for path in folder.iterdir() if path.is_file() and path.suffix.lower() == ".ctr"),
            key=lambda path: path.name,
        )
    except OSError as exc:
        raise ConfigurationError(f"Cannot list CTR input directory: {folder}") from exc
    if not paths:
        raise ConfigurationError(f"No .ctr files found in: {folder}")
    return [input_file(path) for path in paths]


def output_directory(location: str | Path | None = None) -> Path:
    path = _filesystem_path(
        get_settings().output_dir if location is None else location, "Report output"
    )
    try:
        path.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=path):
            pass
    except OSError as exc:
        raise ConfigurationError(f"Report output is not a writable directory: {path}") from exc
    return path


def validate_gui_settings() -> Settings:
    settings = get_settings()
    settings.validate_runtime()
    input_directory()
    _readable_directory(settings.basis_root, "Basis snapshots")
    _readable_directory(settings.assumption_root, "Assumption snapshots")
    output_directory()
    if settings.db_enabled:
        output_directory(settings.artifact_root)
    return settings
