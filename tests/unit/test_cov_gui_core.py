"""Coverage tests for the non-widget gap_pricing_gui modules.

Covers: config, warm_state, file_access, engine_bundle, ensure_requirements,
quote_runner (pure paths plus one real CTR pricing run), and the Qt-free
helpers of launch_gap_gui. Widget-level coverage lives in
test_cov_gui_widgets.py.
"""
from __future__ import annotations

import json
import os
import time
import zipfile
from pathlib import Path
from types import SimpleNamespace

import polars as pl
import pytest
from gap_config import ConfigurationError, load_settings

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO_ROOT = Path(__file__).resolve().parents[2]
REAL_CTR = (REPO_ROOT / "BackendEngine" / "Test" / "Batch run"
            / "100% Run_90641_93944_93066_Sasol South Africa_202506.ctr")


def _make_settings(tmp_path: Path, environ: dict | None = None):
    """A fully filesystem-backed Settings rooted under tmp_path."""
    (tmp_path / "config.json").write_text(json.dumps({
        "paths": {
            "input": "input",
            "output": "output",
            "basis": "basis",
            "assumptions": "assumptions",
            "cache": "cache",
            "staging": "staging",
            "artifacts": "artifacts",
        },
    }), encoding="utf-8")
    for name in ("input", "basis", "assumptions"):
        (tmp_path / name).mkdir(exist_ok=True)
    return load_settings(tmp_path / "config.json", environ or {})


# ---------------------------------------------------------------------------
# file_access
# ---------------------------------------------------------------------------

@pytest.fixture
def fa(tmp_path, monkeypatch):
    from gap_pricing_gui import file_access
    settings = _make_settings(tmp_path)
    monkeypatch.setattr(file_access, "get_settings", lambda: settings)
    return file_access, settings, tmp_path


class TestFileAccess:
    def test_validate_gui_settings_happy_path(self, fa):
        file_access, settings, root = fa
        assert file_access.validate_gui_settings() is settings
        assert (root / "output").is_dir()
        assert settings.cache_dir.is_dir()
        assert settings.staging_dir.is_dir()

    def test_validate_gui_settings_with_db_enabled(self, tmp_path, monkeypatch):
        from gap_pricing_gui import file_access
        settings = _make_settings(tmp_path, {
            "GAP_DB_ENABLED": "true",
            "GAP_DB_CONNECTION_STRING": "DRIVER={x};SERVER=y",
        })
        monkeypatch.setattr(file_access, "get_settings", lambda: settings)
        assert settings.db_enabled
        file_access.validate_gui_settings()
        assert (tmp_path / "artifacts").is_dir()

    def test_input_directory_default_and_explicit(self, fa):
        file_access, _settings, root = fa
        expected = (root / "input").resolve()
        assert file_access.input_directory() == expected
        assert file_access.input_directory("input") == expected

    def test_input_directory_rejects_bad_locations(self, fa):
        file_access, _settings, root = fa
        for bad in ("", "   ", "s3://bucket/prefix", "https://x.invalid/y"):
            with pytest.raises(ConfigurationError):
                file_access.input_directory(bad)
        with pytest.raises(ConfigurationError):
            file_access.input_directory(root / "missing")

    def test_input_file(self, fa):
        file_access, _settings, root = fa
        f = root / "input" / "a.ctr"
        f.write_bytes(b"ctr")
        assert file_access.input_file(f) == f.resolve()
        with pytest.raises(ConfigurationError):
            file_access.input_file(root / "input" / "missing.ctr")
        with pytest.raises(ConfigurationError):
            file_access.input_file(root / "input")  # directory is not a file

    def test_batch_ctr_files_sorted_and_filtered(self, fa):
        file_access, _settings, root = fa
        folder = root / "input"
        (folder / "b.CTR").write_bytes(b"x")
        (folder / "a.ctr").write_bytes(b"x")
        (folder / "skip.txt").write_bytes(b"x")
        (folder / "subdir.ctr").mkdir()
        paths = file_access.batch_ctr_files(folder)
        assert [p.name for p in paths] == ["a.ctr", "b.CTR"]

    def test_batch_ctr_files_empty(self, fa):
        file_access, _settings, root = fa
        with pytest.raises(ConfigurationError, match="No .ctr files"):
            file_access.batch_ctr_files(root / "input")

    def test_output_directory_creates_and_validates(self, fa):
        file_access, _settings, root = fa
        nested = file_access.output_directory("deep/nested/out")
        assert nested == (root / "deep/nested/out").resolve()
        assert nested.is_dir()
        blocker = root / "a_file"
        blocker.write_bytes(b"x")
        with pytest.raises(ConfigurationError, match="writable directory"):
            file_access.output_directory(blocker)
        with pytest.raises(ConfigurationError):
            file_access.output_directory("s3://bucket/out")


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------

