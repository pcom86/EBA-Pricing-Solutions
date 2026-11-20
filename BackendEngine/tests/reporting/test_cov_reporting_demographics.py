"""Coverage tests for reporting.demographics (EOH ladders, aggregates, blocks)."""
from __future__ import annotations

import math
from types import SimpleNamespace

import polars as pl
import pytest
from reporting import demographics as dem

# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------

def _engine_df(n=4):
    n = int(n)
    base = {
        "product_active": [True] * n,
        "status": ["A"] * n,
        "age": [40.0] * n,
        "ceasing_age": [65.0] * n,
        "gender": ["M"] * n,
        "annual_salary": [100_000.0] * n,
        "total_cover": [100_000.0] * n,
        "category_number": [1] * n,
        "qx": [0.001] * n,
        "basic_technical_premium": [100.0] * n,
        "aids_technical_premium": [10.0] * n,
        "co_premium": [1.0] * n,
        "ctc_premium": [2.0] * n,
    }
    return pl.DataFrame(base)


def _mixed_df():
    """4 rows: idx0/1 active covered; idx2 disabled; idx3 over-NRA."""
    return pl.DataFrame({
        "product_active": [True, True, True, True],
        "status": ["A", "A", "DB", "A"],
        "age": [30.0, 40.0, 50.0, 70.0],
        "ceasing_age": [65.0] * 4,
        "gender": ["M", "F", "M", "F"],
        "annual_salary": [100_000.0, 200_000.0, 150_000.0, 120_000.0],
        "total_cover": [300_000.0, 600_000.0, 450_000.0, 360_000.0],
        "category_number": [1, 1, 2, 2],
        "qx": [0.001, 0.002, 0.003, 0.004],
        "basic_technical_premium": [1200.0, 2400.0, 1800.0, 1500.0],
        "aids_technical_premium": [100.0, 200.0, 150.0, 50.0],
        "co_premium": [12.0, 24.0, 18.0, 15.0],
        "ctc_premium": [6.0, 12.0, 9.0, 7.5],
    })


def _assump(**kw):
    globals_ = {
        "scheme_size_large_threshold": 400,
        "eoh_amount": 10_000.0, "eoh_layer1": 500_000.0, "eoh_layer2": 500_000.0,
        "eoh_perc1": 0.5, "eoh_perc2": 0.3, "eoh_perc3": 0.1,
        "eoh_layer1_cap": None, "eoh_layer2_cap": None,
        "eoh_amount_ci": 8_000.0, "eoh_layer1_ci": 400_000.0, "eoh_layer2_ci": 400_000.0,
        "eoh_perc1_ci": 0.5, "eoh_perc2_ci": 0.3, "eoh_perc3_ci": 0.1,
        "eoh_layer1_cap_ci": None, "eoh_layer2_cap_ci": None,
        "eoh_max_gip": 145_000.0, "eoh_max_tip": 145_000.0,
    }
    tables = {
        "product_limits": pl.DataFrame({
            "product": ["lsdb", "ci_new", "ci_existing"],
            "param_name": ["max_cover", "max_cover", "max_cover"],
            "param_value": [2_000_000.0, 3_000_000.0, 1_000_000.0],
        }),
        "ci_table_mapping": pl.DataFrame({
            "product_option": ["opt_x"],
            "variant_type": ["Existing"],
        }),
    }
    a = SimpleNamespace(tables=tables, globals=globals_)
    for k, v in kw.items():
        setattr(a, k, v)
    return a


def _ctr(scheme=None, categories=None):
    if categories is None:
        categories = pl.DataFrame({"product": [], "field": [], "value": []})
    return SimpleNamespace(scheme=scheme or SimpleNamespace(rating={}),
                           categories=categories)


# --------------------------------------------------------------------------
# small predicates / counters
# --------------------------------------------------------------------------

@pytest.mark.unit
def test_tt_active_and_over_nra():
    df = _mixed_df()
    assert dem._tt_active(df).height == 3
    assert dem._over_nra_count(df) == 1


@pytest.mark.unit
def test_disabled_count_respects_product_relevance():
    df = _mixed_df()
    assert dem._disabled_count(df, "gla") == 1
    assert dem._disabled_count(df, "ci") == 1
    assert dem._disabled_count(df, "fc") == 0
    assert dem._disabled_count(df, "spouse_gla") == 0


# --------------------------------------------------------------------------
# _eoh_ladder
# --------------------------------------------------------------------------

