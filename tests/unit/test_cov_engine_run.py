"""Coverage tests for the pricing_engine orchestration layer.

Covers: engine.py (pricing entry point), cli.py / __main__.py,
concurrent_runner.py, aggregation.py and async_state.py.

Real inputs are used where cheap: one CTR workbook is loaded once per
session and the real basis/assumption snapshots are used throughout.
The report writer is stubbed so no xlsx/html generation cost is paid.
"""
from __future__ import annotations

import os
import runpy
import sys
import threading
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import polars as pl
import pricing_engine.concurrent_runner as cr
import pytest
from pricing_engine import aggregation, async_state, cli
from pricing_engine.engine import (
    PricingContext,
    _apply_addons,
    _coerce_limit,
    _load_and_index_members,
    _require_columns,
    _resolve_member_role,
    _resolve_rate_factors,
    apply_cease_override,
    build_context,
    price,
    price_all_products,
)

ENGINE_ROOT = Path(__file__).resolve().parents[2] / "BackendEngine"
BASIS_DIR = ENGINE_ROOT / "basis_snapshots" / "basis_v2026_08"
ASSUMP_DIR = ENGINE_ROOT / "assumption_snapshots" / "assumption_A2026_08"
CTR_FILE = (
    ENGINE_ROOT / "Test" / "Batch run"
    / "100% Run_90641_93944_93066_Sasol South Africa_202506.ctr"
)


# ---------------------------------------------------------------------------
# Session fixtures: real snapshots + one real CTR bundle
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def basis_assump():
    if not BASIS_DIR.exists() or not ASSUMP_DIR.exists():
        pytest.skip("snapshot folders missing")
    return cr.load_shared_snapshots(BASIS_DIR, ASSUMP_DIR)


@pytest.fixture(scope="session")
def ctr_bundle():
    if not CTR_FILE.exists():
        pytest.skip(f"test CTR missing: {CTR_FILE}")
    from ctr_loader.ctr_loader_v2 import load_ctr
    return load_ctr(str(CTR_FILE))


@pytest.fixture(scope="session")
def priced(ctr_bundle, basis_assump):
    """price_all_products on the real CTR, computed once."""
    basis, assump = basis_assump
    ctx = build_context(ctr_bundle, assump)
    calls = []
    results = price_all_products(
        ctr_bundle, basis, assump, ctx,
        on_product_priced=lambda pk, df, t: calls.append((pk, t)),
    )
    return results, ctx, calls


@pytest.fixture
def patched_load_ctr(monkeypatch, ctr_bundle):
    """Make run_single_quote reuse the session CTR bundle."""
    import ctr_loader.ctr_loader_v2 as cl
    monkeypatch.setattr(cl, "load_ctr", lambda path: ctr_bundle)
    return ctr_bundle


def _make_report_stub(calls, write_parquet=True, write_xlsx=True):
    def _stub(workbook_path, all_results, ctr, assump=None, **kw):
        calls.append(dict(kw, workbook_path=str(workbook_path)))
        wp = Path(workbook_path)
        wp.parent.mkdir(parents=True, exist_ok=True)
        if write_xlsx and kw.get("write_xlsx", True):
            wp.write_bytes(b"fake-xlsx")
        if write_parquet:
            pl.DataFrame({
                "product": ["gla"], "member_no": [1],
                "total_annual_premium": [1.0],
            }).write_parquet(wp.with_name(wp.stem + "_calc.parquet"))
        if kw.get("write_html", False):
            wp.with_suffix(".html").write_text("<html></html>", encoding="utf-8")
        return wp
    return _stub


@pytest.fixture
def stub_report_writer(monkeypatch):
    import reporting
    calls = []
    monkeypatch.setattr(
        reporting, "write_pricing_report", _make_report_stub(calls)
    )
    return calls


# ===========================================================================
# engine.py -- pure/unit-level pieces
# ===========================================================================

class TestCoerceLimit:
    @pytest.mark.parametrize("val,kind,expected", [
        (None, "int", None),
        ("3.7", "int", 3),
        ("2.5", "float", 2.5),
        ("yes", "bool", True),
        ("0", "bool", False),
        ("anything", "bool", False),  # only true/1/yes count as True
        (5, "str", "5"),
        (7, None, "7"),
    ])
    def test_coerce(self, val, kind, expected):
        assert _coerce_limit(val, kind) == expected


class TestBuildContext:
    def _ctr(self, n_members):
        scheme = SimpleNamespace(
            commencement_date=date(2025, 1, 1),
            secondary="Mining", primary="Metal", region="GP",
        )
        return SimpleNamespace(
            scheme=scheme,
            members={"gla_sheet": pl.DataFrame({"x": list(range(n_members))})},
        )

    def test_small_scheme_default_threshold(self):
        ctr = self._ctr(3)
        assump = SimpleNamespace(globals={}, tables={})
        ctx = build_context(ctr, assump)
        assert ctx.member_count == 3
        assert ctx.scheme_size == "Small"
        assert ctx.calc_date == date(2025, 1, 1)
        assert ctx.primary_industry == "Metal"
        assert ctx.secondary_industry == "Mining"
        assert ctx.region_selected == "GP"

    def test_large_scheme_threshold(self):
        ctr = self._ctr(5)
        assump = SimpleNamespace(
            globals={"scheme_size_large_threshold": 5}, tables={})
        ctx = build_context(ctr, assump)
        assert ctx.scheme_size == "Large"

    def test_product_limits_coerced_into_assump_limits(self):
        ctr = self._ctr(1)
        assump = SimpleNamespace(
            globals={},
            tables={"product_limits": pl.DataFrame({
                "product": ["gla", "gla", "ci", "ci"],
                "param_name": ["max_cover", "flag", "max_co_age", "note"],
                "param_value": ["1000.0", "true", "65", "abc"],
                "param_type": ["float", "bool", "int", "str"],
            })},
        )
        ctx = build_context(ctr, assump)
        assert ctx.limits_for("gla") == {"max_cover": 1000.0, "flag": True}
        assert ctx.limits_for("ci") == {"max_co_age": 65, "note": "abc"}
        assert ctx.limits_for("unknown") == {}