class TestConfig:
    def test_snapshot_folders_delegates_and_caches(self, monkeypatch):
        from gap_pricing_gui import config
        config.snapshot_folders.cache_clear()
        calls = []
        monkeypatch.setattr(
            config, "snapshot_dir",
            lambda kind: calls.append(kind) or f"/snap/{kind}")
        try:
            assert config.snapshot_folders() == ("/snap/basis", "/snap/assumption")
            assert calls == ["basis", "assumption"]
            # lru_cache hit: second call does not re-invoke snapshot_dir
            assert config.snapshot_folders() == ("/snap/basis", "/snap/assumption")
            assert calls == ["basis", "assumption"]
        finally:
            config.snapshot_folders.cache_clear()

    def test_snapshot_folders_real_config(self):
        from gap_pricing_gui import config
        config.snapshot_folders.cache_clear()
        try:
            basis, assump = config.snapshot_folders()
            assert Path(basis).is_dir()
            assert Path(assump).is_dir()
        finally:
            config.snapshot_folders.cache_clear()

    def test_module_getattr_defaults(self, monkeypatch):
        from gap_pricing_gui import config
        fake = SimpleNamespace(input_dir="IN_DIR", output_dir="OUT_DIR")
        monkeypatch.setattr(config, "get_settings", lambda: fake)
        assert config.DEFAULT_CTR_DIR == "IN_DIR"
        assert config.DEFAULT_BATCH_OUT == "OUT_DIR"
        with pytest.raises(AttributeError):
            config.TOTALLY_UNKNOWN_NAME  # noqa: B018


# ---------------------------------------------------------------------------
# warm_state
# ---------------------------------------------------------------------------

class TestWarmState:
    def test_events(self):
        from gap_pricing_gui import warm_state
        warm_state.UI_WARM_DONE.clear()
        warm_state.ENGINE_WARM_DONE.clear()
        assert not warm_state.UI_WARM_DONE.is_set()
        assert not warm_state.ENGINE_WARM_DONE.is_set()
        warm_state.UI_WARM_DONE.set()
        warm_state.ENGINE_WARM_DONE.set()
        assert warm_state.UI_WARM_DONE.is_set()
        assert warm_state.ENGINE_WARM_DONE.is_set()

    def test_write_counters(self):
        from gap_pricing_gui import warm_state
        for started, finished, pending in (
            (warm_state.db_write_started, warm_state.db_write_finished,
             warm_state.db_writes_pending),
            (warm_state.file_sync_started, warm_state.file_sync_finished,
             warm_state.file_syncs_pending),
            (warm_state.report_write_started, warm_state.report_write_finished,
             warm_state.report_writes_pending),
        ):
            before = pending()
            started()
            assert pending() == before + 1
            finished()
            assert pending() == before
            # never goes negative
            finished()
            assert pending() == before

    def test_register_and_release_local_dir(self, tmp_path):
        from gap_pricing_gui import warm_state
        victim = tmp_path / "staged"
        victim.mkdir()
        warm_state.register_local_dir(str(victim), 0)
        assert not victim.exists()

        keep = tmp_path / "staged2"
        keep.mkdir()
        warm_state.register_local_dir(str(keep), 1)
        assert keep.exists()
        warm_state.release_local_dir(str(keep))
        assert not keep.exists()

    def test_sweep_stale_staging(self, tmp_path):
        from gap_pricing_gui import warm_state
        staging = tmp_path / "staging"
        old = staging / "old"
        fresh = staging / "fresh"
        old.mkdir(parents=True)
        fresh.mkdir(parents=True)
        past = time.time() - 48 * 3600
        os.utime(old, (past, past))
        removed = warm_state.sweep_stale_staging(str(staging), max_age_hours=24.0)
        assert removed == 1
        assert not old.exists() and fresh.exists()
        assert warm_state.sweep_stale_staging(str(tmp_path / "nope")) == 0


