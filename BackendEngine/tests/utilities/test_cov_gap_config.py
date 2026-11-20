"""Additional coverage tests for the gap_config package.

These tests complement BackendEngine/tests/utilities/test_configuration.py and
BackendEngine/tests/utilities/test_storage.py by exercising the validation branches,
environment overrides, and filesystem/S3 edge cases those suites miss.
"""
from __future__ import annotations

import dataclasses
import json
import os
import tempfile
from pathlib import Path
from types import MappingProxyType, SimpleNamespace

import pytest

from gap_config import ConfigurationError, get_settings, load_settings
from gap_config.settings import resolve_location, validate_location
from gap_config import storage
from gap_config.storage import (
    Storage,
    is_s3,
    join_location,
    local_directory,
    local_file,
    select_snapshot,
)
from tests.utilities.fake_s3 import FakeS3, S3Error


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def staging_dir(tmp_path, monkeypatch):
    target = tmp_path / "staging"
    monkeypatch.setattr(
        storage, "get_settings", lambda: SimpleNamespace(staging_dir=target)
    )
    return target


def _write_config(tmp_path: Path, data) -> Path:
    config = tmp_path / "config.json"
    config.write_text(json.dumps(data), encoding="utf-8")
    return config


# ---------------------------------------------------------------------------
# settings.validate_location / resolve_location
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestValidateLocation:
    def test_rejects_empty_blank_and_non_string_values(self):
        for value in ("", "   ", None, 123, b"s3://bucket/p"):
            with pytest.raises(ConfigurationError, match="nonempty strings"):
                validate_location(value)

    def test_rejects_non_s3_uri_schemes(self):
        for value in (
            "https://example.com/x",
            "ftp://host/dir",
            "file://server/share",
        ):
            with pytest.raises(ConfigurationError, match="filesystem paths or s3"):
                validate_location(value)

    def test_accepts_plain_s3_uris(self):
        assert validate_location("s3://bucket") == "s3://bucket"
        assert validate_location("s3://bucket/prefix/sub") == "s3://bucket/prefix/sub"

    @pytest.mark.parametrize(
        "value",
        (
            "s3:///prefix",                  # empty bucket
            "s3://bucket/prefix?x=1",        # query component
            "s3://bucket/prefix#frag",       # fragment component
            "s3://bucket\\prefix",           # backslash
            "s3://bucket/./x",               # dot segment
            "s3://bucket/../x",              # traversal segment
            "s3://bucket:443/p",             # port in netloc
            "s3://user@bucket/p",            # username without password
            "s3://bucket//double",           # empty path segment
        ),
    )
    def test_rejects_malformed_s3_uris(self, value):
        with pytest.raises(ConfigurationError, match="Invalid S3 location"):
            validate_location(value)

    def test_accepts_native_filesystem_paths(self):
        assert validate_location("relative/dir") == "relative/dir"
        absolute = str(Path.cwd())
        assert validate_location(absolute) == absolute


@pytest.mark.unit
class TestResolveLocation:
    def test_strips_trailing_slash_from_s3_uri(self, tmp_path):
        assert resolve_location("s3://bucket/prefix/", tmp_path) == "s3://bucket/prefix"
        assert resolve_location("s3://bucket/", tmp_path) == "s3://bucket"

    def test_resolves_relative_against_base(self, tmp_path):
        assert resolve_location("sub/dir", tmp_path) == str(
            (tmp_path / "sub" / "dir").resolve()
        )

    def test_absolute_path_ignores_base(self, tmp_path):
        target = (tmp_path / "elsewhere" / "abs").resolve()
        assert resolve_location(str(target), tmp_path / "base") == str(target)

    def test_expands_user_home(self, tmp_path):
        assert resolve_location("~", tmp_path) == str(
            Path("~").expanduser().resolve()
        )

    def test_propagates_validation_errors(self, tmp_path):
        with pytest.raises(ConfigurationError):
            resolve_location("ftp://host/x", tmp_path)


