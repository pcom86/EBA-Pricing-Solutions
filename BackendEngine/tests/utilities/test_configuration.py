from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from gap_config import ConfigurationError, get_settings, load_settings
from gap_config.settings import APPLICATION_ROOT, validate_location


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.config = self.root / "config.json"
        self.write({})
        self.addCleanup(get_settings.cache_clear)

    def write(self, data):
        self.config.write_text(json.dumps(data), encoding="utf-8")

    def test_defaults_are_relative_to_config_not_working_directory(self):
        settings = load_settings(self.config, {})
        self.assertEqual(settings.output_dir, str(self.root / "pricing_output"))
        self.assertFalse(settings.db_enabled)
        self.assertTrue(settings.cache_dir.is_absolute())
        self.assertTrue(settings.staging_dir.is_absolute())
        self.assertFalse((self.root / "pricing_output").exists())

    def test_default_file_is_repository_relative(self):
        settings = load_settings(environ={})
        self.assertEqual(settings.config_file, APPLICATION_ROOT / "config.json")

    def test_environment_overrides_json(self):
        self.write({"paths": {"input": "json-input", "output": "json-output"},
                    "workers": {"pricing": 2}, "service_urls": {"pricing": "https://example.com/api"}})
        settings = load_settings(self.config, {
            "GAP_INPUT_DIR": "s3://input-bucket/ctrs", "GAP_OUTPUT_DIR": "env-output",
            "GAP_PRICING_WORKERS": "4", "GAP_SERVICE_PRICING_URL": "https://example.org/api",
            "GAP_CTR_CACHE_DIR": "cache", "GAP_STAGING_DIR": "staging",
        })
        self.assertEqual(settings.input_dir, "s3://input-bucket/ctrs")
        self.assertEqual(settings.output_dir, str(self.root / "env-output"))
        self.assertEqual(settings.pricing_workers, 4)
        self.assertEqual(settings.service_urls["pricing"], "https://example.org/api")
        self.assertEqual(settings.cache_dir, self.root / "cache")
        self.assertEqual(settings.staging_dir, self.root / "staging")

    def test_custom_file_and_cached_loader(self):
        with patch.dict(os.environ, {"GAP_CONFIG_FILE": str(self.config)}, clear=True):
            get_settings.cache_clear()
            self.assertIs(get_settings(), get_settings())
            self.assertEqual(get_settings().config_file, self.config)

    def test_explicit_file_overrides_environment_file(self):
        settings = load_settings(self.config, {"GAP_CONFIG_FILE": "missing.json"})
        self.assertEqual(settings.config_file, self.config)

    def test_legacy_engine_root_is_data_only(self):
        settings = load_settings(self.config, {"GAP_ENGINE_ROOT": str(self.root / "data")})
        self.assertEqual(settings.basis_root, str(self.root / "data" / "basis_snapshots"))

    def test_database_requires_environment_secret(self):
        self.write({"database": {"enabled": True}})
        with self.assertRaises(ConfigurationError):
            load_settings(self.config, {})
        settings = load_settings(self.config, {"GAP_DB_CONNECTION_STRING": "secret-value"})
        self.assertTrue(settings.db_enabled)
        self.assertNotIn("secret-value", repr(settings))
        disabled = load_settings(self.config, {"GAP_DB_ENABLED": "false"})
        self.assertFalse(disabled.db_enabled)

    def test_secrets_import_paths_and_unknown_settings_rejected(self):
        for data in ({"aws_access_key_id": "secret"}, {"sys_path": []},
                     {"database": {"connection_string": "secret"}}, {"paths": {"windows_fallback": "J:"}}):
            with self.subTest(data=data):
                self.write(data)
                with self.assertRaises(ConfigurationError):
                    load_settings(self.config, {})

    def test_invalid_workers_and_booleans_rejected(self):
        for value in (0, -1, True, 1.5, "many"):
            with self.subTest(value=value):
                self.write({"workers": {"pricing": value}})
                with self.assertRaises(ConfigurationError):
                    load_settings(self.config, {})
        self.write({"database": {"enabled": "maybe"}})
        with self.assertRaises(ConfigurationError):
            load_settings(self.config, {})

    def test_services_are_nonsecret_http_urls(self):
        for value in ("s3://bucket/path", "https://user:password@example.org", "https://example.org?token=secret", "relative"):
            with self.subTest(value=value):
                self.write({"service_urls": {"pricing": value}})
                with self.assertRaises(ConfigurationError):
                    load_settings(self.config, {})

    def test_staging_and_cache_cannot_be_s3(self):
        for key in ("cache", "staging"):
            with self.subTest(key=key):
                self.write({"paths": {key: "s3://bucket/path"}})
                with self.assertRaises(ConfigurationError):
                    load_settings(self.config, {})

    def test_snapshots_must_be_single_directory_names(self):
        for value in ("../elsewhere", "folder/version", "", 10):
            self.write({"snapshots": {"basis": value}})
            with self.assertRaises(ConfigurationError):
                load_settings(self.config, {})

    def test_malformed_or_missing_json_is_clear_error(self):
        self.config.write_text("{", encoding="utf-8")
        with self.assertRaises(ConfigurationError):
            load_settings(self.config, {})
        with self.assertRaises(ConfigurationError):
            load_settings(self.root / "missing.json", {})

    def test_linux_rejects_windows_paths(self):
        with patch("gap_config.settings.os.name", "posix"):
            for value in (r"J:\engine", r"\\server\share", r"C:\Temp\cache"):
                with self.assertRaises(ConfigurationError):
                    validate_location(value)

    def test_s3_authority_errors_are_configuration_errors(self):
        for value in ("s3://bucket:bad/prefix", "s3://bucket:0/prefix", "s3://[broken/prefix",
                      "s3://user:secret@bucket/prefix", "s3://bucket//prefix"):
            with self.subTest(value=value), self.assertRaises(ConfigurationError):
                validate_location(value)

    def test_legacy_engine_root_cannot_be_an_s3_uri(self):
        with self.assertRaises(ConfigurationError):
            load_settings(self.config, {"GAP_ENGINE_ROOT": "s3://bucket/data"})

    def test_runtime_creates_only_writable_working_directories(self):
        self.write({"paths": {"cache": "cache", "staging": "staging"}})
        settings = load_settings(self.config, {})
        settings.validate_runtime()
        self.assertTrue(settings.cache_dir.is_dir())
        self.assertTrue(settings.staging_dir.is_dir())
        self.assertFalse(Path(settings.output_dir).exists())


if __name__ == "__main__":
    unittest.main()
