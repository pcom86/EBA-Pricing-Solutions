from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

import polars as pl

logger = logging.getLogger(__name__)

from python_calamine import CalamineWorkbook

from .ctr_cache import local_ctr_path
from .ctr_coerce import (
    clean_text,
    excel_to_date,
    normalize_gender,
    snake_case,
    to_float,
    to_int,
    to_pct,
)
from .ctr_schemas import CTRBundle, SchemeRecord
from .products_registry import IN_SCOPE_PRODUCTS, PRODUCT_REGISTRY, ProductSpec


class _CalamineSheet:

    __slots__ = ("_rows", "ncols", "nrows")

    def __init__(self, rows: list[list]):
        self._rows = rows
        self.nrows = len(rows)
        self.ncols = max((len(r) for r in rows), default=0)

    def cell_value(self, row: int, col: int):
        if row >= self.nrows:
            return ""
        r = self._rows[row]
        if col >= len(r):
            return ""
        return r[col]


class _CalamineWorkbook:

    __slots__ = ("_cache", "_names", "_wb", "datemode")

    def __init__(self, path: Path):
        self._wb = CalamineWorkbook.from_path(str(path))
        self._names = list(self._wb.sheet_names)
        self._cache: dict[str, _CalamineSheet] = {}
        self.datemode = 0

    def sheet_names(self) -> list[str]:
        return self._names

    def sheet_by_name(self, name: str) -> _CalamineSheet:
        if name in self._cache:
            return self._cache[name]
        raw = self._wb.get_sheet_by_name(name).to_python()
        sheet = _CalamineSheet(raw)
        self._cache[name] = sheet
        return sheet

    def unload_sheet(self, name: str) -> None:
        self._cache.pop(name, None)


def _open_workbook(ctr_path: Path) -> _CalamineWorkbook:
    return _CalamineWorkbook(ctr_path)


def _fingerprint(ctr_path: str) -> str:
    p = Path(ctr_path)
    key = f"{p.resolve()}|{p.stat().st_mtime_ns}|{p.stat().st_size}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


_SCHEME_IDENTITY_MAP = {
    0:  ("quote_type",          "text"),
    1:  ("quote_group",         "text"),
    2:  ("quote_number",        "int"),
    3:  ("doer",                "text"),
    4:  ("checker",             "text"),
    5:  ("scheme_name",         "text"),
    23: ("effective_date",      "date"),
    24: ("calculation_date",    "date"),
    25: ("commencement_date",   "date"),
    26: ("intermediary",        "text"),
    27: ("broker_name",         "text"),
    28: ("client_manager",      "text"),
    29: ("branch",              "text"),
    30: ("alexander_forbes_scheme", "text"),
    31: ("country_scale",       "text"),
    32: ("pct_comm_applied",    "pct"),
    33: ("initial_commission",  "text"),
    34: ("collection_commission","text"),
    35: ("collection_fee_pct",  "pct"),
    36: ("include_vat_commission","text"),
    37: ("namibian_stamp_duty", "text"),
    38: ("premium_frequency",   "text"),
    39: ("fee_structure",       "text"),
    40: ("annual_fee_gla",      "float"),
    41: ("admin_fee_pct_gla",   "pct"),
    42: ("annual_fee_gip",      "float"),
    43: ("admin_fee_pct_gip",   "pct"),
    44: ("annual_fee_fc",       "float"),
    45: ("admin_fee_pct_fc",    "pct"),
    46: ("annual_fee_tip",      "float"),
    47: ("admin_fee_pct_tip",   "pct"),
    48: ("primary",             "text"),
    49: ("secondary",           "text"),
    50: ("region",              "text"),
}

_SCHEME_CODE_COLS = list(range(6, 23))

_REGION_COLS = list(range(51, 62))

_SALARY_DEF_COL = 62
_PEAR_TGP_COL = 64

_SECTION_MARKERS = {
    "Rating Selections": "rating",
    "Pricing Basis": "pricing_basis",
    "Fees Priced": "fees_priced",
}
_ESWATINI_MARKER = "Add eSwatini"
_BASIC_FEES_MARKER = "Jigsaw/TSA Basic Fees"


def _coerce(kind, v, datemode):
    if kind == "text":  return clean_text(v)
    if kind == "int":   return to_int(v)
    if kind == "float": return to_float(v)
    if kind == "pct":   return to_pct(v)
    if kind == "date":  return excel_to_date(v, datemode)
    return v


def _parse_scheme_details(ws, datemode) -> SchemeRecord:
    fields: dict = {}

    for col, (attr, kind) in _SCHEME_IDENTITY_MAP.items():
        if col >= ws.ncols:
            continue
        v = ws.cell_value(2, col)
        fields[attr] = _coerce(kind, v, datemode)

    scheme_codes = {}
    for col in _SCHEME_CODE_COLS:
        if col >= ws.ncols:
            continue
        label = clean_text(ws.cell_value(1, col))
        val = clean_text(ws.cell_value(2, col))
        if label:
            key = snake_case(label.replace("Scheme Code -", "").replace("Scheme Code-", ""))
            scheme_codes[key] = val
    fields["scheme_codes"] = scheme_codes

    regions = {}
    for col in _REGION_COLS:
        if col >= ws.ncols:
            continue
        label = clean_text(ws.cell_value(1, col))
        val = to_float(ws.cell_value(2, col))
        if label:
            regions[snake_case(label)] = val
    fields["regions"] = regions

    if _SALARY_DEF_COL < ws.ncols:
        fields["salary_definition"] = clean_text(ws.cell_value(2, _SALARY_DEF_COL))
    if _PEAR_TGP_COL < ws.ncols:
        fields["pear_tgp_pct"] = to_pct(ws.cell_value(2, _PEAR_TGP_COL))

    section_bounds = _find_section_bounds(ws)

    for marker, attr in _SECTION_MARKERS.items():
        bounds = section_bounds.get(marker)
        if bounds is None:
            continue
        start, end = bounds
        block = {}
        for c in range(start, end):
            label = clean_text(ws.cell_value(1, c))
            if not label:
                continue
            if label not in _ALL_PRODUCT_LABELS:
                continue
            raw = ws.cell_value(2, c)
            if attr == "rating":
                block[_product_key_from_label(label)] = clean_text(raw)
            else:
                block[_product_key_from_label(label)] = to_float(raw)
        fields[attr] = block

    esw_bounds = section_bounds.get(_ESWATINI_MARKER)
    if esw_bounds is not None:
        start, _ = esw_bounds
        fields["eswatini"] = to_float(ws.cell_value(2, start))

    basic_fees = _parse_basic_fees(ws)
    fields["basic_fees"] = basic_fees

    return SchemeRecord(**fields)