# ---------------------------------------------------------------------------
# ensure_requirements
# ---------------------------------------------------------------------------

class TestEnsureRequirements:
    def test_parse_requirement_names(self, tmp_path):
        from gap_pricing_gui import ensure_requirements as er
        req = tmp_path / "req.txt"
        req.write_text(
            "# full-line comment\n"
            "\n"
            "pandas>=1.5  # inline comment\n"
            "openpyxl; python_version>'3.8'\n"
            "  xlrd==2.0\n"
            "-e .\n",  # not a package name pattern? '-e' matches NAME_RE ('-e')
            encoding="utf-8",
        )
        names = er.parse_requirement_names(req)
        assert "pandas" in names
        assert "openpyxl" in names
        assert "xlrd" in names
        assert names[0] == "pandas"

    def test_find_missing(self):
        from gap_pricing_gui import ensure_requirements as er
        assert er.find_missing(["pytest"]) == []
        missing = er.find_missing(["definitely-not-a-real-pkg-zz123"])
        assert missing == ["definitely-not-a-real-pkg-zz123"]

    def test_pip_install_invokes_pip(self, monkeypatch):
        from gap_pricing_gui import ensure_requirements as er
        calls = []
        monkeypatch.setattr(er.subprocess, "call",
                            lambda cmd: calls.append(cmd) or 7)
        assert er.pip_install(["-r", "req.txt"]) == 7
        assert "-m" in calls[0] and "pip" in calls[0] and "install" in calls[0]
        assert calls[0][-2:] == ["-r", "req.txt"]

    def test_main_all_installed(self, tmp_path, monkeypatch):
        from gap_pricing_gui import ensure_requirements as er
        req = tmp_path / "requirements.txt"
        req.write_text("pytest\n", encoding="utf-8")
        monkeypatch.setattr(er, "REQ_FILE", req)
        assert er.main() == 0

    def test_main_missing_then_install(self, tmp_path, monkeypatch):
        from gap_pricing_gui import ensure_requirements as er
        req = tmp_path / "requirements.txt"
        req.write_text("somepkg\n", encoding="utf-8")
        monkeypatch.setattr(er, "REQ_FILE", req)
        monkeypatch.setattr(er, "find_missing", lambda names: ["somepkg"])
        monkeypatch.setattr(er, "pip_install", lambda args: 0)
        assert er.main() == 0
        monkeypatch.setattr(er, "pip_install", lambda args: 5)
        assert er.main() == 5

    def test_main_fallback_list(self, tmp_path, monkeypatch):
        from gap_pricing_gui import ensure_requirements as er
        monkeypatch.setattr(er, "REQ_FILE", tmp_path / "absent.txt")
        monkeypatch.setattr(er, "find_missing", lambda names: [])
        assert er.main() == 0
        monkeypatch.setattr(er, "find_missing", lambda names: ["PySide6"])
        monkeypatch.setattr(er, "pip_install", lambda args: 0)
        assert er.main() == 0


# ---------------------------------------------------------------------------
# engine_bundle
# ---------------------------------------------------------------------------

