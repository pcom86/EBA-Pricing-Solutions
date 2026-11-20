"""Coverage tests for reporting.inputs — pure helpers, gathers and builders."""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import polars as pl
import pytest
from reporting import inputs as inp

# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------

def _scheme(**kw):
    s = SimpleNamespace(
        scheme_name="Test Scheme",
        quote_number=7,
        calculation_date=date(2025, 6, 30),
        rating={},
        rate_basis_by_product={},
        premium_frequency="Monthly",
        include_vat_commission="Yes",
        country_scale="South Africa",
        intermediary="Broker",
        profit_refund_loading_by_product={},
        express_over_gla={},
        category_split={},
        priced_margin_overrides={},
        fc_paid_up_benefits_active={},
        fc_pup_death_lsdb={},
        fc_pup_lsdb={},
        fc_pup_death={},
        primary="Mining",
        secondary="Gold",
        region="Gauteng",
        education_product=None,
        ext_fc_pct_male={},
        ext_fc_avg_salary={},
        ext_fc_parents_selected={},
        ext_fc_extended_family_selected={},
    )
    for k, v in kw.items():
        setattr(s, k, v)
    return s


def _assump(**kw):
    tables = {
        "profit_margins": pl.DataFrame({
            "product": ["gla", "gip", "lsdb", "ci", "ti", "adb", "accdb", "dismem"],
            "margin_type": ["standard"] * 8,
            "margin": [0.10, 0.10, 0.08, 0.09, 0.07, 0.05, 0.05, 0.05],
        }),
        "admin_fees": pl.DataFrame({
            "product": ["gla", "lsdb", "lsdb", "ci", "gip", "ext_fc"],
            "variant": ["default", "accelerator", "standalone", "accelerator", "default", "default"],
            "admin": [0.05, 0.04, 0.06, 0.04, 0.05, 0.02],
        }),
        "product_limits": pl.DataFrame({
            "product": ["gip", "lsdb", "ci_new", "fc", "fc", "fc",
                        "educate_a_child", "educate_a_child"],
            "param_name": ["cash_for_loved_ones", "max_cover", "max_cover",
                           "pup_cost_death_lsdb", "pup_cost_lsdb", "pup_cost_death",
                           "profit_margin", "admin_rate"],
            "param_value": [0.02, 2_000_000.0, 3_000_000.0,
                            0.02, 0.015, 0.01, 0.12, 0.03],
        }),
        "ci_table_mapping": pl.DataFrame({
            "product_option": ["opt_x"],
            "variant_type": ["Existing"],
        }),
        "ext_fc_params": pl.DataFrame({
            "param_name": ["rate_basis", "commission_pct"],
            "param_value": [1.0, 0.05],
        }),
    }
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
    a = SimpleNamespace(tables=tables, globals=globals_)
    for k, v in kw.items():
        setattr(a, k, v)
    return a


def _ctr(scheme=None, categories=None):
    if categories is None:
        categories = pl.DataFrame({"product": [], "field": [], "value": []})
    return SimpleNamespace(scheme=scheme or _scheme(), categories=categories)


def _mixed_df():
    """4 rows: idx0/1 active covered; idx2 disabled; idx3 over-NRA."""
    return pl.DataFrame({
        "product_active": [True, True, True, True],
        "status": ["A", "A", "DB", "A"],
        "age": [30.0, 40.0, 50.0, 70.0],
        "age_nb": [30.0, 40.0, 50.0, 70.0],
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
        "waiver_premium": [3.0, 6.0, 4.5, 3.75],
        "prb_premium": [30.0, 60.0, 45.0, 30.0],
        "reinstatement_premium": [0.0] * 4,
        "profit_margin_amount": [120.0, 240.0, 180.0, 150.0],
        "basic_excl_esc": [1100.0, 2200.0, 1650.0, 1400.0],
        "reporting_incidence": [0.01, 0.02, 0.015, 0.02],
        "reporting_incidence_x_cover": [3.0, 12.0, 6.75, 7.2],
    })


def _fc_df():
    df = _mixed_df()
    return df.with_columns([
        pl.Series("primary_insured_person", ["10,000", "20,000", "15,000", "12,000"]),
        pl.Series("maximum_no_of_spouses", [2.0] * 4),
        pl.Series("pct_married", [0.5] * 4),
        pl.Series("n_children_0_13", [1.0] * 4),
        pl.Series("n_children_14_plus", [0.5] * 4),
    ])


def _ext_fc_df():
    return pl.DataFrame({
        "category_number": [1, 1, 2, 2],
        "relative_type": ["parents", "extended_family", "parents", "extended_family"],
        "band_index": [1, 1, 2, 2],
        "risk_premium_per_life_pm": [10.0, 12.0, 15.0, 18.0],
        "start_age": [18.0, 18.0, 60.0, 60.0],
        "end_age": [59.0, 59.0, 100.0, 100.0],
        "cover": [50_000.0] * 4,
    })


# --------------------------------------------------------------------------
# rate-table lookups
# --------------------------------------------------------------------------

@pytest.mark.unit
def test_profit_rate_for():
    a = _assump()
    assert inp._profit_rate_for(a, "gla") == pytest.approx(0.10)
    assert inp._profit_rate_for(a, "spouse_gla") == pytest.approx(0.10)  # maps to gla
    assert inp._profit_rate_for(a, "terminal") == pytest.approx(0.07)    # maps to ti
    assert inp._profit_rate_for(a, "fc") == 0.0                          # no mapping
    assert inp._profit_rate_for(SimpleNamespace(tables={}), "gla") == 0.0
    # non-numeric margin -> 0.0
    bad = SimpleNamespace(tables={"profit_margins": pl.DataFrame(
        {"product": ["gla"], "margin_type": ["standard"], "margin": ["x"]})})
    assert inp._profit_rate_for(bad, "gla") == 0.0


@pytest.mark.unit
def test_admin_rate_for_variants():
    a = _assump()
    assert inp._admin_rate_for(a, "gla", _scheme()) == pytest.approx(0.05)
    assert inp._admin_rate_for(a, "lsdb", _scheme(rating={"lsdb": "Accelerator"})) == pytest.approx(0.04)
    assert inp._admin_rate_for(a, "lsdb", _scheme(rating={"lsdb": "Standalone"})) == pytest.approx(0.06)
    assert inp._admin_rate_for(a, "unknown", _scheme()) == 0.0
    assert inp._admin_rate_for(SimpleNamespace(tables={}), "gla", _scheme()) == 0.0