def _find_section_bounds(ws) -> dict[str, tuple[int, int]]:
    markers = []
    for c in range(ws.ncols):
        v = clean_text(ws.cell_value(0, c))
        if v and v in list(_SECTION_MARKERS.keys()) + [_ESWATINI_MARKER, _BASIC_FEES_MARKER,
                                                        "Scheme Details", "Commission Structre",
                                                        "Fee Structure"]:
            markers.append((c, v))
    bounds = {}
    for i, (c, name) in enumerate(markers):
        end = markers[i + 1][0] if i + 1 < len(markers) else ws.ncols
        bounds[name] = (c, end)
    return bounds


def _parse_basic_fees(ws) -> dict[str, float]:
    if ws.nrows < 6:
        return {}
    out = {}
    for c in range(ws.ncols):
        label = clean_text(ws.cell_value(4, c))
        if not label:
            continue
        if label not in _ALL_PRODUCT_LABELS:
            continue
        val = to_float(ws.cell_value(5, c))
        out[_product_key_from_label(label)] = val
    return out


_LABEL_TO_KEY: dict[str, str] = {}
def _build_label_index():
    for key, spec in PRODUCT_REGISTRY.items():
        for lbl in {spec.rating_label, spec.pricing_basis_label,
                    spec.fees_priced_label, spec.basic_fee_label}:
            _LABEL_TO_KEY[lbl] = key
    _LABEL_TO_KEY["Extended Family Cover"] = "ext_fc"
_build_label_index()


_OUT_OF_SCOPE_LABELS = frozenset({
    "Educate a Child",
    "Spouses & Childrens Income Cover", "S & C Income Cover",
  
})
_ALT_SCHEME_DETAILS_LABELS = frozenset({"Extended Family Cover"})

def _build_all_product_labels() -> frozenset:
    labels = set(_OUT_OF_SCOPE_LABELS)
    labels.update(_ALT_SCHEME_DETAILS_LABELS)
    for spec in PRODUCT_REGISTRY.values():
        labels.update({spec.rating_label, spec.pricing_basis_label,
                       spec.fees_priced_label, spec.basic_fee_label})
    return frozenset(labels)
_ALL_PRODUCT_LABELS = _build_all_product_labels()


def _product_key_from_label(label: str) -> str:
    if label in _LABEL_TO_KEY:
        return _LABEL_TO_KEY[label]
    return snake_case(label)


_CAT_NUM_CATEGORIES_CELL = (0, 7)
_CAT_TOGGLES_ROW = 3
_CAT_LABEL_ROW = 2
_CAT_DATA_START_ROW = 3
_CAT_TOGGLE_COLS = list(range(1, 17))

_SPOUSE_SOURCE_FLAG_CELL = (0, 61)


def _read_spouse_source_flag(ws) -> bool:
    row, col = _SPOUSE_SOURCE_FLAG_CELL
    if ws.nrows <= row or ws.ncols <= col:
        return False
    raw = clean_text(ws.cell_value(row, col))
    if raw is None:
        return False
    return raw.strip().lower() == "yes"


_PRODUCT_OPTION_ORDER = (
    "gla", "lsdb", "ci",
    "spouse_gla", "spouse_lsdb",
    "terminal",
    "accident_death", "accident_disability", "dismemberment",
)

_PRODUCT_OPT_START_ROW = 26
_PRODUCT_OPT_EXPRESS_COL = 35
_EDUCATION_OPT_ROW_OFFSET = 10
_PRODUCT_OPT_SPLIT_COL   = 36


_CATEGORY_SPLIT_SHEET_ORDER = ("gla", "gip", "tip", "fc")
_CATEGORY_SPLIT_ROW_OFFSETS = (0, 1, 2, 3)


def _read_product_options(ws) -> dict[str, bool]:
    express: dict[str, bool] = {}
    for i, pkey in enumerate(_PRODUCT_OPTION_ORDER):
        row = _PRODUCT_OPT_START_ROW + i
        if row >= ws.nrows:
            express[pkey] = False
            continue
        aj = ws.cell_value(row, _PRODUCT_OPT_EXPRESS_COL)
        express[pkey] = _to_bool(aj)
    return express


def _read_education_express_over_gla(ws) -> bool:
    row = _PRODUCT_OPT_START_ROW + _EDUCATION_OPT_ROW_OFFSET
    if row >= ws.nrows:
        return False
    aj = ws.cell_value(row, _PRODUCT_OPT_EXPRESS_COL)
    return _to_bool(aj)


def _read_category_split(ws) -> dict[str, str]:
    cat_split: dict[str, str] = {}
    for sheet_key, offset in zip(_CATEGORY_SPLIT_SHEET_ORDER, _CATEGORY_SPLIT_ROW_OFFSETS):
        row = _PRODUCT_OPT_START_ROW + offset
        if row >= ws.nrows:
            cat_split[sheet_key] = ""
            continue
        ak = ws.cell_value(row, _PRODUCT_OPT_SPLIT_COL)
        cat_split[sheet_key] = "" if ak in (None, "") else str(ak).strip()
    return cat_split


