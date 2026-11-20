from __future__ import annotations

import json
import os
import runpy
import shutil
import zipfile
from datetime import date, datetime
from pathlib import Path
from types import MappingProxyType, SimpleNamespace

import polars as pl
import pytest

from gap_config import get_settings

import ctr_loader.assumption_loader as assumption_loader
import ctr_loader.basis_loader as basis_loader
from ctr_loader import ctr_cache, ctr_coerce, engine_paths
from ctr_loader import products_registry as registry
from ctr_loader import snapshot_bundle_utils as sbu
from ctr_loader.assumption_loader import AssumptionsBundle
from ctr_loader.basis_loader import BasisBundle

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND = REPO_ROOT / "BackendEngine"
REAL_BASIS = BACKEND / "basis_snapshots" / "basis_v2026_08"
REAL_ASSUMP = BACKEND / "assumption_snapshots" / "assumption_A2026_08"

_needs_snapshots = pytest.mark.skipif(
    not (REAL_BASIS.is_dir() and REAL_ASSUMP.is_dir()),
    reason="real snapshot fixtures unavailable",
)

_GAP_ENV_VARS = (
    "GAP_CONFIG_FILE",
    "GAP_ENGINE_ROOT",
    "GAP_BASIS_ROOT",
    "GAP_ASSUMPTION_ROOT",
    "GAP_BASIS_SNAPSHOT",
    "GAP_ASSUMPTION_SNAPSHOT",
)


@pytest.fixture(autouse=True)
def _reset_caches():
    """Keep lru_cached settings/bundles from leaking between tests."""
    get_settings.cache_clear()
    basis_loader.get_basis.cache_clear()
    assumption_loader.get_assumptions.cache_clear()
    yield
    get_settings.cache_clear()
    basis_loader.get_basis.cache_clear()
    assumption_loader.get_assumptions.cache_clear()


@pytest.fixture
def clean_env(monkeypatch):
    """Drop GAP_* overrides so BackendEngine/config.json is authoritative."""
    for var in _GAP_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    get_settings.cache_clear()
    yield


def _write_parquet(folder: Path, name: str, df: pl.DataFrame | None = None) -> Path:
    if df is None:
        df = pl.DataFrame({"x": [1, 2, 3]})
    target = folder / name
    df.write_parquet(target)
    return target


# ---------------------------------------------------------------------------
# ctr_loader.ctr_coerce
# ---------------------------------------------------------------------------


class TestCleanText:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (None, None),
            ("", None),
            ("   ", None),
            ("abc", "abc"),
            ("  padded  ", "padded"),
            (0, "0"),
            (12.5, "12.5"),
        ],
    )
    def test_clean_text(self, value, expected):
        assert ctr_coerce.clean_text(value) == expected


class TestToFloat:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (None, None),
            ("", None),
            ("   ", None),
            (5, 5.0),
            (2.5, 2.5),
            ("42", 42.0),
            ("1,234.5", 1234.5),
            ("1 234", 1234.0),
            ("-1.5", -1.5),
            ("-", 0.0),  # bare dash is treated as an explicit zero
            ("abc", None),
            ("1,2,3", 123.0),
        ],
    )
    def test_to_float(self, value, expected):
        assert ctr_coerce.to_float(value) == expected


class TestToInt:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (None, None),
            ("", None),
            ("42", 42),
            (3.9, 3),
            ("-7", -7),
            ("junk", None),
        ],
    )
    def test_to_int(self, value, expected):
        assert ctr_coerce.to_int(value) == expected


class TestToPct:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (None, None),
            ("", None),
            (0.05, 0.05),
            (5, 5.0),
            ("5%", 0.05),
            ("12.5 %", 0.125),
            ("5,000%", 50.0),
            ("abc%", None),
            ("%", None),
            ("0.075", 0.075),
            ("junk", None),
        ],
    )
    def test_to_pct(self, value, expected):
        assert ctr_coerce.to_pct(value) == expected


class TestExcelToDate:
    def test_none_and_empty(self):
        assert ctr_coerce.excel_to_date(None, 0) is None
        assert ctr_coerce.excel_to_date("", 0) is None

    def test_datetime_and_date_passthrough(self):
        assert ctr_coerce.excel_to_date(datetime(2020, 5, 4, 13, 30), 0) == date(2020, 5, 4)
        assert ctr_coerce.excel_to_date(date(2020, 5, 4), 0) == date(2020, 5, 4)

    def test_excel_serial(self):
        # Serial 45000 in the 1900 date system is 2023-03-15.
        assert ctr_coerce.excel_to_date(45000, 0) == date(2023, 3, 15)

    def test_out_of_range_serial_returns_none(self):
        # ~8,000 years out overflows datetime; the failure is logged, not raised.
        assert ctr_coerce.excel_to_date(3_000_000, 0) is None

    def test_nonpositive_serial_falls_through_to_string_parsing(self):
        assert ctr_coerce.excel_to_date(0, 0) is None
        assert ctr_coerce.excel_to_date(-5, 0) is None

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("12/03/1985", date(1985, 3, 12)),
            ("1985-03-12", date(1985, 3, 12)),
            ("12-03-1985", date(1985, 3, 12)),
            ("03/12/1985", date(1985, 12, 3)),  # %m/%d/%Y
        ],
    )
    def test_string_formats(self, value, expected):
        assert ctr_coerce.excel_to_date(value, 0) == expected

    @pytest.mark.parametrize("value", ["garbage", "32/01/2000", "1985/03/12"])
    def test_unparseable_strings(self, value):
        assert ctr_coerce.excel_to_date(value, 0) is None