@pytest.mark.unit
def test_rate_basis_freq_vat_helpers():
    scheme = _scheme(rate_basis_by_product={"gla": 0.9, "ci": None, "x": "abc"})
    assert inp._rate_basis_for(scheme, "gla") == pytest.approx(0.9)
    assert inp._rate_basis_for(scheme, "ci") == 1.0
    assert inp._rate_basis_for(scheme, "x") == 1.0
    assert inp._rate_basis_for(scheme, "missing") == 1.0

    assert inp._freq_divisor_for(_scheme(premium_frequency="Monthly")) == 12.0
    assert inp._freq_divisor_for(_scheme(premium_frequency="Annually")) == 1.0

    assert inp._vat_on_commission_for(_scheme(include_vat_commission="Yes")) == 0.15
    assert inp._vat_on_commission_for(_scheme(include_vat_commission="no")) == 0.0
    assert inp._vat_on_commission_for(_scheme(include_vat_commission=None)) == 0.0


@pytest.mark.unit
def test_commission_scale_for():
    sa = inp._commission_scale_for(_scheme(country_scale="South Africa"))
    assert sa == inp._BROKER_COMMISSION_SCALES["South Africa"]
    nam = inp._commission_scale_for(_scheme(country_scale="Namibia"))
    assert nam == inp._BROKER_COMMISSION_SCALES["Namibia"]
    rep = inp._commission_scale_for(_scheme(intermediary="rep"))
    assert rep == inp._REP_COMMISSION_SCALES["South Africa"]
    fallback = inp._commission_scale_for(_scheme(country_scale="Mars"))
    assert fallback == inp._BROKER_COMMISSION_SCALES["South Africa"]


@pytest.mark.unit
def test_scheme_size_threshold():
    assert inp._scheme_size_threshold(_assump()) == 400
    assert inp._scheme_size_threshold(_assump(globals={"scheme_size_large_threshold": 50})) == 50
    assert inp._scheme_size_threshold(_assump(globals={"scheme_size_large_threshold": "bad"})) == 400
    assert inp._scheme_size_threshold(SimpleNamespace()) == 400


# --------------------------------------------------------------------------
# commission maths
# --------------------------------------------------------------------------

@pytest.mark.unit
def test_tiered_commission_on_net():
    assert inp._tiered_commission_on_net(0.0, [(0.5, None)]) == 0.0
    assert inp._tiered_commission_on_net(-5.0, [(0.5, None)]) == 0.0
    # single unbounded tier: rate_on_net = 0.5/0.5 = 1 -> commission = base
    assert inp._tiered_commission_on_net(100.0, [(0.5, None)]) == pytest.approx(100.0)
    # two tiers: first bound_on_net = 50*(1-0.5)=25 -> 25*1.0; remaining 75 * (0.25/0.75)
    out = inp._tiered_commission_on_net(100.0, [(0.5, 50.0), (0.25, None)])
    assert out == pytest.approx(25.0 + 25.0)
    # fully consumed inside first bound
    out = inp._tiered_commission_on_net(20.0, [(0.5, 50.0), (0.25, None)])
    assert out == pytest.approx(20.0)
    # exhausted all bounded tiers without hitting None tier
    out = inp._tiered_commission_on_net(100.0, [(0.5, 50.0), (0.25, 10.0)])
    assert out == pytest.approx(25.0 + 7.5 * (0.25 / 0.75))


@pytest.mark.unit
def test_vat_loaded_bucket_rate():
    scale = inp._BROKER_COMMISSION_SCALES["South Africa"]
    assert inp._vat_loaded_bucket_rate(0.0, scale, 0.15) == 0.0
    assert inp._vat_loaded_bucket_rate(-1.0, scale, 0.15) == 0.0
    # small net base only touches first tier: rate = 0.075/(1-0.075*1.15)
    rate = inp._vat_loaded_bucket_rate(10_000.0, scale, 0.15)
    assert rate == pytest.approx(0.075 / (1 - 0.075 * 1.15))
    # vat=0 variant
    rate0 = inp._vat_loaded_bucket_rate(10_000.0, scale, 0.0)
    assert rate0 == pytest.approx(0.075 / (1 - 0.075))


@pytest.mark.unit
def test_resolve_commission_rate_small_scheme():
    scheme = _scheme()
    assump = _assump()
    called = []
    def provider(pkey):
        called.append(pkey)
        return 100.0
    rate = inp._resolve_commission_rate({}, scheme, assump, provider)
    assert rate == pytest.approx(0.075 / (1 - 0.075 * 1.15))
    assert called == []  # subtotal provider not consulted for small schemes


@pytest.mark.unit
def test_resolve_commission_rate_large_scheme():
    scheme = _scheme()
    assump = _assump(globals={"scheme_size_large_threshold": 3})
    gla = pl.DataFrame({"product_active": [True, True, True]})
    rate = inp._resolve_commission_rate({"gla": gla}, scheme, assump,
                                        lambda p: 1_000.0)
    # total net = 10 * 1000 = 10000 -> all in first net band (185000 bound)
    expected = (10_000.0 * (0.075 / 0.925)) / 10_000.0
    assert rate == pytest.approx(expected)

    # total_net <= 0 -> 0
    assert inp._resolve_commission_rate({"gla": gla}, scheme, assump, lambda p: 0.0) == 0.0


@pytest.mark.unit
def test_fc_commission_bucket_rate_empty_and_filled():
    scheme = _scheme()
    assump = _assump()
    ctr = _ctr(scheme)
    assert inp._fc_commission_bucket_rate({}, scheme, assump, ctr) == 0.0
    assert inp._fc_commission_bucket_rate({"fc": pl.DataFrame({"product_active": []})},
                                          scheme, assump, ctr) == 0.0
    rate = inp._fc_commission_bucket_rate({"fc": _fc_df()}, scheme, assump, ctr)
    assert rate > 0.0


@pytest.mark.unit
def test_resolve_split_mode():
    scheme = _scheme(category_split={"gla": "Risk Profile", "fc": "Flat Rand per Mem",
                                     "gip": "bogus"})
    assert inp._resolve_split_mode(scheme, "gla") == "Risk Profile"
    assert inp._resolve_split_mode(scheme, "fc") == "Flat Rand per Mem"
    assert inp._resolve_split_mode(scheme, "gip") == "Sum Assured"   # invalid -> default
    assert inp._resolve_split_mode(scheme, "unknown_pkey") == "Sum Assured"