def _to_bool(v) -> bool:
    if v is None:
        return False
    return str(v).strip().lower() in ("yes", "y", "true", "1")


_PRB_OVERALL_FLAG_CELL = (0, 19)
_PRB_LOADING_CELL      = (0, 20)

_PRB_PRODUCT_FLAG_COLS = {
    "gla":      21,
    "lsdb":     22,
    "ci":       23,

    "gip":      24,
    "fc":       25,
    "tip": 26,
}

_RATE_BASIS_ROW = 2

_RATE_BASIS_PRODUCT_COLS = {
    'gla': 80, 'lsdb': 81, 'ci': 82, 'spouse_gla': 83, 'spouse_lsdb': 84,
    'terminal': 85, 'accident_death': 86, 'accident_disability': 87, 'dismemberment': 88,
    'educate_a_child': 90, 'fc': 91, 'gip': 93, 'tip': 94,
}

def _safe_cell_value(ws, row: int, col: int):
    if row < 0 or col < 0:
        return None
    if row >= ws.nrows or col >= ws.ncols:
        return None
    return ws.cell_value(row, col)


def _normalise_prb_pct(value) -> float | None:
    pct = to_pct(value)

    if pct is None:
        return None

    try:
        pct = float(pct)
    except (TypeError, ValueError):
        return None

    if pct > 1:
        pct = pct / 100.0

    return pct


def _read_profit_refund_loading(ws) -> dict[str, float]:
    overall_row, overall_col = _PRB_OVERALL_FLAG_CELL
    pct_row, pct_col = _PRB_LOADING_CELL

    overall_yes = _to_bool(_safe_cell_value(ws, overall_row, overall_col))
    prb_pct = _normalise_prb_pct(_safe_cell_value(ws, pct_row, pct_col))

    if not overall_yes or prb_pct is None:
        return {}

    out: dict[str, float] = {}

    for product_key, col in _PRB_PRODUCT_FLAG_COLS.items():
        if _to_bool(_safe_cell_value(ws, 0, col)):
            out[product_key] = prb_pct

    return out

def _read_rate_basis(ws) -> dict[str, float]:
    out: dict[str, float] = {}
    for pkey, col in _RATE_BASIS_PRODUCT_COLS.items():
        raw = _safe_cell_value(ws, _RATE_BASIS_ROW, col)
        val = to_float(raw)
        out[pkey] = 1.0 if val is None else float(val)
    return out

_MAX_COVER_COL: dict[tuple[str, str], int] = {
    ("lsdb",                "accelerator"): 35,
    ("lsdb",                "standalone"):  79,
    ("ci",                  "accelerator"): 52,
    ("ci",                  "standalone"):  96,
    ("spouse_gla",          "default"):     64,
    ("accident_death",      "default"):    115,
    ("accident_disability", "default"):    120,
    ("dismemberment",       "default"):    125,
}

_MAX_COVER_ROW_START = 3
_MAX_COVER_ROW_END   = 23


_FC_CATINPUT_FIELD_MAP: dict[int, tuple[str, str]] = {
    138: ("fc_max_no_of_spouses",       "float"),
    139: ("fc_parents_included",        "text"),
    140: ("fc_parents_in_law_included", "text"),
    141: ("fc_include_ctc",             "text"),
    142: ("fc_paid_up_benefits_active", "text"),
    143: ("fc_pup_death",               "text"),
    144: ("fc_pup_lsdb",                "text"),
    145: ("fc_pup_death_lsdb",          "text"),
}

_FC_BENEFIT_AMOUNT_COLS: dict[int, str] = {
    146: "primary_insured",
    147: "spouse",
    148: "child_14_plus",
    149: "child_6_13",
    150: "child_1_5",
    151: "child_under_1",
    152: "stillborn",
    153: "parents_cover",
}


def _read_fc_catinput_fields(
    ws,
    active_categories: dict[str, tuple[int, ...]],
) -> dict[str, dict]:
    out: dict[str, dict] = {
        attr: {} for attr, _ in _FC_CATINPUT_FIELD_MAP.values()
    }
    out["fc_benefit_amounts"] = {}

    fc_active_cats = active_categories.get("fc", ())
    if not fc_active_cats:
        return out

    if ws.ncols <= 153:
        return out

    for cat_num in fc_active_cats:
        row = _CAT_DATA_START_ROW + int(cat_num) - 1
        if row >= ws.nrows:
            continue

        for col, (attr, kind) in _FC_CATINPUT_FIELD_MAP.items():
            raw = _safe_cell_value(ws, row, col)
            if kind == "float":
                val = to_float(raw)
            else:
                val = clean_text(raw)
            if val is not None:
                out[attr][int(cat_num)] = val

        benefits: dict[str, float] = {}
        for col, benefit_key in _FC_BENEFIT_AMOUNT_COLS.items():
            val = to_float(_safe_cell_value(ws, row, col))
            if val is not None:
                benefits[benefit_key] = val
        if benefits:
            out["fc_benefit_amounts"][int(cat_num)] = benefits

    return out


_EXT_FC_CATINPUT_FIELD_MAP: dict[int, tuple[str, str]] = {
    244: ("ext_fc_extended_family_selected", "text"),
    245: ("ext_fc_parents_selected",         "text"),
    246: ("ext_fc_pct_male",                 "float"),
    247: ("ext_fc_avg_salary",               "float"),
    248: ("ext_fc_parent_dist_available",    "text"),
    249: ("ext_fc_family_dist_available",    "text"),
}

_EXT_FC_AGE_BAND_COLS: dict[int, str] = {
    250: "parents",
    251: "extended_family",
}