class TestNormalizeGender:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("M", "M"),
            ("male", "M"),
            ("F", "F"),
            ("Female", "F"),
            (" f ", "F"),
            ("Other", None),
            ("X", None),
            ("", None),
            (None, None),
            (1, None),
        ],
    )
    def test_normalize_gender(self, value, expected):
        assert ctr_coerce.normalize_gender(value) == expected


class TestSnakeCase:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (None, ""),
            ("", ""),
            ("Member No", "member_no"),
            ("Annual-Salary!", "annual_salary"),
            ("Scheme Code - X", "scheme_code_x"),
            ("  Spaced   Out  ", "spaced_out"),
            ("a__b", "a_b"),
            ("CAT#1", "cat_1"),
            (123, "123"),
        ],
    )
    def test_snake_case(self, value, expected):
        assert ctr_coerce.snake_case(value) == expected


# ---------------------------------------------------------------------------
# ctr_loader.ctr_cache
# ---------------------------------------------------------------------------


def _escape_mount(path: Path) -> str:
    """Escape a path the way /proc/self/mountinfo encodes special chars."""
    return str(path).replace("\\", "\\134").replace(" ", "\\040")


class TestCtrCacheHelpers:
    def test_lock_for_reuses_lock_per_key(self):
        lock_a = ctr_cache._lock_for("cov-key-a")
        lock_b = ctr_cache._lock_for("cov-key-a")
        lock_c = ctr_cache._lock_for("cov-key-b")
        assert lock_a is lock_b
        assert lock_a is not lock_c

    def test_fingerprint_changes_with_name_size_and_mtime(self, tmp_path):
        src = tmp_path / "a.ctr"
        src.write_bytes(b"12345")
        fp = ctr_cache._fingerprint(src)
        assert len(fp) == 16
        assert all(c in "0123456789abcdef" for c in fp)
        assert ctr_cache._fingerprint(src) == fp  # deterministic

        twin = tmp_path / "b.ctr"
        twin.write_bytes(b"12345")
        os.utime(twin, ns=(src.stat().st_atime_ns, src.stat().st_mtime_ns))
        assert ctr_cache._fingerprint(twin) != fp  # name differs

        src.write_bytes(b"123456")
        assert ctr_cache._fingerprint(src) != fp  # size differs

    def test_cache_target_naming(self, tmp_path):
        src = tmp_path / "model.ctr"
        src.write_bytes(b"data")
        cache_dir = tmp_path / "cache"
        target = ctr_cache._cache_target(src, cache_dir)
        assert target.parent == cache_dir
        assert target.name == f"model__{ctr_cache._fingerprint(src)}.ctr"

    def test_unescape_mount_field(self):
        assert ctr_cache._unescape_mount_field("C:\\134Temp\\040dir") == "C:\\Temp dir"
        assert ctr_cache._unescape_mount_field("plain") == "plain"

    def test_is_linux_network_mount_missing_mountinfo(self, tmp_path):
        src = tmp_path / "x.ctr"
        src.write_bytes(b"x")
        assert ctr_cache._is_linux_network_mount(
            src, tmp_path / "no-such-mountinfo"
        ) is False

    def test_is_linux_network_mount_detects_network_fs(self, tmp_path):
        mount = tmp_path / "network mount"
        mount.mkdir()
        src = mount / "quote.ctr"
        src.write_bytes(b"quote")
        mountinfo = tmp_path / "mountinfo"
        mountinfo.write_text(
            f"36 25 0:32 / {_escape_mount(mount)} rw,relatime - nfs4 server:/share rw\n",
            encoding="utf-8",
        )
        assert ctr_cache._is_linux_network_mount(src, mountinfo) is True

    def test_is_linux_network_mount_deepest_mount_wins(self, tmp_path):
        outer = tmp_path / "remote"
        inner = outer / "local"
        inner.mkdir(parents=True)
        src = inner / "quote.ctr"
        src.write_bytes(b"quote")
        mountinfo = tmp_path / "mountinfo"
        mountinfo.write_text(
            f"36 25 0:32 / {_escape_mount(outer)} rw - nfs server:/share rw\n"
            f"37 36 0:33 / {_escape_mount(inner)} rw - ext4 /dev/test rw\n",
            encoding="utf-8",
        )
        # The path lives under a local mount nested inside a network mount.
        assert ctr_cache._is_linux_network_mount(src, mountinfo) is False

    def test_is_linux_network_mount_skips_malformed_lines(self, tmp_path):
        src = tmp_path / "x.ctr"
        src.write_bytes(b"x")
        mountinfo = tmp_path / "mountinfo"
        mountinfo.write_text(
            "this line has no separator at all\n"
            "1 2 3 - ext4 /dev/x rw\n"           # too few fields before " - "
            "4 5 0:9 / mnt - \n"                  # separator but no filesystem fields
            "6 7 0:10 / Z:\\040elsewhere rw - nfs server:/s rw\n",  # not an ancestor
            encoding="utf-8",
        )
        assert ctr_cache._is_linux_network_mount(src, mountinfo) is False

    def test_should_cache_ctr_platform_policy(self, tmp_path, monkeypatch):
        local = tmp_path / "x.ctr"
        local.write_bytes(b"x")
        monkeypatch.setattr(ctr_cache, "_is_linux_network_mount", lambda *a, **k: True)
        assert ctr_cache._should_cache_ctr(local, platform="posix") is True
        monkeypatch.setattr(ctr_cache, "_is_linux_network_mount", lambda *a, **k: False)
        assert ctr_cache._should_cache_ctr(local, platform="posix") is False
        # Local Windows paths are never staged; UNC paths always are.
        assert ctr_cache._should_cache_ctr(local, platform="nt") is False
        assert (
            ctr_cache._should_cache_ctr(Path(r"\\server\share\q.ctr"), platform="nt")
            is True
        )
        # Default platform argument falls back to os.name.
        assert ctr_cache._should_cache_ctr(local) == (
            os.name == "posix" and ctr_cache._is_linux_network_mount(local)
        )