@pytest.mark.unit
def test_eoh_ladder_small_scheme_is_zero():
    assert dem._eoh_ladder(10, 1e9, 1.0, 1.0, 1.0, 0.5, 0.3, 0.1) == 0.0


@pytest.mark.unit
def test_eoh_ladder_huge_scheme_uses_sqrt_formula():
    n, cover = 400, 8e6
    expected = 0.75 * math.sqrt(n) * cover / n
    assert dem._eoh_ladder(n, cover, 1.0, 1.0, 1.0, 0.5, 0.3, 0.1) == pytest.approx(expected)


@pytest.mark.unit
def test_eoh_ladder_mid_size_layer_math():
    # total 1.5m >= layer1+layer2 (1m): full layers
    out = dem._eoh_ladder(15, 1_500_000.0, base=10_000.0,
                          layer1=500_000.0, layer2=500_000.0,
                          perc1=0.5, perc2=0.3, perc3=0.1)
    assert out == pytest.approx(10_000.0 + 250_000.0 + 150_000.0 + 50_000.0)


@pytest.mark.unit
def test_eoh_ladder_caps_and_partial_layers():
    # total < layer1: l1 = total*perc1
    out = dem._eoh_ladder(15, 300_000.0, base=1_000.0,
                          layer1=500_000.0, layer2=500_000.0,
                          perc1=0.5, perc2=0.3, perc3=0.1)
    assert out == pytest.approx(1_000.0 + 150_000.0 + 0.0 + 0.0)

    # layer1 <= total < layer1+layer2: partial l2
    out = dem._eoh_ladder(15, 700_000.0, base=1_000.0,
                          layer1=500_000.0, layer2=500_000.0,
                          perc1=0.5, perc2=0.3, perc3=0.1)
    assert out == pytest.approx(1_000.0 + 250_000.0 + 60_000.0 + 0.0)

    # caps apply
    out = dem._eoh_ladder(15, 1_500_000.0, base=0.0,
                          layer1=500_000.0, layer2=500_000.0,
                          perc1=0.5, perc2=0.3, perc3=0.1,
                          layer1_cap=100_000.0, layer2_cap=50_000.0)
    assert out == pytest.approx(100_000.0 + 50_000.0 + 50_000.0)


# --------------------------------------------------------------------------
# _get_limit / _resolve_ci_variant_type / _eoh_agg_active
# --------------------------------------------------------------------------

@pytest.mark.unit
def test_get_limit_paths():
    a = _assump()
    assert dem._get_limit(a, "lsdb", "max_cover") == 2_000_000.0
    assert dem._get_limit(a, "lsdb", "missing") is None
    assert dem._get_limit(SimpleNamespace(tables={}), "lsdb", "max_cover") is None

    bad = SimpleNamespace(tables={
        "product_limits": pl.DataFrame({
            "product": ["lsdb"], "param_name": ["max_cover"], "param_value": ["abc"]})
    })
    assert dem._get_limit(bad, "lsdb", "max_cover") is None


@pytest.mark.unit
def test_resolve_ci_variant_type():
    a = _assump()
    # no ci categories -> "New"
    ctr = _ctr()
    assert dem._resolve_ci_variant_type(ctr, a) == "New"

    # matching product_option -> mapped variant_type
    cats = pl.DataFrame({"product": ["ci"], "field": ["product_option"], "value": ["opt_x"]})
    ctr = _ctr(categories=cats)
    assert dem._resolve_ci_variant_type(ctr, a) == "Existing"

    # product_option not in mapping -> "New"
    cats = pl.DataFrame({"product": ["ci"], "field": ["product_option"], "value": ["nope"]})
    ctr = _ctr(categories=cats)
    assert dem._resolve_ci_variant_type(ctr, a) == "New"

    # mapping table missing entirely -> "New"
    a2 = _assump()
    a2.tables = dict(a2.tables)
    del a2.tables["ci_table_mapping"]
    ctr = _ctr(categories=pl.DataFrame({"product": ["ci"], "field": ["product_option"], "value": ["opt_x"]}))
    assert dem._resolve_ci_variant_type(ctr, a2) == "New"


@pytest.mark.unit
def test_eoh_agg_active():
    assert dem._eoh_agg_active(None) == {"n": 0, "total": 0.0, "highest": 0.0}
    assert dem._eoh_agg_active(pl.DataFrame()) == {"n": 0, "total": 0.0, "highest": 0.0}
    assert dem._eoh_agg_active(pl.DataFrame({"other": [1]})) == {"n": 0, "total": 0.0, "highest": 0.0}

    out = dem._eoh_agg_active(_mixed_df())
    assert out["n"] == 3
    assert out["total"] == pytest.approx(1_350_000.0)
    assert out["highest"] == pytest.approx(600_000.0)