# --------------------------------------------------------------------------
# ctr-scheme accessors
# --------------------------------------------------------------------------

@pytest.mark.unit
def test_ctr_accessors():
    assert inp._ctr_prb_loading(None, "gla") == 0.0
    assert inp._ctr_prb_loading(SimpleNamespace(scheme=None), "gla") == 0.0
    ctr = _ctr(_scheme(profit_refund_loading_by_product={"gla": 0.03, "bad": "x"}))
    assert inp._ctr_prb_loading(ctr, "gla") == pytest.approx(0.03)
    assert inp._ctr_prb_loading(ctr, "missing") == 0.0

    assert inp._ctr_rate_basis(None, "gla") == 1.0
    assert inp._ctr_rate_basis(_ctr(_scheme(rate_basis_by_product={"gla": 0.85})), "gla") == 0.85

    assert inp._ctr_vat_flag(None) is True
    assert inp._ctr_vat_flag(_ctr(_scheme(include_vat_commission="No"))) is False
    assert inp._ctr_vat_flag(SimpleNamespace(scheme=None)) is True

    scale, country, payee = inp._resolve_commission_scale(None)
    assert scale == inp._BROKER_COMMISSION_SCALES["South Africa"]
    assert country == "South Africa"
    assert payee == "Broker"
    _, _, payee = inp._resolve_commission_scale(_ctr(_scheme(intermediary="direct")))
    assert payee == "Representative"


@pytest.mark.unit
def test_resolved_priced_rate():
    assert inp._resolved_priced_rate(None, "gla", "profit_margin", 0.1) == 0.1
    ctr = _ctr(_scheme(priced_margin_overrides={
        "gla": {"profit_margin": 0.2, "empty": "", "bad": "abc"}}))
    assert inp._resolved_priced_rate(ctr, "gla", "profit_margin", 0.1) == 0.2
    assert inp._resolved_priced_rate(ctr, "gla", "empty", 0.1) == 0.1
    assert inp._resolved_priced_rate(ctr, "gla", "bad", 0.1) == 0.1
    assert inp._resolved_priced_rate(ctr, "gla", "missing", 0.1) == 0.1
    assert inp._resolved_priced_rate(ctr, "other", "profit_margin", 0.1) == 0.1


@pytest.mark.unit
def test_sum_col_and_slices():
    df = pl.DataFrame({"a": [1.0, 2.0], "product_active": [True, False],
                       "age": [30.0, 70.0], "ceasing_age": [65.0, 65.0]})
    assert inp._sum_col(df, "a") == 3.0
    assert inp._sum_col(df, "missing") == 0.0
    bad = pl.DataFrame({"s": ["x", "y"]})
    assert inp._sum_col(bad, "s", safe=True) == 0.0

    assert inp._active_slice(None) is None
    no_flag = pl.DataFrame({"a": [1]})
    assert inp._active_slice(no_flag).height == 1
    assert inp._active_slice(df).height == 1

    assert inp._tt_active_slice(None) is None
    assert inp._tt_active_slice(df).height == 1
    # missing age/ceasing -> falls back to product_active filter
    partial = pl.DataFrame({"product_active": [True, False], "a": [1, 2]})
    assert inp._tt_active_slice(partial).height == 1


# --------------------------------------------------------------------------
# tiny numeric helpers
# --------------------------------------------------------------------------

@pytest.mark.unit
def test_weighted_pct_count_avg():
    df = pl.DataFrame({"v": [10.0, 20.0], "w": [1.0, 3.0],
                       "g": ["M", "F"], "flag": [True, False], "s": ["DB", "A"]})
    assert inp._weighted(df, "v", "w") == pytest.approx(70.0 / 4.0)
    assert inp._weighted(df, "v", "missing") == 0.0
    assert inp._weighted(df, "v", "zero") == 0.0
    assert inp._pct(df, "g", "M") == 0.5
    assert inp._pct(df, "missing", "M") == 0.0
    assert inp._count_flag(df, "flag") == 1
    assert inp._count_flag(df, "s", "DB") == 1
    assert inp._count_flag(df, "missing") == 0
    assert inp._avg(df, "v") == pytest.approx(15.0)
    assert inp._avg(df, "missing") == 0.0


@pytest.mark.unit
def test_zero_and_empty_structures():
    z = inp._zero_metrics()
    assert z["n_members"] == 0 and z["total_premium"] == 0.0
    ep = inp._empty_product()
    assert set(ep["cats"].keys()) == set(range(1, 21))
    assert all(v is False for v in ep["active"].values())
    zp = inp._zero_profile()
    assert zp["membership"] == 0 and zp["claim_cover_ratio"] == 0.0


@pytest.mark.unit
def test_famcov_sum():
    assert inp._famcov_sum(pl.DataFrame({"x": [1]})) == 0.0
    df = pl.DataFrame({
        "primary_insured_person": ["10,000", "20,000"],
        "insured_spouse": [5000.0, 5000.0],
        "child_under_age_2": [None, 1000.0],
        "age": [30.0, 70.0],
        "ceasing_age": [65.0, 65.0],
    })
    # idx1 filtered out by ceasing age -> 10000 + 5000
    assert inp._famcov_sum(df) == pytest.approx(15_000.0)


# --------------------------------------------------------------------------
# _compute_metrics / _aggregate_product
# --------------------------------------------------------------------------

@pytest.mark.unit
def test_compute_metrics_math():
    df = pl.DataFrame({
        "basic_technical_premium": [100.0, 200.0],
        "aids_technical_premium": [10.0, 20.0],
        "co_premium": [5.0, 5.0],
        "annual_salary": [50_000.0, 100_000.0],
        "total_cover": [200_000.0, 400_000.0],
        "age": [30.0, 40.0],
        "gender": ["M", "F"],
    })
    m = inp._compute_metrics(df, 0.075, 0.15, profit_rate=0.10, admin_rate=0.05,
                             rate_basis=1.0, freq_divisor=12.0)
    assert m["basic_premium"] == pytest.approx(330 * 1.15 / 12)
    assert m["co_premium"] == pytest.approx(10 / 12)
    assert m["sub_total"] == pytest.approx(31.625 + 10 / 12)
    assert m["commission"] == pytest.approx(m["sub_total"] * 0.075)
    assert m["vat"] == pytest.approx(m["commission"] * 0.15)
    assert m["total_premium"] == pytest.approx(m["sub_total"] + m["commission"] + m["vat"])
    assert m["n_members"] == 2
    assert m["total_salary_annual"] == pytest.approx(150_000.0)
    assert m["total_cover"] == pytest.approx(600_000.0)
    assert m["pct_males"] == 0.5
    assert m["salary_weighted_age"] == pytest.approx(
        (30 * 50_000 + 40 * 100_000) / 150_000)