def _parse_ext_fc_age_band_string(raw) -> list[dict]:
    if not raw or not isinstance(raw, str):
        return []

    bands = []
    for chunk in raw.split(';'):
        chunk = chunk.strip()
        if not chunk:
            continue
        parts = [p.strip() for p in chunk.split(',')]
        if len(parts) != 3:
            continue
        start_s, end_s, cover_s = parts
        if not start_s and not end_s and not cover_s:
            continue
        try:
            start_age = float(start_s) if start_s else None
            end_age = float(end_s) if end_s else None
            cover = float(cover_s) if cover_s else None
        except ValueError:
            continue
        if start_age is None or end_age is None or cover is None:
            continue
        bands.append({"start_age": start_age, "end_age": end_age, "cover": cover})
    return bands


def _read_ext_fc_catinput_fields(
    ws,
    active_categories: dict[str, tuple[int, ...]],
) -> dict[str, dict]:
    out: dict[str, dict] = {
        attr: {} for attr, _ in _EXT_FC_CATINPUT_FIELD_MAP.values()
    }
    out["ext_fc_age_bands"] = {}

    ext_fc_active_cats = active_categories.get("ext_fc", ())
    if not ext_fc_active_cats:
        return out

    if ws.ncols <= 251:
        return out

    for cat_num in ext_fc_active_cats:
        row = _CAT_DATA_START_ROW + int(cat_num) - 1
        if row >= ws.nrows:
            continue

        for col, (attr, kind) in _EXT_FC_CATINPUT_FIELD_MAP.items():
            raw = _safe_cell_value(ws, row, col)
            if kind == "float":
                val = to_float(raw)
            else:
                val = clean_text(raw)
            if val is not None:
                out[attr][int(cat_num)] = val

        bands_for_cat: dict[str, list] = {}
        for col, band_key in _EXT_FC_AGE_BAND_COLS.items():
            raw = _safe_cell_value(ws, row, col)
            parsed = _parse_ext_fc_age_band_string(raw)
            if parsed:
                bands_for_cat[band_key] = parsed
        if bands_for_cat:
            out["ext_fc_age_bands"][int(cat_num)] = bands_for_cat

    return out


_GIP_CATLEVEL_MAP: dict[int, tuple[str, str]] = {
    156: ("gip_nra", "raw"),               157: ("gip_cover_type", "text"),
    158: ("gip_tax_options", "text"),      159: ("gip_escalation_type", "text"),
    160: ("gip_escalation_pct", "float"),  161: ("gip_waiting_period", "text"),
    162: ("gip_backdating", "text"),       163: ("gip_include_conversion_option", "text"),
    164: ("gip_pre_ex_before", "text"),    165: ("gip_pre_ex_after", "text"),
    166: ("gip_core_disability_definition", "text"), 167: ("gip_initial_period", "text"),
    176: ("gip_core_benefit_perc_additional", "float"),
    177: ("gip_core_benefit_layer_additional", "float"),
    178: ("gip_core_overall_maximum", "float"),
    179: ("gip_employer_waiver", "float"), 180: ("gip_employee_waiver", "float"),
    181: ("gip_hcc_disability_definition", "text"),
    190: ("gip_hcc_overall_maximum", "float"), 191: ("gip_include_hcc_waiver", "text"),
    192: ("gip_hcc_payment_period", "float"),  193: ("gip_hcc_payment_type", "text"),
    194: ("gip_hcc_nominal_amount", "float"),  195: ("gip_hcc_escalation_type", "text"),
    196: ("gip_hcc_escalation_pct", "float"),  239: ("gip_waiver_pct_specified", "text"),
}
_GIP_CORE_PERC_COLS  = [168, 170, 172, 174]
_GIP_CORE_LAYER_COLS = [169, 171, 173, 175]
_GIP_HCC_PERC_COLS   = [182, 184, 186, 188]
_GIP_HCC_LAYER_COLS  = [183, 185, 187, 189]
_GIP_ROW_START, _GIP_ROW_END = 3, 23

def _coerce_gip(kind, v):
    if kind == "text":  return clean_text(v)
    if kind == "float": return to_float(v)
    if kind == "int":   return to_int(v)
    return v

def _read_gip_catinput_fields(ws, active_categories: dict[str, tuple[int, ...]]) -> dict[str, dict]:
    gip_active = set(active_categories.get("gip", ()))
    out: dict[str, dict] = {attr: {} for attr, _ in _GIP_CATLEVEL_MAP.values()}
    for k in ("gip_core_benefit_perc", "gip_core_benefit_layer",
              "gip_hcc_benefit_perc",  "gip_hcc_benefit_layer"):
        out[k] = {}
    for r in range(_GIP_ROW_START, min(_GIP_ROW_END, ws.nrows)):
        if _safe_cell_value(ws, r, 155) in (None, ""):
            continue
        cat = r - 2
        if gip_active and cat not in gip_active:
            continue
        for col, (attr, kind) in _GIP_CATLEVEL_MAP.items():
            out[attr][cat] = _coerce_gip(kind, _safe_cell_value(ws, r, col))
        out["gip_core_benefit_perc"][cat]  = [to_float(_safe_cell_value(ws, r, c)) for c in _GIP_CORE_PERC_COLS]
        out["gip_core_benefit_layer"][cat] = [to_float(_safe_cell_value(ws, r, c)) for c in _GIP_CORE_LAYER_COLS]
        out["gip_hcc_benefit_perc"][cat]   = [to_float(_safe_cell_value(ws, r, c)) for c in _GIP_HCC_PERC_COLS]
        out["gip_hcc_benefit_layer"][cat]  = [to_float(_safe_cell_value(ws, r, c)) for c in _GIP_HCC_LAYER_COLS]
    return out