# ---------------------------------------------------------------------------
# settings.load_settings / get_settings / Settings
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLoadSettings:
    def test_defaults_for_empty_config(self, tmp_path):
        config = _write_config(tmp_path, {})
        settings = load_settings(config, {})
        assert settings.config_file == config.resolve()
        assert settings.input_dir == str((tmp_path / "Test" / "Batch run").resolve())
        assert settings.output_dir == str((tmp_path / "pricing_output").resolve())
        assert settings.artifact_root == str((tmp_path / "local_s3_bucket").resolve())
        assert settings.basis_root == str((tmp_path / "basis_snapshots").resolve())
        assert settings.assumption_root == str(
            (tmp_path / "assumption_snapshots").resolve()
        )
        assert settings.cache_dir.is_absolute()
        assert settings.staging_dir.is_absolute()
        assert settings.basis_snapshot is None
        assert settings.assumption_snapshot is None
        assert settings.db_enabled is False
        assert settings.pricing_workers == 6
        assert settings.report_workers == 3
        assert settings.database_workers == 1
        assert dict(settings.service_urls) == {}

    def test_json_null_path_falls_back_to_default(self, tmp_path):
        config = _write_config(tmp_path, {"paths": {"input": None, "output": None}})
        settings = load_settings(config, {})
        assert settings.input_dir == str((tmp_path / "Test" / "Batch run").resolve())
        assert settings.output_dir == str((tmp_path / "pricing_output").resolve())

    def test_environment_overrides_json_for_every_location(self, tmp_path):
        config = _write_config(
            tmp_path,
            {
                "paths": {
                    "input": "j-in",
                    "output": "j-out",
                    "artifacts": "j-art",
                    "basis": "j-basis",
                    "assumptions": "j-ass",
                }
            },
        )
        settings = load_settings(
            config,
            {
                "GAP_INPUT_DIR": "env-in",
                "GAP_OUTPUT_DIR": "env-out",
                "GAP_ARTIFACT_ROOT": "s3://artifacts/root/",
                "GAP_BASIS_ROOT": "env-basis",
                "GAP_ASSUMPTION_ROOT": "env-ass",
            },
        )
        assert settings.input_dir == str((tmp_path / "env-in").resolve())
        assert settings.output_dir == str((tmp_path / "env-out").resolve())
        assert settings.artifact_root == "s3://artifacts/root"
        assert settings.basis_root == str((tmp_path / "env-basis").resolve())
        assert settings.assumption_root == str((tmp_path / "env-ass").resolve())

    def test_non_string_path_in_json_rejected(self, tmp_path):
        config = _write_config(tmp_path, {"paths": {"output": 5}})
        with pytest.raises(ConfigurationError):
            load_settings(config, {})

    def test_empty_env_var_path_rejected(self, tmp_path):
        config = _write_config(tmp_path, {})
        with pytest.raises(ConfigurationError, match="nonempty strings"):
            load_settings(config, {"GAP_OUTPUT_DIR": ""})

    def test_non_mapping_document_rejected(self, tmp_path):
        for payload in ("[1, 2]", '"text"', "42"):
            config = tmp_path / "config.json"
            config.write_text(payload, encoding="utf-8")
            with pytest.raises(ConfigurationError, match="Unknown configuration sections"):
                load_settings(config, {})

    def test_non_mapping_section_rejected(self, tmp_path):
        for data in (
            {"paths": "flat"},
            {"workers": [1, 2]},
            {"service_urls": "x"},
            {"snapshots": None},
            {"database": 5},
        ):
            config = _write_config(tmp_path, data)
            with pytest.raises(ConfigurationError, match="Invalid or unknown settings"):
                load_settings(config, {})

    def test_valid_snapshot_names_and_null(self, tmp_path):
        config = _write_config(
            tmp_path,
            {"snapshots": {"basis": "basis_v1", "assumptions": "ass_A"}},
        )
        settings = load_settings(config, {})
        assert settings.basis_snapshot == "basis_v1"
        assert settings.assumption_snapshot == "ass_A"
        config_null = _write_config(tmp_path, {"snapshots": {"basis": None}})
        assert load_settings(config_null, {}).basis_snapshot is None

    def test_snapshot_env_override_and_validation(self, tmp_path):
        config = _write_config(tmp_path, {"snapshots": {"basis": "json_snap"}})
        settings = load_settings(config, {"GAP_BASIS_SNAPSHOT": "env_snap"})
        assert settings.basis_snapshot == "env_snap"
        for bad in ("..", ".", "a/b", "a\\b", ""):
            with pytest.raises(ConfigurationError, match="GAP_ASSUMPTION_SNAPSHOT"):
                load_settings(config, {"GAP_ASSUMPTION_SNAPSHOT": bad})

    def test_database_flag_numeric_strings(self, tmp_path):
        config = _write_config(tmp_path, {})
        enabled = load_settings(
            config,
            {"GAP_DB_ENABLED": "1", "GAP_DB_CONNECTION_STRING": "server=db"},
        )
        assert enabled.db_enabled is True
        assert enabled.db_connection_string == "server=db"
        disabled = load_settings(config, {"GAP_DB_ENABLED": "0"})
        assert disabled.db_enabled is False
        assert disabled.db_connection_string == ""

    def test_database_flag_rejects_non_boolean_values(self, tmp_path):
        config = _write_config(tmp_path, {})
        for bad in ("yes", "2", "", "enabled"):
            with pytest.raises(ConfigurationError, match="GAP_DB_ENABLED"):
                load_settings(config, {"GAP_DB_ENABLED": bad})
        with pytest.raises(ConfigurationError, match="GAP_DB_ENABLED"):
            load_settings(config, {"GAP_DB_ENABLED": 5})
        config_int = _write_config(tmp_path, {"database": {"enabled": 1}})
        with pytest.raises(ConfigurationError):
            load_settings(config_int, {})

    def test_workers_from_json_and_env(self, tmp_path):
        config = _write_config(
            tmp_path,
            {"workers": {"pricing": 2, "reports": 4, "database": 3}},
        )
        settings = load_settings(config, {})
        assert (
            settings.pricing_workers,
            settings.report_workers,
            settings.database_workers,
        ) == (2, 4, 3)
        env_settings = load_settings(
            config,
            {
                "GAP_PRICING_WORKERS": "8",
                "GAP_REPORT_WORKERS": "7",
                "GAP_DB_WORKERS": " 2 ",
            },
        )
        assert (
            env_settings.pricing_workers,
            env_settings.report_workers,
            env_settings.database_workers,
        ) == (8, 7, 2)

    def test_worker_values_rejected(self, tmp_path):
        config = _write_config(tmp_path, {})
        for bad in ("0", "-2", "3.5", "abc"):
            with pytest.raises(ConfigurationError, match="GAP_REPORT_WORKERS"):
                load_settings(config, {"GAP_REPORT_WORKERS": bad})

    def test_service_urls_accept_valid_entries(self, tmp_path):
        config = _write_config(
            tmp_path,
            {
                "service_urls": {
                    "risk_engine": "https://host.example:8443/api",
                    "pricing": "http://localhost:5000",
                }
            },
        )
        settings = load_settings(
            config, {"GAP_SERVICE_REPORTING_URL": "https://reports.internal/base"}
        )
        assert settings.service_urls["risk_engine"] == "https://host.example:8443/api"
        assert settings.service_urls["pricing"] == "http://localhost:5000"
        assert settings.service_urls["reporting"] == "https://reports.internal/base"
        assert isinstance(settings.service_urls, MappingProxyType)
        with pytest.raises(TypeError):
            settings.service_urls["extra"] = "https://x.example"

    def test_service_urls_ignore_non_url_gap_vars(self, tmp_path):
        config = _write_config(tmp_path, {})
        settings = load_settings(config, {"GAP_SERVICE_PARTIAL": "https://x.example"})
        assert dict(settings.service_urls) == {}

    def test_service_url_invalid_names_and_values(self, tmp_path):
        for data in (
            {"service_urls": {"Invalid": "https://h.example"}},
            {"service_urls": {"has-hyphen": "https://h.example"}},
            {"service_urls": {"ok_name": 123}},
            {"service_urls": {"ok_name": ["https://h.example"]}},
        ):
            config = _write_config(tmp_path, data)
            with pytest.raises(ConfigurationError, match="Service names"):
                load_settings(config, {})
        config = _write_config(tmp_path, {})
        with pytest.raises(ConfigurationError, match="Service names"):
            load_settings(config, {"GAP_SERVICE__URL": "https://h.example"})

    @pytest.mark.parametrize(
        "url",
        (
            "ftp://host.example/x",
            "https://user@host.example/",
            "https://host.example/p#frag",
            "https://host.example:badport/p",
            "https://host.example:99999/p",
            "//host.example/path",
            "http://",
        ),
    )
    def test_service_url_rejects_non_http_or_secret_urls(self, tmp_path, url):
        config = _write_config(tmp_path, {"service_urls": {"svc": url}})
        with pytest.raises(ConfigurationError, match="nonsecret HTTP"):
            load_settings(config, {})

    def test_service_url_invalid_env_value_rejected(self, tmp_path):
        config = _write_config(tmp_path, {})
        with pytest.raises(ConfigurationError, match="nonsecret HTTP"):
            load_settings(config, {"GAP_SERVICE_API_URL": "s3://bucket/p"})

    def test_gap_engine_root_relative_resolution(self, tmp_path):
        config = _write_config(tmp_path, {})
        settings = load_settings(config, {"GAP_ENGINE_ROOT": "engine-data"})
        root = (tmp_path / "engine-data").resolve()
        assert settings.output_dir == str(root / "pricing_output")
        assert settings.basis_root == str(root / "basis_snapshots")

    def test_gap_engine_root_invalid_location_rejected(self, tmp_path):
        config = _write_config(tmp_path, {})
        with pytest.raises(ConfigurationError):
            load_settings(config, {"GAP_ENGINE_ROOT": "https://example.org/data"})

    def test_config_file_env_var_used_when_no_explicit_path(self, tmp_path):
        config = _write_config(tmp_path, {})
        settings = load_settings(environ={"GAP_CONFIG_FILE": str(config)})
        assert settings.config_file == config.resolve()

    def test_accepts_string_config_path(self, tmp_path):
        config = _write_config(tmp_path, {})
        settings = load_settings(str(config), {})
        assert settings.config_file == config.resolve()

    def test_settings_dataclass_is_frozen(self, tmp_path):
        config = _write_config(tmp_path, {})
        settings = load_settings(config, {})
        with pytest.raises(dataclasses.FrozenInstanceError):
            settings.pricing_workers = 9

    def test_validate_runtime_wraps_oserror(self, tmp_path, monkeypatch):
        config = _write_config(
            tmp_path, {"paths": {"cache": "cache", "staging": "staging"}}
        )
        settings = load_settings(config, {})

        def deny(*args, **kwargs):
            raise OSError("read-only filesystem")

        monkeypatch.setattr(tempfile, "TemporaryFile", deny)
        with pytest.raises(ConfigurationError, match="not writable"):
            settings.validate_runtime()

    def test_get_settings_caches_until_cleared(self, tmp_path, monkeypatch):
        for key in [k for k in os.environ if k.startswith("GAP_")]:
            monkeypatch.delenv(key, raising=False)
        config_a = _write_config(tmp_path, {})
        other = tmp_path / "other"
        other.mkdir()
        config_b = _write_config(other, {})
        monkeypatch.setenv("GAP_CONFIG_FILE", str(config_a))
        first = get_settings()
        assert first.config_file == config_a.resolve()
        monkeypatch.setenv("GAP_CONFIG_FILE", str(config_b))
        assert get_settings() is first
        get_settings.cache_clear()
        assert get_settings().config_file == config_b.resolve()