class TestLocalCtrPath:
    def test_returns_source_when_caching_not_needed(self, tmp_path, monkeypatch):
        src = tmp_path / "local.ctr"
        src.write_bytes(b"payload")
        monkeypatch.setattr(
            ctr_cache, "_should_cache_ctr", lambda path, platform=None: False
        )
        logs = []
        result = ctr_cache.local_ctr_path(src, cache_dir=tmp_path / "cache", on_log=logs.append)
        assert result == src
        assert logs == []

    def test_miss_copies_then_hit_returns_cached(self, tmp_path, monkeypatch):
        src = tmp_path / "quote.ctr"
        src.write_bytes(b"quote-bytes")
        cache_dir = tmp_path / "cache"
        monkeypatch.setattr(
            ctr_cache, "_should_cache_ctr", lambda path, platform=None: True
        )
        logs = []
        cached = ctr_cache.local_ctr_path(src, cache_dir=cache_dir, on_log=logs.append)
        assert cached != src
        assert cached.parent == cache_dir
        assert cached.read_bytes() == b"quote-bytes"
        assert any("MISS" in msg for msg in logs)
        assert any("cached ->" in msg for msg in logs)

        logs.clear()
        again = ctr_cache.local_ctr_path(src, cache_dir=cache_dir, on_log=logs.append)
        assert again == cached
        assert any("HIT" in msg for msg in logs)

    def test_evicts_stale_cached_copies(self, tmp_path, monkeypatch):
        src = tmp_path / "model.ctr"
        src.write_bytes(b"v2")
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        stale_file = cache_dir / "model__stalefingerprint.ctr"
        stale_file.write_bytes(b"old")
        blocked = cache_dir / "model__directory.ctr"  # unlink() will fail on a dir
        blocked.mkdir()
        monkeypatch.setattr(
            ctr_cache, "_should_cache_ctr", lambda path, platform=None: True
        )
        cached = ctr_cache.local_ctr_path(src, cache_dir=cache_dir)
        assert cached.read_bytes() == b"v2"
        assert not stale_file.exists()
        # A non-file matching the glob survives the best-effort cleanup.
        assert blocked.is_dir()

    def test_returns_target_completed_while_waiting_on_lock(
        self, tmp_path, monkeypatch
    ):
        src = tmp_path / "prefetch.ctr"
        src.write_bytes(b"prefetched")
        cache_dir = tmp_path / "cache"
        monkeypatch.setattr(
            ctr_cache, "_should_cache_ctr", lambda path, platform=None: True
        )
        target = ctr_cache._cache_target(src, cache_dir)

        class _PrefetchDoneLock:
            def __enter__(self):
                shutil.copy2(src, target)
                return self

            def __exit__(self, *exc):
                return False

        monkeypatch.setattr(ctr_cache, "_lock_for", lambda key: _PrefetchDoneLock())
        logs = []
        result = ctr_cache.local_ctr_path(src, cache_dir=cache_dir, on_log=logs.append)
        assert result == target
        assert any("waited on prefetch" in msg for msg in logs)

    def test_failed_replace_cleans_up_partial_copy(self, tmp_path, monkeypatch):
        src = tmp_path / "big.ctr"
        src.write_bytes(b"payload")
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        monkeypatch.setattr(
            ctr_cache, "_should_cache_ctr", lambda path, platform=None: True
        )
        target = ctr_cache._cache_target(src, cache_dir)
        target.mkdir()  # a directory at the target path blocks os.replace
        with pytest.raises(OSError):
            ctr_cache.local_ctr_path(src, cache_dir=cache_dir)
        assert not list(cache_dir.glob("*.part.*"))
        assert target.is_dir()

    def test_missing_source_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            ctr_cache.local_ctr_path(tmp_path / "missing.ctr", cache_dir=tmp_path)

    def test_uses_settings_cache_dir_when_not_given(self, tmp_path, monkeypatch):
        src = tmp_path / "cfg.ctr"
        src.write_bytes(b"cfg")
        configured = tmp_path / "configured_cache"
        monkeypatch.setattr(
            ctr_cache, "_should_cache_ctr", lambda path, platform=None: True
        )
        monkeypatch.setattr(
            ctr_cache,
            "get_settings",
            lambda: SimpleNamespace(cache_dir=configured),
        )
        cached = ctr_cache.local_ctr_path(src)
        assert cached.parent == configured


