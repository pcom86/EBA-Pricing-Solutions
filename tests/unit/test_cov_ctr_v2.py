"""Coverage-focused tests for ctr_loader.ctr_loader_v2 (end-to-end load_ctr).

Two strategies:
  * tiny synthetic workbooks written with openpyxl into tmp_path to exercise
    every section loader and edge path deterministically;
  * a handful of real production CTR fixtures in ``BackendEngine/Test/Batch run``
    to cover the full pipeline on realistic layouts.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import openpyxl
import polars as pl
import pytest
from ctr_loader import ctr_loader_v2 as cl
from ctr_loader.ctr_schemas import CTRBundle

pytestmark = pytest.mark.unit

BATCH_DIR = Path(__file__).resolve().parents[2] / "BackendEngine" / "Test" / "Batch run"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _make_ctr(path: Path, sheets: dict[str, dict[tuple[int, int], object]]) -> Path:
    """Write a synthetic workbook. ``cells`` keys are 0-based (row, col)."""
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for name, cells in sheets.items():
        ws = wb.create_sheet(title=name)
        # calamine's to_python() trims to the used range, so the parser's
        # absolute (row, col) reads only line up when the used range starts
        # at (0, 0) — real CTRs put a title cell there ("Scheme Details",
        # "General Category Details", "Rating Data").
        if not cells.get((0, 0)):
            title = {
                "Scheme Details - Save": "Scheme Details",
                "CatInput-Save": "General Category Details",
            }.get(name, "Rating Data")
            ws.cell(row=1, column=1, value=title)
        for (r, c), v in cells.items():
            if v is None or v == "":
                continue
            ws.cell(row=r + 1, column=c + 1, value=v)
    wb.save(path)
    return path


def _scheme_cells() -> dict[tuple[int, int], object]:
    """A 'Scheme Details - Save' sheet exercising every parsed region.

    Row 0 = section markers, row 1 = labels, row 2 = values,
    rows 4/5 = Jigsaw/TSA basic fee labels/values.
    """
    return {
        # identity block (cols 0-5)
        (2, 0): "New Business", (2, 1): "Group Risk", (2, 2): 92114,
        (2, 3): "Doer A", (2, 4): "Checker B", (2, 5): "Acme Scheme",
        # scheme codes (cols 6-22): label row 1, value row 2
        (1, 6): "Scheme Code - GLA", (2, 6): "GLA-01",
        (1, 7): "Scheme Code-Region", (2, 7): "R7",
        # dates as excel serials (1900 date system): 46023 = 2026-01-01
        (2, 23): 46023.0, (2, 24): 46024.0, (2, 25): 46025.0,
        # commission / fee identity columns
        (2, 26): "InterMed X", (2, 27): "Broker B", (2, 28): "CM C",
        (2, 29): "Branch D", (2, 30): "Yes", (2, 31): "South Africa",
        (2, 32): 0.5, (2, 33): "Level", (2, 34): "None", (2, 35): 0.02,
        (2, 36): "Yes", (2, 37): "No", (2, 38): "Monthly", (2, 39): "Jigsaw",
        (2, 40): 1200.0, (2, 41): 0.075, (2, 42): 300.0, (2, 43): 0.05,
        (2, 44): 400.0, (2, 45): 0.06, (2, 46): 500.0, (2, 47): 0.07,
        (2, 48): "Primary P", (2, 49): "Secondary S", (2, 50): "Region One",
        # region weightings (cols 51-61)
        (1, 51): "Gauteng", (2, 51): 0.7,
        (1, 52): "Limpopo", (2, 52): 0.3,
        # salary definition + PEAR TGP
        (2, 62): "Pensionable Salary", (2, 64): 0.05,
        # ---- section markers (row 0) + labels (row 1) + values (row 2) ----
        (0, 66): "Rating Selections",
        (1, 66): "GLA", (2, 66): "Yes",
        (1, 67): "LSDB", (2, 67): "Accelerator",
        (1, 68): "CI", (2, 68): "Banana",          # unexpected -> warning + accelerator
        (1, 69): "Spouse - GLA", (2, 69): "Yes",
        (1, 70): "Spouse - LSDB", (2, 70): "No",
        (1, 71): "FC", (2, 71): "Yes",
        (1, 72): "Ext FC", (2, 72): "Yes",
        (1, 73): "GIP", (2, 73): "Yes",
        (1, 74): "TIP", (2, 74): "Yes",
        (1, 75): "Terminal Illness Cover", (2, 75): "No",
        (0, 76): "Pricing Basis", (1, 76): "GLA", (2, 76): 0.011,
        (0, 78): "Fees Priced", (1, 78): "FC", (2, 78): 450.0,
        # rate basis cells live on row 2 of this sheet (cols 80-94)
        (2, 80): 1.2, (2, 91): 1.1,
        (0, 96): "Add eSwatini", (2, 96): 0.02,
        # Jigsaw/TSA basic fees: labels row 4, values row 5
        (4, 66): "GLA", (5, 66): 300.0,
    }


def _minimal_scheme_cells() -> dict[tuple[int, int], object]:
    return {(2, 5): "Minimal Scheme"}


def _catinput_cells(spouse_flag: str = "No") -> dict[tuple[int, int], object]:
    """A 'CatInput-Save' sheet exercising every cat-level reader."""
    return {
        (0, 7): 2,                                        # number of categories
        # profit-refund loading: overall flag, pct, per-product flags (row 0)
        (0, 19): "Yes", (0, 20): 10.0, (0, 21): "Yes", (0, 25): "Yes",
        # toggle labels (row 2, cols 1-16) and per-category Yes/No flags
        (2, 1): "GLA", (2, 2): "FC", (2, 3): "LSDB", (2, 4): "Spouse - GLA",
        (2, 5): "CI", (2, 6): "GIP", (2, 7): "TIP", (2, 8): "Extended Family Cover",
        (3, 0): 1, (3, 1): "Yes", (3, 2): "Yes", (3, 3): "Yes", (3, 4): "Yes",
        (3, 5): "Yes", (3, 6): "Yes", (3, 7): "Yes", (3, 8): "Yes",
        (4, 0): 2, (4, 1): "Yes", (4, 4): "Yes",
        # GLA block (cols 17-30): labels row 2
        (2, 17): "NRA", (2, 18): "Cover Multiple",
        (3, 17): 65.0, (3, 18): 2.0, (4, 17): 60.0, (4, 18): 1.5,
        # LSDB accelerator block (cols 31-46)
        (2, 31): "Waiting Period", (2, 32): "Definition",
        (3, 31): 3.0, (3, 32): "Own Occ",
        # max-cover caps: lsdb accelerator col 35, ci accelerator col 52
        (3, 35): 500000.0, (4, 35): 0.0,
        (3, 52): 75000.0,
        # CI accelerator block (cols 47-59)
        (2, 47): "CI Option", (3, 47): "Option A",
        # spouse GLA block (cols 60-67)
        (2, 60): "Spouse Benefit", (3, 60): "Flat", (4, 60): "Flat",
        # spouse source flag
        (0, 61): spouse_flag,
        # express-over-GLA flags (rows 26-34, col 35)
        (26, 35): "Yes", (28, 35): "Yes",
        # category split (rows 26-29, col 36 -> gla/gip/tip/fc)
        (26, 36): "Per Category", (27, 36): "Combined",
        (28, 36): "Per Category", (29, 36): "Combined",
        # FC block labels (cols 135-153) + FC cat-level fields (cols 138-153)
        (2, 135): "Benefit Structure", (3, 135): "Family",
        (3, 138): 3.0, (3, 139): "Yes", (3, 141): "Yes",
        (3, 146): 50000.0, (3, 148): 10000.0, (3, 153): 20000.0,
        # GIP block label (cols 154-157) + GIP cat-level fields (col 155 sentinel)
        (2, 154): "GIP Waiting", (3, 154): "13 weeks",
        (3, 155): 1, (3, 156): 65.0, (3, 157): "75% Salary",
        (3, 168): 0.75, (3, 169): 1.0,
        (4, 155): 1, (4, 156): 99.0,   # cat 2 not active for gip -> filtered out
        # TIP block label (cols 197-200) + TIP cat-level fields (col 198 sentinel)
        (2, 197): "TIP Option", (3, 197): "Opt A",
        (3, 198): 1, (3, 199): 60.0, (3, 210): 0.5,
        (4, 198): 1, (4, 199): 99.0,   # cat 2 not active for tip -> filtered out
        # Ext FC block label (cols 242-251) + ext-fc cat-level fields
        (2, 242): "Ext FC Option", (3, 242): "Sel",
        (3, 244): "Yes", (3, 245): "No", (3, 246): 0.55, (3, 247): 150000.0,
        (3, 250): "60,70,50000; 70,80,40000", (3, 251): "50,60,30000",
    }


def _member_cells(rows: list[tuple]) -> dict[tuple[int, int], object]:
    cells = {
        (1, 0): "Member No", (1, 1): "Date of Birth", (1, 2): "Gender",
        (1, 3): "Annual Salary", (1, 4): "Category Flag",
    }
    for i, (no, dob, gender, salary, cat) in enumerate(rows, start=2):
        cells[(i, 0)] = no
        cells[(i, 1)] = dob
        cells[(i, 2)] = gender
        cells[(i, 3)] = salary
        cells[(i, 4)] = cat
    return cells


_GLA_MEMBERS = _member_cells([
    ("E001", "15/06/1990", "male", 120000.0, 1.0),
    ("E002", "02/01/1985", "F", 95000.0, 2.0),
])
_ONE_MEMBER = _member_cells([("X1", "01/01/1980", "M", 50000.0, 1.0)])


def _full_sheets(spouse_flag: str = "No", include_tip: bool = True):
    sheets = {
        "Scheme Details - Save": _scheme_cells(),
        "CatInput-Save": _catinput_cells(spouse_flag=spouse_flag),
        "Member Data - GLA": _GLA_MEMBERS,
        "Member Data - FC": _ONE_MEMBER,
        "Member Data - GIP": _ONE_MEMBER,
        "Member Data - Ext FC": _ONE_MEMBER,
    }
    if include_tip:
        sheets["Member Data - TIP"] = _ONE_MEMBER
    if spouse_flag.strip().lower() == "yes":
        sheets["Member Data - SP"] = _ONE_MEMBER
    return sheets


# ---------------------------------------------------------------------------
# error / edge paths
# ---------------------------------------------------------------------------

def test_load_ctr_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        cl.load_ctr(tmp_path / "does_not_exist.ctr")


def test_load_ctr_unparsable_file_raises(tmp_path):
    bad = tmp_path / "bad.ctr"
    bad.write_bytes(b"this is not a spreadsheet at all")
    with pytest.raises(Exception):
        cl.load_ctr(bad)


def test_load_ctr_missing_scheme_details_raises(tmp_path):
    path = _make_ctr(tmp_path / "no_scheme.ctr", {"Some Other Sheet": {(0, 0): "x"}})
    with pytest.raises(ValueError, match="Scheme Details - Save"):
        cl.load_ctr(path)


def test_load_ctr_empty_scheme_identity_raises(tmp_path):
    # sheet exists but has no identity fields -> RuntimeError
    path = _make_ctr(
        tmp_path / "empty_scheme.ctr",
        {"Scheme Details - Save": {(0, 0): "only a title"}},
    )
    with pytest.raises(RuntimeError, match="NO identity fields"):
        cl.load_ctr(path)


def test_load_ctr_minimal_scheme_only(tmp_path):
    path = _make_ctr(
        tmp_path / "minimal.ctr",
        {"Scheme Details - Save": _minimal_scheme_cells()},
    )
    bundle = cl.load_ctr(path)

    assert isinstance(bundle, CTRBundle)
    assert bundle.scheme.scheme_name == "Minimal Scheme"
    assert bundle.active_products == ()
    assert bundle.members == {}
    assert bundle.categories.height == 0
    assert "CatInput-Save sheet missing" in bundle.warnings
    # else-branch defaults applied to the scheme record
    assert bundle.scheme.fc_benefit_amounts == {}
    assert bundle.scheme.education_product is None
    assert bundle.scheme.spouse_use_sp_sheet is False
    assert bundle.scheme.express_over_gla == {}
    assert bundle.scheme.category_split == {}
    assert bundle.scheme.max_cover_per_category == {}
    # rate basis defaults to 1.0 for every product column
    assert bundle.scheme.rate_basis_by_product["gla"] == 1.0
    assert len(bundle.ctr_fingerprint) == 16
    assert bundle.loaded_at.endswith("Z")


def test_load_ctr_catinput_zero_categories(tmp_path):
    path = _make_ctr(
        tmp_path / "zero_cats.ctr",
        {
            "Scheme Details - Save": _minimal_scheme_cells(),
            "CatInput-Save": {(0, 7): 0},
        },
    )
    bundle = cl.load_ctr(path)
    assert "Could not determine number of categories from row 0 col 7" in bundle.warnings
    assert bundle.categories.height == 0


def test_load_ctr_missing_member_sheet_warns(tmp_path):
    sheets = _full_sheets()
    del sheets["Member Data - TIP"]
    path = _make_ctr(tmp_path / "missing_member.ctr", sheets)
    bundle = cl.load_ctr(path)

    assert "Member sheet 'Member Data - TIP' missing" in bundle.warnings
    assert "tip_sheet" not in bundle.members
    # everything else still parsed
    assert "gla_sheet" in bundle.members


# ---------------------------------------------------------------------------
# full synthetic workbook
# ---------------------------------------------------------------------------

def test_load_ctr_full_synthetic(tmp_path):
    path = _make_ctr(tmp_path / "full.ctr", _full_sheets())
    bundle = cl.load_ctr(path)

    # --- scheme identity ---
    s = bundle.scheme
    assert s.scheme_name == "Acme Scheme"
    assert s.quote_number == 92114
    assert s.quote_type == "New Business"
    assert s.doer == "Doer A"
    assert s.scheme_codes == {"gla": "GLA-01", "region": "R7"}
    assert s.effective_date == date(2026, 1, 1)
    assert s.calculation_date == date(2026, 1, 2)
    assert s.commencement_date == date(2026, 1, 3)
    assert s.intermediary == "InterMed X"
    assert s.pct_comm_applied == pytest.approx(0.5)
    assert s.collection_fee_pct == pytest.approx(0.02)
    assert s.premium_frequency == "Monthly"
    assert s.fee_structure == "Jigsaw"
    assert s.annual_fee_gla == pytest.approx(1200.0)
    assert s.admin_fee_pct_gla == pytest.approx(0.075)
    assert s.annual_fee_tip == pytest.approx(500.0)
    assert s.primary == "Primary P"
    assert s.secondary == "Secondary S"
    assert s.region == "Region One"
    assert s.regions == {"gauteng": pytest.approx(0.7), "limpopo": pytest.approx(0.3)}
    assert s.salary_definition == "Pensionable Salary"
    assert s.pear_tgp_pct == pytest.approx(0.05)

    # --- scheme sections ---
    assert s.rating["gla"] == "Yes"
    assert s.rating["lsdb"] == "Accelerator"
    assert s.rating["terminal"] == "No"
    assert s.pricing_basis == {"gla": pytest.approx(0.011)}
    assert s.fees_priced == {"fc": pytest.approx(450.0)}
    assert s.basic_fees == {"gla": pytest.approx(300.0)}
    assert s.eswatini == pytest.approx(0.02)
    assert s.rate_basis_by_product["gla"] == pytest.approx(1.2)
    assert s.rate_basis_by_product["fc"] == pytest.approx(1.1)
    assert s.rate_basis_by_product["tip"] == pytest.approx(1.0)

    # --- active products / warnings ---
    assert set(bundle.active_products) == {
        "gla", "lsdb", "ci", "spouse_gla", "fc", "ext_fc", "gip", "tip",
    }
    assert "[ci] unexpected rating 'Banana', defaulting to accelerator" in bundle.warnings
    assert all(isinstance(w, str) for w in bundle.warnings)

    # --- active categories from toggles ---
    assert bundle.active_categories["gla"] == (1, 2)
    assert bundle.active_categories["fc"] == (1,)
    assert bundle.active_categories["spouse_gla"] == (1, 2)
    assert bundle.active_categories["gip"] == (1,)
    assert bundle.active_categories["ext_fc"] == (1,)

    # --- long-format categories frame ---
    cats = bundle.categories
    assert cats.columns == ["product", "variant", "category_number", "field", "value"]
    # gla 2 cats x 2 fields + lsdb 1x2 + ci 1 + spouse 2 + fc 1 + gip 1 + tip 1 + ext_fc 1
    assert cats.height == 13
    gla_nra = cats.filter(
        (pl.col("product") == "gla") & (pl.col("field") == "nra")
        & (pl.col("category_number") == 1)
    )
    assert gla_nra.height == 1
    assert float(gla_nra["value"][0]) == pytest.approx(65.0)
    lsdb_rows = cats.filter(pl.col("product") == "lsdb")
    assert set(lsdb_rows["variant"].to_list()) == {"accelerator"}

    # --- catinput extras ---
    assert s.spouse_use_sp_sheet is False
    assert s.express_over_gla["gla"] is True
    assert s.express_over_gla["ci"] is True
    assert s.express_over_gla["lsdb"] is False
    assert s.express_over_gla["dismemberment"] is False
    assert s.category_split == {
        "gla": "Per Category", "gip": "Combined",
        "tip": "Per Category", "fc": "Combined",
    }
    assert s.profit_refund_loading_by_product == {
        "gla": pytest.approx(0.1), "fc": pytest.approx(0.1),
    }
    assert s.max_cover_per_category["lsdb"] == {1: 500000.0}  # cat 2 inactive for lsdb -> skipped
    assert s.max_cover_per_category["ci"] == {1: 75000.0}
    assert s.max_cover_per_category["spouse_gla"] == {1: None, 2: None}

    # --- FC cat-level fields ---
    assert s.fc_max_no_of_spouses == {1: 3.0}
    assert s.fc_parents_included == {1: "Yes"}
    assert s.fc_include_ctc == {1: "Yes"}
    assert s.fc_benefit_amounts[1] == {
        "primary_insured": 50000.0, "child_14_plus": 10000.0,
        "parents_cover": 20000.0,
    }

    # --- Ext FC cat-level fields ---
    assert s.ext_fc_extended_family_selected == {1: "Yes"}
    assert s.ext_fc_parents_selected == {1: "No"}
    assert s.ext_fc_pct_male == {1: pytest.approx(0.55)}
    assert s.ext_fc_avg_salary == {1: pytest.approx(150000.0)}
    bands = s.ext_fc_age_bands[1]
    assert bands["parents"] == [
        {"start_age": 60.0, "end_age": 70.0, "cover": 50000.0},
        {"start_age": 70.0, "end_age": 80.0, "cover": 40000.0},
    ]
    assert bands["extended_family"] == [
        {"start_age": 50.0, "end_age": 60.0, "cover": 30000.0},
    ]

    # --- GIP cat-level fields (cat 2 row filtered out) ---
    assert s.gip_nra == {1: 65.0}
    assert s.gip_cover_type == {1: "75% Salary"}
    assert s.gip_core_benefit_perc == {1: [0.75, None, None, None]}
    assert s.gip_core_benefit_layer == {1: [1.0, None, None, None]}
    assert s.gip_hcc_benefit_perc == {1: [None, None, None, None]}

    # --- TIP cat-level fields ---
    assert s.tip_nra == {1: 60.0}
    assert s.tip_core_benefit_perc == {1: [0.5, None, None, None]}

    # --- member sheets and roles ---
    assert set(bundle.members) == {
        "gla_sheet", "fc_sheet", "gip_sheet", "tip_sheet", "ext_fc_sheet",
    }
    gla_df = bundle.members["gla_sheet"]
    assert gla_df.height == 2
    assert gla_df["member_no"].to_list() == ["E001", "E002"]
    assert gla_df["date_of_birth"].to_list() == [date(1990, 6, 15), date(1985, 1, 2)]
    assert gla_df["gender"].to_list() == ["M", "F"]
    assert gla_df["annual_salary"].to_list() == [120000.0, 95000.0]
    assert bundle.members["fc_sheet"].height == 1

    # --- fingerprint / identity ---
    assert bundle.ctr_path == path
    assert len(bundle.ctr_fingerprint) == 16
    int(bundle.ctr_fingerprint, 16)  # hex


def test_load_ctr_spouse_flag_uses_sp_sheet(tmp_path):
    path = _make_ctr(tmp_path / "spouse.ctr", _full_sheets(spouse_flag="Yes"))
    bundle = cl.load_ctr(path)

    assert bundle.scheme.spouse_use_sp_sheet is True
    assert "sp_sheet" in bundle.members
    assert bundle.members["sp_sheet"].height == 1
    # GLA sheet still loaded for main products
    assert "gla_sheet" in bundle.members


def test_load_ctr_products_subset(tmp_path):
    path = _make_ctr(tmp_path / "subset.ctr", _full_sheets())
    bundle = cl.load_ctr(path, products=("gla",))

    assert bundle.active_products == ("gla",)
    assert set(bundle.members) == {"gla_sheet"}
    assert bundle.categories.height == 4  # 2 cats x 2 labeled fields
    assert bundle.active_categories == {"gla": (1, 2)}
    assert bundle.warnings == []


def test_load_ctr_education_guardian(tmp_path):
    catinput = {
        (0, 7): 1,
        (3, 0): 1,
        (3, 15): "Yes",          # education active toggle (block A)
        (3, 133): "Guardian",    # cover type -> education_guardian
        (3, 134): "Yes",         # include CTC
        # block B (rows 26-45): cat = row - 25
        (26, 13): 100000.0,      # max cover, cat 1
        (26, 25): 65.0,          # NRA
        (26, 26): "Yes",         # deferred
        (26, 27): 18.0,          # deferred age
        (26, 31): 2.0,           # multiple
        (40, 13): 200000.0,      # max cover, cat 15 (also pads nrows > 36)
        (36, 35): "Yes",         # education express-over-GLA flag
    }
    path = _make_ctr(
        tmp_path / "edu.ctr",
        {
            "Scheme Details - Save": _minimal_scheme_cells(),
            "CatInput-Save": catinput,
            "Member Data - GLA": _ONE_MEMBER,
        },
    )
    bundle = cl.load_ctr(path)
    s = bundle.scheme

    assert s.education_product == "education_guardian"
    assert "education_guardian" in bundle.active_products
    assert bundle.active_categories["education_guardian"] == (1,)
    assert s.express_over_gla["education_guardian"] is True
    assert s.education_active[1] is True
    assert s.education_include_ctc == {1: "Yes"}
    assert s.education_max_cover == {1: 100000.0, 15: 200000.0}
    assert s.education_nra == {1: 65.0}
    assert s.education_deferred == {1: "Yes"}
    assert s.education_deferred_age == {1: 18.0}
    assert s.education_multiple == {1: 2.0}
    # education_guardian reads members from the GLA sheet
    assert "gla_sheet" in bundle.members


def test_load_ctr_education_multiple_variant(tmp_path):
    catinput = {
        (0, 7): 1,
        (3, 0): 1,
        (3, 15): "Yes",
        (3, 133): "Multiple",    # cover type -> educate_a_child
    }
    path = _make_ctr(
        tmp_path / "edu_multi.ctr",
        {
            "Scheme Details - Save": _minimal_scheme_cells(),
            "CatInput-Save": catinput,
            "Member Data - GLA": _ONE_MEMBER,
        },
    )
    bundle = cl.load_ctr(path)
    assert bundle.scheme.education_product == "educate_a_child"
    assert "educate_a_child" in bundle.active_products
    # express flag row (36) beyond nrows -> False
    assert bundle.scheme.express_over_gla["educate_a_child"] is False


def test_load_ctr_fingerprint_deterministic(tmp_path):
    path = _make_ctr(tmp_path / "fp.ctr", {"Scheme Details - Save": _minimal_scheme_cells()})
    b1 = cl.load_ctr(path)
    b2 = cl.load_ctr(path)
    assert b1.ctr_fingerprint == b2.ctr_fingerprint


# ---------------------------------------------------------------------------
# _CalamineWorkbook / _fingerprint unit-level coverage (real files)
# ---------------------------------------------------------------------------

def test_calamine_workbook_cache_and_unload(tmp_path):
    path = _make_ctr(
        tmp_path / "wb.ctr",
        {"SheetA": {(0, 0): "a", (1, 0): "b"}, "SheetB": {(0, 0): "c"}},
    )
    wb = cl._open_workbook(path)
    assert set(wb.sheet_names()) == {"SheetA", "SheetB"}
    s1 = wb.sheet_by_name("SheetA")
    assert wb.sheet_by_name("SheetA") is s1  # cached
    wb.unload_sheet("SheetA")
    s2 = wb.sheet_by_name("SheetA")
    assert s2 is not s1
    wb.unload_sheet("Nonexistent")  # no-op
    assert wb.datemode == 0


def test_fingerprint_changes_with_content(tmp_path):
    f = tmp_path / "a.ctr"
    f.write_bytes(b"abc")
    fp1 = cl._fingerprint(str(f))
    assert len(fp1) == 16
    f.write_bytes(b"abcd")  # size change -> different fingerprint
    fp2 = cl._fingerprint(str(f))
    assert fp1 != fp2


# ---------------------------------------------------------------------------
# real CTR fixtures
# ---------------------------------------------------------------------------

_REAL_CTR_CASES = {
    "Transnet_92114_GLA_100%_20260107.ctr": {"products": {"gla"}},
    "90620_GLA_100% run_18092024.ctr": {"products": {"gla"}},
    "SACCAWU_90896_Spouses_100%_20240912.ctr": {
        "products_any": ("spouse_gla", "spouse_lsdb"),
    },
    "AMAZON_Tech Run_All Products_FS LSDB, FS CI, FC with Parents Cover_PRO.ctr": {
        "products": {"gla", "lsdb", "ci", "fc"},
        "member_sheets": {"fc_sheet"},
    },
}


@pytest.mark.parametrize("filename", sorted(_REAL_CTR_CASES))
def test_real_ctr_loads(filename):
    path = BATCH_DIR / filename
    if not path.exists():
        pytest.skip(f"fixture {filename} not present")

    bundle = cl.load_ctr(path)
    expect = _REAL_CTR_CASES[filename]

    # universal invariants
    assert isinstance(bundle, CTRBundle)
    assert len(bundle.ctr_fingerprint) == 16
    assert bundle.loaded_at.endswith("Z")
    assert isinstance(bundle.warnings, list)
    assert isinstance(bundle.categories, pl.DataFrame)
    assert len(bundle.members) >= 1
    assert sum(df.height for df in bundle.members.values()) > 0
    assert len(bundle.active_products) >= 1

    if "products" in expect:
        assert expect["products"].issubset(set(bundle.active_products))
    if "products_any" in expect:
        assert any(p in bundle.active_products for p in expect["products_any"])
    if "member_sheets" in expect:
        assert expect["member_sheets"].issubset(set(bundle.members))


def test_real_ctr_fingerprint_stable_across_loads():
    path = BATCH_DIR / "90620_GLA_100% run_18092024.ctr"
    if not path.exists():
        pytest.skip("fixture not present")
    assert cl.load_ctr(path).ctr_fingerprint == cl.load_ctr(path).ctr_fingerprint
