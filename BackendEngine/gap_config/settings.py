from __future__ import annotations

import json
import os
import re
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path, PureWindowsPath
from types import MappingProxyType
from urllib.parse import urlsplit

APPLICATION_ROOT = Path(__file__).resolve().parent.parent


class ConfigurationError(ValueError):
	pass


def validate_location(value: str) -> str:
	if not isinstance(value, str) or not value.strip():
		raise ConfigurationError("Storage locations must be nonempty strings")
	if value.startswith("s3://"):
		try:
			parts = urlsplit(value)
		except ValueError as exc:
			raise ConfigurationError("Invalid S3 location; expected s3://bucket/prefix") from exc
		if (not parts.netloc or parts.username or parts.password or ":" in parts.netloc
				or parts.query or parts.fragment or "\\" in value or "//" in parts.path
				or any(p in (".", "..") for p in parts.path.split("/"))):
			raise ConfigurationError("Invalid S3 location; expected s3://bucket/prefix")
	elif "://" in value:
		raise ConfigurationError("Storage locations support only filesystem paths or s3:// URIs")
	elif os.name != "nt" and (PureWindowsPath(value).drive or "\\" in value):
		raise ConfigurationError("Windows paths are not valid on Linux; configure a mounted path or S3 URI")
	return value


def resolve_location(value: str, base: Path) -> str:
	validate_location(value)
	if value.startswith("s3://"):
		return value.rstrip("/")
	path = Path(value).expanduser()
	return str((base / path).resolve() if not path.is_absolute() else path.resolve())


def _boolean(value, name: str) -> bool:
	if isinstance(value, bool):
		return value
	if isinstance(value, str) and value.lower() in ("true", "false", "1", "0"):
		return value.lower() in ("true", "1")
	raise ConfigurationError(f"{name} must be true or false")


def _positive_int(value, name: str) -> int:
	if isinstance(value, bool) or not isinstance(value, (int, str)):
		raise ConfigurationError(f"{name} must be a positive integer")
	try:
		result = int(value)
	except ValueError as exc:
		raise ConfigurationError(f"{name} must be a positive integer") from exc
	if result < 1:
		raise ConfigurationError(f"{name} must be a positive integer")
	return result


@dataclass(frozen=True)
class Settings:
	config_file: Path
	input_dir: str
	output_dir: str
	cache_dir: Path
	staging_dir: Path
	artifact_root: str
	basis_root: str
	assumption_root: str
	basis_snapshot: str | None
	assumption_snapshot: str | None
	db_enabled: bool
	db_connection_string: str = field(repr=False)
	pricing_workers: int
	report_workers: int
	database_workers: int
	service_urls: Mapping[str, str]

	def validate_runtime(self) -> None:
		for path in (self.cache_dir, self.staging_dir):
			path.mkdir(parents=True, exist_ok=True)
			try:
				with tempfile.TemporaryFile(dir=path):
					pass
			except OSError as exc:
				raise ConfigurationError(f"Working directory is not writable: {path}") from exc