@pytest.mark.unit
def test_compute_eoh_constants_reads_all_globals():
    C, g = dem._compute_eoh_constants(_assump())
    assert C["gla_amount"] == 10_000.0
    assert C["ci_amount"] == 8_000.0
    assert g["eoh_max_gip"] == 145_000.0


# --------------------------------------------------------------------------
# product EOH computations
# --------------------------------------------------------------------------

def _eoh_results(cover=100_000.0, n=15):
    return {"gla": _engine_df(n).with_columns(pl.lit(cover).alias("total_cover"))}


@pytest.mark.unit
def test_compute_gla_eoh_no_data_returns_nones():
    C, _ = dem._compute_eoh_constants(_assump())
    out = dem._compute_gla_eoh({}, C)
    assert out == {"tech_eoh": None, "custom_eoh": None, "eoh_limit": None}


@pytest.mark.unit
def test_compute_gla_eoh_capped_by_highest_cover():
    C, _ = dem._compute_eoh_constants(_assump())
    out = dem._compute_gla_eoh(_eoh_results(), C)
    # ladder would be 460k, but 1.2 * highest (120k) caps it
    assert out["eoh_limit"] == pytest.approx(120_000.0)


@pytest.mark.unit
def test_compute_gla_eoh_small_scheme_zero():
    C, _ = dem._compute_eoh_constants(_assump())
    out = dem._compute_gla_eoh(_eoh_results(n=5), C)
    assert out["eoh_limit"] == 0.0


@pytest.mark.unit
def test_compute_lsdb_eoh_requires_standalone():
    C, _ = dem._compute_eoh_constants(_assump())
    out = dem._compute_lsdb_eoh(_eoh_results(), C, "Accelerator", 2e6)
    assert out["eoh_limit"] is None

    out = dem._compute_lsdb_eoh({"lsdb": pl.DataFrame()}, C, "Standalone", 2e6)
    assert out["eoh_limit"] is None


@pytest.mark.unit
def test_compute_lsdb_eoh_standalone():
    C, _ = dem._compute_eoh_constants(_assump())
    all_results = {"lsdb": _engine_df(15)}
    out = dem._compute_lsdb_eoh(all_results, C, "Standalone", 2_000_000.0)
    assert out["eoh_limit"] == pytest.approx(120_000.0)

    # small max_cover clamps the result
    out = dem._compute_lsdb_eoh(all_results, C, "Standalone", 50_000.0)
    assert out["eoh_limit"] == pytest.approx(50_000.0)


@pytest.mark.unit
def test_compute_ci_eoh_requires_standalone_and_data():
    C, _ = dem._compute_eoh_constants(_assump())
    out = dem._compute_ci_eoh(_eoh_results(), C, "Accelerator", None)
    assert out["eoh_limit"] is None

    out = dem._compute_ci_eoh({"ci": pl.DataFrame()}, C, "Standalone", None)
    assert out["eoh_limit"] is None


@pytest.mark.unit
def test_compute_ci_eoh_standalone_variants():
    C, _ = dem._compute_eoh_constants(_assump())
    all_results = {"ci": _engine_df(15)}
    out = dem._compute_ci_eoh(all_results, C, "Standalone", None)
    assert out["eoh_limit"] == pytest.approx(120_000.0)

    # f323 limit participates in the min()
    out = dem._compute_ci_eoh(all_results, C, "Standalone", 90_000.0)
    assert out["eoh_limit"] == pytest.approx(90_000.0)

    # n <= 10 -> zero ladder -> zero limit
    out = dem._compute_ci_eoh({"ci": _engine_df(5)}, C, "Standalone", None)
    assert out["eoh_limit"] == 0.0


def _gip_like_df(n=15, monthly_cover=10_000.0):
    return pl.DataFrame({
        "product_active": [True] * n,
        "total_cover": [monthly_cover] * n,
    })


@pytest.mark.unit
def test_compute_gip_eoh_empty_and_missing_cover():
    g = _assump().globals
    out = dem._compute_gip_eoh({}, g)
    assert out["eoh_limit"] is None
    assert out["members_over"] == 0

    out = dem._compute_gip_eoh({"gip": pl.DataFrame({"x": [1]})}, g)
    assert out["eoh_limit"] is None


@pytest.mark.unit
def test_compute_gip_eoh_large_scheme():
    g = _assump().globals
    out = dem._compute_gip_eoh({"gip": _gip_like_df(15)}, g)
    assert out["eoh_limit"] == pytest.approx(12_000.0)
    assert out["max_cover"] == pytest.approx(10_000.0)
    assert out["members_over"] == 0


