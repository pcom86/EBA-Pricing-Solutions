from __future__ import annotations

import json

import polars as pl
import pytest
from ctr_loader.assumption_loader import _coerce_global, load_assumptions
from ctr_loader.assumption_loader import _read_meta as read_assumption_meta
from ctr_loader.basis_loader import _read_meta as read_basis_meta
from ctr_loader.basis_loader import load_basis


@pytest.mark.unit
@pytest.mark.parametrize(
    ("value", "kind", "expected"),
    [
        ("12.5", "float", 12.5),
        ("12.5", "int", 12),
        ("YES", "bool", True),
        ("0", "bool", False),
        ("text", "string", "text"),
        ("bad", "float", None),
        ("bad", "int", None),
        (None, "float", None),
    ],
)
def test_coerce_global_handles_supported_types(value, kind, expected):
    assert _coerce_global(value, kind) == expected


@pytest.mark.unit
def test_load_assumptions_reads_manifest_and_all_global_types(tmp_path):
    manifest = {"built_at": "2026-01-01T10:00:00Z", "source_sha256": "abc123"}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    pl.DataFrame(
        {
            "param_name": ["threshold", "enabled", "factor", "label", "invalid"],
            "param_value": ["12", "yes", "1.25", "Life", "bad"],
            "param_type": ["int", "bool", "float", "str", "float"],
        }
    ).write_parquet(tmp_path / "globals.parquet")

    bundle = load_assumptions(tmp_path, use_bundle=False)

    assert bundle.built_at == manifest["built_at"]
    assert bundle.source_sha256 == manifest["source_sha256"]
    assert bundle.globals == {
        "threshold": 12,
        "enabled": True,
        "factor": 1.25,
        "label": "Life",
        "invalid": None,
    }


@pytest.mark.unit
def test_load_assumptions_rejects_empty_snapshot(tmp_path):
    with pytest.raises(FileNotFoundError, match=r"No \.parquet files"):
        load_assumptions(tmp_path, use_bundle=False)


@pytest.mark.unit
def test_load_basis_rejects_empty_snapshot(tmp_path):
    with pytest.raises(FileNotFoundError, match=r"No \.parquet files"):
        load_basis(tmp_path, use_bundle=False)


@pytest.mark.unit
def test_metadata_falls_back_to_folder_name(tmp_path):
    basis_meta = read_basis_meta(tmp_path)
    assumption_meta = read_assumption_meta(tmp_path)

    assert basis_meta == {
        "version": tmp_path.name,
        "built_at": "",
        "source_sha256": "",
    }
    assert assumption_meta == {"built_at": "", "source_sha256": ""}


@pytest.mark.unit
def test_metadata_reads_build_log(tmp_path):
    (tmp_path / "build_log.csv").write_text(
        "version,built_at,source_sha256\n"
        "v1,2026-01-01,hash-one\n"
        "v2,2026-01-02,hash-two\n",
        encoding="utf-8",
    )

    basis_meta = read_basis_meta(tmp_path)
    assumption_meta = read_assumption_meta(tmp_path)

    assert basis_meta["version"] == "v2"
    assert basis_meta["built_at"] == "2026-01-02"
    assert basis_meta["source_sha256"] == "hash-two"
    assert assumption_meta == {
        "built_at": "2026-01-02",
        "source_sha256": "hash-two",
    }


@pytest.mark.unit
def test_metadata_ignores_malformed_build_log(tmp_path):
    (tmp_path / "build_log.csv").write_text("not,a,valid\nvalue", encoding="utf-8")

    basis_meta = read_basis_meta(tmp_path)
    assumption_meta = read_assumption_meta(tmp_path)

    assert basis_meta["version"] == tmp_path.name
    assert assumption_meta == {"built_at": "", "source_sha256": ""}
