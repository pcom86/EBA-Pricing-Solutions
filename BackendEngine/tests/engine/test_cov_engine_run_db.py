"""Coverage tests for pricing_engine.db_loader.

Everything here runs without a live SQL Server: pyodbc.connect is patched
to return scripted fake connections/cursors, and Storage/S3 interactions
use a real local-directory Storage or a recording fake. The genuine
pyodbc wire behaviour (real connection, executemany batching, rollback
semantics) is not exercised -- no database is available in this env.
"""
from __future__ import annotations

import hashlib
import io
import json
import tempfile
import uuid
from datetime import date
from pathlib import Path

import pandas as pd
import polars as pl
import pricing_engine.db_loader as dbl
import pytest
from ctr_loader.ctr_schemas import CTRBundle, SchemeRecord

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeCursor:
    """Scriptable pyodbc cursor.

    fetchone_map / fetchall_map keys are matched as substrings of the most
    recently executed SQL statement. fail_on raises when the substring is
    present in an executed statement.
    """

    def __init__(self, fetchone_map=None, fetchall_map=None, fail_on=None):
        self.fetchone_map = fetchone_map or {}
        self.fetchall_map = fetchall_map or {}
        self.fail_on = fail_on
        self.executed = []          # list of (sql, params)
        self.batches = []           # list of (sql, [row, ...])
        self.fast_executemany = False
        self.closed = False
        self._last_sql = ""

    def execute(self, sql, *params):
        self._last_sql = sql
        self.executed.append((sql, params))
        if self.fail_on and self.fail_on in sql:
            raise RuntimeError(f"boom: {self.fail_on}")
        return self

    def executemany(self, sql, rows):
        self._last_sql = sql
        self.batches.append((sql, list(rows)))
        return self

    def fetchone(self):
        for key, val in self.fetchone_map.items():
            if key in self._last_sql:
                return val
        return None

    def fetchall(self):
        for key, val in self.fetchall_map.items():
            if key in self._last_sql:
                return val
        return []

    def close(self):
        self.closed = True

    def sql_texts(self):
        return [s for s, _ in self.executed]

    def batch_sql_texts(self):
        return [s for s, _ in self.batches]


class FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def cursor(self):
        return self._cursor

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


class FakeS3:
    """Recording stand-in for gap_config.storage.Storage."""

    def __init__(self, objects=None):
        self.objects = dict(objects or {})  # key -> bytes
        self.put_calls = []

    def exists(self, key):
        return key in self.objects

    def put_file(self, key, local_path):
        self.put_calls.append(key)
        self.objects[key] = Path(local_path).read_bytes()
        return key

    def get_path(self, key):
        tmp = Path(tempfile.mkdtemp()) / Path(key).name
        tmp.write_bytes(self.objects[key])
        return tmp


def _patch_pyodbc(monkeypatch, conn):
    import pyodbc
    if isinstance(conn, Exception):
        monkeypatch.setattr(
            pyodbc, "connect",
            lambda *a, **k: (_ for _ in ()).throw(conn))
    else:
        monkeypatch.setattr(pyodbc, "connect", lambda *a, **k: conn)


def _scheme():
    return SchemeRecord(
        quote_type="New", quote_group="G1", quote_number=7,
        doer="doer", checker="checker", scheme_name="Test Scheme",
        scheme_codes={"gla": "GLA01"},
        effective_date=date(2025, 1, 1),
        calculation_date=date(2025, 2, 1),
        commencement_date=date(2025, 1, 1),
        intermediary="i", broker_name="b", client_manager="cm",
        branch="JHB", region="GP",
        salary_definition="Annual", premium_frequency="Monthly",
        rating={"gla": "Yes", "gip": "Yes"},
        pricing_basis={"gla": "basis-x"},
        gip_nra={1: "65"},
        max_cover_per_category={"gla": {2: 500_000.0}},
        fc_max_no_of_spouses={1: 2.0},
    )