@pytest.mark.unit
def test_compute_gip_eoh_small_scheme_zero_limit():
    g = _assump().globals
    out = dem._compute_gip_eoh({"gip": _gip_like_df(5)}, g)
    assert out["eoh_limit"] == pytest.approx(0.0)
    assert out["members_over"] == 5
    assert out["cover_over"] == pytest.approx(50_000.0)


@pytest.mark.unit
def test_compute_tip_eoh():
    g = _assump().globals
    out = dem._compute_tip_eoh({"tip": _gip_like_df(15)}, g)
    assert out["eoh_limit"] == pytest.approx(12_000.0)
    out = dem._compute_tip_eoh({}, g)
    assert out["eoh_limit"] is None


@pytest.mark.unit
def test_compute_eoh_for_scheme_end_to_end():
    scheme = SimpleNamespace(rating={"lsdb": "Standalone", "ci": "Standalone"})
    ctr = _ctr(scheme=scheme)
    all_results = {
        "gla": _engine_df(15),
        "lsdb": _engine_df(15),
        "ci": _engine_df(15),
        "gip": _gip_like_df(15),
        "tip": _gip_like_df(15),
    }
    res = dem.compute_eoh_for_scheme(all_results, ctr, _assump())
    assert res["gla"]["eoh_limit"] == pytest.approx(120_000.0)
    assert res["lsdb"]["eoh_limit"] == pytest.approx(120_000.0)
    assert res["ci"]["eoh_limit"] == pytest.approx(120_000.0)
    assert res["gip"]["eoh_limit"] == pytest.approx(12_000.0)
    assert res["tip"]["eoh_limit"] == pytest.approx(12_000.0)
    for p in ("spouse_gla", "spouse_lsdb", "terminal",
              "accident_death", "accident_disability", "dismemberment"):
        assert res[p] == {"tech_eoh": None, "custom_eoh": None, "eoh_limit": None}


# --------------------------------------------------------------------------
# product_aggregates / compute_eoh_row
# --------------------------------------------------------------------------

@pytest.mark.unit
def test_product_aggregates_math():
    aggs = dem.product_aggregates(_mixed_df(), {"basis": {"gla": 1.0}}, product_key="gla")
    assert aggs["n_members"] == 3
    assert aggs["n_over_nra"] == 1
    assert aggs["n_disabled"] == 1
    assert aggs["avg_age"] == pytest.approx(40.0)
    assert aggs["pct_males"] == pytest.approx(2 / 3)
    assert aggs["salary_bill"] == pytest.approx(450_000.0)
    assert aggs["avg_salary"] == pytest.approx(150_000.0)
    assert aggs["age_weighted_by_salary"] == pytest.approx(18_500_000 / 450_000)
    assert aggs["total_cover"] == pytest.approx(1_350_000.0)
    assert aggs["avg_multiple"] == pytest.approx(3.0)
    assert aggs["avg_cover"] == pytest.approx(450_000.0)
    # qx-weighted: sum(basic+aids)=1300+2600+1950=5850 over the 3 active rows
    assert aggs["expected_claims"] == pytest.approx(5850.0)
    assert aggs["incidence_rate"] == pytest.approx(5850 / 3 * 1000)
    assert aggs["expected_claim_size"] > 0
    assert aggs["ave_claim_over_ave_cover"] > 0


@pytest.mark.unit
def test_product_aggregates_empty_df():
    df = _mixed_df().head(0)  # typed columns, zero rows
    aggs = dem.product_aggregates(df, {}, product_key="gla")
    assert aggs["n_members"] == 0
    assert aggs["avg_age"] == 0.0
    assert aggs["incidence_rate"] == 0.0


@pytest.mark.unit
def test_basis_key_for_product_is_gla():
    assert dem._basis_key_for_product(pl.DataFrame(), {}) == "gla"


@pytest.mark.unit
def test_compute_eoh_row_without_eoh():
    out = dem.compute_eoh_row("fc", {}, _mixed_df(), {})
    assert out["eoh_limit"] is None
    assert out["members_over_eoh"] is None
    assert out["max_cover"] == pytest.approx(600_000.0)