# ---------------------------------------------------------------------------
# storage module-level helpers
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestStorageHelpers:
    def test_is_s3(self):
        assert is_s3("s3://bucket/prefix") is True
        assert is_s3("/local/path") is False
        assert is_s3(Path("some/dir")) is False

    def test_join_location_local(self, tmp_path):
        assert join_location(str(tmp_path), "a", "b.txt") == str(
            tmp_path / "a" / "b.txt"
        )

    def test_join_location_s3_strips_slashes_and_skips_empty_parts(self):
        assert (
            join_location("s3://b/p/", "a/", "b", "f.txt")
            == "s3://b/p/a/b/f.txt"
        )
        assert join_location("s3://b/p", "", "x") == "s3://b/p/x"
        assert join_location("s3://b/p/") == "s3://b/p"

    def test_join_location_rejects_unsafe_parts(self, tmp_path):
        for part in ("..", "/abs", "a/../b", "C:/drive", "a\\b", 5):
            with pytest.raises(ValueError):
                join_location(str(tmp_path), part)
            with pytest.raises(ValueError):
                join_location("s3://b/p", part)

    def test_join_location_rejects_invalid_root(self):
        with pytest.raises(ConfigurationError):
            join_location("   ", "x")


@pytest.mark.unit
class TestStorageInit:
    def test_local_storage_attributes(self, tmp_path):
        store = Storage(str(tmp_path))
        assert store.remote is False
        assert store.path == tmp_path

    def test_s3_storage_attributes(self):
        client = FakeS3()
        store = Storage("s3://bucket/pre/fix", client)
        assert store.remote is True
        assert store.bucket == "bucket"
        assert store.prefix == "pre/fix"
        assert store.client is client

    def test_invalid_root_rejected(self):
        with pytest.raises(ConfigurationError):
            Storage("")