def _ctr(members=None, warnings=None):
    members = members or {
        "gla_sheet": pl.DataFrame({
            "member_name": ["John Smith", "Jane Doe"],
            "date_of_birth": ["1980-01-01", "1990-05-05"],
            "gender": ["M", "F"],
            "annual_salary": [100_000.0, 200_000.0],
        }),
    }
    return CTRBundle(
        ctr_path=Path("C:/inputs/test.ctr"),
        ctr_fingerprint="fp-deadbeef-001",
        loaded_at="2025-01-01T00:00:00Z",
        scheme=_scheme(),
        categories=pl.DataFrame({
            "product": ["gla", "gla", "gla"],
            "variant": ["default", "default", "default"],
            "category_number": [1, 1, 1],
            "field": ["category_name", "benefit_multiple", "category_flag"],
            "value": ["Staff", "2.5", "not-a-number"],
        }),
        members=members,
        active_products=("gla", "gip"),
        active_categories={"gla": (1,), "gip": (1,)},
        warnings=warnings if warnings is not None else ["w1"],
    )


# ===========================================================================
# Pure helpers
# ===========================================================================

class TestSoundex:
    @pytest.mark.parametrize("name,expected", [
        ("", ""),
        ("A", "A000"),
        ("Robert", "R163"),
        ("Rupert", "R163"),
        ("Smith", "S530"),
        ("Tymczak", "T522"),
        ("smith", "S530"),  # case-insensitive
    ])
    def test_soundex(self, name, expected):
        assert dbl._soundex(name) == expected


class TestMatchScore:
    def test_dob_mismatch_is_zero(self):
        assert dbl.match_score(
            "John Smith", "1980-01-01", "M", 100_000.0,
            "John Smith", "1980-01-02", "M", 100_000.0) == 0.0

    def test_identical_is_perfect(self):
        score = dbl.match_score(
            "John Smith", "1980-01-01", "M", 100_000.0,
            "John Smith", "1980-01-01", "M", 100_000.0)
        assert score == 100.0

    def test_gender_mismatch_penalised(self):
        # 100 * 0.2 gender penalty, then +5 for the ~equal salary ratio
        score = dbl.match_score(
            "John Smith", "1980-01-01", "M", 100_000.0,
            "John Smith", "1980-01-01", "F", 100_000.0)
        assert score == pytest.approx(25.0)

    def test_salary_bands(self):
        near = dbl.match_score("John Smith", "1980-01-01", "M", 100_000.0,
                               "John Smith", "1980-01-01", "M", 105_000.0)
        far = dbl.match_score("John Smith", "1980-01-01", "M", 100_000.0,
                              "John Smith", "1980-01-01", "M", 300_000.0)
        assert near == 100.0          # ratio <= 1.15 -> +5 capped
        assert far == 85.0            # ratio > 2 -> -15
        mid = dbl.match_score("John Smith", "1980-01-01", "M", 100_000.0,
                              "John Smith", "1980-01-01", "M", 160_000.0)
        assert mid == 95.0            # 1.4 < ratio <= 2.0 -> -5
        neutral = dbl.match_score("John Smith", "1980-01-01", "M", 100_000.0,
                                  "John Smith", "1980-01-01", "M", 130_000.0)
        assert neutral == 100.0       # 1.15 < ratio <= 1.40 -> unchanged

    def test_missing_salary_no_adjustment(self):
        score = dbl.match_score("John Smith", "1980-01-01", "M", None,
                                "John Smith", "1980-01-01", "M", 50.0)
        assert score == 100.0

    def test_phonetic_match_floor(self):
        # soundex-equal but not identical names score at least 90
        score = dbl.match_score("Robert", "1980-01-01", "M", None,
                                "Rupert", "1980-01-01", "M", None)
        assert score >= 90.0
        assert score <= 100.0


class TestEncodeValue:
    @pytest.mark.parametrize("val,expected", [
        ([1, 2, 3], "1,2,3"),
        (["a", "b"], "a,b"),
        (None, None),
        (5, "5"),
        ("x", "x"),
    ])
    def test_encode(self, val, expected):
        assert dbl._encode_value(val) == expected


class TestHashFile:
    def test_sha256(self, tmp_path):
        f = tmp_path / "blob.bin"
        f.write_bytes(b"hello world")
        assert dbl._hash_file(f) == hashlib.sha256(b"hello world").hexdigest()