@pytest.mark.unit
def test_compute_eoh_row_with_limit():
    eoh = {"gla": {"tech_eoh": 500_000.0, "custom_eoh": 480_000.0, "eoh_limit": 500_000.0}}
    out = dem.compute_eoh_row("gla", {}, _mixed_df(), {}, eoh)
    assert out["tech_eoh"] == 500_000.0
    assert out["members_over_eoh"] == 1          # 600k > 500k
    assert out["cover_over_eoh"] == pytest.approx(100_000.0)
    assert out["max_cover"] == pytest.approx(600_000.0)


@pytest.mark.unit
def test_max_cover_empty():
    assert dem._max_cover(pl.DataFrame({"total_cover": []})) == 0.0


# --------------------------------------------------------------------------
# build_membership_block / build_normal_margins_block
# --------------------------------------------------------------------------

@pytest.mark.unit
def test_build_membership_block_without_ctr():
    all_results = {"gla": _mixed_df()}
    scheme_params = {"basis": {"gla": 1.0}, "lsdb_product": "Standalone",
                     "ci_product": "Standalone"}
    block = dem.build_membership_block(all_results, scheme_params)
    assert isinstance(block, pl.DataFrame)
    assert "Metric" in block.columns
    assert "GLA" in block.columns
    assert "LSDB Standalone" in block.columns

    membership = block.filter(pl.col("Metric") == "Membership")
    assert membership["GLA"][0] == "3"

    pct = block.filter(pl.col("Metric") == "% Males")
    assert pct["GLA"][0] == "66.67%"

    # products with no data get zero column
    eoh_row = block.filter(pl.col("Metric") == "EOH Limit")
    assert eoh_row["GLA"][0] == "N/A"


@pytest.mark.unit
def test_build_membership_block_with_ctr_and_assump():
    scheme = SimpleNamespace(rating={})
    ctr = _ctr(scheme=scheme)
    all_results = {"gla": _engine_df(15), "lsdb": _engine_df(15),
                   "education": _engine_df(15)}
    block = dem.build_membership_block(all_results, {}, ctr=ctr, assump=_assump())
    membership = block.filter(pl.col("Metric") == "Membership")
    assert membership["GLA"][0] == "15"
    assert membership["Educate-a-Child"][0] == "15"
    # gla has an eoh_limit -> money format, not N/A
    eoh_row = block.filter(pl.col("Metric") == "EOH Limit")
    assert eoh_row["GLA"][0] != "N/A"


@pytest.mark.unit
def test_resolve_product_labels_variants():
    labels = dem._resolve_product_labels({})
    assert labels["lsdb"] == "LSDB Accelerator"
    labels = dem._resolve_product_labels({"lsdb_product": "Standalone", "ci_product": "Existing"})
    assert labels["lsdb"] == "LSDB Standalone"
    assert labels["ci"] == "CI Existing"
    assert labels["education"] == "Educate-a-Child"


@pytest.mark.unit
def test_build_normal_margins_block():
    scheme_params = {
        "profit_margin": {"gla": 0.10, "lsdb": 0.08},
        "admin_fee": {"gla": 0.05},
        "business_decision_margin": {"gla": 0.01},
        "phasing_margin": {},
        "reinsurance_profit": {},
        "reinsurance_cost": {},
        "rate_basis": {"gla": 1.0},
        "prb_active": {"gla": True},
        "prb_percentage": 0.03,
    }
    all_results = {"gla": _mixed_df()}
    block = dem.build_normal_margins_block(all_results, scheme_params)
    assert isinstance(block, pl.DataFrame)

    pm = block.filter(pl.col("Metric") == "Profit Margin")
    assert pm["GLA"][0] == "10.00%"

    prb = block.filter(pl.col("Metric") == "Profit Refund Option Loading")
    assert prb["GLA"][0] == "3.00%"
    # product not flagged -> 0.00%
    assert prb["LSDB Accelerator"][0] == "0.00%"

    co = block.filter(pl.col("Metric") == "Conversion Option")
    # co sum over active = 54; net = 5850 -> ~0.92%
    assert co["GLA"][0] == "0.92%"


# --------------------------------------------------------------------------
# _format_val
# --------------------------------------------------------------------------

@pytest.mark.unit
@pytest.mark.parametrize(
    "val,fmt,expected",
    [
        (None, "int", "N/A"),
        (1234.5, "int", "1,234"),
        (None, "int_or_na", "N/A"),
        (5, "int_or_na", "5"),
        (1.5, "float2", "1.50"),
        (1234.5, "money", "1,234.50"),
        (None, "money_or_na", "N/A"),
        (0.5, "pct2", "50.00%"),
        ("raw", "other", "raw"),
    ],
)
def test_format_val(val, fmt, expected):
    assert dem._format_val(val, fmt) == expected