class TestEngineBundle:
    def test_in_search_dirs(self):
        from gap_pricing_gui import engine_bundle as eb
        assert eb._in_search_dirs(eb.HERE / "models.py")
        assert not eb._in_search_dirs(Path("Z:/totally/elsewhere/x.py"))

    def test_discover_sources(self, tmp_path, monkeypatch):
        from gap_pricing_gui import engine_bundle as eb
        cfg = tmp_path / "config.json"
        cfg.write_text("{}", encoding="utf-8")
        monkeypatch.setattr(eb, "get_settings",
                            lambda: SimpleNamespace(config_file=cfg))
        srcs = eb.discover_sources()
        assert "gap_pricing_gui/models.py" in srcs
        assert "gap_pricing_gui/main_window.py" in srcs
        assert srcs["config.json"] == cfg

    def test_rezip_is_fresh_and_embedded_meta(self, tmp_path, monkeypatch):
        from gap_pricing_gui import engine_bundle as eb
        monkeypatch.setattr(eb, "BUNDLE_PATH", tmp_path / "bundle.zip")
        src = tmp_path / "mod.py"
        src.write_text("x = 1\n", encoding="utf-8")
        z = eb.rezip({"mod.py": src})
        assert z.is_file()
        emb = eb._embedded_meta(z)
        assert emb is not None and "mod.py" in emb["files"]
        assert eb.is_fresh(z)
        # stale after source changes
        src.write_text("x = 12345\n", encoding="utf-8")
        assert not eb.is_fresh(z)

    def test_rezip_empty_raises(self, monkeypatch, tmp_path):
        from gap_pricing_gui import engine_bundle as eb
        monkeypatch.setattr(eb, "BUNDLE_PATH", tmp_path / "b.zip")
        with pytest.raises(RuntimeError, match="no source modules"):
            eb.rezip({})

    def test_is_fresh_edge_cases(self, tmp_path):
        from gap_pricing_gui import engine_bundle as eb
        assert not eb.is_fresh(tmp_path / "missing.zip")
        bad = tmp_path / "notazip.zip"
        bad.write_bytes(b"garbage")
        assert eb._embedded_meta(bad) is None
        assert not eb.is_fresh(bad)
        # valid zip but empty meta
        empty_meta = tmp_path / "empty_meta.zip"
        with zipfile.ZipFile(empty_meta, "w") as zf:
            zf.writestr(eb.META_NAME, "{}")
        assert eb._embedded_meta(empty_meta) == {}
        assert not eb.is_fresh(empty_meta)
        # meta references a missing file
        missing_src = tmp_path / "missing_src.zip"
        with zipfile.ZipFile(missing_src, "w") as zf:
            zf.writestr(eb.META_NAME,
                        json.dumps({"files": {"x.py": ["no/such.py", 0, 0]}}))
        assert not eb.is_fresh(missing_src)

    def test_activate(self):
        from gap_pricing_gui import engine_bundle as eb
        status = eb.activate(verbose=False)
        assert status["used_zip"] is False
        assert status["rebuilt"] is False
        assert "reason" in status and "seconds" in status

    def test_warm_async(self):
        from gap_pricing_gui import engine_bundle as eb
        t1 = eb.warm_async(modules=("no_such_module_zzz",),
                           verbose=False, delay_s=0.3)
        # second call while the first is still alive returns the same thread
        t2 = eb.warm_async(modules=("json",), verbose=False)
        assert t2 is t1
        t1.join(15)
        assert not t1.is_alive()

    def test_selftest(self, tmp_path, monkeypatch):
        from gap_pricing_gui import engine_bundle as eb
        monkeypatch.setattr(eb, "BUNDLE_PATH", tmp_path / "selftest.zip")
        cfg = tmp_path / "config.json"
        cfg.write_text("{}", encoding="utf-8")
        monkeypatch.setattr(eb, "get_settings",
                            lambda: SimpleNamespace(config_file=cfg))
        assert eb._selftest(keep=False) == 0
        assert not (tmp_path / "selftest.zip").exists()


# ---------------------------------------------------------------------------
# quote_runner
# ---------------------------------------------------------------------------

