"""Golden-master regression test for the pricing engine.

Prices the fictitious all-products CTR with the pinned basis/assumption
snapshots and compares every member-level output column with the reference
calc parquet in ``BackendEngine/Test/phase1_verify_20260824`` (engine
v2026.07, generated 2026-08-24 from the same CTR and snapshots).

If a pricing change is intentional, regenerate the reference outputs, have
them signed off, and point ``REFERENCE_CALC`` at the new file.
"""
from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest
from ctr_loader.ctr_loader_v2 import load_ctr
from pricing_engine.concurrent_runner import load_shared_snapshots
from pricing_engine.engine import build_context, price_all_products
from reporting.meta_engine import build_engine_frame

pytestmark = pytest.mark.golden

ENGINE_ROOT = Path(__file__).resolve().parents[2] / "BackendEngine"
BASIS_DIR = ENGINE_ROOT / "basis_snapshots" / "basis_v2026_08"
ASSUMP_DIR = ENGINE_ROOT / "assumption_snapshots" / "assumption_A2026_08"
CTR_FILE = (
    ENGINE_ROOT / "Test" / "Single run"
    / "Fictitious_Accel LSDB + FS CI No ORB + ParCov+PRO+EduG 30.07.2026.ctr"
)
REFERENCE_CALC = (
    ENGINE_ROOT / "Test" / "phase1_verify_20260824"
    / "Fictitious_Test_All_NOOP_RECHECK_20260824_calc.parquet"
)

REFERENCE_PRODUCTS = (
    "gla", "lsdb", "ci", "spouse_gla", "spouse_lsdb", "terminal",
    "accident_death", "accident_disability", "dismemberment", "fc", "gip",
    "tip", "education_guardian",
)
# Products priced by the current engine that post-date the reference run.
PRODUCTS_NOT_IN_REFERENCE = {"ext_fc"}

ABS_TOL = 1e-6
REL_TOL = 1e-9


@pytest.fixture(scope="module")
def reference() -> pl.DataFrame:
    for path in (BASIS_DIR, ASSUMP_DIR, CTR_FILE, REFERENCE_CALC):
        if not path.exists():
            pytest.skip(f"golden-master input missing: {path}")
    return pl.read_parquet(REFERENCE_CALC)


@pytest.fixture(scope="module")
def actual(reference) -> pl.DataFrame:
    basis, assump = load_shared_snapshots(BASIS_DIR, ASSUMP_DIR)
    ctr = load_ctr(str(CTR_FILE))
    ctx = build_context(ctr, assump)
    return build_engine_frame(price_all_products(ctr, basis, assump, ctx))


def _product(df: pl.DataFrame, product: str) -> pl.DataFrame:
    return df.filter(pl.col("product") == product).sort("member_no")


def _mismatches(expected: pl.DataFrame, actual: pl.DataFrame) -> list[str]:
    """Describe every reference column whose values differ in ``actual``.

    Rows are aligned on ``member_no``; only member numbers are reported so
    member PII never reaches test output.
    """
    joined = expected.join(actual, on="member_no", how="full", suffix="__actual",
                           coalesce=True)
    problems = []
    for col in expected.columns:
        if col == "member_no":
            continue
        exp, act = joined[col], joined[f"{col}__actual"]
        null_diff = exp.is_null() != act.is_null()
        if exp.dtype.is_numeric() and act.dtype.is_numeric():
            exp_f, act_f = exp.cast(pl.Float64), act.cast(pl.Float64)
            value_diff = ((exp_f - act_f).abs() > ABS_TOL + REL_TOL * exp_f.abs())
        else:
            value_diff = exp.cast(pl.Utf8) != act.cast(pl.Utf8)
        bad = (null_diff | value_diff.fill_null(False))
        n_bad = int(bad.sum())
        if not n_bad:
            continue
        first = joined.filter(bad).row(0, named=True)
        detail = f"{col}: {n_bad} member(s) differ, first member_no={first['member_no']}"
        if exp.dtype.is_numeric():
            detail += f" expected={first[col]!r} actual={first[f'{col}__actual']!r}"
        problems.append(detail)
    return problems


def test_priced_products_match_reference(reference, actual):
    expected = set(reference["product"].unique())
    priced = set(actual["product"].unique())
    assert expected == set(REFERENCE_PRODUCTS)
    assert priced - expected == PRODUCTS_NOT_IN_REFERENCE
    assert expected <= priced


def test_output_columns_cover_reference(reference, actual):
    missing = [c for c in reference.columns if c not in actual.columns]
    assert not missing, f"columns dropped from engine output: {missing}"


@pytest.mark.parametrize("product", REFERENCE_PRODUCTS)
def test_member_level_outputs_match_reference(reference, actual, product):
    expected = _product(reference, product)
    priced = _product(actual, product).select(reference.columns)
    assert priced.height == expected.height
    problems = _mismatches(expected, priced)
    assert not problems, f"{product} differs from golden master:\n" + "\n".join(problems)