_TIP_CATLEVEL_MAP: dict[int, tuple[str, str]] = {
    199: ("tip_nra", "raw"),                200: ("tip_cover_type", "text"),
    201: ("tip_tax_options", "text"),       202: ("tip_escalation_type", "text"),
    203: ("tip_escalation_pct", "float"),   204: ("tip_waiting_period", "text"),
    205: ("tip_backdating", "text"),        206: ("tip_pre_ex_before", "text"),
    207: ("tip_pre_ex_after", "text"),      208: ("tip_core_disability_definition", "text"),
    209: ("tip_initial_period", "text"),
    218: ("tip_core_benefit_perc_additional", "float"),
    219: ("tip_core_benefit_layer_additional", "float"),
    220: ("tip_core_overall_maximum", "float"),
    221: ("tip_employer_waiver", "float"),  222: ("tip_employee_waiver", "float"),
    223: ("tip_hcc_disability_definition", "text"),
    232: ("tip_hcc_overall_maximum", "float"), 233: ("tip_include_hcc_waiver", "text"),
    234: ("tip_hcc_payment_period", "float"),  235: ("tip_hcc_payment_type", "text"),
    236: ("tip_hcc_nominal_amount", "float"),  237: ("tip_hcc_escalation_type", "text"),
    238: ("tip_hcc_escalation_pct", "float"),  240: ("tip_payment_period", "text"),
    241: ("tip_waiver_pct_specified", "text"),
}
_TIP_CORE_PERC_COLS  = [210, 212, 214, 216]
_TIP_CORE_LAYER_COLS = [211, 213, 215, 217]
_TIP_HCC_PERC_COLS   = [224, 226, 228, 230]
_TIP_HCC_LAYER_COLS  = [225, 227, 229, 231]
_TIP_ROW_START, _TIP_ROW_END = 3, 23

def _read_tip_catinput_fields(ws, active_categories):
    tip_active = set(active_categories.get("tip", ()))
    out = {attr: {} for attr, _ in _TIP_CATLEVEL_MAP.values()}
    for k in ("tip_core_benefit_perc","tip_core_benefit_layer",
              "tip_hcc_benefit_perc","tip_hcc_benefit_layer"):
        out[k] = {}
    for r in range(_TIP_ROW_START, min(_TIP_ROW_END, ws.nrows)):
        if _safe_cell_value(ws, r, 198) in (None, ""):
            continue
        cat = r - 2
        if tip_active and cat not in tip_active:
            continue
        for col, (attr, kind) in _TIP_CATLEVEL_MAP.items():
            out[attr][cat] = _coerce_gip(kind, _safe_cell_value(ws, r, col))
        out["tip_core_benefit_perc"][cat]  = [to_float(_safe_cell_value(ws, r, c)) for c in _TIP_CORE_PERC_COLS]
        out["tip_core_benefit_layer"][cat] = [to_float(_safe_cell_value(ws, r, c)) for c in _TIP_CORE_LAYER_COLS]
        out["tip_hcc_benefit_perc"][cat]   = [to_float(_safe_cell_value(ws, r, c)) for c in _TIP_HCC_PERC_COLS]
        out["tip_hcc_benefit_layer"][cat]  = [to_float(_safe_cell_value(ws, r, c)) for c in _TIP_HCC_LAYER_COLS]
    return out

def _read_max_cover_caps(
    ws,
    scheme: SchemeRecord,
    products: tuple[str, ...],
    active_categories: dict[str, tuple[int, ...]] | None = None,
) -> dict[str, dict[int, float | None]]:
    caps: dict[str, dict[int, float | None]] = {}

    for pkey in products:
        spec = PRODUCT_REGISTRY[pkey]
        variant = _pick_variant(spec, scheme, [])
        if variant is None:
            continue

        col = _MAX_COVER_COL.get((pkey, variant))
        if col is None:
            continue

        product_caps: dict[int, float | None] = {}
        active_set = set(active_categories.get(pkey, ())) if active_categories else None

        for r in range(_MAX_COVER_ROW_START, min(_MAX_COVER_ROW_END, ws.nrows)):
            cat_num = to_int(ws.cell_value(r, 0))
            if cat_num is None:
                continue
            if active_set is not None and cat_num not in active_set:
                continue
            raw = ws.cell_value(r, col)
            val = to_float(raw)
            if val is None or val <= 0:
                product_caps[cat_num] = None
            else:
                product_caps[cat_num] = val
        caps[pkey] = product_caps

    return caps


def _parse_catinput(ws, scheme: SchemeRecord, products: tuple[str, ...]
                    ) -> tuple[pl.DataFrame, dict[str, tuple[int, ...]], list[str]]:
    warnings = []

    num_cats = to_int(ws.cell_value(*_CAT_NUM_CATEGORIES_CELL)) or 0
    if num_cats <= 0:
        warnings.append("Could not determine number of categories from row 0 col 7")
        return pl.DataFrame(schema={"product": pl.Utf8, "category_number": pl.Int64,
                                    "field": pl.Utf8, "value": pl.Utf8}), {}, warnings

    toggle_labels = {}
    for c in _CAT_TOGGLE_COLS:
        lbl = clean_text(ws.cell_value(2, c))
        if lbl:
            toggle_labels[c] = _product_key_from_label(lbl)

    active_categories: dict[str, list[int]] = {p: [] for p in products}
    for cat_i in range(num_cats):
        row = _CAT_DATA_START_ROW + cat_i
        cat_num = to_int(ws.cell_value(row, 0))
        if cat_num is None:
            continue
        for col, prod_key in toggle_labels.items():
            if prod_key not in products:
                continue
            val = clean_text(ws.cell_value(row, col))
            if val and val.lower() == "yes":
                active_categories[prod_key].append(cat_num)

    long_rows: list[dict] = []
    for prod_key in products:
        spec = PRODUCT_REGISTRY[prod_key]

        variant = _pick_variant(spec, scheme, warnings)
        if variant is None:
            continue
        block = spec.catinput_blocks.get(variant)
        if block is None:
            warnings.append(f"[{prod_key}] no block for variant '{variant}'")
            continue
        start, end = block

        fields_in_block = {}
        for c in range(start, min(end, ws.ncols)):
            lbl = clean_text(ws.cell_value(_CAT_LABEL_ROW, c))
            if lbl:
                fields_in_block[c] = snake_case(lbl)

        active = set(active_categories.get(prod_key, []))
        for cat_i in range(num_cats):
            row = _CAT_DATA_START_ROW + cat_i
            cat_num = to_int(ws.cell_value(row, 0))
            if cat_num is None or cat_num not in active:
                continue
            for c, fld in fields_in_block.items():
                raw = ws.cell_value(row, c)
                val = clean_text(raw) if not isinstance(raw, (int, float)) else raw
                long_rows.append({
                    "product": prod_key,
                    "variant": variant,
                    "category_number": cat_num,
                    "field": fld,
                    "value": None if val is None else str(val),
                })

    df = pl.DataFrame(long_rows, schema={
        "product": pl.Utf8, "variant": pl.Utf8,
        "category_number": pl.Int64, "field": pl.Utf8, "value": pl.Utf8,
    }) if long_rows else pl.DataFrame(schema={
        "product": pl.Utf8, "variant": pl.Utf8,
        "category_number": pl.Int64, "field": pl.Utf8, "value": pl.Utf8,
    })

    active_frozen = {p: tuple(sorted(cs)) for p, cs in active_categories.items()}
    return df, active_frozen, warnings