# ---------------------------------------------------------------------------
# Storage object operations
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestStorageOperations:
    def test_objects_yields_single_file(self, tmp_path):
        target = tmp_path / "data.txt"
        target.write_bytes(b"x")
        store = Storage(str(tmp_path))
        objects = list(store.objects("data.txt"))
        assert [obj["Key"] for obj in objects] == ["data.txt"]
        assert objects[0]["LastModified"] == target.stat().st_mtime

    def test_objects_missing_prefix_is_empty(self, tmp_path):
        store = Storage(str(tmp_path))
        assert list(store.objects("missing")) == []
        assert store.list("missing") == []

    def test_list_returns_posix_relative_keys(self, tmp_path):
        nested = tmp_path / "sub"
        nested.mkdir()
        (nested / "f.txt").write_bytes(b"x")
        (tmp_path / "top.txt").write_bytes(b"y")
        assert sorted(Storage(str(tmp_path)).list()) == ["sub/f.txt", "top.txt"]

    def test_get_path_local_missing_key(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="No object at key: missing"):
            Storage(str(tmp_path)).get_path("missing")

    def test_put_file_rejects_traversal_key(self, tmp_path):
        source = tmp_path / "src.bin"
        source.write_bytes(b"d")
        store = Storage(str(tmp_path / "out"))
        with pytest.raises(ValueError):
            store.put_file("../evil.bin", source)

    def test_s3_put_joins_key_with_and_without_prefix(self, tmp_path):
        source = tmp_path / "src.bin"
        source.write_bytes(b"d")
        client = FakeS3()
        assert (
            Storage("s3://bucket", client).put_file("k.bin", source)
            == "s3://bucket/k.bin"
        )
        assert (
            Storage("s3://bucket/pre", client).put_file("k.bin", source)
            == "s3://bucket/pre/k.bin"
        )
        assert client.uploads == [("bucket", "k.bin"), ("bucket", "pre/k.bin")]

    def test_exists_returns_false_for_all_missing_codes(self):
        class HeadError:
            def __init__(self, code):
                self.code = code

            def head_object(self, Bucket, Key):
                raise S3Error(self.code)

        for code in ("404", "NoSuchKey", "NotFound"):
            assert Storage("s3://bucket", HeadError(code)).exists("k") is False

    def test_exists_reraises_unexpected_errors(self):
        class Broken:
            def head_object(self, Bucket, Key):
                raise RuntimeError("boom")

        with pytest.raises(RuntimeError, match="boom"):
            Storage("s3://bucket", Broken()).exists("k")

    def test_s3_get_path_creates_destination_directories(self, tmp_path):
        client = FakeS3()
        client.seed("bucket", "p/k.bin", b"data")
        destination = tmp_path / "new" / "deep" / "f.bin"
        result = Storage("s3://bucket/p", client).get_path("k.bin", destination)
        assert result == destination
        assert destination.read_bytes() == b"data"

    def test_s3_get_path_defaults_to_staging_downloads(self, staging_dir):
        client = FakeS3()
        client.seed("bucket", "data.bin", b"payload")
        result = Storage("s3://bucket", client).get_path("data.bin")
        assert result.name == "data.bin"
        assert result.read_bytes() == b"payload"
        assert staging_dir in result.parents

    def test_s3_objects_filters_paginator_results(self):
        class LoosePaginator:
            def get_paginator(self, operation):
                assert operation == "list_objects_v2"
                return self

            def paginate(self, Bucket, Prefix):
                assert (Bucket, Prefix) == ("bucket", "p/sub/")
                yield {
                    "Contents": [
                        {"Key": "p/other/ignore.txt", "LastModified": 1},
                        {"Key": "p/sub/dir/", "LastModified": 2},
                        {"Key": "p/sub/ok.txt", "LastModified": 3},
                    ]
                }
                yield {}

        objects = list(Storage("s3://bucket/p", LoosePaginator()).objects("sub"))
        assert [obj["Key"] for obj in objects] == ["sub/ok.txt"]
        assert objects[0]["LastModified"] == 3


