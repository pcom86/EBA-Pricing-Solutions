
from __future__ import annotations
from datetime import date, datetime
import re
import xlrd
import logging

logger = logging.getLogger(__name__)


def clean_text(v):
    if v is None or v == "":
        return None
    s = str(v).strip()
    return s if s else None


def to_float(v):
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(" ", "").replace(",", "")
    if s in ("", "-"):
        return 0.0 if s == "-" else None
    try:
        return float(s)
    except ValueError:
        return None


def to_int(v):
    f = to_float(v)
    return int(f) if f is not None else None


def to_pct(v):
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "")
    if s.endswith("%"):
        try:
            return float(s[:-1]) / 100.0
        except ValueError:
            return None
    try:
        return float(s)
    except ValueError:
        return None


def excel_to_date(v, datemode):
    if v is None or v == "":
        return None
    
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v 

    if isinstance(v, (int, float)) and v > 0:
        try:
            y, m, d, *_ = xlrd.xldate_as_tuple(v, datemode)
            return date(y, m, d)
        except Exception as e:
            logger.warning(f"[ctr_coerce] excel_to_date failed for serial {v!r} (datemode={datemode}): {e}")
            return None
    s = str(v).strip()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def normalize_gender(v):
    s = clean_text(v)
    if s is None:
        return None
    s = s.upper()[:1]
    return s if s in ("M", "F") else None


_snake_re1 = re.compile(r"[^\w\s]+")
_snake_re2 = re.compile(r"\s+")
_snake_re3 = re.compile(r"_+")

def snake_case(s: str) -> str:
    if s is None:
        return ""
    s = str(s).strip().lower()
    s = _snake_re1.sub(" ", s)
    s = _snake_re2.sub("_", s)
    s = _snake_re3.sub("_", s)
    return s.strip("_")