class TestPricingContext:
    def test_limits_for_defaults(self):
        ctx = PricingContext(
            scheme=None, globals={}, calc_date=date(2025, 1, 1),
            member_count=0, scheme_size="Small",
        )
        assert ctx.limits_for("gla") == {}
        ctx.assump_limits["gla"] = {"x": 1}
        assert ctx.limits_for("gla") == {"x": 1}


class TestApplyCeaseOverride:
    def _ctx(self, gla_df, eog=None):
        return PricingContext(
            scheme=SimpleNamespace(express_over_gla=eog or {}),
            globals={}, calc_date=date(2025, 1, 1),
            member_count=0, scheme_size="Small",
            prior_results={"gla": gla_df} if gla_df is not None else {},
        )

    def _df(self):
        return pl.DataFrame({
            "member_no": [1, 2],
            "ceasing_age": [60.0, 60.0],
        })

    def test_no_gla_prior_returns_df_unchanged(self):
        df = self._df()
        out = apply_cease_override(df, "spouse_gla", self._ctx(None))
        assert out.equals(df)

    def test_unrelated_product_returns_unchanged(self):
        gla = pl.DataFrame({"member_no": [1, 2], "ceasing_age": [65.0, 65.0]})
        df = self._df()
        out = apply_cease_override(df, "lsdb", self._ctx(gla, {"lsdb": False}))
        assert out["ceasing_age"].to_list() == [60.0, 60.0]

    def test_gla_itself_returns_unchanged(self):
        gla = pl.DataFrame({"member_no": [1, 2], "ceasing_age": [65.0, 65.0]})
        df = self._df()
        out = apply_cease_override(df, "gla", self._ctx(gla))
        assert out["ceasing_age"].to_list() == [60.0, 60.0]

    def test_express_over_gla_product_uses_gla_ceasing_age(self):
        gla = pl.DataFrame({"member_no": [1, 2], "ceasing_age": [65.0, 65.0]})
        df = self._df()
        out = apply_cease_override(df, "lsdb", self._ctx(gla, {"lsdb": True}))
        assert out["ceasing_age"].to_list() == [65.0, 65.0]

    def test_spouse_gla_uses_gla_ceasing_age(self):
        gla = pl.DataFrame({"member_no": [1, 2], "ceasing_age": [65.0, 70.0]})
        out = apply_cease_override(self._df(), "spouse_gla", self._ctx(gla))
        assert out["ceasing_age"].to_list() == [65.0, 70.0]

    def test_spouse_lsdb_uses_gla_nra_final(self):
        gla = pl.DataFrame({"member_no": [1, 2], "nra_final": [63.0, 63.0]})
        out = apply_cease_override(self._df(), "spouse_lsdb", self._ctx(gla))
        assert out["ceasing_age"].to_list() == [63.0, 63.0]


class TestResolveMemberRole:
    @pytest.mark.parametrize("pkey,use_sp,expected", [
        ("gla", False, "gla_sheet"),
        ("lsdb", True, "gla_sheet"),
        ("spouse_gla", True, "sp_sheet"),
        ("spouse_gla", False, "gla_sheet"),
        ("spouse_lsdb", True, "sp_sheet"),
        ("fc", False, "fc_sheet"),
        ("gip", False, "gip_sheet"),
        ("tip", False, "tip_sheet"),
        ("ci", False, "gla_sheet"),
    ])
    def test_roles(self, pkey, use_sp, expected):
        scheme = SimpleNamespace(spouse_use_sp_sheet=use_sp)
        assert _resolve_member_role(pkey, None, scheme) == expected


class TestResolveRateFactors:
    def test_explicit_rate_factors_win(self):
        recipe = {"rate_factors": ["A"], "rate_factors_sp_source": ["B"],
                  "rate_factors_gla_source": ["C"]}
        scheme = SimpleNamespace(spouse_use_sp_sheet=True)
        assert _resolve_rate_factors(recipe, scheme) == ["A"]

    def test_spouse_sheet_source(self):
        recipe = {"rate_factors_sp_source": ["B"],
                  "rate_factors_gla_source": ["C"]}
        scheme = SimpleNamespace(spouse_use_sp_sheet=True)
        assert _resolve_rate_factors(recipe, scheme) == ["B"]

    def test_gla_source_default(self):
        recipe = {"rate_factors_sp_source": ["B"],
                  "rate_factors_gla_source": ["C"]}
        scheme = SimpleNamespace(spouse_use_sp_sheet=False)
        assert _resolve_rate_factors(recipe, scheme) == ["C"]


class TestRequireColumns:
    def test_missing_raises_keyerror(self):
        df = pl.DataFrame({"a": [1]})
        with pytest.raises(KeyError) as exc:
            _require_columns(df, ["a", "b", "c"], where="unit")
        assert "b" in str(exc.value) and "c" in str(exc.value)

    def test_all_present_passes(self):
        df = pl.DataFrame({"a": [1], "b": [2]})
        _require_columns(df, ["a", "b"], where="unit")