class TestSqlSeqGuid:
    def test_returns_parseable_uuid(self):
        g = dbl.sql_seq_guid()
        assert isinstance(g, str)
        assert uuid.UUID(g)  # raises if malformed

    def test_unique(self):
        assert dbl.sql_seq_guid() != dbl.sql_seq_guid()


class TestSchemeRecordCodec:
    def test_roundtrip_restores_typed_fields(self):
        scheme = _scheme()
        json_str = dbl.encode_scheme_record(scheme)
        assert isinstance(json_str, str)
        raw = json.loads(json_str)
        # dates serialised to ISO strings
        assert raw["commencement_date"] == "2025-01-01"
        # int dict keys serialised to strings by json
        assert raw["gip_nra"] == {"1": "65"}

        back = dbl.decode_scheme_record(json_str)
        assert isinstance(back, SchemeRecord)
        assert back.scheme_name == "Test Scheme"
        assert back.quote_number == 7
        assert back.commencement_date == date(2025, 1, 1)
        assert back.gip_nra == {1: "65"}
        assert back.max_cover_per_category == {"gla": {2: 500_000.0}}
        assert back.fc_max_no_of_spouses == {1: 2.0}
        # string-keyed dicts keep string keys
        assert back.rating == {"gla": "Yes", "gip": "Yes"}

    def test_decode_handles_missing_and_null_fields(self):
        back = dbl.decode_scheme_record('{"scheme_name": "X"}')
        assert back.scheme_name == "X"
        assert back.commencement_date is None
        assert back.gip_nra is None  # absent -> None, not default dict


class TestLandingHelpers:
    def test_land_snapshot_bundle_missing(self, tmp_path):
        s3 = FakeS3()
        assert dbl.land_snapshot_bundle(s3, tmp_path, "v1", "basis") is None
        assert s3.put_calls == []

    def test_land_snapshot_bundle_uploads_once(self, tmp_path):
        folder = tmp_path / "snap"
        folder.mkdir()
        bundle = folder / "_snapshot_bundle.zip"
        bundle.write_bytes(b"zip-bytes")
        s3 = FakeS3()
        h = dbl.land_snapshot_bundle(s3, folder, "v1", "basis")
        assert h == hashlib.sha256(b"zip-bytes").hexdigest()
        key = f"_snapshots/basis/v1/{h[:16]}/_snapshot_bundle.zip"
        assert s3.put_calls == [key]
        # second call: object exists -> no re-upload
        h2 = dbl.land_snapshot_bundle(s3, folder, "v1", "basis")
        assert h2 == h
        assert s3.put_calls == [key]

    def test_land_engine_version_missing_file(self, tmp_path):
        s3 = FakeS3()
        assert dbl.land_engine_version(
            s3, tmp_path / "nope.py") is None

    def test_land_engine_version_uploads(self, tmp_path):
        f = tmp_path / "engine.py"
        f.write_bytes(b"# engine v1")
        s3 = FakeS3()
        h = dbl.land_engine_version(s3, f)
        assert h == hashlib.sha256(b"# engine v1").hexdigest()
        assert s3.put_calls == [
            f"_snapshots/engine/{h[:16]}/engine.py"]

    def test_land_engine_version_defaults_to_real_engine(self):
        s3 = FakeS3()
        h = dbl.land_engine_version(s3)
        assert h is not None and len(h) == 64
        assert s3.put_calls[0].endswith("engine.py")


# ===========================================================================
# Member resolution
# ===========================================================================