class TestPrefetchAsync:
    def test_prefetch_async_stages_copy_in_background(self, tmp_path, monkeypatch):
        src = tmp_path / "async.ctr"
        src.write_bytes(b"async-bytes")
        cache_dir = tmp_path / "cache"
        monkeypatch.setattr(
            ctr_cache, "_should_cache_ctr", lambda path, platform=None: True
        )
        thread = ctr_cache.prefetch_async(src, cache_dir=cache_dir)
        assert thread.daemon is True
        assert thread.name == "ctr_prefetch"
        thread.join(timeout=30)
        assert not thread.is_alive()
        target = ctr_cache._cache_target(src, cache_dir)
        assert target.read_bytes() == b"async-bytes"

    def test_prefetch_async_swallows_failures(self, tmp_path):
        thread = ctr_cache.prefetch_async(
            tmp_path / "missing.ctr", cache_dir=tmp_path / "cache"
        )
        thread.join(timeout=30)
        assert not thread.is_alive()


# ---------------------------------------------------------------------------
# ctr_loader.snapshot_bundle_utils
# ---------------------------------------------------------------------------


class TestSnapshotBundleUtils:
    def test_live_stat_map(self, tmp_path):
        p = _write_parquet(tmp_path, "a.parquet")
        st = p.stat()
        assert sbu._live_stat_map([p]) == {p.name: [st.st_size, st.st_mtime_ns]}

    def test_rezip_snapshot_and_freshness_roundtrip(self, tmp_path):
        folder = tmp_path / "snap"
        folder.mkdir()
        df_a = pl.DataFrame({"product": ["gla"], "v": [1.5]})
        _write_parquet(folder, "a.parquet", df_a)
        _write_parquet(folder, "b.parquet", pl.DataFrame({"w": [7]}))

        zpath = sbu.rezip_snapshot(folder)
        assert zpath == folder / sbu.BUNDLE_NAME
        assert zpath.exists()
        assert not (folder / f"{sbu.BUNDLE_NAME}.tmp").exists()

        files = sorted(folder.glob("*.parquet"))
        assert sbu._bundle_is_fresh(zpath, files) is True

        tables = sbu._read_from_bundle(zpath)
        assert set(tables) == {"a", "b"}  # _bundle_meta.json is not a table
        assert tables["a"].to_dicts() == df_a.to_dicts()

    def test_rezip_snapshot_rejects_empty_folder(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="No .parquet files to bundle"):
            sbu.rezip_snapshot(tmp_path)

    def test_bundle_is_fresh_missing_zip(self, tmp_path):
        p = _write_parquet(tmp_path, "a.parquet")
        assert sbu._bundle_is_fresh(tmp_path / "nope.zip", [p]) is False

    def test_bundle_is_fresh_bad_zip(self, tmp_path):
        p = _write_parquet(tmp_path, "a.parquet")
        bad = tmp_path / "bad.zip"
        bad.write_bytes(b"this is not a zip archive")
        assert sbu._bundle_is_fresh(bad, [p]) is False

    def test_bundle_is_fresh_missing_meta(self, tmp_path):
        p = _write_parquet(tmp_path, "a.parquet")
        zpath = tmp_path / "nometa.zip"
        with zipfile.ZipFile(zpath, "w") as zf:
            zf.writestr("a.parquet", b"junk")
        assert sbu._bundle_is_fresh(zpath, [p]) is False

    def test_bundle_is_fresh_corrupt_meta_json(self, tmp_path):
        p = _write_parquet(tmp_path, "a.parquet")
        zpath = tmp_path / "badmeta.zip"
        with zipfile.ZipFile(zpath, "w") as zf:
            zf.writestr(sbu._META_NAME, "{not valid json")
        assert sbu._bundle_is_fresh(zpath, [p]) is False

    def test_bundle_is_fresh_stale_stats(self, tmp_path):
        folder = tmp_path / "snap"
        folder.mkdir()
        p = _write_parquet(folder, "a.parquet", pl.DataFrame({"x": [1]}))
        zpath = sbu.rezip_snapshot(folder)
        files = sorted(folder.glob("*.parquet"))
        assert sbu._bundle_is_fresh(zpath, files) is True
        # Rewrite the parquet with different content -> recorded stats go stale.
        pl.DataFrame({"x": [1, 2, 3, 4, 5]}).write_parquet(p)
        assert sbu._bundle_is_fresh(zpath, sorted(folder.glob("*.parquet"))) is False

    def test_read_threaded_clamps_workers(self, tmp_path):
        p1 = _write_parquet(tmp_path, "one.parquet", pl.DataFrame({"a": [1]}))
        p2 = _write_parquet(tmp_path, "two.parquet", pl.DataFrame({"b": [2]}))
        tables = sbu._read_threaded([p1, p2], max_workers=0)
        assert set(tables) == {"one", "two"}
        tables = sbu._read_threaded([p1, p2], max_workers=64)
        assert tables["one"].height == 1


# ---------------------------------------------------------------------------
# ctr_loader.engine_paths
# ---------------------------------------------------------------------------


