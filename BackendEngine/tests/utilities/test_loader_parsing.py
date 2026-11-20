from __future__ import annotations

import polars as pl
import pytest

from ctr_loader.basis_loader import BasisBundle, load_basis
from ctr_loader.assumption_loader import AssumptionsBundle, load_assumptions


@pytest.mark.unit
def test_basis_bundle_for_product_filters_supported_tables(tmp_path):
    folder = tmp_path / "basis_snapshot"
    folder.mkdir()

    mortality = pl.DataFrame(
        {
            "product": ["gla", "gla"],
            "age": [30, 31],
            "gender": ["M", "M"],
            "rate": [0.01, 0.02],
        }
    )
    salary_band = pl.DataFrame(
        {
            "product": ["gla"],
            "gender": ["M"],
            "salary_band": ["A"],
            "inc_adj": [1.0],
        }
    )
    other = pl.DataFrame({"product": ["ci"], "age": [1], "gender": ["M"], "rate": [0.1]})

    for name, df in {
        "mortality.parquet": mortality,
        "salary_band.parquet": salary_band,
        "industry.parquet": other,
    }.items():
        df.write_parquet(folder / name)

    bundle = load_basis(folder, use_bundle=False)

    assert isinstance(bundle, BasisBundle)
    assert "mortality" in bundle.tables
    assert "salary_band" in bundle.tables
    slice_ = bundle.for_product("gla")
    assert "mortality" in slice_
    assert "salary_band" in slice_
    assert "industry" not in slice_


@pytest.mark.unit
def test_assumptions_bundle_builds_globals_and_filters_product_tables(tmp_path):
    folder = tmp_path / "assumption_snapshot"
    folder.mkdir()

    globals_df = pl.DataFrame(
        {
            "param_name": ["scheme_size_large_threshold", "line_of_business"],
            "param_value": ["400", "Life"],
            "param_type": ["int", "str"],
        }
    )
    limits = pl.DataFrame(
        {
            "product": ["gla", "ci"],
            "param_name": ["max_cover", "max_cover"],
            "param_value": [200000.0, 300000.0],
            "param_type": ["float", "float"],
        }
    )
    margins = pl.DataFrame(
        {
            "product": ["gla"],
            "param_name": ["profit_margin"],
            "param_value": [0.1],
            "param_type": ["float"],
        }
    )

    for name, df in {
        "globals.parquet": globals_df,
        "product_limits.parquet": limits,
        "profit_margins.parquet": margins,
    }.items():
        df.write_parquet(folder / name)

    bundle = load_assumptions(folder, use_bundle=False)

    assert isinstance(bundle, AssumptionsBundle)
    assert bundle.globals["scheme_size_large_threshold"] == 400
    assert bundle.globals["line_of_business"] == "Life"
    product_slice = bundle.for_product("gla")
    assert "product_limits" in product_slice
    assert "profit_margins" in product_slice


@pytest.mark.unit
def test_load_basis_raises_for_missing_folder(tmp_path):
    missing = tmp_path / "does-not-exist"
    with pytest.raises(FileNotFoundError):
        load_basis(missing, use_bundle=False)


@pytest.mark.unit
def test_load_assumptions_raises_for_missing_folder(tmp_path):
    missing = tmp_path / "does-not-exist"
    with pytest.raises(FileNotFoundError):
        load_assumptions(missing, use_bundle=False)