class TestResolveMember:
    def _cand(self, **kw):
        base = {"member_id": "m1", "name": "John Smith",
                "dob": "1980-01-01", "gender": "M", "salary": 100_000.0}
        base.update(kw)
        return base

    def test_auto_link_and_claim(self):
        cand = self._cand()
        by_dob = {"1980-01-01": [cand]}
        claimed = set()
        mid, method, score, needs_new, q = dbl._resolve_member(
            "John Smith", "1980-01-01", "M", 100_000.0, by_dob, claimed)
        assert mid == "m1"
        assert method == "fuzzy_auto"
        assert score >= dbl.MATCH_AUTO_LINK_THRESHOLD
        assert needs_new is False
        assert q is None
        assert claimed == {"m1"}

        # same candidate now claimed -> treated as new member
        mid2, method2, _, needs_new2, q2 = dbl._resolve_member(
            "John Smith", "1980-01-01", "M", 100_000.0, by_dob, claimed)
        assert mid2 is None and method2 == "new" and needs_new2 and q2 is None

    def test_queue_band(self):
        # same name/dob/gender but salary ratio > 2 -> score 85 -> queued
        cand = self._cand(salary=350_000.0)
        by_dob = {"1980-01-01": [cand]}
        mid, method, score, needs_new, q = dbl._resolve_member(
            "John Smith", "1980-01-01", "M", 100_000.0, by_dob, set())
        assert mid is None
        assert method == "new"
        assert dbl.MATCH_QUEUE_THRESHOLD <= score < dbl.MATCH_AUTO_LINK_THRESHOLD
        assert needs_new is True
        assert q == {"candidate_member_id": "m1", "score": 85.0}

    def test_no_candidates_is_new(self):
        mid, method, score, needs_new, q = dbl._resolve_member(
            "X", "1970-01-01", "F", 1.0, {}, set())
        assert mid is None and method == "new" and score == 0.0
        assert needs_new is True and q is None


class TestResolveAndInsertMemberIdentities:
    def test_mixed_resolution(self):
        cur = FakeCursor(fetchall_map={
            "FROM member_identity": [
                ("m-existing", "John Smith", "1980-01-01", "M", 100_000.0),
                ("m-cand", "Jane Doe", "1990-05-05", "F", 700_000.0),
            ],
        })
        gla_df = pl.DataFrame({
            "member_name": ["John Smith", "Jane Doe", "New Person"],
            "date_of_birth": ["1980-01-01", "1990-05-05", "1970-01-01"],
            "gender": ["M", "F", "M"],
            "annual_salary": [100_000.0, 200_000.0, 50_000.0],
        })
        member_id_map = dbl._resolve_and_insert_member_identities(
            cur, "scheme-1", gla_df, "run-1")

        # member 0 auto-linked; 1 and 2 are new identities
        assert member_id_map[0] == "m-existing"
        assert set(member_id_map.keys()) == {0, 1, 2}
        assert member_id_map[1] != member_id_map[2]

        inserts = [b for b in cur.batches
                   if "INSERT INTO member_identity" in b[0]]
        assert len(inserts) == 1
        rows = inserts[0][1]
        assert len(rows) == 2  # only the two new members
        methods = sorted(r[7] for r in rows)
        # Jane Doe scores 85 (salary ratio > 2) -> queued; New Person -> new
        assert methods == ["new", "new_queued"]

        queues = [b for b in cur.batches
                  if "INSERT INTO member_match_queue" in b[0]]
        assert len(queues) == 1
        assert queues[0][1][0][2] == "m-cand"  # candidate_member_id

    def test_all_new_no_candidates(self):
        cur = FakeCursor()
        gla_df = pl.DataFrame({
            "member_name": ["Only Person"],
            "date_of_birth": ["1980-01-01"],
            "gender": ["M"],
            "annual_salary": [1.0],
        })
        m = dbl._resolve_and_insert_member_identities(
            cur, "s", gla_df, "r")
        assert set(m.keys()) == {0}
        assert "INSERT INTO member_identity" in cur.batch_sql_texts()[0]
        assert not any(
            "member_match_queue" in s for s in cur.batch_sql_texts())