@pytest.mark.unit
def test_compute_metrics_empty_and_skip_waiver():
    m = inp._compute_metrics(pl.DataFrame({"a": []}), 0.0, 0.0, profit_rate=0.0,
                             admin_rate=0.0, rate_basis=1.0, freq_divisor=12.0)
    assert m["sub_total"] == 0.0

    df = pl.DataFrame({
        "basic_technical_premium": [120.0],
        "waiver_premium": [60.0],
        "annual_salary": [10_000.0],
        "total_cover": [10_000.0],
    })
    m1 = inp._compute_metrics(df, 0.0, 0.0, profit_rate=0.0, admin_rate=0.0,
                              rate_basis=1.0, freq_divisor=1.0)
    m2 = inp._compute_metrics(df, 0.0, 0.0, profit_rate=0.0, admin_rate=0.0,
                              rate_basis=1.0, freq_divisor=1.0, skip_waiver=True)
    assert m1["sub_total"] == pytest.approx(180.0)
    assert m2["sub_total"] == pytest.approx(120.0)


@pytest.mark.unit
def test_aggregate_product_splits_categories():
    prod = inp._aggregate_product(_mixed_df(), 0.05, 0.15, profit_rate=0.10,
                                  admin_rate=0.05, rate_basis=1.0, freq_divisor=12.0)
    assert prod["scheme"]["n_members"] == 3
    assert prod["cats"][1]["n_members"] == 2
    assert prod["cats"][2]["n_members"] == 1      # over-NRA member excluded
    assert prod["cats"][3]["n_members"] == 0
    assert prod["active"][1] is True
    assert prod["active"][3] is False
    assert "famcov" in prod["scheme"]


@pytest.mark.unit
def test_aggregate_product_without_activity_columns():
    df = pl.DataFrame({"basic_technical_premium": [1.0],
                       "annual_salary": [1.0], "total_cover": [1.0]})
    prod = inp._aggregate_product(df, 0.0, 0.0, profit_rate=0.0, admin_rate=0.0,
                                  rate_basis=1.0, freq_divisor=1.0)
    assert prod["scheme"]["n_members"] == 1
    # no category_number column -> all cats zero
    assert prod["cats"][1]["n_members"] == 0


# --------------------------------------------------------------------------
# _pup_rate_for
# --------------------------------------------------------------------------

@pytest.mark.unit
def test_pup_rate_for():
    a = _assump()
    assert inp._pup_rate_for(a, _scheme(), "gla") == 0.0          # not fc
    assert inp._pup_rate_for(a, _scheme(), "fc") == 0.0           # paid-up not active
    s = _scheme(fc_paid_up_benefits_active={1: "Yes"},
                fc_pup_death_lsdb={1: "Yes"})
    assert inp._pup_rate_for(a, s, "fc") == pytest.approx(0.02)
    s = _scheme(fc_paid_up_benefits_active={1: "yes"},
                fc_pup_lsdb={1: "Yes"})
    assert inp._pup_rate_for(a, s, "fc") == pytest.approx(0.015)
    s = _scheme(fc_paid_up_benefits_active={1: "YES"},
                fc_pup_death={1: "Yes"})
    assert inp._pup_rate_for(a, s, "fc") == pytest.approx(0.01)
    s = _scheme(fc_paid_up_benefits_active={1: "yes"})            # no pup option
    assert inp._pup_rate_for(a, s, "fc") == 0.0
    assert inp._pup_rate_for(SimpleNamespace(tables={}), _scheme(
        fc_paid_up_benefits_active={1: "yes"}, fc_pup_death={1: "yes"}), "fc") == 0.0


# --------------------------------------------------------------------------
# _reallocate_cb_basic_pooled
# --------------------------------------------------------------------------

def _prod_for_realloc(pkey="gla"):
    cats = {
        1: {"basic_premium": 60.0, "sub_total": 80.0, "total_cover": 60.0,
            "n_members": 6, "commission": 0.0, "vat": 0.0, "total_premium": 0.0},
        2: {"basic_premium": 40.0, "sub_total": 50.0, "total_cover": 40.0,
            "n_members": 4, "commission": 0.0, "vat": 0.0, "total_premium": 0.0},
    }
    return {
        "_pkey": pkey,
        "scheme": {"basic_premium": 100.0, "total_cover": 100.0,
                   "sub_total": 130.0, "commission": 13.0, "vat": 1.95,
                   "n_members": 10, "famcov": 0.0},
        "cats": cats,
    }


@pytest.mark.unit
def test_reallocate_sum_assured():
    prod = _prod_for_realloc()
    inp._reallocate_cb_basic_pooled(prod, split_mode="Sum Assured")
    # pooled rate = 1.0 per cover -> basic == cover
    assert prod["cats"][1]["basic_premium"] == pytest.approx(60.0)
    assert prod["cats"][2]["basic_premium"] == pytest.approx(40.0)
    # addons preserved: cat1 addons 20, cat2 addons 10
    assert prod["cats"][1]["sub_total"] == pytest.approx(80.0)
    assert prod["cats"][2]["sub_total"] == pytest.approx(50.0)
    # comm_pct = 13/130 = 0.1, vat_pct = 0.15
    assert prod["cats"][1]["commission"] == pytest.approx(8.0)
    assert prod["cats"][1]["vat"] == pytest.approx(1.2)
    assert prod["cats"][1]["total_premium"] == pytest.approx(89.2)
    # scheme totals recomputed for engine products
    assert prod["scheme"]["commission"] == pytest.approx(13.0)
    assert prod["scheme"]["total_premium"] == pytest.approx(130.0 + 13.0 + 1.95)


@pytest.mark.unit
def test_reallocate_risk_profile_keeps_basic():
    prod = _prod_for_realloc()
    inp._reallocate_cb_basic_pooled(prod, split_mode="Risk Profile")
    assert prod["cats"][1]["basic_premium"] == pytest.approx(60.0)
    assert prod["cats"][1]["sub_total"] == pytest.approx(80.0)