def load_settings(config_file: str | Path | None = None,
				  environ: Mapping[str, str] | None = None) -> Settings:
	env = os.environ if environ is None else environ
	path = Path(config_file or env.get("GAP_CONFIG_FILE") or APPLICATION_ROOT / "config.json").expanduser().resolve()
	try:
		with path.open(encoding="utf-8") as source:
			data = json.load(source)
	except (OSError, ValueError) as exc:
		raise ConfigurationError(f"Cannot read configuration file: {path}") from exc
	schema = {
		"paths": {"input", "output", "cache", "staging", "artifacts", "basis", "assumptions"},
		"snapshots": {"basis", "assumptions"},
		"database": {"enabled"},
		"workers": {"pricing", "reports", "database"},
		"service_urls": None,
	}
	if not isinstance(data, dict) or set(data) - schema.keys():
		raise ConfigurationError("Unknown configuration sections; secrets and import paths do not belong in JSON")
	for section, keys in schema.items():
		values = data.get(section, {})
		if not isinstance(values, dict) or (keys is not None and set(values) - keys):
			raise ConfigurationError(f"Invalid or unknown settings in {section}")
	base = path.parent
	if env.get("GAP_ENGINE_ROOT"):
		legacy_root = resolve_location(env["GAP_ENGINE_ROOT"], base)
		if legacy_root.startswith("s3://"):
			raise ConfigurationError("GAP_ENGINE_ROOT must be a filesystem directory; configure individual S3 locations instead")
		base = Path(legacy_root)
	paths = data.get("paths", {})

	def location(key, variable, default):
		value = env.get(variable, paths.get(key, default))
		if value is None:
			value = default
		return resolve_location(value, base)

	cache = location("cache", "GAP_CTR_CACHE_DIR", str(Path(tempfile.gettempdir()) / "GAP_CTR_Cache"))
	staging = location("staging", "GAP_STAGING_DIR", str(Path(tempfile.gettempdir()) / "GAP_Pricing_Staging"))
	if cache.startswith("s3://") or staging.startswith("s3://"):
		raise ConfigurationError("Cache and staging must be local filesystem directories")
	snapshots = data.get("snapshots", {})

	def snapshot(key, variable):
		value = env.get(variable, snapshots.get(key))
		if value is not None and (not isinstance(value, str) or not value
								  or value in (".", "..") or "/" in value or "\\" in value):
			raise ConfigurationError(f"{variable} must be a single snapshot directory name or null")
		return value

	enabled = _boolean(env.get("GAP_DB_ENABLED", data.get("database", {}).get("enabled", False)), "GAP_DB_ENABLED")
	connection = env.get("GAP_DB_CONNECTION_STRING", "")
	if enabled and not connection.strip():
		raise ConfigurationError("GAP_DB_CONNECTION_STRING must be injected when database loading is enabled")
	urls = dict(data.get("service_urls", {}))
	for key, value in env.items():
		if key.startswith("GAP_SERVICE_") and key.endswith("_URL"):
			urls[key[len("GAP_SERVICE_"):-len("_URL")].lower()] = value
	for name, value in urls.items():
		if not re.fullmatch(r"[a-z][a-z0-9_]*", name) or not isinstance(value, str):
			raise ConfigurationError("Service names must be lowercase identifiers and URLs must be strings")
		try:
			parts = urlsplit(value)
			valid = parts.scheme in ("http", "https") and parts.hostname and not parts.username and not parts.password
			parts.port  # noqa: B018 - accessing .port raises ValueError for an invalid port
		except ValueError:
			valid = False
		if not valid or parts.query or parts.fragment:
			raise ConfigurationError(f"Service {name} requires a nonsecret HTTP(S) base URL without credentials/query/fragment")
	workers = data.get("workers", {})
	return Settings(
		config_file=path,
		input_dir=location("input", "GAP_INPUT_DIR", "Test/Batch run"),
		output_dir=location("output", "GAP_OUTPUT_DIR", "pricing_output"),
		cache_dir=Path(cache), staging_dir=Path(staging),
		artifact_root=location("artifacts", "GAP_ARTIFACT_ROOT", "local_s3_bucket"),
		basis_root=location("basis", "GAP_BASIS_ROOT", "basis_snapshots"),
		assumption_root=location("assumptions", "GAP_ASSUMPTION_ROOT", "assumption_snapshots"),
		basis_snapshot=snapshot("basis", "GAP_BASIS_SNAPSHOT"),
		assumption_snapshot=snapshot("assumptions", "GAP_ASSUMPTION_SNAPSHOT"),
		db_enabled=enabled, db_connection_string=connection,
		pricing_workers=_positive_int(env.get("GAP_PRICING_WORKERS", workers.get("pricing", 6)), "GAP_PRICING_WORKERS"),
		report_workers=_positive_int(env.get("GAP_REPORT_WORKERS", workers.get("reports", 3)), "GAP_REPORT_WORKERS"),
		database_workers=_positive_int(env.get("GAP_DB_WORKERS", workers.get("database", 1)), "GAP_DB_WORKERS"),
		service_urls=MappingProxyType(urls),
	)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
	return load_settings()