class TestInsertPricingResults:
    def test_rows_inserted_with_member_map(self):
        cur = FakeCursor()
        df_result = pd.DataFrame({
            "member_no": [1, 2, None, 9],
            "product": ["gla"] * 4,
            "total_cover": [10.0, 20.0, 30.0, 40.0],
        })
        dbl._insert_pricing_results(
            cur, "run-1", df_result, {0: "m1", 1: "m2"})

        inserts = [b for b in cur.batches
                   if "INSERT INTO pricing_result" in b[0]]
        assert len(inserts) == 1
        _sql, rows = inserts[0]
        assert len(rows) == 2               # None and unmapped skipped
        assert len(rows[0]) == 3 + len(dbl.FACT_COLUMNS)
        assert rows[0][0] == "run-1"
        assert rows[0][1] == "m1"
        assert rows[0][2] == "gla"
        assert rows[1][1] == "m2"
        # missing FACT_COLUMNS filled with None
        age_idx = 3 + dbl.FACT_COLUMNS.index("age")
        cover_idx = 3 + dbl.FACT_COLUMNS.index("total_cover")
        assert rows[0][age_idx] is None
        assert rows[0][cover_idx] == 10.0


# ===========================================================================
# Scheme / config loaders (fake cursor)
# ===========================================================================

class TestGetOrCreateScheme:
    def test_existing_fully_formed_scheme(self):
        cur = FakeCursor(fetchone_map={
            "FROM scheme WHERE ctr_fingerprint": ("sid-9", "case-1", "biz-1"),
        })
        sid = dbl._get_or_create_scheme(cur, _ctr(), case_id="c", quote_seq=2)
        assert sid == "sid-9"
        # only the lookup SELECT ran
        assert len(cur.executed) == 1

    def test_existing_scheme_heals_missing_case_and_biz_key(self):
        cur = FakeCursor(fetchone_map={
            "FROM scheme WHERE ctr_fingerprint": ("sid-9", None, None),
        })
        sid = dbl._get_or_create_scheme(cur, _ctr(), case_id="c9", quote_seq=3)
        assert sid == "sid-9"
        sqls = cur.sql_texts()
        assert any("INSERT INTO case_" in s for s in sqls)
        assert sqls.count(
            "UPDATE scheme SET case_id = ?, quote_seq = ? WHERE scheme_id = ?") == 1
        assert any("scheme_business_key" in s for s in sqls)

    def test_new_scheme_with_case_id_not_in_case_table(self):
        cur = FakeCursor(fetchone_map={
            "FROM scheme WHERE ctr_fingerprint": None,
            "FROM case_": None,
        })
        sid = dbl._get_or_create_scheme(
            cur, _ctr(), case_id="case-x", quote_seq=5)
        uuid.UUID(sid)  # returned value is a guid string
        sqls = cur.sql_texts()
        assert any("INSERT INTO case_" in s for s in sqls)
        insert = next(p for s, p in cur.executed
                  if "INSERT INTO scheme (" in s)
        # scheme_id, internal_gla_code, quote_number ... fingerprint, path, case, seq, bizkey
        assert insert[0] == sid
        assert insert[1] == "GLA01"
        assert insert[2] == 7
        assert insert[17] == "fp-deadbeef-001"
        assert insert[19] == "case-x"
        assert insert[20] == 5

    def test_new_scheme_with_known_case_id_skips_case_insert(self):
        cur = FakeCursor(fetchone_map={
            "FROM scheme WHERE ctr_fingerprint": None,
            "FROM case_": (1,),
        })
        dbl._get_or_create_scheme(cur, _ctr(), case_id="case-x", quote_seq=1)
        assert not any("INSERT INTO case_" in s for s in cur.sql_texts())

    def test_new_scheme_no_case_id_creates_case(self):
        cur = FakeCursor()  # all fetches return None / []
        sid = dbl._get_or_create_scheme(cur, _ctr())
        uuid.UUID(sid)
        assert any("INSERT INTO case_" in s for s in cur.sql_texts())