class TestQuoteRunnerPure:
    def test_slug(self):
        from gap_pricing_gui import quote_runner as qr
        assert qr._slug("A B/C\\D_E-F") == "A_B_C_D_E-F"
        assert qr._slug("") == "unknown"
        assert qr._slug("///") == "unknown"
        assert qr._slug("  spaced  ") == "spaced"

    def _quote_result(self):
        from gap_pricing_gui import quote_runner as qr
        df = pl.DataFrame({
            "product_active": [True, False, True],
            "total_annual_premium": [100.0, 50.0, 25.0],
            "total_cover": [1000.0, 500.0, 200.0],
        })
        return qr.QuoteResult(
            ctr_path="x.ctr",
            scheme_name="Scheme A",
            quote_number="Q1",
            calculation_date="2025-01-01",
            active_products=("gla", "fc"),
            per_product_results={"gla": df, "fc": df},
            aggregates={"total_annual_premium": 250.0},
            timings={"gla": 0.1, "fc": 0.2},
            warnings=["watch out"],
        )

    def test_summary_text(self):
        res = self._quote_result()
        text = res.summary_text()
        assert "Scheme A" in text
        assert "GLA" in text and "FC" in text
        assert "TOTAL" in text
        assert "250.00" in text
        assert "watch out" in text

    def test_log_compute_increments(self):
        from gap_pricing_gui import quote_runner as qr
        before = qr._run_count["n"]
        qr._log_compute("scheme", 0.01)
        assert qr._run_count["n"] == before + 1

    def test_db_disabled_raises(self, monkeypatch):
        from gap_pricing_gui import quote_runner as qr
        monkeypatch.setattr(qr, "get_settings",
                            lambda: SimpleNamespace(db_enabled=False))
        with pytest.raises(ValueError, match="Database access is disabled"):
            qr.list_db_schemes()
        with pytest.raises(ValueError, match="Database access is disabled"):
            qr.price_quote_from_db("run-1")

    def test_price_quote_missing_file(self):
        from gap_pricing_gui import quote_runner as qr
        with pytest.raises(ConfigurationError):
            qr.price_quote("C:/nonexistent/nothing.ctr")


@pytest.fixture(scope="module")
def priced_result():
    if not REAL_CTR.is_file():
        pytest.skip(f"real CTR fixture not found: {REAL_CTR}")
    from gap_pricing_gui import quote_runner as qr
    progress: list[str] = []
    res = qr.price_quote(str(REAL_CTR), progress_cb=progress.append)
    res._test_progress = progress
    return res