@pytest.mark.unit
def test_reallocate_bucket_rate_paths():
    prod = _prod_for_realloc()
    prod["cb_bucket_rate"] = 0.2
    inp._reallocate_cb_basic_pooled(prod)
    # bucket: comm = 0.2 * new_sub
    assert prod["cats"][1]["commission"] == pytest.approx(0.2 * 80.0)

    prod = _prod_for_realloc()
    prod["cb_bucket_rate"] = 0.2
    prod["express_over_gla"] = True
    inp._reallocate_cb_basic_pooled(prod)
    # express: comm = bucket * scheme_sub * cover_share
    assert prod["cats"][1]["commission"] == pytest.approx(0.2 * 130.0 * 0.6)


@pytest.mark.unit
def test_reallocate_flat_rand_per_mem_fc():
    prod = _prod_for_realloc(pkey="fc")
    prod["scheme"]["famcov"] = 0.0
    raw = pl.DataFrame({
        "product_active": [True] * 10,
        "age": [30.0] * 10,
        "ceasing_age": [65.0] * 10,
        "category_number": [1] * 6 + [2] * 4,
    })
    inp._reallocate_cb_basic_pooled(prod, split_mode="Flat Rand per Mem", raw_df=raw)
    # pooled_rate_per_member = 100/10 = 10 -> cat1 60, cat2 40
    assert prod["cats"][1]["basic_premium"] == pytest.approx(60.0)
    assert prod["cats"][2]["basic_premium"] == pytest.approx(40.0)


@pytest.mark.unit
def test_reallocate_noop_when_no_cover():
    prod = _prod_for_realloc()
    prod["scheme"]["total_cover"] = 0.0
    inp._reallocate_cb_basic_pooled(prod)
    assert prod["cats"][1]["basic_premium"] == pytest.approx(60.0)  # unchanged


# --------------------------------------------------------------------------
# profiles
# --------------------------------------------------------------------------

@pytest.mark.unit
def test_po_profile_gla():
    df = _mixed_df()
    p = inp._po_profile_gla(df)
    assert p["membership"] == 3
    assert p["over_nra"] == 1
    assert p["pct_males"] == pytest.approx(2 / 3)
    assert p["expected_claims"] == pytest.approx(0.006)

    assert inp._po_profile_gla(None)["membership"] == 0
    assert inp._po_profile_gla(pl.DataFrame({"a": []}))["membership"] == 0

    with pytest.raises(KeyError, match="GLA Pricing Outputs profile"):
        inp._po_profile_gla(pl.DataFrame({"age": [1.0]}))


@pytest.mark.unit
def test_po_profile_lsdb():
    df = _mixed_df()
    p = inp._po_profile_lsdb(df)
    assert p["membership"] == 2          # active, not disabled, not over-NRA, cover>0
    assert p["over_nra"] == 1
    assert p["disabled"] == 1
    assert p["salary_bill"] == pytest.approx(300_000.0)
    assert p["total_cover"] == pytest.approx(900_000.0)
    assert p["expected_claims"] == pytest.approx(0.003)
    assert p["expected_claim_size"] == pytest.approx(1500.0 / 0.003)

    assert inp._po_profile_lsdb(None)["membership"] == 0
    with pytest.raises(KeyError, match="LSDB Pricing Outputs profile"):
        inp._po_profile_lsdb(pl.DataFrame({"age": [1.0]}))


@pytest.mark.unit
def test_po_profile_generic():
    df = _mixed_df().with_columns(
        pl.Series("over_nra", [False, False, False, True]))
    p = inp._po_profile(df, rate_basis=0.5)
    assert p["membership"] == 4
    assert p["over_nra"] == 1                     # over_nra flag column counted
    assert p["disabled"] == 1                     # status == "DB"
    assert p["expected_claims"] == pytest.approx(0.005)
    assert p["incidence_rate"] == pytest.approx(0.005 * 1000 / 4)
    assert p["expected_claim_size"] == pytest.approx(4290.0 / 0.01)
    assert p["claim_cover_ratio"] > 0
    assert inp._po_profile(None)["membership"] == 0


@pytest.mark.unit
def test_po_profile_fc():
    p = inp._po_profile_fc(_fc_df())
    assert p["membership"] == 3
    assert p["over_nra"] == 1
    assert p["disabled"] == 1
    assert p["total_cover_main"] == pytest.approx(45_000.0)   # comma-stripped strings
    assert p["avg_cover_main"] == pytest.approx(15_000.0)
    # expected claims = sum(qx * (pct_married*spouses + kids + 1)) over active
    assert p["expected_claims"] == pytest.approx(0.006 * 3.5)
    assert p["incidence_rate"] == pytest.approx(0.006 * 3.5 * 1000 / 3)


@pytest.mark.unit
def test_po_profile_fc_missing_composition_cols():
    df = _mixed_df()  # no qx/spouse/children cols needed? qx present but not spouse cols
    p = inp._po_profile_fc(df)
    assert p["membership"] == 3
    assert p["expected_claims"] == 0.0


@pytest.mark.unit
def test_po_profile_gip_and_tip():
    df = _mixed_df()
    gip = inp._po_profile_gip(df)
    assert gip["membership"] == 4
    assert gip["over_nra"] == 1                       # age_nb > ceasing_age
    assert gip["total_cover"] == pytest.approx(1_710_000.0 * 12)
    assert gip["expected_claims"] == pytest.approx(0.065)
    assert gip["expected_claim_size"] > 0

    tip = inp._po_profile_tip(df)
    assert tip["membership"] == 4
    assert tip["expected_claims"] == pytest.approx(0.065)

    # no reporting incidence columns -> zeros
    df2 = _mixed_df().drop(["reporting_incidence", "reporting_incidence_x_cover"])
    assert inp._po_profile_gip(df2)["expected_claims"] == 0.0


# --------------------------------------------------------------------------
# margins & buildups
# --------------------------------------------------------------------------

@pytest.mark.unit
def test_po_margins_and_priced():
    a = _assump()
    df = _mixed_df()
    m = inp._po_margins(a, "gla", df=df, ctr=None)
    assert m["profit_margin"] == pytest.approx(0.10)
    assert m["admin"] == pytest.approx(0.05)
    # co = 69 / net(7400) on the full df
    assert m["conversion_option"] == pytest.approx(69.0 / 7400.0)
    assert m["rate_basis"] == 1.0

    ctr = _ctr(_scheme(priced_margin_overrides={"gla": {"profit_margin": 0.2}},
                       rate_basis_by_product={"gla": 0.9}))
    pm = inp._po_priced_margins(a, "gla", df=df, ctr=ctr)
    assert pm["profit_margin"] == pytest.approx(0.2)
    assert pm["rate_basis"] == pytest.approx(0.9)