class TestEnginePaths:
    def test_constants(self):
        assert engine_paths.ENGINE_ROOT == BACKEND
        assert engine_paths.CTR_LOADER_DIR == BACKEND / "ctr_loader"
        assert engine_paths.CTR_LOADER is engine_paths.CTR_LOADER_DIR
        assert engine_paths.CTR_LOADER_DIR.is_dir()

    def test_snapshot_dir_defaults_from_config(self, clean_env):
        basis = engine_paths.snapshot_dir("basis")
        assert basis.is_dir()
        assert basis.name == "basis_v2026_08"
        assert any(basis.glob("*.parquet"))
        # kind is case-insensitive
        assumption = engine_paths.snapshot_dir("ASSUMPTION")
        assert assumption.name == "assumption_A2026_08"
        assert any(assumption.glob("*.parquet"))

    def test_snapshot_dir_rejects_unknown_kind(self, clean_env):
        with pytest.raises(ValueError, match="kind must be 'basis' or 'assumption'"):
            engine_paths.snapshot_dir("claims")

    def test_snapshot_dir_explicit_name(self, clean_env):
        folder = engine_paths.snapshot_dir("basis", name="basis_v2026_08")
        assert folder.name == "basis_v2026_08"
        with pytest.raises(FileNotFoundError):
            engine_paths.snapshot_dir("basis", name="missing_snapshot")

    @pytest.mark.parametrize("bad_name", ["nested/name", "..", ""])
    def test_snapshot_dir_name_validation(self, clean_env, bad_name):
        with pytest.raises(ValueError):
            engine_paths.snapshot_dir("basis", name=bad_name)

    def test_snapshot_dir_respects_env_overrides(self, tmp_path, monkeypatch):
        snap = tmp_path / "snap_x"
        snap.mkdir()
        _write_parquet(snap, "t.parquet")
        monkeypatch.setenv("GAP_BASIS_ROOT", str(tmp_path))
        monkeypatch.setenv("GAP_BASIS_SNAPSHOT", "snap_x")
        get_settings.cache_clear()
        result = engine_paths.snapshot_dir("basis")
        assert result.is_dir()
        assert result.name == "snap_x"

    def test_snapshot_dir_picks_newest_snapshot_when_unconfigured(
        self, tmp_path, monkeypatch
    ):
        root = tmp_path / "roots"
        root.mkdir()
        older = root / "snap_old"
        newer = root / "snap_new"
        older.mkdir()
        newer.mkdir()
        (root / "a_file.txt").write_text("not a dir")
        (root / "empty_dir").mkdir()  # dir without parquet is ignored
        _write_parquet(older, "t.parquet")
        _write_parquet(newer, "t.parquet")
        os.utime(older, ns=(2_000_000_000, 2_000_000_000))
        os.utime(newer, ns=(3_000_000_000, 3_000_000_000))
        fake = SimpleNamespace(
            basis_root=str(root),
            basis_snapshot=None,
            assumption_root=str(root),
            assumption_snapshot=None,
        )
        monkeypatch.setattr(engine_paths, "get_settings", lambda: fake)
        assert engine_paths.snapshot_dir("basis") == newer
        assert engine_paths.snapshot_dir("assumption") == newer

    def test_snapshot_dir_empty_root_raises(self, tmp_path, monkeypatch):
        root = tmp_path / "empty"
        root.mkdir()
        fake = SimpleNamespace(
            basis_root=str(root),
            basis_snapshot=None,
            assumption_root=str(root),
            assumption_snapshot=None,
        )
        monkeypatch.setattr(engine_paths, "get_settings", lambda: fake)
        with pytest.raises(FileNotFoundError, match="No non-empty snapshot"):
            engine_paths.snapshot_dir("assumption")

    def test_main_block_runs(self, clean_env):
        # Executes the `if __name__ == "__main__"` logging block end-to-end.
        runpy.run_path(str(engine_paths.__file__), run_name="__main__")


# ---------------------------------------------------------------------------
# ctr_loader.products_registry
# ---------------------------------------------------------------------------


class TestProductsRegistry:
    def test_get_returns_spec(self):
        spec = registry.get("gla")
        assert spec.key == "gla"
        assert spec.display_name == "Group Life Assurance"
        assert spec.rating_type == "yes_no"

    def test_get_unknown_key_raises(self):
        with pytest.raises(KeyError, match="not registered"):
            registry.get("not_a_product")

    def test_registry_invariants(self):
        assert registry.IN_SCOPE_PRODUCTS == tuple(registry.PRODUCT_REGISTRY.keys())
        for key, spec in registry.PRODUCT_REGISTRY.items():
            assert spec.key == key
            assert spec.catinput_blocks, key
            for name, (start, end) in spec.catinput_blocks.items():
                assert start < end, (key, name)
            if spec.rating_type == "accel_standalone":
                assert {"accelerator", "standalone"} <= set(spec.catinput_blocks), key


def _ci_mapping() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "product_option": ["opt_a", "opt_b"],
            "accelerator_table": ["CI_MALES_ACCELERATOR", "plain_table"],
            "standalone_table": ["CI_MALES_STANDALONE", "plain_table"],
            "age_definition": ["ANB", "ALB"],
            "variant_type": ["New", "Existing"],
        }
    )


class TestResolveCiMortalityTable:
    def test_accelerator_suffix_normalized(self):
        result = registry.resolve_ci_mortality_table(
            _ci_mapping(), "opt_a", "accelerator"
        )
        assert result == {
            "table_key": "ci_males_acc",
            "age_definition": "ANB",
            "variant_type": "New",
        }

    def test_standalone_suffix_normalized(self):
        result = registry.resolve_ci_mortality_table(
            _ci_mapping(), "opt_a", "standalone"
        )
        assert result["table_key"] == "ci_males_std"
        assert result["variant_type"] == "New"

    def test_unsuffixed_table_defaults_to_acc(self):
        for variant in ("accelerator", "standalone"):
            result = registry.resolve_ci_mortality_table(
                _ci_mapping(), "opt_b", variant
            )
            assert result["table_key"] == "plain_table_acc"
            assert result["age_definition"] == "ALB"

    def test_unknown_variant_uses_standalone_column(self):
        result = registry.resolve_ci_mortality_table(
            _ci_mapping(), "opt_a", "anything-else"
        )
        assert result["table_key"] == "ci_males_std"

    def test_unknown_product_option_raises(self):
        with pytest.raises(KeyError) as excinfo:
            registry.resolve_ci_mortality_table(_ci_mapping(), "missing", "accelerator")
        message = str(excinfo.value)
        assert "missing" in message
        assert "opt_a" in message and "opt_b" in message

    @_needs_snapshots
    def test_resolve_against_real_ci_table_mapping(self):
        mapping = pl.read_parquet(REAL_ASSUMP / "ci_table_mapping.parquet")
        required = {
            "product_option",
            "accelerator_table",
            "standalone_table",
            "age_definition",
            "variant_type",
        }
        assert required <= set(mapping.columns)
        option = mapping["product_option"][0]
        for variant in ("accelerator", "standalone"):
            result = registry.resolve_ci_mortality_table(mapping, option, variant)
            assert set(result) == {"table_key", "age_definition", "variant_type"}
            assert result["table_key"] == result["table_key"].lower()
            assert result["table_key"]