class TestLoadCategoryConfig:
    def test_inserts_new_rows(self):
        cur = FakeCursor()
        n = dbl._load_category_config(cur, "sid-1", _ctr())
        # one grouped row from ctr.categories + one from scheme gip_nra
        inserts = [b for b in cur.batches
                   if "INSERT INTO category_config" in b[0]]
        assert len(inserts) == 1
        rows = inserts[0][1]
        assert n == len(rows) == 2
        keys = {(r[1], r[2], r[3]) for r in rows}
        assert keys == {("gla", "default", 1), ("gip", "default", 1)}
        gla_row = next(r for r in rows if r[1] == "gla")
        assert gla_row[4] == "Staff"      # category_name
        assert gla_row[5] is None         # bad category_flag -> None
        assert gla_row[7] == 2.5          # benefit_multiple
        assert json.loads(gla_row[8]) == {}
        gip_row = next(r for r in rows if r[1] == "gip")
        assert json.loads(gip_row[8]) == {"nra": "65"}

    def test_updates_existing_rows(self):
        cur = FakeCursor(fetchall_map={
            "FROM category_config": [
                ("gla", "default", 1, None, None, None, None, '{"old": "x"}'),
            ],
        })
        n = dbl._load_category_config(cur, "sid-1", _ctr())
        updates = [b for b in cur.batches
                   if "UPDATE category_config" in b[0]]
        assert len(updates) == 1
        row = updates[0][1][0]
        assert row[0] == "Staff"               # cname filled
        assert json.loads(row[4]) == {"old": "x"}
        assert n == 2                        # 1 update + 1 gip insert


class TestLoadSchemeProductConfig:
    def test_inserts_per_product(self):
        cur = FakeCursor()
        ctr = _ctr()
        # scheme_codes normalisation: "0.0" -> None
        # SchemeRecord is frozen; rebuild with a different scheme_codes dict
        scheme = _scheme()
        object.__setattr__(scheme, "scheme_codes", {"gla": "0.0"})
        ctr = CTRBundle(
            ctr_path=ctr.ctr_path, ctr_fingerprint=ctr.ctr_fingerprint,
            loaded_at=ctr.loaded_at, scheme=scheme,
            categories=ctr.categories, members=ctr.members,
            active_products=ctr.active_products,
            active_categories=ctr.active_categories,
            warnings=ctr.warnings,
        )
        n = dbl._load_scheme_product_config(cur, "sid-1", ctr)
        inserts = [b for b in cur.batches
                   if "INSERT INTO scheme_product_config" in b[0]]
        assert len(inserts) == 1
        rows = inserts[0][1]
        assert n == len(rows) == 2           # gla and gip both got fields
        gla_row = next(r for r in rows if r[1] == "gla")
        assert gla_row[2] == 1               # is_priced
        # SPC_FIELDS order: rating, pricing_basis, fees_priced, basic_fees,
        # profit_refund_loading_by_product, rate_basis_by_product,
        # express_over_gla, category_split, scheme_codes
        assert gla_row[3] == "Yes"           # rating
        assert gla_row[4] == "basis-x"       # pricing_basis
        assert gla_row[-1] is None           # "0.0" scheme_codes normalised

    def test_updates_only_null_fields(self):
        cur = FakeCursor(fetchall_map={
            "FROM scheme_product_config": [
                ("gla", "Yes", None, None, None, None, None, None, None, None),
            ],
        })
        n = dbl._load_scheme_product_config(cur, "sid-1", _ctr())
        updates = [b for b in cur.batches
                   if "UPDATE scheme_product_config" in b[0]]
        assert len(updates) == 1
        row = updates[0][1][0]
        assert row[0] == "Yes"        # existing rating kept
        assert row[1] == "basis-x"    # null pricing_basis filled
        assert n >= 1


class TestLoadSchemeSnapshot:
    def test_writes_manifest_and_uploads_member_sheets(self):
        cur = FakeCursor()
        s3 = FakeS3()
        ctr = _ctr()
        dbl._load_scheme_snapshot(cur, s3, "sid-1", "run-1", ctr)

        sqls = cur.sql_texts()
        assert any("INSERT INTO scheme_snapshot" in s for s in sqls)
        params = next(p for s, p in cur.executed
                  if "INSERT INTO scheme_snapshot" in s)
        assert params[0] == "run-1"
        assert params[1] == "sid-1"
        assert json.loads(params[3]) == ["gla", "gip"]
        assert json.loads(params[4]) == {"gla": [1], "gip": [1]}
        assert json.loads(params[5]) == ["w1"]

        assert s3.put_calls == ["member_sheets/run-1/gla_sheet.parquet"]
        manifest = next(b for b in cur.batches
                    if "INSERT INTO member_sheet_manifest" in b[0])[1]
        assert manifest[0][1] == "gla_sheet"
        assert manifest[0][3] == 2       # row count
        assert manifest[0][4] == 4       # col count

    def test_no_warnings_serialises_none(self):
        cur = FakeCursor()
        s3 = FakeS3()
        dbl._load_scheme_snapshot(cur, s3, "s", "r", _ctr(warnings=[]))
        params = next(p for s, p in cur.executed
                  if "INSERT INTO scheme_snapshot" in s)
        assert params[5] is None