@pytest.mark.unit
def test_addon_pct_of_net_risk():
    df = _mixed_df()
    assert inp._addon_pct_of_net_risk(df, "co_premium") == pytest.approx(69.0 / 7400.0)
    assert inp._addon_pct_of_net_risk(df, "missing") == 0.0
    assert inp._addon_pct_of_net_risk(None, "co_premium") == 0.0
    zero_net = pl.DataFrame({"basic_technical_premium": [0.0],
                             "aids_technical_premium": [0.0],
                             "co_premium": [5.0]})
    assert inp._addon_pct_of_net_risk(zero_net, "co_premium") == 0.0


@pytest.mark.unit
def test_assump_margin_helpers():
    a = _assump()
    assert inp._assump_profit_margin(a, "gla") == pytest.approx(0.10)
    assert inp._assump_profit_margin(None, "gla") == 0.0
    assert inp._assump_profit_margin(SimpleNamespace(tables={}), "gla") == 0.0
    assert inp._assump_profit_margin(a, "fc") == 0.0        # unmapped pkey

    assert inp._assump_admin(a, _ctr(), "gla") == pytest.approx(0.05)
    assert inp._assump_admin(None, _ctr(), "gla") == 0.0
    assert inp._admin_variant_for(_ctr(_scheme(rating={"lsdb": "Standalone"})), "lsdb") == "standalone"
    assert inp._admin_variant_for(_ctr(_scheme(rating={"ci": "x"})), "ci") == "accelerator"
    assert inp._admin_variant_for(None, "gla") == "default"


@pytest.mark.unit
def test_product_basis_subtotal():
    df = _mixed_df()
    a = _assump()
    sub = inp._product_basis_subtotal(df, "gla", a, _ctr())
    # net 7400 + addons (69+34.5+17.25+165) + profit 740 + admin 370
    assert sub == pytest.approx(7400 + 285.75 + 740 + 370)
    assert inp._product_basis_subtotal(None, "gla", a, _ctr()) == 0.0
    assert inp._product_basis_subtotal(pl.DataFrame({"a": []}), "gla", a, _ctr()) == 0.0


@pytest.mark.unit
def test_compute_scheme_commission_rate_flat_and_bucketed():
    a = _assump()
    all_results = {"gla": _mixed_df()}
    rates = inp._compute_scheme_commission_rate(all_results, a, _ctr())
    flat = 0.075 / (1 - 0.075 * 1.15)
    for p in inp.PO_PRODUCTS:
        assert rates[p] == pytest.approx(flat)

    # empty results -> all zero
    rates = inp._compute_scheme_commission_rate({}, a, _ctr())
    assert all(v == 0.0 for v in rates.values())

    # large scheme -> bucketed
    a2 = _assump(globals={"scheme_size_large_threshold": 3})
    rates2 = inp._compute_scheme_commission_rate(all_results, a2, _ctr())
    assert rates2["gla"] > 0.0
    assert rates2["spouse_gla"] == 0.0        # empty bucket -> rate 0


@pytest.mark.unit
def test_po_buildup_gla():
    df = _mixed_df()
    a = _assump()
    bu = inp._po_buildup(df, "gla", a, _ctr(), commission_rate=0.1, vat_pct=0.15)
    assert bu["tech"][0] == ("Net Risk Premium", 7400.0, None)
    labels = [r[0] for r in bu["tech"]]
    assert labels[:5] == ["Net Risk Premium", "Conversion Option ", "Cover to Continue",
                          "Waiver of Premium", "PRO Premium "]
    assert labels[-1] == "Profit "
    assert bu["tech"][-1][1] == pytest.approx(690.0)      # profit_margin_amount sum
    basis_labels = [r[0] for r in bu["basis"]]
    assert basis_labels[-1] == "Gross Risk Prem "
    assert basis_labels[-2] == "VAT on Commission"
    gross = bu["basis"][-1][1]
    assert gross > 0
    assert bu["salary"] == pytest.approx(570_000.0)
    assert bu["cover"] == pytest.approx(1_710_000.0)
    assert bu["rate_basis"] == 1.0

    # empty df
    empty = inp._po_buildup(None, "gla", a, _ctr())
    assert empty == {"tech": [], "basis": [], "salary": 0.0, "cover": 0.0}


@pytest.mark.unit
def test_po_buildup_express_over_gla():
    df = _mixed_df()
    a = _assump()
    ctr = _ctr(_scheme(express_over_gla={"gla": True}))
    bu = inp._po_buildup(df, "gla", a, ctr, gla_salary=999.0, gla_cover=888.0)
    assert bu["salary"] == 999.0
    assert bu["cover"] == 888.0


@pytest.mark.unit
def test_po_buildup_fc_and_margins_fc():
    a = _assump()
    ctr = _ctr()
    bu = inp._po_buildup_fc(_fc_df(), a, ctr, commission_rate=0.05, vat_pct=0.15)
    tech_labels = [r[0] for r in bu["tech"]]
    assert "Cover to Continue" in tech_labels and "Paid-Up Cover" in tech_labels
    basis_labels = [r[0] for r in bu["basis"]]
    assert basis_labels[-1] == "Gross Risk Prem "
    assert bu["cover"] == pytest.approx(1_350_000.0)

    m = inp._po_margins_fc(_fc_df(), a, ctr)
    assert m["profit_margin"] == pytest.approx(0.10)
    assert m["admin"] == pytest.approx(0.05)
    assert "pup_cost" in m


@pytest.mark.unit
def test_po_gip_tip_margins_and_buildups():
    a = _assump()
    ctr = _ctr()
    df = _mixed_df()

    mg = inp._po_margins_gip(df, a, ctr)
    assert mg["cash_for_loved_ones"] == pytest.approx(0.02)
    assert mg["profit_margin"] == pytest.approx(0.10)
    assert mg["admin"] == pytest.approx(0.10)

    bu = inp._po_buildup_gip(df, a, ctr)
    assert [r[0] for r in bu["tech"]][0] == "Net Risk Premium"
    assert [r[0] for r in bu["basis"]][-1] == "Gross Risk Prem "
    assert bu["cover"] == pytest.approx(1_710_000.0 * 12)

    mt = inp._po_margins_tip(df, a, ctr)
    assert mt["admin"] == pytest.approx(0.10)
    but = inp._po_buildup_tip(df, a, ctr)
    assert [r[0] for r in but["basis"]][-1] == "Gross Risk Prem "