class TestLoadAndIndexMembers:
    def _ctx(self):
        return SimpleNamespace(
            scheme=SimpleNamespace(spouse_use_sp_sheet=False))

    def test_missing_sheet_key(self):
        ctr = SimpleNamespace(members={})
        with pytest.raises(RuntimeError, match="gla_sheet"):
            _load_and_index_members("gla", ctr, None, self._ctx())

    def test_empty_sheet(self):
        ctr = SimpleNamespace(
            members={"gla_sheet": pl.DataFrame({"a": []})})
        with pytest.raises(RuntimeError, match="empty"):
            _load_and_index_members("gla", ctr, None, self._ctx())

    def test_no_columns(self):
        ctr = SimpleNamespace(
            members={"gla_sheet": pl.DataFrame()})
        with pytest.raises(RuntimeError):
            _load_and_index_members("gla", ctr, None, self._ctx())

    def test_indexes_members_one_based(self):
        ctr = SimpleNamespace(
            members={"gla_sheet": pl.DataFrame({"x": [10, 20, 30]})})
        df = _load_and_index_members("gla", ctr, None, self._ctx())
        assert df["member_no"].to_list() == [1, 2, 3]
        assert df["x"].to_list() == [10, 20, 30]


class TestApplyAddons:
    def test_unknown_addon_zeroed(self):
        df = pl.DataFrame({"x": [1, 2]})
        recipe = {"add_ons": ["mystery_premium"], "ineligible_status": ()}
        out = _apply_addons(df, recipe, None, {}, ctx=None)
        assert out["mystery_premium"].to_list() == [0.0, 0.0]

    def test_no_addons_unchanged(self):
        df = pl.DataFrame({"x": [1]})
        out = _apply_addons(df, {"add_ons": []}, None, {}, ctx=None)
        assert out.columns == ["x"]


# ===========================================================================
# engine.py -- real end-to-end pricing
# ===========================================================================

class TestRealPricing:
    def test_price_all_products_structure(self, priced, ctr_bundle):
        results, ctx, calls = priced
        assert set(results.keys()) == set(ctr_bundle.active_products)
        assert len(results) >= 1
        # on_product_priced fired once per product
        assert sorted(pk for pk, _ in calls) == sorted(results.keys())
        for pk, df in results.items():
            assert isinstance(df, pl.DataFrame)
            assert df.height > 0
            if "member_no" in df.columns and pk != "ext_fc":
                assert df["member_no"].is_sorted()
            if "total_annual_premium" in df.columns:
                assert df["total_annual_premium"].is_not_null().all()
            if "product_active" in df.columns:
                active = df.filter(pl.col("product_active"))
                if "total_annual_premium" in df.columns:
                    assert float(
                        active["total_annual_premium"].sum() or 0.0) >= 0.0
                if "total_cover" in df.columns:
                    assert float(
                        active["total_cover"].sum() or 0.0) >= 0.0

    def test_gla_output_contract(self, priced):
        results, ctx, calls = priced
        if "gla" not in results:
            pytest.skip("CTR has no gla product")
        df = results["gla"]
        for col in ("member_no", "product_active", "total_cover",
                    "basic_technical_premium", "total_annual_premium",
                    "qx", "profit_margin_amount"):
            assert col in df.columns, f"missing {col}"
        active = df.filter(pl.col("product_active"))
        assert active.height > 0
        assert bool(
            (active["total_annual_premium"].fill_null(0.0) >= 0).all())

    def test_ctx_prior_results_populated(self, priced, ctr_bundle):
        results, ctx, calls = priced
        for pk in results:
            if pk != "ext_fc":
                assert pk in ctx.prior_results

    def test_price_unknown_product_raises(self, ctr_bundle, basis_assump):
        basis, assump = basis_assump
        ctx = build_context(ctr_bundle, assump)
        with pytest.raises(KeyError):
            price("not_a_product", ctr_bundle, basis, assump, ctx)

    def test_price_single_product_direct(self, ctr_bundle, basis_assump):
        basis, assump = basis_assump
        candidates = [p for p in ctr_bundle.active_products if p != "ext_fc"]
        if not candidates:
            pytest.skip("CTR has only ext_fc")
        # price the first product -- same one the batch engine prices first
        pkey = candidates[0]
        ctx = build_context(ctr_bundle, assump)
        df = price(pkey, ctr_bundle, basis, assump, ctx)
        assert isinstance(df, pl.DataFrame)
        assert df.height > 0


# ===========================================================================
# aggregation.py
# ===========================================================================

def _member_df(**overrides):
    base = {
        "member_no": [1, 2],
        "member_name": ["Alice", "Bob"],
        "gender": ["F", "M"],
        "age": [30.0, 40.0],
        "category_number": [1, 2],
        "annual_salary": [100_000.0, 200_000.0],
        "product_active": [True, True],
        "total_cover": [1000.0, 2000.0],
        "basic_technical_premium": [10.0, 20.0],
        "total_annual_premium": [11.0, 22.0],
        "profit_margin_amount": [1.0, 2.0],
    }
    base.update(overrides)
    return pl.DataFrame(base)


class TestSchemeSummary:
    def test_sums_and_grand_total(self):
        res = {"gla": _member_df(), "unknown_prod": _member_df(
            product_active=[True, False],
            total_cover=[5.0, 999.0],
            basic_technical_premium=[1.0, 9.0],
            total_annual_premium=[2.0, 9.0],
            profit_margin_amount=[0.5, 9.0],
        )}
        df = aggregation.scheme_summary(res)
        gla_row = df.filter(pl.col("product_key") == "gla").row(0, named=True)
        assert gla_row["product_name"] == "Group Life Assurance"
        assert gla_row["n_members"] == 2
        assert gla_row["total_cover"] == 3000.0
        assert gla_row["total_annual_premium"] == 33.0
        assert gla_row["total_risk_premium"] == pytest.approx(36.0)

        other = df.filter(pl.col("product_key") == "unknown_prod").row(
            0, named=True)
        assert other["product_name"] == "unknown_prod"
        assert other["n_members"] == 1  # only active member counted
        assert other["total_cover"] == 5.0

        grand = df.filter(pl.col("product_key") == "GRAND_TOTAL").row(
            0, named=True)
        assert grand["product_name"] == "GRAND TOTAL"
        assert grand["n_members"] is None
        assert grand["total_cover"] == 3005.0
        assert grand["total_annual_premium"] == 35.0

    def test_missing_metric_cols_filled_with_zero(self):
        df = aggregation.scheme_summary({
            "gla": pl.DataFrame({
                "product_active": [True], "total_cover": [7.0]}),
        })
        gla_row = df.filter(pl.col("product_key") == "gla").row(0, named=True)
        assert gla_row["basic_technical_premium"] == 0.0
        assert gla_row["total_cover"] == 7.0


