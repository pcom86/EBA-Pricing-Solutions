from __future__ import annotations

import tempfile
import types
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock, patch

from gap_config import ConfigurationError
from gap_config.storage import (
    Storage,
    join_location,
    local_directory,
    local_file,
    select_snapshot,
)

from tests.utilities.fake_s3 import FakeS3, S3Error


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.client = FakeS3()
        settings = types.SimpleNamespace(staging_dir=self.root / "staging")
        self.settings = patch("gap_config.storage.get_settings", return_value=settings)
        self.settings.start()
        self.addCleanup(self.settings.stop)

    def test_local_storage_round_trip_without_boto3(self):
        source = self.root / "source.bin"
        source.write_bytes(b"payload")
        with patch.dict("sys.modules", {"boto3": None}):
            store = Storage(self.root / "outputs")
            location = store.put_file("nested/result.bin", source)
            self.assertEqual(Path(location).read_bytes(), b"payload")
            self.assertTrue(store.exists("nested/result.bin"))
            self.assertFalse(store.exists("missing"))
            self.assertEqual(store.list(), ["nested/result.bin"])
            self.assertEqual(store.get_path("nested/result.bin"), Path(location))
            store.put_file("nested/result.bin", location)

    def test_local_input_validation(self):
        with self.assertRaises(FileNotFoundError):
            local_file(self.root / "missing")
        with self.assertRaises(FileNotFoundError):
            local_directory(self.root / "missing")
        self.assertEqual(local_directory(self.root), self.root)

    def test_s3_upload_and_download(self):
        source = self.root / "source.bin"
        source.write_bytes(b"payload")
        store = Storage("s3://bucket/results", self.client)
        self.assertEqual(store.put_file("nested/result.bin", source), "s3://bucket/results/nested/result.bin")
        self.assertEqual(store.get_path("nested/result.bin").read_bytes(), b"payload")
        self.assertTrue(store.exists("nested/result.bin"))
        self.assertFalse(store.exists("missing"))

    def test_pagination_and_prefix_boundaries(self):
        for key in ("inputs/a.ctr", "inputs/b.ctr", "inputs/nested/c.ctr", "inputs/", "inputs-other/leak.ctr"):
            self.client.seed("bucket", key)
        folder = local_directory("s3://bucket/inputs", self.client)
        self.assertEqual(sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file()),
                    ["a.ctr", "b.ctr", "nested/c.ctr"])
        self.assertEqual(self.client.list_requests, [("bucket", "inputs/")])

    def test_single_object_staging(self):
        self.client.seed("bucket", "inputs/a.ctr", b"ctr-data")
        path = local_file("s3://bucket/inputs/a.ctr", self.client)
        self.assertEqual(path.name, "a.ctr")
        self.assertEqual(path.read_bytes(), b"ctr-data")
        with self.assertRaises(ValueError):
            local_file("s3://bucket/", self.client)

    def test_empty_prefix_is_an_error(self):
        with self.assertRaises(FileNotFoundError):
            local_directory("s3://bucket/missing", self.client)

    def test_atomic_download_preserves_existing_file_on_failure(self):
        target = self.root / "result.bin"
        target.write_bytes(b"original")
        self.client.download_error = OSError("interrupted download")
        with self.assertRaises(OSError):
            Storage("s3://bucket", self.client).get_path("result.bin", target)
        self.assertEqual(target.read_bytes(), b"original")
        self.assertEqual(list(self.root.glob("*.part")), [])

    def test_access_denied_is_not_treated_as_missing(self):
        self.client.head_error = S3Error("AccessDenied")
        with self.assertRaises(S3Error):
            Storage("s3://bucket", self.client).exists("file")

    def test_traversal_and_drive_keys_rejected(self):
        for root in (self.root, "s3://bucket/prefix"):
            for key in ("../outside", "/absolute", "folder/../outside", "C:/drive", r"folder\file"):
                with self.subTest(root=root, key=key), self.assertRaises(ValueError):
                    Storage(root, self.client).get_path(key)

    def test_malicious_listed_key_rejected(self):
        self.client.seed("bucket", "inputs/../outside")
        with self.assertRaises(ValueError):
            local_directory("s3://bucket/inputs", self.client)

    def test_client_uses_default_credential_chain_lazily(self):
        factory = Mock(return_value=self.client)
        with patch.dict("sys.modules", {"boto3": types.SimpleNamespace(client=factory)}):
            store = Storage("s3://bucket")
            factory.assert_not_called()
            self.assertFalse(store.exists("missing"))
            factory.assert_called_once_with("s3")

    def test_missing_sdk_has_actionable_error(self):
        with patch.dict("sys.modules", {"boto3": None}), self.assertRaisesRegex(ConfigurationError, "requirements-s3"):
            Storage("s3://bucket").exists("missing")

    def test_pinned_and_latest_s3_snapshots(self):
        self.client.seed("bucket", "basis/v1/table.parquet", modified=datetime(2025, 1, 1, tzinfo=UTC))
        self.client.seed("bucket", "basis/v2/table.parquet", b"latest", datetime(2026, 1, 1, tzinfo=UTC))
        self.client.seed("bucket", "basis/not-a-snapshot/note.txt")
        self.assertEqual(select_snapshot("s3://bucket/basis", "v1", self.client).name, "v1")
        latest = select_snapshot("s3://bucket/basis", None, self.client)
        self.assertEqual(latest.name, "v2")
        self.assertEqual((latest / "table.parquet").read_bytes(), b"latest")

    def test_snapshot_requires_parquet_and_single_directory_name(self):
        self.client.seed("bucket", "basis/empty/note.txt")
        with self.assertRaises(FileNotFoundError):
            select_snapshot("s3://bucket/basis", "empty", self.client)
        with self.assertRaises(ValueError):
            select_snapshot("s3://bucket/basis", "nested/version", self.client)

    def test_local_snapshot_selection(self):
        folder = self.root / "v1"
        folder.mkdir()
        (folder / "table.parquet").write_bytes(b"parquet")
        self.assertEqual(select_snapshot(str(self.root), "v1"), folder)
        self.assertEqual(select_snapshot(str(self.root), None), folder)

    def test_uri_joining_does_not_use_windows_separators(self):
        self.assertEqual(join_location("s3://bucket/output", "nested", "file.xlsx"), "s3://bucket/output/nested/file.xlsx")


if __name__ == "__main__":
    unittest.main()