# ---------------------------------------------------------------------------
# ctr_loader.basis_loader
# ---------------------------------------------------------------------------


class TestBasisLoaderMeta:
    def test_manifest_takes_precedence(self, tmp_path):
        manifest = {"version": "vX", "built_at": "2026-01-01", "source_sha256": "deadbeef"}
        (tmp_path / "manifest.json").write_text(json.dumps(manifest))
        meta = basis_loader._read_meta(tmp_path)
        assert meta == manifest

    def test_manifest_defaults_fill_missing_keys(self, tmp_path):
        (tmp_path / "manifest.json").write_text(json.dumps({"built_at": "T"}))
        meta = basis_loader._read_meta(tmp_path)
        assert meta == {
            "version": tmp_path.name,
            "built_at": "T",
            "source_sha256": "",
        }

    def test_build_log_last_row_used(self, tmp_path):
        (tmp_path / "build_log.csv").write_text(
            "version,built_at,source_sha256\nv1,b1,h1\nv2,b2,h2\n"
        )
        meta = basis_loader._read_meta(tmp_path)
        assert meta == {"version": "v2", "built_at": "b2", "source_sha256": "h2"}

    def test_unreadable_build_log_falls_back_to_defaults(self, tmp_path):
        (tmp_path / "build_log.csv").mkdir()  # a directory: read_csv raises
        meta = basis_loader._read_meta(tmp_path)
        assert meta == {
            "version": tmp_path.name,
            "built_at": "",
            "source_sha256": "",
        }


class TestLoadBasis:
    def test_missing_folder_raises(self, tmp_path, monkeypatch):
        ghost = tmp_path / "ghost"
        monkeypatch.setattr(
            basis_loader, "local_directory", lambda loc, client=None: ghost
        )
        with pytest.raises(FileNotFoundError, match="Basis snapshot folder not found"):
            basis_loader.load_basis(tmp_path)

    def test_reads_fresh_bundle(self, tmp_path):
        folder = tmp_path / "snap"
        folder.mkdir()
        df = pl.DataFrame({"product": ["gla"], "v": [1.5]})
        _write_parquet(folder, "mortality.parquet", df)
        _write_parquet(folder, "fc_rates.parquet", pl.DataFrame({"r": [2.0]}))
        sbu.rezip_snapshot(folder)
        bundle = basis_loader.load_basis(folder)  # use_bundle=True default
        assert set(bundle.tables) == {"mortality", "fc_rates"}
        assert bundle.tables["mortality"].to_dicts() == df.to_dicts()
        assert bundle.version == folder.name  # no manifest/build_log

    def test_threaded_read_and_meta(self, tmp_path):
        folder = tmp_path / "snap"
        folder.mkdir()
        _write_parquet(folder, "mortality.parquet")
        (folder / "manifest.json").write_text(
            json.dumps({"version": "v1", "built_at": "B", "source_sha256": "S"})
        )
        bundle = basis_loader.load_basis(folder, use_bundle=False, max_workers=2)
        assert bundle.version == "v1"
        assert bundle.built_at == "B"
        assert bundle.source_sha256 == "S"
        assert set(bundle.tables) == {"mortality"}

    @_needs_snapshots
    def test_loads_real_snapshot(self):
        bundle = basis_loader.load_basis(REAL_BASIS)
        manifest = json.loads((REAL_BASIS / "manifest.json").read_text())
        assert bundle.version == manifest.get("version", REAL_BASIS.name)
        assert bundle.built_at == manifest.get("built_at", "")
        assert "mortality" in bundle.tables
        assert "annuity_factors" in bundle.tables


