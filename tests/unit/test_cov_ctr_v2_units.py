"""Unit-level coverage for ctr_loader.ctr_loader_v2 internals.

Uses lightweight FakeWorksheet objects (same pattern as
test_ctr_loader_parsing.py) to hit every reader/section function without
needing real Excel files.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import date

import polars as pl
import pytest
from ctr_loader import ctr_loader_v2 as cl
from ctr_loader.ctr_schemas import SchemeRecord
from ctr_loader.products_registry import PRODUCT_REGISTRY

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# test doubles
# ---------------------------------------------------------------------------

class FakeWorksheet:
    def __init__(self, rows):
        self.rows = rows
        self.nrows = len(rows)
        self.ncols = max(map(len, rows), default=0)

    def cell_value(self, row, column):
        if row < 0 or row >= self.nrows or column < 0 or column >= len(self.rows[row]):
            return ""
        return self.rows[row][column]


def _ws(cells: dict[tuple[int, int], object], nrows=None, ncols=None) -> FakeWorksheet:
    max_r = max((r for r, _ in cells), default=-1)
    max_c = max((c for _, c in cells), default=-1)
    nrows = nrows if nrows is not None else max_r + 1
    ncols = ncols if ncols is not None else max_c + 1
    rows = [[""] * ncols for _ in range(nrows)]
    for (r, c), v in cells.items():
        rows[r][c] = v
    return FakeWorksheet(rows)


class FakeWorkbook:
    def __init__(self, sheets: dict[str, FakeWorksheet]):
        self._sheets = sheets
        self.datemode = 0
        self.unloaded = []

    def sheet_by_name(self, name):
        return self._sheets[name]

    def unload_sheet(self, name):
        self.unloaded.append(name)


# ---------------------------------------------------------------------------
# _CalamineSheet
# ---------------------------------------------------------------------------

def test_calamine_sheet_bounds():
    sheet = cl._CalamineSheet([[1, "a"], [2]])
    assert sheet.nrows == 2
    assert sheet.ncols == 2
    assert sheet.cell_value(0, 0) == 1
    assert sheet.cell_value(0, 1) == "a"
    assert sheet.cell_value(1, 1) == ""     # ragged row
    assert sheet.cell_value(5, 0) == ""     # row out of range
    assert sheet.cell_value(0, 9) == ""     # col out of range
    empty = cl._CalamineSheet([])
    assert empty.nrows == 0 and empty.ncols == 0


# ---------------------------------------------------------------------------
# _coerce / _to_bool / _safe_cell_value / _normalise_prb_pct
# ---------------------------------------------------------------------------

def test_coerce_each_kind():
    assert cl._coerce("text", "  hi ", 0) == "hi"
    assert cl._coerce("int", "42", 0) == 42
    assert cl._coerce("float", "1.5", 0) == 1.5
    assert cl._coerce("pct", "25%", 0) == pytest.approx(0.25)
    assert cl._coerce("date", 46023, 0) == date(2026, 1, 1)
    sentinel = object()
    assert cl._coerce("unknown-kind", sentinel, 0) is sentinel  # passthrough


@pytest.mark.parametrize("raw,expected", [
    ("Yes", True), ("yes", True), ("Y", True), ("true", True), ("1", True),
    ("No", False), ("n", False), ("", False), (None, False), ("0", False),
    ("banana", False), (123, False),
])
def test_to_bool(raw, expected):
    assert cl._to_bool(raw) is expected


def test_safe_cell_value():
    ws = _ws({(0, 0): "x"})
    assert cl._safe_cell_value(ws, 0, 0) == "x"
    assert cl._safe_cell_value(ws, -1, 0) is None
    assert cl._safe_cell_value(ws, 0, -1) is None
    assert cl._safe_cell_value(ws, 5, 0) is None
    assert cl._safe_cell_value(ws, 0, 5) is None


@pytest.mark.parametrize("raw,expected", [
    (None, None), ("", None), ("abc", None),
    (0.15, 0.15), (50, 0.5), (1.0, 1.0), ("25%", 0.25), (150, 1.5),
])
def test_normalise_prb_pct(raw, expected):
    assert cl._normalise_prb_pct(raw) == expected


# ---------------------------------------------------------------------------
# label -> product key
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("label,expected", [
    ("GLA", "gla"),
    ("Extended Family Cover", "ext_fc"),
    ("Ext FC", "ext_fc"),
    ("Spouse - GLA", "spouse_gla"),
    ("Educate a Child", "educate_a_child"),
    ("Something Unmapped", "something_unmapped"),
])
def test_product_key_from_label(label, expected):
    assert cl._product_key_from_label(label) == expected


# ---------------------------------------------------------------------------
# _pick_variant
# ---------------------------------------------------------------------------

def _scheme_with_rating(rating: dict) -> SchemeRecord:
    return SchemeRecord(rating=rating)


def test_pick_variant_yes_no():
    spec = PRODUCT_REGISTRY["gla"]
    warns = []
    assert cl._pick_variant(spec, _scheme_with_rating({"gla": "Yes"}), warns) == "default"
    assert cl._pick_variant(spec, _scheme_with_rating({"gla": "no"}), warns) is None
    assert cl._pick_variant(spec, _scheme_with_rating({}), warns) is None
    assert warns == []


def test_pick_variant_accelerator_standalone():
    spec = PRODUCT_REGISTRY["lsdb"]
    warns = []
    assert cl._pick_variant(spec, _scheme_with_rating({"lsdb": "Accelerator"}), warns) == "accelerator"
    assert cl._pick_variant(spec, _scheme_with_rating({"lsdb": "Standalone"}), warns) == "standalone"
    assert cl._pick_variant(spec, _scheme_with_rating({"lsdb": "No"}), warns) is None
    assert cl._pick_variant(spec, _scheme_with_rating({"lsdb": None}), warns) is None
    assert warns == []


def test_pick_variant_unexpected_rating_warns_and_defaults():
    spec = PRODUCT_REGISTRY["ci"]
    warns = []
    assert cl._pick_variant(spec, _scheme_with_rating({"ci": "Weird"}), warns) == "accelerator"
    assert warns == ["[ci] unexpected rating 'Weird', defaulting to accelerator"]


# ---------------------------------------------------------------------------
# ext-FC age band parser
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    (None, []),
    (123, []),
    ("", []),
    ("60,70,50000", [{"start_age": 60.0, "end_age": 70.0, "cover": 50000.0}]),
    (
        "60,70,50000; 70,80,40000",
        [
            {"start_age": 60.0, "end_age": 70.0, "cover": 50000.0},
            {"start_age": 70.0, "end_age": 80.0, "cover": 40000.0},
        ],
    ),
    ("60,70", []),                    # wrong arity
    ("a,b,c", []),                    # non-numeric
    ("60,,50000", []),                # missing end age -> dropped
    (";;", []),                       # empty chunks
    ("60,70,50000;junk;70,80,40000", [
        {"start_age": 60.0, "end_age": 70.0, "cover": 50000.0},
        {"start_age": 70.0, "end_age": 80.0, "cover": 40000.0},
    ]),
])
def test_parse_ext_fc_age_band_string(raw, expected):
    assert cl._parse_ext_fc_age_band_string(raw) == expected


# ---------------------------------------------------------------------------
# CatInput-Save small readers
# ---------------------------------------------------------------------------

def test_read_spouse_source_flag():
    assert cl._read_spouse_source_flag(_ws({(0, 61): "Yes"})) is True
    assert cl._read_spouse_source_flag(_ws({(0, 61): " no "})) is False
    assert cl._read_spouse_source_flag(_ws({(0, 0): "x"})) is False   # sheet too small
    assert cl._read_spouse_source_flag(_ws({(0, 61): ""})) is False


def test_read_product_options():
    cells = {(r, 35): "Yes" if r % 2 == 0 else "No" for r in range(26, 35)}
    ws = _ws(cells)
    express = cl._read_product_options(ws)
    assert set(express) == set(cl._PRODUCT_OPTION_ORDER)
    assert express["gla"] is True           # row 26 -> "Yes"
    assert express["lsdb"] is False         # row 27 -> "No"
    assert express["terminal"] is False     # row 31 -> "No"
    assert express["dismemberment"] is True  # row 34 -> "Yes"
    # short sheet -> all False
    short = cl._read_product_options(_ws({(0, 0): "x"}))
    assert all(v is False for v in short.values())


def test_read_category_split():
    ws = _ws({(26, 36): "Per Category", (27, 36): " Combined "})
    assert cl._read_category_split(ws) == {
        "gla": "Per Category", "gip": "Combined", "tip": "", "fc": "",
    }
    short = cl._read_category_split(_ws({(0, 0): "x"}))
    assert short == {"gla": "", "gip": "", "tip": "", "fc": ""}


def test_read_education_express_over_gla():
    assert cl._read_education_express_over_gla(_ws({(36, 35): "Yes"})) is True
    assert cl._read_education_express_over_gla(_ws({(36, 35): "No"})) is False
    assert cl._read_education_express_over_gla(_ws({(0, 0): "x"})) is False


def test_read_profit_refund_loading():
    cells = {(0, 19): "Yes", (0, 20): 10.0, (0, 21): "Yes", (0, 24): "y", (0, 25): "No"}
    assert cl._read_profit_refund_loading(_ws(cells)) == {
        "gla": pytest.approx(0.1), "gip": pytest.approx(0.1),
    }
    # overall flag off -> empty
    assert cl._read_profit_refund_loading(_ws({(0, 19): "No", (0, 20): 10.0})) == {}
    # missing pct -> empty
    assert cl._read_profit_refund_loading(_ws({(0, 19): "Yes", (0, 21): "Yes"})) == {}
    # bad pct -> empty
    assert cl._read_profit_refund_loading(
        _ws({(0, 19): "Yes", (0, 20): "abc", (0, 21): "Yes"})
    ) == {}


def test_read_rate_basis():
    ws = _ws({(2, 80): 2.5, (2, 94): 0.9}, nrows=3)
    basis = cl._read_rate_basis(ws)
    assert basis["gla"] == pytest.approx(2.5)
    assert basis["tip"] == pytest.approx(0.9)
    assert basis["fc"] == pytest.approx(1.0)   # missing -> default 1.0
    assert set(basis) == set(cl._RATE_BASIS_PRODUCT_COLS)


# ---------------------------------------------------------------------------
# scheme details parsing
# ---------------------------------------------------------------------------

def test_find_section_bounds_and_basic_fees():
    cells = {
        (0, 0): "Scheme Details",
        (0, 10): "Rating Selections",
        (0, 15): "Add eSwatini",
        (0, 20): "Fee Structure",
        (4, 10): "GLA", (5, 10): 250.0,
        (4, 11): "Not A Product", (5, 11): 999.0,
        (4, 12): "FC", (5, 12): 100.0,
    }
    ws = _ws(cells, nrows=6)
    bounds = cl._find_section_bounds(ws)
    assert bounds["Scheme Details"] == (0, 10)
    assert bounds["Rating Selections"] == (10, 15)
    assert bounds["Add eSwatini"] == (15, 20)
    assert bounds["Fee Structure"] == (20, ws.ncols)

    fees = cl._parse_basic_fees(ws)
    assert fees == {"gla": 250.0, "fc": 100.0}   # non-product label ignored
    # too few rows -> {}
    assert cl._parse_basic_fees(_ws({(0, 0): "x"})) == {}


def test_parse_scheme_details_full():
    cells = {
        (2, 0): "Requote", (2, 1): "GRP", (2, 2): 555,
        (2, 3): "Doer", (2, 4): "Checker", (2, 5): "Scheme X",
        (1, 6): "Scheme Code - GLA", (2, 6): "G1",
        (2, 23): 46023, (2, 24): "15/02/2026",
        (2, 31): "SA", (2, 32): 0.4, (2, 35): 0.01,
        (1, 51): "KZN", (2, 51): 0.6,
        (2, 62): "Total Cost", (2, 64): 0.08,
        (0, 66): "Rating Selections",
        (1, 66): "GLA", (2, 66): "Yes",
        (1, 67): "LSDB", (2, 67): "Standalone",
        (1, 68): "Not A Product", (2, 68): "ignored",
        (0, 70): "Pricing Basis", (1, 70): "GIP", (2, 70): 0.02,
        (0, 72): "Fees Priced", (1, 72): "TIP", (2, 72): 99.0,
        (0, 74): "Add eSwatini", (2, 74): 0.03,
        (4, 76): "GIP", (5, 76): 111.0,
    }
    scheme = cl._parse_scheme_details(_ws(cells, nrows=6), datemode=0)

    assert scheme.quote_type == "Requote"
    assert scheme.quote_group == "GRP"
    assert scheme.quote_number == 555
    assert scheme.scheme_name == "Scheme X"
    assert scheme.scheme_codes == {"gla": "G1"}
    assert scheme.effective_date == date(2026, 1, 1)
    assert scheme.calculation_date == date(2026, 2, 15)  # string date parsed
    assert scheme.country_scale == "SA"
    assert scheme.pct_comm_applied == pytest.approx(0.4)
    assert scheme.collection_fee_pct == pytest.approx(0.01)
    assert scheme.regions == {"kzn": 0.6}
    assert scheme.salary_definition == "Total Cost"
    assert scheme.pear_tgp_pct == pytest.approx(0.08)
    assert scheme.rating == {"gla": "Yes", "lsdb": "Standalone"}
    assert scheme.pricing_basis == {"gip": 0.02}
    assert scheme.fees_priced == {"tip": 99.0}
    assert scheme.eswatini == pytest.approx(0.03)
    assert scheme.basic_fees == {"gip": 111.0}


def test_parse_scheme_details_narrow_sheet():
    # only 3 columns -> all guarded sections skipped
    scheme = cl._parse_scheme_details(_ws({(2, 0): "NB", (2, 5): None}), datemode=0)
    assert scheme.quote_type == "NB"
    assert scheme.scheme_name is None
    assert scheme.scheme_codes == {}
    assert scheme.regions == {}
    assert scheme.rating == {}
    assert scheme.basic_fees == {}
    assert scheme.salary_definition is None


# ---------------------------------------------------------------------------
# _parse_catinput
# ---------------------------------------------------------------------------

def _catinput_ws(**extra):
    cells = {
        (0, 7): 3,
        (2, 1): "GLA",
        (3, 0): 1, (3, 1): "Yes",
        (4, 0): 2, (4, 1): "No",
        (5, 0): "not-a-cat",       # non-numeric category row -> skipped
        (2, 17): "NRA", (2, 18): "Cover Multiple",
        (3, 17): 65, (3, 18): 2.0, (4, 17): 60,
    }
    cells.update(extra)
    return _ws(cells)


def test_parse_catinput_active_categories_and_rows():
    scheme = _scheme_with_rating({"gla": "Yes"})
    df, active, warns = cl._parse_catinput(_catinput_ws(), scheme, ("gla",))

    assert warns == []
    assert active == {"gla": (1,)}
    assert df.height == 2            # only cat 1 is active
    assert df.columns == ["product", "variant", "category_number", "field", "value"]
    assert set(df["field"].to_list()) == {"nra", "cover_multiple"}
    row = df.filter(pl.col("field") == "nra").row(0, named=True)
    assert row["product"] == "gla" and row["variant"] == "default"
    assert float(row["value"]) == pytest.approx(65.0)


def test_parse_catinput_zero_categories_warns():
    ws = _ws({(0, 7): 0})
    df, active, warns = cl._parse_catinput(ws, _scheme_with_rating({}), ("gla",))
    assert warns == ["Could not determine number of categories from row 0 col 7"]
    assert df.height == 0
    assert active == {}


def test_parse_catinput_no_labels_yields_empty_frame():
    scheme = _scheme_with_rating({"gla": "Yes"})
    # block label cells removed -> fields_in_block empty -> empty long frame
    cells = {(0, 7): 1, (2, 1): "GLA", (3, 0): 1, (3, 1): "Yes"}
    df, active, _warns = cl._parse_catinput(_ws(cells), scheme, ("gla",))
    assert df.height == 0
    assert "variant" in df.columns
    assert active == {"gla": (1,)}


def test_parse_catinput_missing_block_warns(monkeypatch):
    spec = PRODUCT_REGISTRY["gla"]
    monkeypatch.setitem(
        cl.PRODUCT_REGISTRY, "gla", replace(spec, catinput_blocks={})
    )
    scheme = _scheme_with_rating({"gla": "Yes"})
    df, _active, warns = cl._parse_catinput(_catinput_ws(), scheme, ("gla",))
    assert "[gla] no block for variant 'default'" in warns
    assert df.height == 0


def test_parse_catinput_inactive_product_skipped():
    scheme = _scheme_with_rating({"gla": "No"})
    df, active, _warns = cl._parse_catinput(_catinput_ws(), scheme, ("gla",))
    # variant None -> skipped entirely (toggle still counted in active map)
    assert df.height == 0
    assert active == {"gla": (1,)}


# ---------------------------------------------------------------------------
# _resolve_active_products_section
# ---------------------------------------------------------------------------

def test_resolve_active_products_excludes_education():
    scheme = _scheme_with_rating({
        "gla": "Yes", "educate_a_child": "Yes", "education_guardian": "Yes",
        "lsdb": "Standalone", "fc": "No",
    })
    warns = []
    active = cl._resolve_active_products_section(
        ("gla", "educate_a_child", "education_guardian", "lsdb", "fc"), scheme, warns
    )
    assert active == ("gla", "lsdb")


# ---------------------------------------------------------------------------
# _read_max_cover_caps
# ---------------------------------------------------------------------------

def test_read_max_cover_caps():
    cells = {
        (3, 0): 1, (3, 35): 500000.0, (3, 64): 80000.0,
        (4, 0): 2, (4, 35): 0.0,                      # <=0 -> None
        (5, 0): 3,                                    # blank cap -> None
        (6, 0): "xx", (6, 35): 123.0,                 # non-numeric cat -> skipped
    }
    ws = _ws(cells)
    scheme = _scheme_with_rating({"lsdb": "Accelerator", "spouse_gla": "Yes"})

    caps = cl._read_max_cover_caps(ws, scheme, ("lsdb", "spouse_gla", "gla"))
    # gla has no cap column -> not in output
    assert set(caps) == {"lsdb", "spouse_gla"}
    assert caps["lsdb"] == {1: 500000.0, 2: None, 3: None}
    assert caps["spouse_gla"] == {1: 80000.0, 2: None, 3: None}

    # active-category filter
    caps_filtered = cl._read_max_cover_caps(
        ws, scheme, ("lsdb",), {"lsdb": (1,)}
    )
    assert caps_filtered["lsdb"] == {1: 500000.0}


def test_read_max_cover_caps_inactive_product_skipped():
    scheme = _scheme_with_rating({"lsdb": "No"})
    caps = cl._read_max_cover_caps(_ws({(3, 0): 1}), scheme, ("lsdb",))
    assert caps == {}


# ---------------------------------------------------------------------------
# FC / Ext FC cat-level fields
# ---------------------------------------------------------------------------

def test_read_fc_catinput_fields():
    ws = _ws({
        (3, 0): 1, (3, 138): 4.0, (3, 139): "Yes",
        (3, 146): 50000.0, (3, 152): 5000.0,
        (4, 0): 2, (4, 138): 2.0,
    }, ncols=160)

    out = cl._read_fc_catinput_fields(ws, {"fc": (1,)})
    assert out["fc_max_no_of_spouses"] == {1: 4.0}
    assert out["fc_parents_included"] == {1: "Yes"}
    assert out["fc_benefit_amounts"] == {
        1: {"primary_insured": 50000.0, "stillborn": 5000.0},
    }
    # cat 2 not in active set -> absent
    assert 2 not in out["fc_max_no_of_spouses"]

    # no active fc categories -> skeleton only
    empty = cl._read_fc_catinput_fields(ws, {})
    assert empty["fc_benefit_amounts"] == {}
    assert all(v == {} for v in empty.values())

    # narrow sheet -> skeleton
    narrow = cl._read_fc_catinput_fields(_ws({(0, 0): "x"}), {"fc": (1,)})
    assert all(v == {} for v in narrow.values())


def test_read_ext_fc_catinput_fields():
    ws = _ws({
        (3, 0): 1,
        (3, 244): "Yes", (3, 246): 0.5, (3, 247): 120000.0,
        (3, 250): "60,70,50000", (3, 251): "50,60,30000;bad",
    }, ncols=260)
    out = cl._read_ext_fc_catinput_fields(ws, {"ext_fc": (1,)})
    assert out["ext_fc_extended_family_selected"] == {1: "Yes"}
    assert out["ext_fc_pct_male"] == {1: 0.5}
    assert out["ext_fc_avg_salary"] == {1: 120000.0}
    assert out["ext_fc_age_bands"] == {
        1: {
            "parents": [{"start_age": 60.0, "end_age": 70.0, "cover": 50000.0}],
            "extended_family": [{"start_age": 50.0, "end_age": 60.0, "cover": 30000.0}],
        }
    }
    # no active cats / narrow sheet -> skeleton
    assert all(v == {} for v in cl._read_ext_fc_catinput_fields(ws, {}).values())
    assert all(
        v == {} for v in cl._read_ext_fc_catinput_fields(
            _ws({(0, 0): "x"}), {"ext_fc": (1,)}
        ).values()
    )


# ---------------------------------------------------------------------------
# GIP / TIP cat-level fields
# ---------------------------------------------------------------------------

def test_read_gip_catinput_fields():
    ws = _ws({
        (3, 155): "sentinel", (3, 156): 65, (3, 157): "75%",
        (3, 160): 0.03, (3, 168): 0.75, (3, 170): 0.5, (3, 169): 1.0,
        (4, 155): "sentinel", (4, 156): 99,   # cat 2 filtered out below
        (5, 156): 77,                          # no sentinel col 155 -> skipped
    })
    out = cl._read_gip_catinput_fields(ws, {"gip": (1,)})
    assert out["gip_nra"] == {1: 65}
    assert out["gip_cover_type"] == {1: "75%"}
    assert out["gip_escalation_pct"] == {1: 0.03}
    assert out["gip_core_benefit_perc"] == {1: [0.75, 0.5, None, None]}
    assert out["gip_core_benefit_layer"] == {1: [1.0, None, None, None]}
    assert out["gip_hcc_benefit_perc"] == {1: [None, None, None, None]}
    assert 2 not in out["gip_nra"]

    # empty active set -> no filtering, all sentinel rows read
    out_all = cl._read_gip_catinput_fields(ws, {})
    assert set(out_all["gip_nra"]) == {1, 2}


def test_read_tip_catinput_fields():
    ws = _ws({
        (3, 198): "s", (3, 199): 60, (3, 200): "60%",
        (3, 210): 0.5, (3, 211): 1.0,
        (4, 198): "s", (4, 199): 99,
    })
    out = cl._read_tip_catinput_fields(ws, {"tip": (1,)})
    assert out["tip_nra"] == {1: 60}
    assert out["tip_cover_type"] == {1: "60%"}
    assert out["tip_core_benefit_perc"] == {1: [0.5, None, None, None]}
    assert out["tip_core_benefit_layer"] == {1: [1.0, None, None, None]}
    assert out["tip_hcc_benefit_perc"] == {1: [None, None, None, None]}
    assert 2 not in out["tip_nra"]


# ---------------------------------------------------------------------------
# education readers
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("cover,expected", [
    ("Guardian", "education_guardian"),
    ("Multiple", "educate_a_child"),
    ("UnknownType", None),
    ("", None),
])
def test_resolve_education_product(cover, expected):
    ws = _ws({(3, 133): cover} if cover else {})
    assert cl._resolve_education_product(ws) == expected


def test_read_education_catinput_fields():
    ws = _ws({
        (3, 15): "Yes", (3, 134): "Yes",          # cat 1 active + include CTC
        (4, 15): "No",                             # cat 2 inactive, no ctc cell
        (26, 13): 100000.0, (26, 25): 65,          # cat 1 max cover / nra
        (26, 26): "Yes", (26, 27): 18, (26, 31): 2,
        (27, 13): "",                              # empty cells skipped
        (28, 25): 60, (28, 27): 17,                # cat 3 partial data
    })
    out = cl._read_education_catinput_fields(ws)
    assert out["education_active"][1] is True
    assert out["education_active"][2] is False
    assert out["education_include_ctc"] == {1: "Yes"}
    assert out["education_max_cover"] == {1: 100000.0}
    assert out["education_nra"] == {1: 65, 3: 60}
    assert out["education_deferred"] == {1: "Yes"}
    assert out["education_deferred_age"] == {1: 18.0, 3: 17.0}
    assert out["education_multiple"] == {1: 2.0}


# ---------------------------------------------------------------------------
# _parse_member_sheet (beyond existing tests)
# ---------------------------------------------------------------------------

def test_parse_member_sheet_unlabeled_and_numeric_headers():
    ws = FakeWorksheet([
        ["ignored"],
        ["Member No", "", "Gender", "Date of Disability", "GLA"],
        ["M1", "raw-text", "female", 46023, 250000],
        ["", "skip-me"],                              # empty first col -> skipped
        ["M2", None, "X", "bad-date", "not-a-number"],
    ])
    df = cl._parse_member_sheet(ws, datemode=0)
    assert df.height == 2
    assert df.columns == ["member_no", "col_1", "gender", "date_of_disability", "gla"]
    assert df["member_no"].to_list() == ["M1", "M2"]
    assert df["col_1"].to_list() == ["raw-text", None]
    assert df["gender"].to_list() == ["F", None]       # 'X' -> None
    assert df["date_of_disability"].to_list() == [date(2026, 1, 1), None]
    assert df["gla"].to_list() == [250000.0, None]


def test_parse_member_sheet_header_only_is_empty():
    ws = FakeWorksheet([["x"], ["Member No", "Gender"]])
    assert cl._parse_member_sheet(ws, datemode=0).is_empty()


# ---------------------------------------------------------------------------
# _load_member_data_sections
# ---------------------------------------------------------------------------

def test_load_member_data_sections_roles_and_missing():
    member_ws = FakeWorksheet([
        ["x"],
        ["Member No"],
        ["A1"], ["A2"],
    ])
    wb = FakeWorkbook({
        "Member Data - GLA": member_ws,
        "Member Data - SP": member_ws,
        "Member Data - FC": member_ws,
    })
    warns = []

    members = cl._load_member_data_sections(
        wb, {"Member Data - GLA", "Member Data - SP", "Member Data - FC"},
        ("gla", "spouse_gla", "fc"), spouse_use_sp_sheet=False, warnings=warns,
    )
    assert set(members) == {"gla_sheet", "fc_sheet"}   # spouse via GLA sheet
    assert warns == []
    assert members["gla_sheet"].height == 2

    warns2 = []
    members2 = cl._load_member_data_sections(
        wb, {"Member Data - GLA", "Member Data - SP"},
        ("gla", "spouse_gla"), spouse_use_sp_sheet=True, warnings=warns2,
    )
    assert set(members2) == {"gla_sheet", "sp_sheet"}

    warns3 = []
    members3 = cl._load_member_data_sections(
        wb, {"Member Data - GLA"},
        ("gla", "fc"), spouse_use_sp_sheet=False, warnings=warns3,
    )
    assert members3.keys() == {"gla_sheet"}
    assert "Member sheet 'Member Data - FC' missing" in warns3


def test_load_member_data_sections_skip_and_fallback_role(monkeypatch):
    member_ws = FakeWorksheet([["x"], ["Member No"], ["A1"]])
    wb = FakeWorkbook({"Member Data - Custom": member_ws})

    skip_spec = replace(
        PRODUCT_REGISTRY["gla"], key="fake_skip", member_source="Member Data",
    )
    custom_spec = replace(
        PRODUCT_REGISTRY["gla"], key="fake_custom",
        member_source="Member Data - Custom",
    )
    monkeypatch.setitem(cl.PRODUCT_REGISTRY, "fake_skip", skip_spec)
    monkeypatch.setitem(cl.PRODUCT_REGISTRY, "fake_custom", custom_spec)

    warns = []
    members = cl._load_member_data_sections(
        wb, {"Member Data", "Member Data - Custom"},
        ("fake_skip", "fake_custom"), spouse_use_sp_sheet=False, warnings=warns,
    )
    # 'Member Data' is in SKIP_SHEETS -> silently skipped (no warning)
    assert warns == []
    # unknown suffix -> snake_case role name
    assert set(members) == {"member_data_custom"}


# ---------------------------------------------------------------------------
# _load_catinput_section / _load_scheme_details_section / _finalize helpers
# ---------------------------------------------------------------------------

def test_load_scheme_details_section_missing_raises():
    wb = FakeWorkbook({})
    with pytest.raises(ValueError, match="Scheme Details - Save"):
        cl._load_scheme_details_section(wb, set())


def test_finalize_applies_catinput_results():
    scheme_ws = _ws({(2, 5): "S", (2, 80): 3.0}, nrows=3, ncols=100)
    catinput_ws = _ws({(0, 19): "Yes", (0, 20): 5.0, (0, 21): "Yes"})
    wb = FakeWorkbook({"Scheme Details - Save": scheme_ws})

    result = cl.CatInputResult(
        spouse_use_sp_sheet=True,
        express_over_gla={"gla": True},
        category_split={"gla": "Split"},
        max_cover_per_category={"lsdb": {1: 100.0}},
        fc_catinput_fields={"fc_parents_included": {1: "Yes"}},
        ext_fc_catinput_fields={"ext_fc_pct_male": {1: 0.5}},
        education_product_pkey="education_guardian",
        education_fields={"education_active": {1: True}},
    )
    scheme = cl._finalize_scheme_with_catinput_and_rate_basis(
        wb, SchemeRecord(), result, catinput_ws
    )
    assert scheme.spouse_use_sp_sheet is True
    assert scheme.express_over_gla == {"gla": True}
    assert scheme.category_split == {"gla": "Split"}
    assert scheme.max_cover_per_category == {"lsdb": {1: 100.0}}
    assert scheme.fc_parents_included == {1: "Yes"}
    assert scheme.ext_fc_pct_male == {1: 0.5}
    assert scheme.education_product == "education_guardian"
    assert scheme.education_active == {1: True}
    assert scheme.profit_refund_loading_by_product == {"gla": 0.05}
    assert scheme.rate_basis_by_product["gla"] == 3.0