class TestCategorySummary:
    def test_groups_by_category(self):
        res = {"gla": _member_df()}
        df = aggregation.category_summary(res)
        assert df.height == 2
        row1 = df.filter(pl.col("category_number") == 1).row(0, named=True)
        assert row1["product_key"] == "gla"
        assert row1["product_name"] == "Group Life Assurance"
        assert row1["n_members"] == 1
        assert row1["total_cover"] == 1000.0
        assert row1["total_risk_premium"] == pytest.approx(12.0)
        assert df.columns == [
            "product_key", "product_name", "category_number",
            "n_members", "total_cover", "basic_technical_premium",
            "total_annual_premium", "profit_margin_amount",
            "total_risk_premium",
        ]

    def test_inactive_only_product_skipped(self):
        res = {"gla": _member_df(product_active=[False, False])}
        df = aggregation.category_summary(res)
        assert df.height == 0

    def test_empty_input(self):
        assert aggregation.category_summary({}).height == 0


class TestMemberSummary:
    def test_joins_products_and_totals(self):
        res = {
            "gla": _member_df(),
            "lsdb": _member_df(total_cover=[3.0, 6.0],
                               total_annual_premium=[4.0, 8.0],
                               profit_margin_amount=[0.4, 0.8]),
        }
        df = aggregation.member_summary(res)
        assert df.height == 2
        assert df["member_no"].to_list() == [1, 2]
        for col in ("member_name", "gender", "age", "category_number",
                    "annual_salary", "gla_cover", "gla_premium", "gla_margin",
                    "lsdb_cover", "lsdb_premium", "lsdb_margin"):
            assert col in df.columns
        m1 = df.filter(pl.col("member_no") == 1).row(0, named=True)
        assert m1["gla_cover"] == 1000.0
        assert m1["lsdb_premium"] == 4.0
        assert m1["total_premium_all_products"] == pytest.approx(15.0)
        assert m1["total_margin_all_products"] == pytest.approx(1.4)
        assert m1["total_risk_all_products"] == pytest.approx(16.4)

    def test_empty_input_returns_empty(self):
        assert aggregation.member_summary({}).height == 0

    def test_anchor_falls_back_to_first_product(self):
        res = {"lsdb": _member_df()}
        df = aggregation.member_summary(res)
        assert df.height == 2
        assert "lsdb_cover" in df.columns


# ===========================================================================
# async_state.py
# ===========================================================================

class TestAsyncCounters:
    def test_db_write_counter(self):
        assert async_state.db_writes_pending() == 0
        async_state.db_write_started()
        async_state.db_write_started()
        assert async_state.db_writes_pending() == 2
        async_state.db_write_finished()
        assert async_state.db_writes_pending() == 1
        async_state.db_write_finished()
        assert async_state.db_writes_pending() == 0
        # never goes negative
        async_state.db_write_finished()
        assert async_state.db_writes_pending() == 0

    def test_file_sync_counter(self):
        assert async_state.file_syncs_pending() == 0
        async_state.file_sync_started()
        assert async_state.file_syncs_pending() == 1
        async_state.file_sync_finished()
        assert async_state.file_syncs_pending() == 0
        async_state.file_sync_finished()
        assert async_state.file_syncs_pending() == 0

    def test_report_write_counter(self):
        assert async_state.report_writes_pending() == 0
        async_state.report_write_started()
        assert async_state.report_writes_pending() == 1
        async_state.report_write_finished()
        assert async_state.report_writes_pending() == 0