@pytest.mark.unit
def test_education_helpers():
    a = _assump()
    assert inp._profit_rate_for_education(a, "educate_a_child") == pytest.approx(0.12)
    assert inp._admin_rate_for_education(a, "educate_a_child") == pytest.approx(0.03)
    assert inp._profit_rate_for_education(None, "educate_a_child") == 0.0
    assert inp._admin_rate_for_education(SimpleNamespace(tables={}), "x") == 0.0

    m = inp._po_margins_education(a, "educate_a_child")
    assert m["profit_margin"] == pytest.approx(0.12)
    assert m["admin"] == pytest.approx(0.03)

    df = _mixed_df()
    bu = inp._po_buildup_education(df, "educate_a_child", a, _ctr())
    assert bu["tech"][0][0] == "Net Risk Premium"
    assert bu["salary"] == pytest.approx(570_000.0)
    # express variant
    bu2 = inp._po_buildup_education(df, "educate_a_child", a, _ctr(),
                                    express_over_gla=True, gla_salary=1.0, gla_cover=2.0)
    assert bu2["salary"] == 1.0
    assert inp._po_buildup_education(None, "x", a, _ctr())["tech"] == []


# --------------------------------------------------------------------------
# ext_fc helpers
# --------------------------------------------------------------------------

@pytest.mark.unit
def test_ext_fc_row_calc():
    out = inp._ext_fc_row_calc(100.0, 0.10, 0.05, 0.075, 0.15)
    assert out["risk_premium"] == 100.0
    assert out["profit"] == pytest.approx(10.0)
    assert out["admin"] == pytest.approx(5.0)
    # comm_gross = 0.075/0.925 -> initial_commission = 115 * 0.081081
    assert out["initial_commission"] == pytest.approx(115.0 * 0.075 / 0.925)
    assert out["vat"] == pytest.approx((115.0 + out["initial_commission"]) * 0.15 / 0.85)
    assert out["gross"] == pytest.approx(115.0 + out["initial_commission"] + out["vat"])

    # commission >= 1 -> 0 gross rate
    out = inp._ext_fc_row_calc(100.0, 0.0, 0.0, 1.0, 0.0)
    assert out["initial_commission"] == 0.0
    assert out["gross"] == pytest.approx(100.0)


@pytest.mark.unit
def test_ext_fc_age_band_label():
    assert inp._ext_fc_age_band_label(18, 59) == "18 to 59"
    assert inp._ext_fc_age_band_label(60, 100) == "60+"
    assert inp._ext_fc_age_band_label(60.4, 99.6) == "60+"
    assert inp._ext_fc_age_band_label(18.4, 59.6) == "18 to 60"


@pytest.mark.unit
def test_ext_fc_population():
    scheme = _scheme(ext_fc_pct_male={1: 0.5, 2: 0.6},
                     ext_fc_avg_salary={1: 123_000.0},
                     ext_fc_parents_selected={1: "Yes"},
                     ext_fc_extended_family_selected={1: "yes"})
    pop = inp._ext_fc_population({"fc": _fc_df()}, scheme)
    assert pop[1]["n_members"] == 2            # fc cat 1 active rows
    assert pop[1]["salary_bill"] == pytest.approx(300_000.0)
    assert pop[1]["pct_male"] == pytest.approx(0.5)
    assert pop[1]["avg_salary"] == pytest.approx(123_000.0)
    assert pop[1]["parents_selected"] is True
    assert pop[1]["extended_family_selected"] is True
    assert pop[2]["pct_male"] == pytest.approx(0.6)
    assert pop[2]["avg_salary"] is None
    assert pop[2]["parents_selected"] is False


@pytest.mark.unit
def test_ext_fc_rate_rows():
    df = _ext_fc_df()
    rows = inp._ext_fc_rate_rows(df, 1, "parents", 0.10, 0.05, 0.075, 0.15)
    assert len(rows) == 1
    assert rows[0]["age_band"] == "18 to 59"
    assert rows[0]["cover"] == pytest.approx(50_000.0)
    assert rows[0]["risk_premium"] == pytest.approx(10.0)
    rows2 = inp._ext_fc_rate_rows(df, 2, "extended_family", 0.0, 0.0, 0.0, 0.0)
    assert rows2[0]["age_band"] == "60+"
    assert rows2[0]["gross"] == pytest.approx(18.0)


@pytest.mark.unit
def test_cb_ext_fc():
    scheme = _scheme(ext_fc_pct_male={1: 0.5},
                     ext_fc_parents_selected={1: "Yes"},
                     ext_fc_extended_family_selected={1: "Yes"})
    a = _assump()
    ctr = _ctr(scheme)
    all_results = {"ext_fc": _ext_fc_df(), "fc": _fc_df()}
    out = inp._cb_ext_fc(all_results, scheme, a, ctr)
    assert out["_is_ext_fc"] is True
    assert out["_display_title"] == "Extended Family Cover"
    assert sorted(out["categories"].keys()) == [1, 2]
    cat1 = out["categories"][1]
    assert cat1["parents_selected"] is True
    assert len(cat1["parents_standard"]) == 1
    assert len(cat1["parents_priced"]) == 1
    assert len(cat1["extended_family_standard"]) == 1
    assert out["profit_rate_standard"] == pytest.approx(0.10)
    assert out["admin_rate_standard"] == pytest.approx(0.02)
    assert out["rate_basis_standard"] == pytest.approx(1.0)
    assert out["commission_pct_standard"] == pytest.approx(0.05)

    # no ext_fc df -> None
    assert inp._cb_ext_fc({}, scheme, a, ctr) is None
    assert inp._cb_ext_fc({"ext_fc": pl.DataFrame({"category_number": []})}, scheme, a, ctr) is None


# --------------------------------------------------------------------------
# gather_cb_data / gather_po_data (integration over the helpers)
# --------------------------------------------------------------------------

@pytest.mark.unit
def test_gather_cb_data_minimal():
    cb = inp.gather_cb_data({"gla": _mixed_df()}, _scheme(), _assump())
    assert set(cb.keys()) >= set(inp.CB_PRODUCTS)
    gla = cb["gla"]
    assert gla["scheme"]["n_members"] == 3
    assert gla["scheme"]["total_cover"] == pytest.approx(1_350_000.0)
    assert gla["scheme"]["total_premium"] > gla["scheme"]["sub_total"] > 0
    assert gla["cats"][1]["n_members"] == 2
    assert gla["active"][1] is True
    assert gla["freq_divisor"] == 12.0
    assert gla["express_over_gla"] is False
    # products with no data -> zero product dict
    assert cb["ci"]["scheme"]["n_members"] == 0
    # no ctr -> gip/tip/education/ext_fc special builders skipped
    assert "ext_fc" not in cb


