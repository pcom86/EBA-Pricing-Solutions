from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit

from .settings import ConfigurationError, get_settings, validate_location


def is_s3(location: str | Path) -> bool:
    return str(location).startswith("s3://")


def join_location(root: str | Path, *parts: str) -> str:
    root = validate_location(str(root))
    for part in parts:
        _safe_key(part)
    if is_s3(root):
        return "/".join([root.rstrip("/"), *(p.strip("/") for p in parts if p)])
    return str(Path(root).joinpath(*parts))


def _safe_key(key: str) -> str:
    if (not isinstance(key, str) or "\\" in key or key.startswith("/")
            or any(p in (".", "..") or ":" in p for p in key.split("/"))):
        raise ValueError("Object keys must be relative paths without traversal or drive components")
    return key


class Storage:
    def __init__(self, root: str | Path, client=None):
        self.root = validate_location(str(root))
        self._client = client
        self.remote = is_s3(self.root)
        if self.remote:
            parts = urlsplit(self.root)
            self.bucket = parts.netloc
            self.prefix = parts.path.strip("/")
        else:
            self.path = Path(self.root)

    @property
    def client(self):
        if self._client is None:
            try:
                import boto3
            except ImportError as exc:
                raise ConfigurationError("S3 storage requires boto3; install requirements-s3.txt in the image") from exc
            self._client = boto3.client("s3")
        return self._client

    def _key(self, key: str) -> str:
        _safe_key(key)
        return "/".join(p for p in (self.prefix, key) if p)

    def put_file(self, key: str, local_path: str | Path) -> str:
        _safe_key(key)
        source = Path(local_path)
        if self.remote:
            self.client.upload_file(str(source), self.bucket, self._key(key))
            return join_location(self.root, key)
        target = self.path / key
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.resolve() != target.resolve():
            shutil.copy2(source, target)
        return str(target)

    def exists(self, key: str) -> bool:
        _safe_key(key)
        if not self.remote:
            return (self.path / key).exists()
        try:
            self.client.head_object(Bucket=self.bucket, Key=self._key(key))
            return True
        except Exception as exc:
            response = getattr(exc, "response", {})
            if str(response.get("Error", {}).get("Code")) in ("404", "NoSuchKey", "NotFound"):
                return False
            raise

    def objects(self, prefix: str = ""):
        _safe_key(prefix)
        if not self.remote:
            base = self.path / prefix
            if base.is_file():
                yield {"Key": str(base.relative_to(self.path)).replace(os.sep, "/"),
                       "LastModified": base.stat().st_mtime}
            elif base.is_dir():
                for path in base.rglob("*"):
                    if path.is_file():
                        yield {"Key": path.relative_to(self.path).as_posix(), "LastModified": path.stat().st_mtime}
            return
        root_prefix = self.prefix + "/" if self.prefix else ""
        query_prefix = root_prefix + prefix.strip("/")
        if prefix:
            query_prefix += "/"
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=query_prefix):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                if not key.startswith(query_prefix) or key.endswith("/"):
                    continue
                relative = key[len(root_prefix):]
                _safe_key(relative)
                yield {**obj, "Key": relative}

    def list(self, prefix: str = "") -> list[str]:
        return [obj["Key"] for obj in self.objects(prefix)]

    def get_path(self, key: str, destination: str | Path | None = None) -> Path:
        _safe_key(key)
        if not self.remote:
            path = self.path / key
            if not path.is_file():
                raise FileNotFoundError(f"No object at key: {key}")
            return path
        target = Path(destination) if destination else _download_root() / PurePosixPath(key).name
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=target.parent, suffix=".part")
        os.close(fd)
        try:
            self.client.download_file(self.bucket, self._key(key), temporary)
            os.replace(temporary, target)
        finally:
            Path(temporary).unlink(missing_ok=True)
        return target


def _download_root() -> Path:
    staging = get_settings().staging_dir / "downloads"
    staging.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(dir=staging))


def local_file(location: str | Path, client=None) -> Path:
    location = validate_location(str(location))
    if not is_s3(location):
        path = Path(location)
        if not path.is_file():
            raise FileNotFoundError(f"Input file not found: {path}")
        return path
    parts = urlsplit(location)
    key = parts.path.lstrip("/")
    if not key or key.endswith("/"):
        raise ValueError("Expected an S3 object URI, not a directory")
    return Storage(f"s3://{parts.netloc}", client).get_path(key)


def local_directory(location: str | Path, client=None) -> Path:
    location = validate_location(str(location))
    if not is_s3(location):
        path = Path(location)
        if not path.is_dir():
            raise FileNotFoundError(f"Input directory not found: {path}")
        return path
    store = Storage(location, client)
    keys = store.list()
    if not keys:
        raise FileNotFoundError(f"No input objects under {location}")
    name = PurePosixPath(urlsplit(location).path).name or "inputs"
    target = _download_root() / name
    for key in keys:
        store.get_path(key, target / key)
    return target


def select_snapshot(root: str, name: str | None, client=None) -> Path:
    store = Storage(root, client)
    if name is not None:
        _safe_key(name)
        if not name or "/" in name:
            raise ValueError("Snapshot name must be a single directory name")
        folder = local_directory(join_location(root, name), client)
    elif store.remote:
        modified: dict[str, Any] = {}
        for obj in store.objects():
            parts = PurePosixPath(obj["Key"]).parts
            if len(parts) == 2 and parts[-1].endswith(".parquet"):
                version = parts[0]
                timestamp = obj["LastModified"]
                modified[version] = max(modified.get(version, timestamp), timestamp)
        if not modified:
            raise FileNotFoundError(f"No non-empty snapshot under {root}")
        folder = local_directory(join_location(root, max(modified, key=lambda k: modified[k])), client)
    else:
        base = Path(root)
        candidates = [p for p in base.iterdir() if p.is_dir() and any(p.glob("*.parquet"))]
        if not candidates:
            raise FileNotFoundError(f"No non-empty snapshot under {root}")
        folder = max(candidates, key=lambda p: p.stat().st_mtime)
    if not any(folder.glob("*.parquet")):
        raise FileNotFoundError(f"Snapshot has no parquet files: {folder}")
    return folder