# ===========================================================================
# load_run_to_db (full flow, fake pyodbc)
# ===========================================================================

class TestLoadRunToDb:
    def _df_result(self):
        return pd.DataFrame({
            "member_no": [1, 2],
            "product": ["gla", "gla"],
            "total_cover": [10.0, 20.0],
            "total_annual_premium": [1.0, 2.0],
        })

    def test_successful_full_load(self, tmp_path, monkeypatch):
        cur = FakeCursor(fetchone_map={
            "FROM scheme WHERE ctr_fingerprint": None,
            "FROM case_": None,
            "COUNT(*) FROM run": (0,),
        })
        conn = FakeConn(cur)
        _patch_pyodbc(monkeypatch, conn)
        s3 = FakeS3()

        parquet = tmp_path / "pricing.parquet"
        pl.DataFrame({"a": [1]}).write_parquet(parquet)

        basis_folder = tmp_path / "basis_snap"
        basis_folder.mkdir()
        (basis_folder / "_snapshot_bundle.zip").write_bytes(b"b-zip")

        result = dbl.load_run_to_db(
            "DRIVER={X};SERVER=none", s3, _ctr(), self._df_result(), parquet,
            basis_snapshot="basis_v1", assumption_snapshot="assump_v1",
            basis_snapshot_folder=basis_folder,
            assumption_snapshot_folder=tmp_path / "no-such-dir",
            case_id="case-1", quote_seq=2,
        )

        assert result["status"] == "success", result["error"]
        assert result["rows"] == 2
        uuid.UUID(result["run_id"])
        uuid.UUID(result["scheme_id"])
        assert conn.commits == 1
        assert conn.rollbacks == 0
        assert conn.closed is True
        for key in ("scheme", "category_config", "member_resolution",
                    "s3_write", "run_and_pricing_result", "total"):
            assert key in result["timing"]

        sqls = cur.sql_texts()
        assert any("INSERT INTO scheme (" in s for s in sqls)
        assert any("INSERT INTO run (" in s for s in sqls)
        assert any("INSERT INTO scheme_snapshot" in s for s in sqls)
        assert any("UPDATE run SET status='success'" in s for s in sqls)

        batch_sqls = cur.batch_sql_texts()
        assert any("INSERT INTO member_identity" in s for s in batch_sqls)
        assert any("INSERT INTO member_snapshot" in s for s in batch_sqls)
        assert any("INSERT INTO pricing_result" in s for s in batch_sqls)
        assert any("INSERT INTO category_config" in s for s in batch_sqls)
        assert any("INSERT INTO scheme_product_config" in s for s in batch_sqls)

        # parquet + member sheets + basis bundle + engine version landed
        assert any(k.endswith("pricing_result.parquet") for k in s3.put_calls)
        assert any(k.startswith("member_sheets/") for k in s3.put_calls)
        assert any("_snapshots/basis/" in k for k in s3.put_calls)
        assert any(k.endswith("engine.py") for k in s3.put_calls)

    def test_existing_scheme_short_circuits_insert(
            self, tmp_path, monkeypatch):
        cur = FakeCursor(fetchone_map={
            "FROM scheme WHERE ctr_fingerprint": ("sid-x", "c", "b"),
            "COUNT(*) FROM run": (3,),
        })
        conn = FakeConn(cur)
        _patch_pyodbc(monkeypatch, conn)
        parquet = tmp_path / "p.parquet"
        pl.DataFrame({"a": [1]}).write_parquet(parquet)
        result = dbl.load_run_to_db(
            "cs", FakeS3(), _ctr(), self._df_result(), parquet)
        assert result["status"] == "success", result["error"]
        assert result["scheme_id"] == "sid-x"
        assert not any("INSERT INTO scheme (" in s for s in cur.sql_texts())
        # rerun_number = existing count + 1 -> 4 in run insert params
        run_insert = next(p for s, p in cur.executed
                      if "INSERT INTO run (" in s)
        assert run_insert[11] == 4

    def test_connect_failure_returns_failed(self, monkeypatch):
        _patch_pyodbc(monkeypatch, RuntimeError("cannot connect"))
        result = dbl.load_run_to_db(
            "cs", FakeS3(), _ctr(), pd.DataFrame(), Path("x.parquet"))
        assert result["status"] == "failed"
        assert result["run_id"] is None
        assert "cannot connect" in result["error"]

    def test_mid_load_failure_rolls_back(self, tmp_path, monkeypatch):
        cur = FakeCursor(fail_on="INSERT INTO scheme (")
        conn = FakeConn(cur)
        _patch_pyodbc(monkeypatch, conn)
        result = dbl.load_run_to_db(
            "cs", FakeS3(), _ctr(), self._df_result(),
            tmp_path / "p.parquet")
        assert result["status"] == "failed"
        assert "boom" in result["error"]
        assert conn.rollbacks == 1
        assert conn.commits == 0
        assert conn.closed is True