class TestQuoteRunnerReal:
    """Real engine run against the smallest repo CTR fixture."""

    def test_price_quote_real(self, priced_result):
        res = priced_result
        assert res.scheme_name
        assert len(res.active_products) >= 1
        assert res.aggregates["total_annual_premium"] > 0
        assert {"basis", "assump", "ctr"} <= set(res.timings)
        assert any(res.per_product_results[p].height > 0
                   for p in res.active_products)
        assert res._test_progress
        assert any("Pricing" in m or "Loading" in m
                   for m in res._test_progress)
        assert res.total_runtime_s and res.total_runtime_s > 0
        assert "TOTAL" in res.summary_text()

    def test_save_report_xlsx_and_parquet(self, priced_result, tmp_path):
        from gap_pricing_gui import quote_runner as qr
        out = tmp_path / "reports"
        returned = qr.save_report(priced_result, out_dir=str(out),
                                  write_xlsx=True, write_parquet=True)
        returned = Path(returned)
        assert returned.suffix == ".xlsx" and returned.is_file()
        parquets = list(out.glob("*_calc.parquet"))
        assert len(parquets) == 1
        assert pl.read_parquet(parquets[0]).height > 0
        assert priced_result.xlsx_path == returned
        assert priced_result.parquet_path == parquets[0]

    def test_save_report_html_only(self, priced_result, tmp_path):
        from gap_pricing_gui import quote_runner as qr
        out = tmp_path / "reports"
        returned = Path(qr.save_report(priced_result, out_dir=str(out),
                                       write_xlsx=False, write_parquet=False,
                                       write_html=True))
        assert returned.suffix == ".html" and returned.is_file()
        assert "<html" in returned.read_text(encoding="utf-8").lower()
        assert priced_result.html_path == returned

    def test_save_report_missing_output_raises(self, priced_result, tmp_path,
                                               monkeypatch):
        from gap_pricing_gui import quote_runner as qr
        settings = _make_settings(tmp_path)
        monkeypatch.setattr(qr, "get_settings", lambda: settings)
        monkeypatch.setattr("reporting.write_pricing_report", lambda **kw: None)
        with pytest.raises(RuntimeError, match="did not produce"):
            qr.save_report(priced_result, out_dir=str(tmp_path / "o"),
                           write_xlsx=True)
        # failed save must not leak the staging folder
        assert not any(settings.staging_dir.iterdir())

    def test_save_report_writer_failure_cleans_staging(self, priced_result,
                                                       tmp_path, monkeypatch):
        from gap_pricing_gui import quote_runner as qr
        settings = _make_settings(tmp_path)
        monkeypatch.setattr(qr, "get_settings", lambda: settings)

        def boom(**kw):
            raise RuntimeError("writer exploded")

        monkeypatch.setattr("reporting.write_pricing_report", boom)
        with pytest.raises(RuntimeError, match="writer exploded"):
            qr.save_report(priced_result, out_dir=str(tmp_path / "o"))
        assert not any(settings.staging_dir.iterdir())

    def test_save_report_publication_failure(self, priced_result, tmp_path,
                                             monkeypatch):
        from gap_pricing_gui import quote_runner as qr
        settings = _make_settings(tmp_path)
        monkeypatch.setattr(qr, "get_settings", lambda: settings)
        # the real scheme slug would push the staged filename past Windows
        # MAX_PATH from this deep pytest tmp dir; keep paths short
        monkeypatch.setattr(qr, "_slug", lambda s: "s")

        def fake_writer(**kw):
            wb = Path(kw["workbook_path"])
            if kw["write_xlsx"]:
                wb.write_bytes(b"xlsx")
            if kw["write_parquet"]:
                wb.with_name(wb.stem + "_calc.parquet").write_bytes(b"pq")

        def pub_fail(self, key, local_path):
            raise PermissionError("publication failed")

        monkeypatch.setattr("reporting.write_pricing_report", fake_writer)
        monkeypatch.setattr(qr.Storage, "put_file", pub_fail)
        with pytest.raises(PermissionError, match="publication failed"):
            qr.save_report(priced_result, out_dir=str(tmp_path / "o"),
                           write_xlsx=True)
        assert not any(settings.staging_dir.iterdir())


# ---------------------------------------------------------------------------
# launch_gap_gui helpers (Qt-free parts; main() is covered in widget tests)
# ---------------------------------------------------------------------------

class TestLaunchHelpers:
    def test_log(self):
        from gap_pricing_gui.launch_gap_gui import _log
        _log("coverage test message")  # must not raise

    def test_install_sigterm_handler(self):
        import signal as signal_mod

        from gap_pricing_gui.launch_gap_gui import _install_sigterm_handler

        class FakeSignals:
            # NB: can't be named `signal` — a class-local method of the same
            # name below would shadow the enclosing import for the whole body.
            SIGTERM = signal_mod.SIGTERM

            def __init__(self):
                self.handler = object()

            def getsignal(self, signum):
                return self.handler

            def signal(self, signum, handler):
                previous = self.handler
                self.handler = handler
                return previous

        queued = []

        class FakeQtCore:
            class QTimer:
                @staticmethod
                def singleShot(delay, callback):
                    queued.append((delay, callback))

        class FakeWindow:
            def __init__(self):
                self.closed = False

            def close(self):
                self.closed = True

        signals = FakeSignals()
        original = signals.handler
        window = FakeWindow()
        restore = _install_sigterm_handler(window, FakeQtCore, signals)
        installed = signals.handler
        installed(signal_mod.SIGTERM, None)
        assert len(queued) == 1 and not window.closed
        queued[0][1]()
        assert window.closed
        restore()
        assert signals.handler is original