class TestBasisForProduct:
    def _bundle(self) -> BasisBundle:
        return BasisBundle(
            version="v",
            built_at="",
            source_sha256="",
            tables={
                "mortality": pl.DataFrame({"product": ["gla"], "v": [1.0]}),
                # present but missing the "product" column -> filtered out
                "salary_band": pl.DataFrame({"not_product": [1]}),
                # present but no rows for the product alias -> filtered out
                "industry": pl.DataFrame({"product": ["other"], "v": [1.0]}),
                "ti_factors": pl.DataFrame({"t": [1]}),
                "fc_rates": pl.DataFrame({"f": [1]}),
                "education_guardian_cover": pl.DataFrame(
                    {"product": ["education_guardian"], "v": [1.0]}
                ),
                "wp_loading": pl.DataFrame({"w": [1]}),
            },
        )

    def test_alias_filtering(self):
        out = self._bundle().for_product("gla")
        assert set(out) == {"mortality"}

    def test_ti_factors_flag(self):
        out = self._bundle().for_product("terminal")
        assert "ti_factors" in out

    def test_flag_sheets(self):
        assert "fc_rates" in self._bundle().for_product("fc")
        assert "wp_loading" in self._bundle().for_product("gip")

    def test_education_guardian_tables(self):
        out = self._bundle().for_product("education_guardian")
        assert "education_guardian_cover" in out
        # salary_band sheet lacks a "product" column so it is skipped even
        # though the education_guardian spec declares a salary-band alias.
        assert "salary_band" not in out

    def test_unknown_product_raises(self):
        with pytest.raises(KeyError):
            self._bundle().for_product("not_a_product")

    @_needs_snapshots
    def test_for_product_on_real_snapshot(self):
        bundle = basis_loader.load_basis(REAL_BASIS)

        gla = bundle.for_product("gla")
        assert "mortality" in gla
        spec = registry.PRODUCT_REGISTRY["gla"]
        assert set(gla["mortality"]["product"].unique().to_list()) <= set(
            spec.basis_mortality
        )

        gip = bundle.for_product("gip")
        for sheet in (
            "wp_loading",
            "annuity_factors",
            "annuity_esc_loading",
            "annuity_nra_interpolation",
            "wp_annuity_mapping",
            "wp_base_inceptions",
            "backdating_loading",
            "rr_loading",
            "ip_loading",
            "db_loading",
            "annuity_salary_bands",
            "annuity_age_bands",
            "gip_hcc_basis",
        ):
            assert sheet in gip, sheet

        tip = bundle.for_product("tip")
        for sheet in (
            "tip_annuity_factors",
            "tip_payment_period_map",
            "tip_age_interp",
            "tip_esc_loading",
            "tip_wp_ann_map",
            "tip_db_loading",
            "wp_loading",
            "rr_loading",
            "wp_base_inceptions",
            "annuity_salary_bands",
            "ip_loading",
        ):
            assert sheet in tip, sheet

        fc = bundle.for_product("fc")
        for sheet in ("fc_rates", "fc_demographics", "fc_family_composition"):
            assert sheet in fc, sheet

        ext_fc = bundle.for_product("ext_fc")
        for sheet in (
            "ext_fc_base_rates",
            "ext_fc_salary_factors",
            "ext_fc_basis_params",
            "ext_fc_assumed_age_dist",
        ):
            assert sheet in ext_fc, sheet

        edu = bundle.for_product("education_guardian")
        assert "education_guardian_cover" in edu
        assert "education_guardian_salary_bands" in edu

        terminal = bundle.for_product("terminal")
        assert "ti_factors" in terminal


class TestGetBasis:
    def test_get_basis_caches_and_freezes_tables(self, tmp_path):
        folder = tmp_path / "snap"
        folder.mkdir()
        _write_parquet(folder, "mortality.parquet")
        first = basis_loader.get_basis(folder)
        second = basis_loader.get_basis(str(folder))  # str() conversion in get_basis
        assert first is second
        assert isinstance(first.tables, MappingProxyType)
        with pytest.raises(TypeError):
            first.tables["mortality"] = pl.DataFrame({"x": [1]})


# ---------------------------------------------------------------------------
# ctr_loader.assumption_loader
# ---------------------------------------------------------------------------


class TestCoerceGlobal:
    @pytest.mark.parametrize(
        ("value", "kind", "expected"),
        [
            (None, "float", None),
            ("1.5", "float", 1.5),
            ("bad", "float", None),
            (2.0, "float", 2.0),
            ("12", "int", 12),
            ("12.9", "int", 12),
            ("bad", "int", None),
            ("yes", "bool", True),
            ("TRUE", "bool", True),
            ("0", "bool", False),
            ("nope", "bool", False),
            ("text", "string", "text"),
            ("text", None, "text"),  # unknown/None type falls back to str
            (5, "other", "5"),
        ],
    )
    def test_coerce_global(self, value, kind, expected):
        assert assumption_loader._coerce_global(value, kind) == expected


class TestAssumptionMeta:
    def test_manifest(self, tmp_path):
        manifest = {"built_at": "T", "source_sha256": "S", "extra": "ignored"}
        (tmp_path / "manifest.json").write_text(json.dumps(manifest))
        assert assumption_loader._read_meta(tmp_path) == {
            "built_at": "T",
            "source_sha256": "S",
        }

    def test_build_log_last_row_used(self, tmp_path):
        (tmp_path / "build_log.csv").write_text(
            "built_at,source_sha256\nb1,h1\nb2,h2\n"
        )
        assert assumption_loader._read_meta(tmp_path) == {
            "built_at": "b2",
            "source_sha256": "h2",
        }

    def test_unreadable_build_log_falls_back_to_defaults(self, tmp_path):
        (tmp_path / "build_log.csv").mkdir()
        assert assumption_loader._read_meta(tmp_path) == {
            "built_at": "",
            "source_sha256": "",
        }