def _pick_variant(spec: ProductSpec, scheme: SchemeRecord, warnings: list[str]) -> str | None:
    rating = (scheme.rating or {}).get(spec.key)
    rating_norm = rating.strip() if isinstance(rating, str) else rating
    if spec.rating_type == "yes_no":
        if rating_norm and rating_norm.lower() != "no":
            return "default"
        return None
    if rating_norm == "Accelerator":
        return "accelerator"
    if rating_norm == "Standalone":
        return "standalone"
    if rating_norm in (None, "No"):
        return None
    warnings.append(f"[{spec.key}] unexpected rating '{rating}', defaulting to accelerator")
    return "accelerator"


def _parse_member_sheet(ws, datemode) -> pl.DataFrame:
    if ws.nrows < 3:
        return pl.DataFrame()

    headers = []
    for c in range(ws.ncols):
        lbl = clean_text(ws.cell_value(1, c))
        headers.append(snake_case(lbl) if lbl else f"col_{c}")

    DATE_HEADERS = {"date_of_birth", "date_of_disability"}
    GENDER_HEADERS = {"gender"}
    NUMERIC_HEADERS = {
        "annual_salary", "category_flag", "custom_nra", "nra",
        "gla", "lsdb", "ci", "spouses_life_cover", "spouses_db",
        "dismemberment", "accident_death", "accident_db",
        "terminal_illness", "non_reduced", "reduced",
        "spouses_childrens_or_educ_guardian",
        "waiver", "hcc_waiver",   
    }

    records = []
    for r in range(2, ws.nrows):
        row = {}
        first_val = ws.cell_value(r, 0)
        if first_val in (None, ""):
            continue
        for c, h in enumerate(headers):
            raw = ws.cell_value(r, c)
            if h in DATE_HEADERS:
                row[h] = excel_to_date(raw, datemode)
            elif h in GENDER_HEADERS:
                row[h] = normalize_gender(raw)
            elif h in NUMERIC_HEADERS:
                row[h] = to_float(raw)
            else:
                row[h] = clean_text(raw)
        records.append(row)

    if not records:
        return pl.DataFrame()
    return pl.DataFrame(records)


def _load_scheme_details_section(wb, available):
    if "Scheme Details - Save" not in available:
        raise ValueError("CTR is missing 'Scheme Details - Save' — cannot proceed")
    t = time.perf_counter()
    ws = wb.sheet_by_name("Scheme Details - Save")
    scheme = _parse_scheme_details(ws, wb.datemode)
    try:
        wb.unload_sheet("Scheme Details - Save")
    except Exception:
        logger.debug("Could not unload sheet %r", "Scheme Details - Save", exc_info=True)
    logger.info(f"  ✓ Scheme Details - Save parsed in {time.perf_counter()-t:.2f}s")

    if scheme.scheme_name is None and scheme.quote_number is None:
        raise RuntimeError(
            "[load_ctr] Scheme Details parsed but produced NO identity fields "
            "(scheme_name and quote_number both None). "
            "This usually means the reader returned empty strings for all label cells. "
            f"Sheet dims: {ws.nrows} rows × {ws.ncols} cols. "
            f"Available sheets: {sorted(available)}"
        )
    return scheme, ws


def _resolve_active_products_section(products, scheme, warnings):
    _EDUCATION_PKEYS = ("educate_a_child", "education_guardian")
    active_products = tuple(
        p for p in products
        if p not in _EDUCATION_PKEYS
        and _pick_variant(PRODUCT_REGISTRY[p], scheme, warnings) is not None
    )
    return active_products


@dataclass
class CatInputResult:
    spouse_use_sp_sheet: bool
    express_over_gla: dict
    category_split: dict
    max_cover_per_category: dict
    fc_catinput_fields: dict
    ext_fc_catinput_fields: dict
    education_product_pkey: str | None
    education_fields: dict