# ---------------------------------------------------------------------------
# local_file / local_directory / select_snapshot
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLocalInputs:
    def test_local_file_returns_existing_path(self, tmp_path):
        source = tmp_path / "input.ctr"
        source.write_bytes(b"ctr")
        assert local_file(source) == source
        assert local_file(str(source)) == source

    def test_local_file_rejects_invalid_location(self):
        with pytest.raises(ConfigurationError):
            local_file("   ")

    def test_s3_uri_requires_object_key(self):
        for uri in ("s3://bucket", "s3://bucket/dir/"):
            with pytest.raises(ValueError, match="S3 object URI"):
                local_file(uri, FakeS3())

    def test_local_directory_rejects_invalid_location(self):
        with pytest.raises(ConfigurationError):
            local_directory("   ")

    def test_s3_bucket_root_download_uses_inputs_fallback(self, staging_dir):
        client = FakeS3()
        client.seed("bucket", "alpha.ctr", b"ctr-data")
        target = local_directory("s3://bucket", client)
        assert target.name == "inputs"
        assert (target / "alpha.ctr").read_bytes() == b"ctr-data"
        assert staging_dir in target.parents


@pytest.mark.unit
class TestSelectSnapshot:
    def test_empty_name_rejected(self, tmp_path):
        with pytest.raises(ValueError, match="single directory name"):
            select_snapshot(str(tmp_path), "")

    def test_local_root_without_snapshots(self, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        with pytest.raises(FileNotFoundError, match="No non-empty snapshot"):
            select_snapshot(str(empty), None)

    def test_named_local_snapshot_requires_parquet(self, tmp_path):
        folder = tmp_path / "v1"
        folder.mkdir()
        (folder / "notes.txt").write_text("x")
        with pytest.raises(FileNotFoundError, match="no parquet"):
            select_snapshot(str(tmp_path), "v1")

    def test_local_picks_most_recent_snapshot(self, tmp_path):
        (tmp_path / "stray.txt").write_text("x")
        not_a_snapshot = tmp_path / "not_a_snapshot"
        not_a_snapshot.mkdir()
        (not_a_snapshot / "notes.txt").write_text("x")
        older = tmp_path / "v1"
        newer = tmp_path / "v2"
        for folder in (older, newer):
            folder.mkdir()
            (folder / "table.parquet").write_bytes(b"p")
        os.utime(older, (1_000_000, 1_000_000))
        os.utime(newer, (2_000_000, 2_000_000))
        assert select_snapshot(str(tmp_path), None) == newer

    def test_s3_root_without_two_level_parquet(self):
        client = FakeS3()
        client.seed("bucket", "basis/v1/notes.txt")
        client.seed("bucket", "basis/deep/nested/table.parquet")
        with pytest.raises(FileNotFoundError, match="No non-empty snapshot"):
            select_snapshot("s3://bucket/basis", None, client)