class TestLoadAssumptions:
    def test_missing_folder_raises(self, tmp_path, monkeypatch):
        ghost = tmp_path / "ghost"
        monkeypatch.setattr(
            assumption_loader, "local_directory", lambda loc, client=None: ghost
        )
        with pytest.raises(
            FileNotFoundError, match="Assumption snapshot folder not found"
        ):
            assumption_loader.load_assumptions(tmp_path)

    def test_reads_fresh_bundle(self, tmp_path):
        folder = tmp_path / "snap"
        folder.mkdir()
        df = pl.DataFrame({"product": ["gla"], "v": [1.0]})
        _write_parquet(folder, "product_limits.parquet", df)
        sbu.rezip_snapshot(folder)
        bundle = assumption_loader.load_assumptions(folder)
        assert set(bundle.tables) == {"product_limits"}
        assert bundle.tables["product_limits"].to_dicts() == df.to_dicts()
        assert bundle.globals == {}

    def test_globals_coerced_and_missing_columns_ignored(self, tmp_path):
        folder = tmp_path / "snap"
        folder.mkdir()
        _write_parquet(
            folder,
            "globals.parquet",
            pl.DataFrame(
                {
                    "param_name": ["n", "flag", "label"],
                    "param_value": ["7", "yes", "Life"],
                    "param_type": ["int", "bool", "str"],
                }
            ),
        )
        _write_parquet(folder, "other.parquet")
        bundle = assumption_loader.load_assumptions(folder, use_bundle=False)
        assert bundle.globals == {"n": 7, "flag": True, "label": "Life"}

        folder2 = tmp_path / "snap2"
        folder2.mkdir()
        # Missing param_type column -> globals extraction skipped entirely.
        _write_parquet(
            folder2,
            "globals.parquet",
            pl.DataFrame({"param_name": ["a"], "param_value": ["1"]}),
        )
        _write_parquet(folder2, "other.parquet")
        bundle2 = assumption_loader.load_assumptions(folder2, use_bundle=False)
        assert bundle2.globals == {}

    @_needs_snapshots
    def test_loads_real_snapshot(self):
        bundle = assumption_loader.load_assumptions(REAL_ASSUMP)
        manifest = json.loads((REAL_ASSUMP / "manifest.json").read_text())
        assert bundle.built_at == manifest.get("built_at", "")
        assert bundle.source_sha256 == manifest.get("source_sha256", "")
        assert "globals" in bundle.tables
        assert isinstance(bundle.globals, dict)
        for key in bundle.globals:
            assert isinstance(key, str)


class TestAssumptionsForProduct:
    def _bundle(self) -> AssumptionsBundle:
        return AssumptionsBundle(
            built_at="",
            source_sha256="",
            globals={},
            tables={
                "product_limits": pl.DataFrame({"product": ["gla"], "v": [1.0]}),
                # no "product" column -> skipped
                "profit_margins": pl.DataFrame({"not_product": [1]}),
                # alias present but no matching rows -> skipped
                "conversion_option": pl.DataFrame({"product": ["zzz"], "v": [1.0]}),
                "tax_tables": pl.DataFrame({"t": [1]}),
                "ctc_options": pl.DataFrame({"c": [1]}),
                "spouse_factors": pl.DataFrame({"s": [1]}),
                "lsdb_features": pl.DataFrame({"l": [1]}),
                "ci_table_mapping": pl.DataFrame({"m": [1]}),
                "ci_reinstatement": pl.DataFrame({"m": [1]}),
                "fc_db_loading": pl.DataFrame({"f": [1]}),
                "gip_fee_table": pl.DataFrame({"g": [1]}),
                "wp_categories": pl.DataFrame({"w": [1]}),
            },
        )

    def test_alias_and_flag_filtering(self):
        bundle = self._bundle()
        gla = bundle.for_product("gla")
        assert set(gla) == {"product_limits", "tax_tables"}

        for product, sheet in (
            ("lsdb", "lsdb_features"),
            ("spouse_gla", "spouse_factors"),
            ("spouse_lsdb", "spouse_factors"),
            ("spouse_lsdb", "ctc_options"),
            ("ci", "ci_table_mapping"),
            ("ci", "ci_reinstatement"),
            ("fc", "fc_db_loading"),
            ("gip", "gip_fee_table"),
            ("gip", "wp_categories"),
        ):
            assert sheet in bundle.for_product(product), (product, sheet)

    def test_unknown_product_raises(self):
        with pytest.raises(KeyError):
            self._bundle().for_product("not_a_product")

    @_needs_snapshots
    def test_for_product_on_real_snapshot(self):
        bundle = assumption_loader.load_assumptions(REAL_ASSUMP)

        # Note: the real snapshot ships no ctc_options.parquet, so spouse
        # products only surface the sheets that actually exist on disk.
        for product, expected in (
            ("gla", {"tax_tables"}),
            ("lsdb", {"lsdb_features"}),
            ("ci", {"ci_table_mapping", "ci_reinstatement"}),
            ("spouse_gla", {"spouse_factors"}),
            ("fc", {"fc_db_loading"}),
            ("gip", {"gip_fee_table", "wp_categories"}),
        ):
            out = bundle.for_product(product)
            assert expected <= set(out), product

        ci = bundle.for_product("ci")
        if "product_limits" in ci:
            allowed = set(registry.PRODUCT_REGISTRY["ci"].assumption_limits)
            assert set(ci["product_limits"]["product"].unique().to_list()) <= allowed


class TestGetAssumptions:
    def test_get_assumptions_caches_and_freezes(self, tmp_path):
        folder = tmp_path / "snap"
        folder.mkdir()
        _write_parquet(folder, "product_limits.parquet")
        first = assumption_loader.get_assumptions(folder)
        second = assumption_loader.get_assumptions(str(folder))
        assert first is second
        assert isinstance(first.tables, MappingProxyType)
        assert isinstance(first.globals, MappingProxyType)
        with pytest.raises(TypeError):
            first.globals["x"] = 1