def _load_catinput_section(wb, available, scheme, active_products, warnings, ws):
    categories_df = pl.DataFrame()
    active_categories: dict[str, tuple[int, ...]] = {}
    spouse_use_sp_sheet = False
    express_over_gla: dict[str, bool] = {}
    category_split: dict[str, str] = {}
    max_cover_per_category: dict[str, dict[int, float | None]] = {}

    if "CatInput-Save" in available:
        t = time.perf_counter()
        ws = wb.sheet_by_name("CatInput-Save")

        spouse_use_sp_sheet = _read_spouse_source_flag(ws)

        express_over_gla = _read_product_options(ws)
        category_split = _read_category_split(ws)

        categories_df, active_categories, warns = _parse_catinput(ws, scheme, active_products)
        warnings.extend(warns)

        max_cover_per_category = _read_max_cover_caps(
            ws, scheme, active_products, active_categories
        )

        fc_catinput_fields = _read_fc_catinput_fields(ws, active_categories)

        ext_fc_catinput_fields = _read_ext_fc_catinput_fields(ws, active_categories)

        education_product_pkey = _resolve_education_product(ws)
        education_fields = _read_education_catinput_fields(ws)
        if education_product_pkey is not None:
            express_over_gla[education_product_pkey] = _read_education_express_over_gla(ws)
            active_edu_cats = tuple(sorted(
                c for c, is_on in education_fields["education_active"].items() if is_on
            ))
            active_categories[education_product_pkey] = active_edu_cats
            if (education_product_pkey in PRODUCT_REGISTRY
                    and education_product_pkey not in active_products):
                active_products = active_products + (education_product_pkey,)
        
        if "gip" in active_products:
            gip_fields = _read_gip_catinput_fields(ws, active_categories)
            scheme = replace(scheme, **gip_fields)

        if "tip" in active_products:
            tip_fields = _read_tip_catinput_fields(ws, active_categories)
            scheme = replace(scheme, **tip_fields)

        try:
            wb.unload_sheet("CatInput-Save")
        except Exception:
            logger.debug("Could not unload sheet %r", "CatInput-Save", exc_info=True)
        logger.info(f"  ✓ CatInput-Save parsed in {time.perf_counter()-t:.2f}s  "
              f"({categories_df.height} rows, BJ1 spouse_use_sp_sheet={spouse_use_sp_sheet})")
        logger.info(f"     express_over_gla = {express_over_gla}")
        logger.info(f"     category_split   = {category_split}")
        caps_preview = {
            p: {c: (f"{v:,.0f}" if v is not None else "—")
                for c, v in list(cats.items())[:3]}
            for p, cats in max_cover_per_category.items()
        }
        logger.info(f"     max_cover (first 3 cats/product): {caps_preview}")
        fc_cats_ct = len(fc_catinput_fields.get("fc_benefit_amounts", {}))
        if fc_cats_ct:
            first_fc_cat = next(iter(fc_catinput_fields["fc_benefit_amounts"]))
            first_benefits = fc_catinput_fields["fc_benefit_amounts"][first_fc_cat]
            logger.info(f"     fc: {fc_cats_ct} active cat(s); "
                  f"cat {first_fc_cat} benefits = "
                  f"{ {k: f'{v:,.0f}' for k, v in first_benefits.items()} }")
    else:
        warnings.append("CatInput-Save sheet missing")
        fc_catinput_fields = {
            "fc_max_no_of_spouses": {},
            "fc_parents_included": {},
            "fc_parents_in_law_included": {},
            "fc_include_ctc": {},
            "fc_paid_up_benefits_active": {},
            "fc_pup_death": {},
            "fc_pup_lsdb": {},
            "fc_pup_death_lsdb": {},
            "fc_benefit_amounts": {},
        }
        ext_fc_catinput_fields = {
            "ext_fc_extended_family_selected": {},
            "ext_fc_parents_selected": {},
            "ext_fc_pct_male": {},
            "ext_fc_avg_salary": {},
            "ext_fc_parent_dist_available": {},
            "ext_fc_family_dist_available": {},
            "ext_fc_age_bands": {},
        }
        education_product_pkey = None
        education_fields = {
            "education_active": {}, "education_include_ctc": {},
            "education_max_cover": {}, "education_nra": {},
            "education_deferred": {}, "education_deferred_age": {},
            "education_multiple": {},
        }

    cat_input_result = CatInputResult(
        spouse_use_sp_sheet=spouse_use_sp_sheet,
        express_over_gla=express_over_gla,
        category_split=category_split,
        max_cover_per_category=max_cover_per_category,
        fc_catinput_fields=fc_catinput_fields,
        ext_fc_catinput_fields=ext_fc_catinput_fields,
        education_product_pkey=education_product_pkey,
        education_fields=education_fields,
    )
    return (scheme, ws, active_products, categories_df, active_categories, cat_input_result)


def _finalize_scheme_with_catinput_and_rate_basis(wb, scheme, cat_input_result, ws):
    scheme = replace(
        scheme,
        spouse_use_sp_sheet=cat_input_result.spouse_use_sp_sheet,
        express_over_gla=cat_input_result.express_over_gla,
        category_split=cat_input_result.category_split,
        max_cover_per_category=cat_input_result.max_cover_per_category,
        profit_refund_loading_by_product=_read_profit_refund_loading(ws),
        education_product=cat_input_result.education_product_pkey,
        **cat_input_result.fc_catinput_fields,
        **cat_input_result.ext_fc_catinput_fields,
        **cat_input_result.education_fields,
    )

    sd_ws = wb.sheet_by_name("Scheme Details - Save")
    rate_basis = _read_rate_basis(sd_ws)
    scheme = replace(scheme, rate_basis_by_product=rate_basis)
    return scheme