# ===========================================================================
# reconstruct_ctr_bundle (fake pyodbc + fake s3)
# ===========================================================================

class TestReconstructCtrBundle:
    def _cursor(self, scheme):
        snap_row = (
            "sid-1",
            dbl.encode_scheme_record(scheme),
            json.dumps(["gla"]),
            json.dumps({"gla": [1]}),
            json.dumps(["a warning"]),
            "2025-01-01T00:00:00Z",
        )
        return FakeCursor(
            fetchone_map={
                "FROM scheme_snapshot": snap_row,
                "FROM scheme WHERE scheme_id": ("C:/in/test.ctr", "fp-1"),
            },
            fetchall_map={
                "FROM category_config": [
                    ("gla", "default", 1, "Staff", "1", "Multiple", "2.5",
                     '{"nra": "65"}'),
                ],
                "FROM member_sheet_manifest": [
                    ("gla_sheet", "member_sheets/r1/gla_sheet.parquet"),
                ],
            },
        )

    def test_reconstructs_bundle(self, tmp_path, monkeypatch):
        scheme = _scheme()
        cur = self._cursor(scheme)
        conn = FakeConn(cur)
        _patch_pyodbc(monkeypatch, conn)

        member_df = pl.DataFrame({"member_name": ["A"], "age": [30]})
        buf = io.BytesIO()
        member_df.write_parquet(buf)
        s3 = FakeS3({"member_sheets/r1/gla_sheet.parquet": buf.getvalue()})

        ctr = dbl.reconstruct_ctr_bundle("cs", s3, "run-1")
        assert isinstance(ctr, CTRBundle)
        assert ctr.scheme.scheme_name == "Test Scheme"
        assert ctr.scheme.commencement_date == date(2025, 1, 1)
        assert ctr.scheme.gip_nra == {1: "65"}
        assert ctr.active_products == ("gla",)
        assert ctr.active_categories == {"gla": (1,)}
        assert ctr.warnings == ["a warning"]
        assert ctr.ctr_fingerprint == "fp-1"
        assert ctr.ctr_path == Path("C:/in/test.ctr")
        assert ctr.members["gla_sheet"].height == 1
        # category config rows flattened back into field/value pairs
        fields = set(ctr.categories["field"].to_list())
        assert {"category_name", "category_flag", "cover_type",
                "benefit_multiple", "nra"} <= fields
        assert conn.closed is True

    def test_missing_snapshot_raises(self, monkeypatch):
        cur = FakeCursor()  # scheme_snapshot fetchone -> None
        conn = FakeConn(cur)
        _patch_pyodbc(monkeypatch, conn)
        with pytest.raises(ValueError, match="No scheme_snapshot"):
            dbl.reconstruct_ctr_bundle("cs", FakeS3(), "run-x")
        assert conn.closed is True