class TestLocalDirLifecycle:
    def test_zero_consumers_deletes_immediately(self, tmp_path):
        d = tmp_path / "stage"
        d.mkdir()
        (d / "f.txt").write_text("x")
        async_state.register_local_dir(str(d), 0)
        assert not d.exists()

    def test_register_then_release_deletes_at_zero(self, tmp_path):
        d = tmp_path / "stage"
        d.mkdir()
        (d / "f.txt").write_text("x")
        async_state.register_local_dir(str(d), 2)
        assert d.exists()
        async_state.release_local_dir(str(d))
        assert d.exists()  # one consumer left
        async_state.release_local_dir(str(d))
        assert not d.exists()

    def test_release_unregistered_is_noop(self, tmp_path):
        d = tmp_path / "stage"
        d.mkdir()
        async_state.release_local_dir(str(d))
        assert d.exists()

    def test_counters_are_threadsafe(self):
        def churn():
            for _ in range(200):
                async_state.db_write_started()
                async_state.file_sync_started()
                async_state.report_write_started()
                async_state.report_write_finished()
                async_state.file_sync_finished()
                async_state.db_write_finished()
        threads = [threading.Thread(target=churn) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert async_state.db_writes_pending() == 0
        assert async_state.file_syncs_pending() == 0
        assert async_state.report_writes_pending() == 0


class TestSweepStaleStaging:
    def test_nonexistent_root(self, tmp_path):
        assert async_state.sweep_stale_staging(str(tmp_path / "nope")) == 0

    def test_removes_only_stale_dirs(self, tmp_path):
        fresh = tmp_path / "fresh"
        stale = tmp_path / "stale"
        fresh.mkdir()
        stale.mkdir()
        (fresh / "a.txt").write_text("x")
        (stale / "b.txt").write_text("x")
        old = 1_000_000.0
        os.utime(stale, (old, old))
        os.utime(stale / "b.txt", (old, old))
        removed = async_state.sweep_stale_staging(str(tmp_path), max_age_hours=24)
        assert removed == 1
        assert fresh.exists()
        assert not stale.exists()

    def test_ignores_loose_files(self, tmp_path):
        f = tmp_path / "file.txt"
        f.write_text("x")
        old = 1_000_000.0
        os.utime(f, (old, old))
        assert async_state.sweep_stale_staging(str(tmp_path)) == 0
        assert f.exists()


# ===========================================================================
# concurrent_runner.py -- helpers
# ===========================================================================

class TestSlug:
    @pytest.mark.parametrize("raw,expected", [
        ("Acme Pty Ltd", "Acme_Pty_Ltd"),
        ("A/B\\C", "A_B_C"),
        ("  spaced  ", "spaced"),
        ("", "quote"),
        ("!!!", "___"),
        ("a" * 100, "a" * 60),
        ("quote-42_x", "quote-42_x"),
    ])
    def test_slug(self, raw, expected):
        assert cr._slug(raw) == expected


class TestQuoteResultAsRow:
    def test_full_row(self):
        r = cr.QuoteResult(
            ctr_name="a.ctr", ctr_path="/p/a.ctr", scheme_name="Acme",
            quote_number=9, output_path="/out/report.xlsx",
            n_products=3, runtime_s=1.23456, status="OK", error=None,
            html_path="/out/report.html",
        )
        row = r.as_row()
        assert row == {
            "ctr_name": "a.ctr", "scheme": "Acme", "quote_no": 9,
            "n_products": 3, "runtime_s": 1.235,
            "output": "report.xlsx", "html": "report.html",
            "status": "OK", "error": "",
        }

    def test_defaults_and_error_truncation(self):
        r = cr.QuoteResult(
            ctr_name="b.ctr", ctr_path="b.ctr", scheme_name=None,
            quote_number=None, output_path=None, n_products=0,
            runtime_s=0.0, status="ERROR", error="x" * 500,
        )
        row = r.as_row()
        assert row["scheme"] == ""
        assert row["quote_no"] == 0
        assert row["output"] == "" and row["html"] == ""
        assert len(row["error"]) == 200


class TestLoadSharedSnapshots:
    def test_loads_real_bundles(self, basis_assump):
        basis, assump = basis_assump
        assert basis.tables, "basis tables empty"
        assert isinstance(assump.globals, Mapping)  # MappingProxyType, not a plain dict
        # product slices resolve through the registry
        assert isinstance(basis.for_product("gla"), dict)
        assert isinstance(assump.for_product("gla"), dict)


class TestDbHelpers:
    def test_db_unreachable_when_disabled(self, monkeypatch):
        monkeypatch.setattr(cr, "_db_reachable", None)
        monkeypatch.setattr(
            cr, "get_settings",
            lambda: SimpleNamespace(db_enabled=False))
        assert cr._is_db_reachable() is False

    def test_db_unreachable_on_connect_failure(self, monkeypatch):
        import pyodbc
        monkeypatch.setattr(cr, "_db_reachable", None)
        monkeypatch.setattr(
            cr, "get_settings",
            lambda: SimpleNamespace(db_enabled=True,
                                    db_connection_string="Driver={X}"))
        monkeypatch.setattr(
            pyodbc, "connect",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no srv")))
        assert cr._is_db_reachable() is False
        # cached -- second call must not retry
        monkeypatch.setattr(
            pyodbc, "connect",
            lambda *a, **k: (_ for _ in ()).throw(AssertionError("retried")))
        assert cr._is_db_reachable() is False

    def test_db_reachable_when_connect_succeeds(self, monkeypatch):
        import pyodbc
        monkeypatch.setattr(cr, "_db_reachable", None)
        monkeypatch.setattr(
            cr, "get_settings",
            lambda: SimpleNamespace(db_enabled=True,
                                    db_connection_string="Driver={X}"))
        closed = []
        monkeypatch.setattr(
            pyodbc, "connect",
            lambda *a, **k: SimpleNamespace(close=lambda: closed.append(1)))
        assert cr._is_db_reachable() is True
        assert closed == [1]

    def test_executors_created_and_cached(self, monkeypatch):
        fake = SimpleNamespace(database_workers=1, report_workers=1)
        monkeypatch.setattr(cr, "get_settings", lambda: fake)
        monkeypatch.setattr(cr, "_db_executor", None)
        monkeypatch.setattr(cr, "_report_executor", None)
        db_ex = cr._get_db_executor()
        rep_ex = cr._get_report_executor()
        try:
            assert db_ex is cr._get_db_executor()
            assert rep_ex is cr._get_report_executor()
            assert db_ex is not rep_ex
        finally:
            db_ex.shutdown(wait=False)
            rep_ex.shutdown(wait=False)


# ===========================================================================
# concurrent_runner.py -- run_single_quote / run_quotes_concurrent
# ===========================================================================

class TestRunSingleQuote:
    def test_success_writes_xlsx_and_parquet(
            self, tmp_path, basis_assump, patched_load_ctr,
            stub_report_writer, ctr_bundle):
        basis, assump = basis_assump
        sink_calls = []
        res = cr.run_single_quote(
            CTR_FILE, basis, assump, tmp_path,
            basis_snapshot="basis_v2026_08", assump_snapshot="assumption_A2026_08",
            write_report=True, wait_for_reports=True,
            result_sink=lambda *a: sink_calls.append(a),
            basis_folder=str(BASIS_DIR), assump_folder=str(ASSUMP_DIR),
        )
        assert res.status == "OK", res.error
        assert res.ctr_name == CTR_FILE.name
        assert res.scheme_name == getattr(ctr_bundle.scheme, "scheme_name", None)
        assert res.n_products == len(ctr_bundle.active_products)
        assert res.runtime_s > 0
        # artifacts synced into the output dir
        assert res.output_path is not None
        assert Path(res.output_path).name.endswith(".xlsx")
        assert Path(res.output_path).exists()
        parquet = Path(res.output_path).with_name(
            Path(res.output_path).stem + "_calc.parquet")
        assert parquet.exists()
        # sink fired with results
        assert len(sink_calls) == 1
        assert stub_report_writer, "writer stub never called"
        # all background counters drained (synchronous mode)
        assert async_state.report_writes_pending() == 0
        assert async_state.file_syncs_pending() == 0

    def test_parquet_only_output_path(
            self, tmp_path, basis_assump, patched_load_ctr,
            stub_report_writer):
        basis, assump = basis_assump
        res = cr.run_single_quote(
            CTR_FILE, basis, assump, tmp_path,
            write_report=False, wait_for_reports=True,
        )
        assert res.status == "OK", res.error
        assert res.output_path.endswith("_calc.parquet")
        assert Path(res.output_path).exists()
        assert res.html_path is None

    def test_html_report_path(
            self, tmp_path, basis_assump, patched_load_ctr,
            stub_report_writer):
        basis, assump = basis_assump
        res = cr.run_single_quote(
            CTR_FILE, basis, assump, tmp_path,
            write_report="html", wait_for_reports=True,
        )
        assert res.status == "OK", res.error
        assert res.html_path is not None
        assert Path(res.html_path).name.endswith(".html")
        assert Path(res.html_path).exists()
        # xlsx was not requested -> output points at parquet
        assert res.output_path.endswith("_calc.parquet")

    def test_failing_result_sink_is_swallowed(
            self, tmp_path, basis_assump, patched_load_ctr,
            stub_report_writer):
        basis, assump = basis_assump

        def boom(*a):
            raise RuntimeError("sink exploded")

        res = cr.run_single_quote(
            CTR_FILE, basis, assump, tmp_path,
            write_report=False, wait_for_reports=True, result_sink=boom,
        )
        assert res.status == "OK", res.error

    def test_missing_ctr_returns_error(self, tmp_path, basis_assump):
        basis, assump = basis_assump
        res = cr.run_single_quote(
            tmp_path / "does_not_exist.ctr", basis, assump, tmp_path,
            wait_for_reports=True,
        )
        assert res.status == "ERROR"
        assert res.ctr_name == "does_not_exist.ctr"
        assert res.n_products == 0
        assert "not found" in res.error.lower() or "Error" in res.error

    def test_report_writer_missing_artifacts_errors(
            self, tmp_path, basis_assump, patched_load_ctr, monkeypatch):
        import reporting
        monkeypatch.setattr(
            reporting, "write_pricing_report",
            _make_report_stub([], write_parquet=False, write_xlsx=False))
        basis, assump = basis_assump
        res = cr.run_single_quote(
            CTR_FILE, basis, assump, tmp_path,
            write_report=True, wait_for_reports=True,
        )
        assert res.status == "ERROR"
        assert "did not produce all requested artifacts" in res.error

    def test_report_writer_exception_propagates_as_error(
            self, tmp_path, basis_assump, patched_load_ctr, monkeypatch):
        import reporting

        def explode(**kw):
            raise OSError("disk on fire")

        monkeypatch.setattr(reporting, "write_pricing_report", explode)
        basis, assump = basis_assump
        res = cr.run_single_quote(
            CTR_FILE, basis, assump, tmp_path,
            write_report=False, wait_for_reports=True,
        )
        assert res.status == "ERROR"
        assert "disk on fire" in res.error


class TestWriteReportWorkerDbPaths:
    """Direct tests of _write_report_worker DB-related branches."""

    def _job(self, tmp_path, **kw):
        defaults = dict(
            ctr_path=Path("test.ctr"), scheme_name="TestScheme",
            all_results={}, ctr=SimpleNamespace(), assump=None,
            basis_snapshot="b", assump_snapshot="a",
            out_path=tmp_path / "out.xlsx",
            final_parquet=tmp_path / "out_calc.parquet",
            final_html=tmp_path / "out.html",
            write_xlsx=True, write_html=False,
            output_root=str(tmp_path), wait_for_reports=True,
            basis_folder="bf", assump_folder="af",
        )
        defaults.update(kw)
        return cr.ReportWriteJob(**defaults)

    def test_db_enabled_but_unreachable_raises(
            self, tmp_path, monkeypatch, stub_report_writer):
        fake = SimpleNamespace(
            db_enabled=True, staging_dir=tmp_path / "staging",
            artifact_root=str(tmp_path / "art"),
            db_connection_string="x", database_workers=1)
        monkeypatch.setattr(cr, "get_settings", lambda: fake)
        monkeypatch.setattr(cr, "_is_db_reachable", lambda: False)
        with pytest.raises(RuntimeError, match="unreachable"):
            cr._write_report_worker(self._job(tmp_path))

    def test_db_load_success_path(
            self, tmp_path, monkeypatch, stub_report_writer):
        fake = SimpleNamespace(
            db_enabled=True, staging_dir=tmp_path / "staging",
            artifact_root=str(tmp_path / "art"),
            db_connection_string="x", database_workers=1)
        monkeypatch.setattr(cr, "get_settings", lambda: fake)
        monkeypatch.setattr(cr, "_is_db_reachable", lambda: True)
        import pricing_engine.db_loader as dbl
        seen = []
        monkeypatch.setattr(
            dbl, "load_run_to_db",
            lambda *a, **k: seen.append(k) or {
                "status": "success", "run_id": "r1", "rows": 3,
                "timing": {"total": 0.01},
            })
        cr._write_report_worker(self._job(tmp_path))
        assert len(seen) == 1
        assert async_state.db_writes_pending() == 0
        assert async_state.file_syncs_pending() == 0

    def test_db_load_failure_raises_in_wait_mode(
            self, tmp_path, monkeypatch, stub_report_writer):
        fake = SimpleNamespace(
            db_enabled=True, staging_dir=tmp_path / "staging",
            artifact_root=str(tmp_path / "art"),
            db_connection_string="x", database_workers=1)
        monkeypatch.setattr(cr, "get_settings", lambda: fake)
        monkeypatch.setattr(cr, "_is_db_reachable", lambda: True)
        import pricing_engine.db_loader as dbl
        monkeypatch.setattr(
            dbl, "load_run_to_db",
            lambda *a, **k: {"status": "failed", "error": "deadlock"})
        with pytest.raises(RuntimeError, match="database load failed"):
            cr._write_report_worker(self._job(tmp_path))
        assert async_state.db_writes_pending() == 0

    def test_db_worker_unexpected_error_raises_in_wait_mode(
            self, tmp_path, monkeypatch, stub_report_writer):
        fake = SimpleNamespace(
            db_enabled=True, staging_dir=tmp_path / "staging",
            artifact_root=str(tmp_path / "art"),
            db_connection_string="x", database_workers=1)
        monkeypatch.setattr(cr, "get_settings", lambda: fake)
        monkeypatch.setattr(cr, "_is_db_reachable", lambda: True)
        import pricing_engine.db_loader as dbl
        monkeypatch.setattr(
            dbl, "load_run_to_db",
            lambda *a, **k: (_ for _ in ()).throw(KeyError("weird")))
        with pytest.raises(KeyError):
            cr._write_report_worker(self._job(tmp_path))
        assert async_state.db_writes_pending() == 0

    def test_file_sync_failure_propagates_in_wait_mode(
            self, tmp_path, monkeypatch, stub_report_writer):
        fake = SimpleNamespace(
            db_enabled=False, staging_dir=tmp_path / "staging",
            artifact_root=str(tmp_path / "art"),
            db_connection_string="x", database_workers=1)
        monkeypatch.setattr(cr, "get_settings", lambda: fake)

        class BoomStorage:
            def __init__(self, root):
                self.root = root

            def put_file(self, *a, **k):
                raise OSError("network share down")

        monkeypatch.setattr(cr, "Storage", BoomStorage)
        with pytest.raises(OSError, match="network share down"):
            cr._write_report_worker(self._job(tmp_path))
        assert async_state.file_syncs_pending() == 0


class TestRunQuotesConcurrent:
    def test_mixed_success_and_error_preserves_order(
            self, tmp_path, basis_assump, patched_load_ctr,
            stub_report_writer):
        basis, assump = basis_assump
        bogus = tmp_path / "missing.ctr"
        sinks = []
        summary = cr.run_quotes_concurrent(
            [CTR_FILE, bogus, CTR_FILE], basis, assump, tmp_path,
            basis_snapshot="b", assump_snapshot="a",
            max_workers=2, verbose=True, write_report=False,
            result_sink=lambda *a: sinks.append(a),
            wait_for_reports=True,
        )
        assert summary.height == 3
        statuses = summary["status"].to_list()
        assert statuses.count("OK") == 2
        assert statuses.count("ERROR") == 1
        # duplicate ctr names collapse to the last index in the ordering
        # map, so the unique bogus row (index 1) sorts first
        err_row = summary.row(0, named=True)
        assert err_row["ctr_name"] == "missing.ctr"
        assert err_row["status"] == "ERROR"
        assert len(err_row["error"]) <= 200
        ok_row = summary.row(1, named=True)
        assert ok_row["ctr_name"] == CTR_FILE.name
        assert ok_row["n_products"] > 0
        assert ok_row["status"] == "OK"
        # sink only fires for successful quotes
        assert len(sinks) == 2

    def test_empty_input_returns_empty_frame(
            self, tmp_path, basis_assump):
        basis, assump = basis_assump
        summary = cr.run_quotes_concurrent(
            [], basis, assump, tmp_path, verbose=False)
        assert summary.height == 0

    def test_sql_seq_guid_fallback_to_uuid(
            self, tmp_path, basis_assump, patched_load_ctr,
            stub_report_writer, monkeypatch):
        import pricing_engine.db_loader as dbl
        monkeypatch.setattr(
            dbl, "sql_seq_guid",
            lambda: (_ for _ in ()).throw(RuntimeError("no guid")))
        basis, assump = basis_assump
        summary = cr.run_quotes_concurrent(
            [CTR_FILE], basis, assump, tmp_path,
            max_workers=1, verbose=True, write_report=False,
            wait_for_reports=True,
        )
        assert summary.height == 1
        assert summary["status"].to_list() == ["OK"]


# ===========================================================================
# cli.py / __main__.py
# ===========================================================================

class TestCli:
    def test_check_config_ok(self, caplog):
        import logging
        caplog.set_level(logging.INFO)
        rc = cli.main(["--check-config"])
        assert rc == 0
        assert "Configuration valid" in caplog.text

    def test_workers_must_be_positive(self):
        assert cli.main(["--workers", "0"]) == 1
        assert cli.main(["--workers", "-3"]) == 1

    def test_no_ctr_inputs_fails(self, tmp_path):
        assert cli.main(["--ctr-dir", str(tmp_path)]) == 1

    def test_missing_ctr_dir_fails(self, tmp_path):
        rc = cli.main(["--ctr-dir", str(tmp_path / "nope")])
        assert rc == 1

    def test_missing_ctr_file_fails(self, tmp_path):
        rc = cli.main(["--ctr-file", str(tmp_path / "nope.ctr")])
        assert rc == 1

    def _patch_runner(self, monkeypatch, rows, captured):
        def _snap(b, a):
            captured["snapshots"] = (b, a)
            return "BASIS", "ASSUMP"

        def _run(inputs, basis, assump, out_dir, **kw):
            captured.update(inputs=inputs, out_dir=out_dir, kw=kw)
            return pl.DataFrame(rows)

        monkeypatch.setattr(cr, "load_shared_snapshots", _snap)
        monkeypatch.setattr(cr, "run_quotes_concurrent", _run)

    def test_headless_success_returns_zero(
            self, tmp_path, monkeypatch, caplog):
        import logging
        caplog.set_level(logging.INFO)
        ctr_dir = tmp_path / "in"
        ctr_dir.mkdir()
        (ctr_dir / "a.ctr").write_bytes(b"x")
        (ctr_dir / "ignore.txt").write_text("not a ctr")
        out_dir = tmp_path / "out"
        captured = {}
        self._patch_runner(
            monkeypatch,
            {"ctr_name": ["a.ctr"], "status": ["OK"], "error": [None]},
            captured)
        rc = cli.main([
            "--ctr-dir", str(ctr_dir),
            "--basis-dir", str(BASIS_DIR),
            "--assumption-dir", str(ASSUMP_DIR),
            "--output-dir", str(out_dir),
            "--workers", "2", "--report", "parquet",
        ])
        assert rc == 0
        assert "Completed and published 1 quote(s)" in caplog.text
        assert len(captured["inputs"]) == 1
        assert captured["inputs"][0].name == "a.ctr"
        assert captured["kw"]["write_report"] is False  # parquet -> False
        assert captured["kw"]["max_workers"] == 2
        assert captured["kw"]["basis_snapshot"] == BASIS_DIR.name
        assert captured["kw"]["assump_snapshot"] == ASSUMP_DIR.name
        assert captured["kw"]["wait_for_reports"] is True

    def test_ctr_file_repeatable(self, tmp_path, monkeypatch):
        f1 = tmp_path / "one.ctr"
        f2 = tmp_path / "two.ctr"
        f1.write_bytes(b"x")
        f2.write_bytes(b"x")
        captured = {}
        self._patch_runner(
            monkeypatch,
            {"ctr_name": ["one.ctr", "two.ctr"],
             "status": ["OK", "OK"], "error": [None, None]},
            captured)
        rc = cli.main([
            "--ctr-file", str(f1), "--ctr-file", str(f2),
            "--basis-dir", str(BASIS_DIR),
            "--assumption-dir", str(ASSUMP_DIR),
            "--output-dir", str(tmp_path / "out"),
            "--report", "html",
        ])
        assert rc == 0
        assert len(captured["inputs"]) == 2
        assert captured["kw"]["write_report"] == "html"

    def test_failed_quote_returns_one(self, tmp_path, monkeypatch, caplog):
        import logging
        caplog.set_level(logging.INFO)
        f = tmp_path / "bad.ctr"
        f.write_bytes(b"x")
        captured = {}
        self._patch_runner(
            monkeypatch,
            {"ctr_name": ["bad.ctr"], "status": ["ERROR"],
             "error": ["kaboom"]},
            captured)
        rc = cli.main([
            "--ctr-file", str(f),
            "--basis-dir", str(BASIS_DIR),
            "--assumption-dir", str(ASSUMP_DIR),
            "--output-dir", str(tmp_path / "out"),
            "--report", "xlsx",
        ])
        assert rc == 1
        assert "Quote failed" in caplog.text
        assert captured["kw"]["write_report"] is True

    def test_config_flag_sets_env_and_validates(
            self, tmp_path):
        cfg = tmp_path / "cfg.json"
        cfg.write_text(
            '{"paths": {"cache": null, "staging": null}, "database": {"enabled": false}}')
        from gap_config import get_settings
        orig = os.environ.get("GAP_CONFIG_FILE")
        try:
            rc = cli.main(["--config", str(cfg), "--check-config"])
            assert rc == 0
            assert os.environ["GAP_CONFIG_FILE"] == str(cfg.resolve())
        finally:
            if orig is None:
                os.environ.pop("GAP_CONFIG_FILE", None)
            else:
                os.environ["GAP_CONFIG_FILE"] = orig
            get_settings.cache_clear()

    def test_unreadable_config_returns_one(self, tmp_path):
        cfg = tmp_path / "missing.json"
        orig = os.environ.get("GAP_CONFIG_FILE")
        try:
            rc = cli.main(["--config", str(cfg), "--check-config"])
            assert rc == 1
        finally:
            if orig is None:
                os.environ.pop("GAP_CONFIG_FILE", None)
            else:
                os.environ["GAP_CONFIG_FILE"] = orig
            from gap_config import get_settings
            get_settings.cache_clear()


class TestMainModule:
    def test_dunder_main_check_config(self, monkeypatch):
        monkeypatch.setattr(
            sys, "argv", ["pricing_engine", "--check-config"])
        with pytest.raises(SystemExit) as exc:
            runpy.run_module("pricing_engine", run_name="__main__")
        assert exc.value.code == 0

    def test_dunder_main_bad_args(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            sys, "argv",
            ["pricing_engine", "--ctr-dir", str(tmp_path / "empty")])
        with pytest.raises(SystemExit) as exc:
            runpy.run_module("pricing_engine", run_name="__main__")
        assert exc.value.code == 1