def _load_member_data_sections(wb, available, active_products, spouse_use_sp_sheet, warnings):
    members: dict[str, pl.DataFrame] = {}
    needed_sheets: set[str] = set()
    for p in active_products:
        spec = PRODUCT_REGISTRY[p]
        if p in ("spouse_gla", "spouse_lsdb"):
            needed_sheets.add("Member Data - SP" if spouse_use_sp_sheet
                              else "Member Data - GLA")
        else:
            needed_sheets.add(spec.member_source)

    SKIP_SHEETS = {"Member Data", "GhostDraft Parameter Sheet"}
    for sheet_name in needed_sheets:
        if sheet_name in SKIP_SHEETS:
            continue
        if sheet_name not in available:
            warnings.append(f"Member sheet '{sheet_name}' missing")
            continue
        t = time.perf_counter()
        ws = wb.sheet_by_name(sheet_name)
        df = _parse_member_sheet(ws, wb.datemode)
        try:
            wb.unload_sheet(sheet_name)
        except Exception:
            logger.debug("Could not unload sheet %r", sheet_name, exc_info=True)
        role = "gla_sheet" if sheet_name.endswith("- GLA") else \
               "sp_sheet" if sheet_name.endswith("- SP") else \
               "ext_fc_sheet" if sheet_name.endswith("- Ext FC") else \
               "fc_sheet" if sheet_name.endswith("- FC") else \
               "gip_sheet" if sheet_name.endswith("- GIP") else \
               "tip_sheet" if sheet_name.endswith("- TIP") else \
               snake_case(sheet_name)
        members[role] = df
        logger.info(f"  ✓ {sheet_name} parsed in {time.perf_counter()-t:.2f}s  ({df.height} members)")
    return members


def load_ctr(ctr_path: str | Path,
             products: tuple[str, ...] | None = None) -> CTRBundle:
    ctr_path = Path(ctr_path)
    ctr_path = local_ctr_path(ctr_path)
    products = products or IN_SCOPE_PRODUCTS
    warnings: list[str] = []

    fp = _fingerprint(str(ctr_path))
    loaded_at = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")

    t0 = time.perf_counter()
    wb = _open_workbook(ctr_path)
    available = set(wb.sheet_names())

    scheme, ws = _load_scheme_details_section(wb, available)

    active_products = _resolve_active_products_section(products, scheme, warnings)

    (scheme, ws, active_products, categories_df, active_categories,
     cat_input_result) = _load_catinput_section(
        wb, available, scheme, active_products, warnings, ws)

    scheme = _finalize_scheme_with_catinput_and_rate_basis(
        wb, scheme, cat_input_result, ws)

    members = _load_member_data_sections(wb, available, active_products, cat_input_result.spouse_use_sp_sheet, warnings)

    total = time.perf_counter() - t0
    logger.info(f"  ⏱  Total CTR load: {total:.2f}s")

    return CTRBundle(
        ctr_path=ctr_path,
        ctr_fingerprint=fp,
        loaded_at=loaded_at,
        scheme=scheme,
        categories=categories_df,
        members=members,
        active_products=active_products,
        active_categories=active_categories,
        warnings=warnings,
    )

_EDU_CATEGORY_NAME_COL   = 131
_EDU_CATEGORY_FLAG_COL   = 132
_EDU_COVER_TYPE_COL      = 133
_EDU_INCLUDE_CTC_COL     = 134

_EDU_ACTIVE_TOGGLE_COL   = 15

_EDU_MAX_COVER_COL       = 13
_EDU_NRA_COL             = 25
_EDU_DEFERRED_COL        = 26
_EDU_DEFERRED_AGE_COL    = 27
_EDU_MULTIPLE_COL        = 31

_EDU_BLOCK_A_ROW_START, _EDU_BLOCK_A_ROW_END = 3, 23
_EDU_BLOCK_B_ROW_START, _EDU_BLOCK_B_ROW_END = 26, 46

_EDU_COVER_TYPE_TO_PKEY = {
    "Guardian": "education_guardian",
    "Multiple": "educate_a_child",
}


def _resolve_education_product(ws) -> str | None:
    for r in range(_EDU_BLOCK_A_ROW_START, min(_EDU_BLOCK_A_ROW_END, ws.nrows)):
        raw = clean_text(_safe_cell_value(ws, r, _EDU_COVER_TYPE_COL))
        if raw:
            return _EDU_COVER_TYPE_TO_PKEY.get(raw)
    return None


def _read_education_catinput_fields(ws) -> dict[str, dict]:
    out: dict[str, dict] = {
        "education_active":        {},
        "education_include_ctc":   {},
        "education_max_cover":     {},
        "education_nra":           {},
        "education_deferred":      {},
        "education_deferred_age":  {},
        "education_multiple":      {},
    }

    for r in range(_EDU_BLOCK_A_ROW_START, min(_EDU_BLOCK_A_ROW_END, ws.nrows)):
        cat = r - 2
        active_raw = clean_text(_safe_cell_value(ws, r, _EDU_ACTIVE_TOGGLE_COL))
        out["education_active"][cat] = _to_bool(active_raw)
        ctc_raw = clean_text(_safe_cell_value(ws, r, _EDU_INCLUDE_CTC_COL))
        if ctc_raw is not None:
            out["education_include_ctc"][cat] = ctc_raw

    for r in range(_EDU_BLOCK_B_ROW_START, min(_EDU_BLOCK_B_ROW_END, ws.nrows)):
        cat = r - 25
        max_cover_raw = _safe_cell_value(ws, r, _EDU_MAX_COVER_COL)
        if max_cover_raw not in (None, ""):
            out["education_max_cover"][cat] = to_float(max_cover_raw)
        nra_raw = _safe_cell_value(ws, r, _EDU_NRA_COL)
        if nra_raw not in (None, ""):
            out["education_nra"][cat] = nra_raw
        deferred_raw = clean_text(_safe_cell_value(ws, r, _EDU_DEFERRED_COL))
        if deferred_raw is not None:
            out["education_deferred"][cat] = deferred_raw
        deferred_age_raw = _safe_cell_value(ws, r, _EDU_DEFERRED_AGE_COL)
        if deferred_age_raw not in (None, ""):
            out["education_deferred_age"][cat] = to_float(deferred_age_raw)
        multiple_raw = _safe_cell_value(ws, r, _EDU_MULTIPLE_COL)
        if multiple_raw not in (None, ""):
            out["education_multiple"][cat] = to_float(multiple_raw)

    return out