@pytest.mark.unit
def test_gather_cb_data_full_with_ctr():
    scheme = _scheme(education_product="educate_a_child",
                     ext_fc_pct_male={1: 0.5},
                     ext_fc_parents_selected={1: "Yes"},
                     ext_fc_extended_family_selected={1: "Yes"})
    ctr = _ctr(scheme)
    all_results = {
        "gla": _mixed_df(),
        "lsdb": _mixed_df(),
        "fc": _fc_df(),
        "gip": _mixed_df(),
        "tip": _mixed_df(),
        "educate_a_child": _mixed_df(),
        "ext_fc": _ext_fc_df(),
    }
    cb = inp.gather_cb_data(all_results, scheme, _assump(), ctr=ctr)
    assert cb["gip"]["scheme"]["n_members"] == 4
    assert cb["gip"]["scheme"]["total_premium"] > 0
    assert cb["tip"]["scheme"]["n_members"] == 4
    assert cb["education"]["_pkey"] == "education"
    assert cb["education"]["_display_title"] == "Educate a Child"
    assert cb["education"]["scheme"]["n_members"] == 3
    assert cb["ext_fc"]["_is_ext_fc"] is True
    assert cb["fc"]["scheme"]["n_members"] == 3


@pytest.mark.unit
def test_gather_cb_data_gla_crossproduct_fixup():
    scheme = _scheme(rating={"lsdb": "Standalone"})
    # all four lsdb rows inside NRA -> 4 active members vs gla's 3
    lsdb_df = _mixed_df().with_columns(pl.Series("age", [40.0] * 4))
    cb = inp.gather_cb_data({"gla": _mixed_df(), "lsdb": lsdb_df},
                            scheme, _assump())
    # gla inherits standalone-lsdb scheme salary/members
    assert cb["gla"]["scheme"]["n_members"] == 4
    assert cb["gla"]["scheme"]["total_salary_annual"] == pytest.approx(
        cb["lsdb"]["scheme"]["total_salary_annual"])


@pytest.mark.unit
def test_gather_po_data_minimal():
    scheme = _scheme()
    po = inp.gather_po_data({"gla": _mixed_df()}, scheme, assump=_assump())
    assert po["products"] == list(inp.PO_PRODUCTS)
    assert po["profile"]["gla"]["membership"] == 3
    assert po["profile"]["gla"]["over_nra"] == 1
    assert po["profile"]["ci"]["membership"] == 0
    assert po["scheme"]["industry"] == "Gold"
    assert po["scheme"]["region"] == "Gauteng"
    assert po["vat_applied"] is True
    assert set(po["buildup"].keys()) == set(inp.PO_PRODUCTS)
    assert po["buildup"]["gla"]["tech"][0][0] == "Net Risk Premium"
    assert po["eoh"]["gla"]["max_cover"] == pytest.approx(600_000.0)
    # lsdb present? no -> profile zero
    assert po["eoh"]["lsdb"]["eoh_limit"] is None


@pytest.mark.unit
def test_gather_po_data_full_with_ctr():
    scheme = _scheme(education_product="educate_a_child",
                     ext_fc_pct_male={1: 0.5},
                     ext_fc_parents_selected={1: "Yes"})
    ctr = _ctr(scheme)
    all_results = {
        "gla": _mixed_df(),
        "lsdb": _mixed_df(),
        "ci": _mixed_df(),
        "spouse_gla": _mixed_df(),
        "fc": _fc_df(),
        "gip": _mixed_df(),
        "tip": _mixed_df(),
        "educate_a_child": _mixed_df(),
        "ext_fc": _ext_fc_df(),
    }
    po = inp.gather_po_data(all_results, scheme, assump=_assump(), ctr=ctr)
    for p in ("fc", "gip", "tip", "education"):
        assert p in po["products"]
        assert p in po["buildup"]
    assert po["profile"]["education"]["_display_title"] == "Educate a Child"
    assert po["profile"]["fc"]["membership"] == 3
    assert po["profile"]["gip"]["membership"] == 4
    assert po["margins"]["gip"]["cash_for_loved_ones"] == pytest.approx(0.02)
    assert po["ext_fc_data"]["_is_ext_fc"] is True
    assert po["commission_rate"]["gla"] > 0
    # spouse products hardcode disabled=0
    assert po["profile"]["spouse_gla"]["disabled"] == 0


@pytest.mark.unit
def test_cb_gip_and_tip_direct():
    scheme = _scheme()
    a = _assump()
    ctr = _ctr(scheme)
    res = {"gip": _mixed_df(), "tip": _mixed_df()}

    gip = inp._cb_gip(res, scheme, a, ctr)
    assert gip["_pkey"] == "gip"
    assert set(gip["cats"].keys()) == {1, 2}
    assert gip["scheme"]["total_premium"] > 0
    assert gip["scheme"]["commission"] > 0
    assert gip["scheme"]["vat"] > 0

    tip = inp._cb_tip(res, scheme, a, ctr)
    assert tip["_pkey"] == "tip"
    assert set(tip["cats"].keys()) == {1, 2}
    assert tip["scheme"]["total_premium"] > 0

    # risk profile split keeps per-cat basic
    gip2 = inp._cb_gip(res, scheme, a, ctr, split_mode="Risk Profile")
    assert gip2["scheme"]["total_premium"] > 0
    tip2 = inp._cb_tip(res, scheme, a, ctr, split_mode="Risk Profile")
    assert tip2["scheme"]["total_premium"] > 0

    assert inp._cb_gip({}, scheme, a, ctr) is None
    assert inp._cb_tip({}, scheme, a, ctr) is None
    inactive = {"tip": _mixed_df().with_columns(pl.lit(False).alias("product_active"))}
    assert inp._cb_tip(inactive, scheme, a, ctr) is None


@pytest.mark.unit
def test_cb_education_direct():
    scheme = _scheme(education_product="educate_a_child")
    a = _assump()
    out = inp._cb_education({"educate_a_child": _mixed_df()}, scheme, a, _ctr(scheme))
    assert out is not None
    assert out["_display_title"] == "Educate a Child"
    assert out["scheme"]["n_members"] == 3
    assert out["cb_bucket_rate"] > 0

    assert inp._cb_education({}, scheme, a) is None
    assert inp._cb_education({"educate_a_child": _mixed_df()}, _scheme(), a) is None
