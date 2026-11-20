from __future__ import annotations

import dataclasses
import hashlib
import json
import logging
import time
import uuid
from collections import defaultdict
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from ctr_loader.ctr_schemas import SchemeRecord
from gap_config.storage import Storage
from rapidfuzz import fuzz

logger = logging.getLogger(__name__)

MATCH_AUTO_LINK_THRESHOLD = 90
MATCH_QUEUE_THRESHOLD = 70

FACT_COLUMNS = [
    "category_number", "status", "age", "nra_final", "ceasing_age",
    "product_active", "rate_basic", "pre_tax_cover", "total_cover",
    "basic_technical_premium", "co_pct", "co_premium", "waiver_premium",
    "prb_premium", "qx", "profit_margin_amount", "total_annual_premium",
    "reporting_incidence", "reporting_incidence_x_cover",
]


LocalS3 = Storage


def _soundex(name: str) -> str:
    if not name:
        return ""
    name = name.upper()
    codes = {
        "B": "1", "F": "1", "P": "1", "V": "1",
        "C": "2", "G": "2", "J": "2", "K": "2", "Q": "2", "S": "2", "X": "2", "Z": "2",
        "D": "3", "T": "3",
        "L": "4",
        "M": "5", "N": "5",
        "R": "6",
    }
    first_letter = name[0]
    tail = []
    prev_code = codes.get(name[0], "")
    for ch in name[1:]:
        code = codes.get(ch, "")
        if code and code != prev_code:
            tail.append(code)
        prev_code = code
    return (first_letter + "".join(tail) + "000")[:4]


def match_score(name_a, dob_a, gender_a, salary_a,
                name_b, dob_b, gender_b, salary_b) -> float:
    if dob_a != dob_b:
        return 0.0

    name_sim = fuzz.token_sort_ratio(name_a, name_b)
    phonetic_match = _soundex(name_a) == _soundex(name_b)
    score = name_sim
    if phonetic_match:
        score = max(score, 90)

    if gender_a and gender_b and gender_a != gender_b:
        score *= 0.2

    if salary_a and salary_b and salary_a > 0 and salary_b > 0:
        ratio = max(salary_a, salary_b) / min(salary_a, salary_b)
        if ratio <= 1.15:
            score = min(100, score + 5)
        elif ratio <= 1.40:
            pass
        elif ratio <= 2.0:
            score -= 5
        else:
            score -= 15

    return max(0.0, min(100.0, score))


def _encode_value(val):
    if isinstance(val, list):
        return ",".join(str(v) for v in val)
    return str(val) if val is not None else None


def _hash_file(path, algo: str = "sha256") -> str:
    h = hashlib.new(algo)
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def land_snapshot_bundle(s3: LocalS3, snapshot_folder, snapshot_version_name: str, kind: str):
    bundle_path = Path(snapshot_folder) / "_snapshot_bundle.zip"
    if not bundle_path.exists():
        return None

    content_hash = _hash_file(bundle_path)
    short_hash = content_hash[:16]
    s3_key = f"_snapshots/{kind}/{snapshot_version_name}/{short_hash}/_snapshot_bundle.zip"

    if not s3.exists(s3_key):
        s3.put_file(s3_key, bundle_path)

    return content_hash


def land_engine_version(s3: LocalS3, engine_file_path=None) -> str | None:
    if engine_file_path is None:
        from . import engine as _engine_module
        engine_file_path = Path(_engine_module.__file__)
    else:
        engine_file_path = Path(engine_file_path)

    if not engine_file_path.exists():
        return None

    content_hash = _hash_file(engine_file_path)
    short_hash = content_hash[:16]
    s3_key = f"_snapshots/engine/{short_hash}/engine.py"

    if not s3.exists(s3_key):
        s3.put_file(s3_key, engine_file_path)

    return content_hash


_COMB_EPOCH = datetime(2020, 1, 1)

def sql_seq_guid() -> str:
    rnd = bytearray(uuid.uuid4().bytes[:10])
    now = datetime.now(UTC).replace(tzinfo=None)
    days = (now - _COMB_EPOCH).days
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    ticks = int((now - midnight).total_seconds() * 1000 / 3.333333)
    combined = bytes(rnd) + days.to_bytes(2, "big") + ticks.to_bytes(4, "big")
    return str(uuid.UUID(bytes=combined))


_INT_KEYED_DICT_FIELDS = {
    "fc_max_no_of_spouses", "fc_parents_included", "fc_parents_in_law_included",
    "fc_include_ctc", "fc_paid_up_benefits_active", "fc_pup_death", "fc_pup_lsdb",
    "fc_pup_death_lsdb", "fc_benefit_amounts",
    "gip_nra", "gip_cover_type", "gip_tax_options", "gip_escalation_type",
    "gip_escalation_pct", "gip_waiting_period", "gip_backdating",
    "gip_include_conversion_option", "gip_pre_ex_before", "gip_pre_ex_after",
    "gip_initial_period", "gip_core_disability_definition", "gip_core_benefit_perc",
    "gip_core_benefit_layer", "gip_core_benefit_perc_additional",
    "gip_core_benefit_layer_additional", "gip_core_overall_maximum",
    "gip_employer_waiver", "gip_employee_waiver", "gip_hcc_disability_definition",
    "gip_hcc_benefit_perc", "gip_hcc_benefit_layer", "gip_hcc_overall_maximum",
    "gip_include_hcc_waiver", "gip_hcc_payment_period", "gip_hcc_payment_type",
    "gip_hcc_nominal_amount", "gip_hcc_escalation_type", "gip_hcc_escalation_pct",
    "gip_waiver_pct_specified",
    "tip_nra", "tip_cover_type", "tip_tax_options", "tip_escalation_type",
    "tip_escalation_pct", "tip_waiting_period", "tip_backdating",
    "tip_pre_ex_before", "tip_pre_ex_after", "tip_initial_period",
    "tip_core_disability_definition", "tip_core_benefit_perc", "tip_core_benefit_layer",
    "tip_core_benefit_perc_additional", "tip_core_benefit_layer_additional",
    "tip_core_overall_maximum", "tip_employer_waiver", "tip_employee_waiver",
    "tip_hcc_disability_definition", "tip_hcc_benefit_perc", "tip_hcc_benefit_layer",
    "tip_hcc_overall_maximum", "tip_include_hcc_waiver", "tip_hcc_payment_period",
    "tip_hcc_payment_type", "tip_hcc_nominal_amount", "tip_hcc_escalation_type",
    "tip_hcc_escalation_pct", "tip_payment_period", "tip_waiver_pct_specified",
    "tip_waiver_included",
    "education_active", "education_include_ctc", "education_max_cover",
    "education_nra", "education_deferred", "education_deferred_age",
    "education_multiple",
}
_NESTED_INT_KEYED_DICT_FIELDS = {"max_cover_per_category"}
_DATE_FIELDS = {"effective_date", "calculation_date", "commencement_date"}


def encode_scheme_record(scheme_rec) -> str:
    out = {}
    for f in dataclasses.fields(scheme_rec):
        val = getattr(scheme_rec, f.name)
        if f.name in _DATE_FIELDS and val is not None:
            out[f.name] = val.isoformat()
        else:
            out[f.name] = val
    return json.dumps(out, default=str)


def decode_scheme_record(json_str: str):
    raw = json.loads(json_str)
    kwargs = {}
    for f in dataclasses.fields(SchemeRecord):
        val = raw.get(f.name)
        if f.name in _DATE_FIELDS and val is not None:
            val = date.fromisoformat(val)
        elif f.name in _NESTED_INT_KEYED_DICT_FIELDS and isinstance(val, dict):
            val = {ok: {int(ik): iv for ik, iv in id_.items()} for ok, id_ in val.items()}
        elif f.name in _INT_KEYED_DICT_FIELDS and isinstance(val, dict):
            val = {int(k): v for k, v in val.items()}
        kwargs[f.name] = val
    return SchemeRecord(**kwargs)


def _load_scheme_snapshot(cur, s3, scheme_id: str, run_id: str, ctr) -> None:
    scheme_json = encode_scheme_record(ctr.scheme)
    products_json = json.dumps(list(ctr.active_products))
    categories_json = json.dumps({k: list(v) for k, v in ctr.active_categories.items()})
    warnings_json = json.dumps(list(ctr.warnings)) if ctr.warnings else None

    cur.execute(
        """
        INSERT INTO scheme_snapshot
            (run_id, scheme_id, scheme_record_json, active_products_json,
             active_categories_json, warnings_json, ctr_loaded_at)
        VALUES (?,?,?,?,?,?,?)
        """,
        run_id, scheme_id, scheme_json, products_json, categories_json,
        warnings_json, str(ctr.loaded_at),
    )

    import tempfile
    manifest_rows = []
    for sheet_name, df in ctr.members.items():
        local_path = Path(tempfile.gettempdir()) / f"{run_id}_{sheet_name}.parquet"
        df.write_parquet(local_path)
        s3_key = f"member_sheets/{run_id}/{sheet_name}.parquet"
        s3.put_file(s3_key, local_path)
        local_path.unlink(missing_ok=True)
        manifest_rows.append((run_id, sheet_name, s3_key, df.height, df.width, json.dumps(df.columns)))

    if manifest_rows:
        cur.executemany(
            """
            INSERT INTO member_sheet_manifest (run_id, sheet_name, s3_key, row_count, col_count, columns_json)
            VALUES (?,?,?,?,?,?)
            """,
            manifest_rows,
        )


def _get_or_create_scheme(cur, ctr, case_id: str | None = None, quote_seq: int | None = None) -> str:
    cur.execute(
        "SELECT scheme_id, case_id, scheme_business_key FROM scheme WHERE ctr_fingerprint = ?",
        ctr.ctr_fingerprint,
    )
    row = cur.fetchone()
    if row:
        existing_scheme_id, existing_case_id, existing_biz_key = row
        if existing_case_id is None:
            healed_case_id = case_id or sql_seq_guid()
            cur.execute(
                "INSERT INTO case_ (case_id, chorus_quote_number, chorus_gapq) VALUES (?, NULL, NULL)",
                healed_case_id,
            )
            cur.execute(
                "UPDATE scheme SET case_id = ?, quote_seq = ? WHERE scheme_id = ?",
                healed_case_id, quote_seq or 1, existing_scheme_id,
            )
        if existing_biz_key is None:
            s = ctr.scheme
            internal_gla_code = s.scheme_codes.get("gla", "UNKNOWN")
            _biz_ts = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
            _biz_quote = s.quote_number if s.quote_number is not None else "X"
            healed_biz_key = f"{_biz_ts}_{internal_gla_code}_{_biz_quote}_{ctr.ctr_fingerprint[:8]}"
            cur.execute(
                "UPDATE scheme SET scheme_business_key = ? WHERE scheme_id = ?",
                healed_biz_key, existing_scheme_id,
            )
        return str(existing_scheme_id)

    resolved_case_id = case_id
    if resolved_case_id is None:
        resolved_case_id = sql_seq_guid()
        cur.execute(
            "INSERT INTO case_ (case_id, chorus_quote_number, chorus_gapq) VALUES (?, NULL, NULL)",
            resolved_case_id,
        )
    else:
        cur.execute("SELECT 1 FROM case_ WHERE case_id = ?", resolved_case_id)
        if cur.fetchone() is None:
            cur.execute(
                "INSERT INTO case_ (case_id, chorus_quote_number, chorus_gapq) VALUES (?, NULL, NULL)",
                resolved_case_id,
            )

    resolved_quote_seq = quote_seq if quote_seq is not None else 1

    scheme_id = sql_seq_guid()
    s = ctr.scheme
    internal_gla_code = s.scheme_codes.get("gla", "UNKNOWN")

    _biz_ts = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    _biz_quote = s.quote_number if s.quote_number is not None else "X"
    scheme_business_key = f"{_biz_ts}_{internal_gla_code}_{_biz_quote}_{ctr.ctr_fingerprint[:8]}"

    cur.execute(
        """
        INSERT INTO scheme (
            scheme_id, internal_gla_code, quote_number, quote_type, quote_group,
            scheme_name, doer, checker, intermediary, broker_name,
            client_manager, branch, region, salary_definition,
            premium_frequency, commencement_date, calculation_date,
            ctr_fingerprint, ctr_path, case_id, quote_seq, scheme_business_key
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        scheme_id, internal_gla_code, s.quote_number, s.quote_type, s.quote_group,
        s.scheme_name, s.doer, s.checker, s.intermediary, s.broker_name,
        s.client_manager, s.branch, s.region, s.salary_definition,
        s.premium_frequency, s.commencement_date, s.calculation_date,
        ctr.ctr_fingerprint, str(ctr.ctr_path), resolved_case_id, resolved_quote_seq,
        scheme_business_key,
    )
    return scheme_id


CORE_CATEGORY_FIELDS = {"category_name", "category_flag", "cover_type", "benefit_multiple"}


def _load_category_config(cur, scheme_id: str, ctr) -> int:
    import json

    cur.execute(
        "SELECT product, variant, category_number, category_name, category_flag, "
        "cover_type, benefit_multiple, fields FROM category_config WHERE scheme_id = ?",
        scheme_id,
    )
    existing = {}
    for prod, variant, cat_num, cname, cflag, ctype, bmult, fields_json in cur.fetchall():
        existing[(prod, variant, cat_num)] = {
            "category_name": cname, "category_flag": cflag,
            "cover_type": ctype, "benefit_multiple": bmult,
            "fields": json.loads(fields_json) if fields_json else {},
        }

    grouped: dict[tuple, dict] = {}
    for row in ctr.categories.iter_rows(named=True):
        key = (row["product"], row["variant"], row["category_number"])
        grouped.setdefault(key, {})[row["field"]] = row["value"]

    products = ctr.active_products
    active_categories = ctr.active_categories
    scheme_rec = ctr.scheme
    for f in dataclasses.fields(scheme_rec):
        val = getattr(scheme_rec, f.name)
        if not isinstance(val, dict) or len(val) == 0:
            continue
        matched_product, field_name = None, f.name
        for p in products:
            prefix = p if p != "education_guardian" else "education"
            if field_name.startswith(prefix + "_"):
                matched_product = p
                field_name = field_name[len(prefix) + 1:]
                break
        if matched_product is None:
            continue
        keys = list(val.keys())
        if not all(isinstance(k, int) for k in keys):
            continue
        valid_cats = set(active_categories.get(matched_product, ()))
        for cat_num, leaf_val in val.items():
            if valid_cats and cat_num not in valid_cats:
                continue
            key = (matched_product, "default", cat_num)
            grouped.setdefault(key, {})[field_name] = _encode_value(leaf_val)

    to_insert = []
    to_update = []
    for key, field_map in grouped.items():
        product, variant, cat_num = key
        core = {k: field_map.get(k) for k in CORE_CATEGORY_FIELDS}
        tail = {k: v for k, v in field_map.items() if k not in CORE_CATEGORY_FIELDS}

        cat_flag = None
        if core.get("category_flag") is not None:
            try:
                cat_flag = float(core["category_flag"])
            except (TypeError, ValueError):
                cat_flag = None
        bmult = None
        if core.get("benefit_multiple") is not None:
            try:
                bmult = float(core["benefit_multiple"])
            except (TypeError, ValueError):
                bmult = None

        if key not in existing:
            to_insert.append((
                scheme_id, product, variant, cat_num,
                core.get("category_name"), cat_flag, core.get("cover_type"), bmult,
                json.dumps(tail),
            ))
        else:
            old = existing[key]
            merged_tail = dict(old["fields"])
            changed = False
            for k, v in tail.items():
                if k not in merged_tail:
                    merged_tail[k] = v
                    changed = True
            new_cname = old["category_name"] if old["category_name"] is not None else core.get("category_name")
            new_cflag = old["category_flag"] if old["category_flag"] is not None else cat_flag
            new_ctype = old["cover_type"] if old["cover_type"] is not None else core.get("cover_type")
            new_bmult = old["benefit_multiple"] if old["benefit_multiple"] is not None else bmult
            if changed or new_cname != old["category_name"] or new_cflag != old["category_flag"] \
                    or new_ctype != old["cover_type"] or new_bmult != old["benefit_multiple"]:
                to_update.append((new_cname, new_cflag, new_ctype, new_bmult,
                                   json.dumps(merged_tail), scheme_id, product, variant, cat_num))

    if to_insert:
        cur.executemany(
            "INSERT INTO category_config (scheme_id, product, variant, category_number, "
            "category_name, category_flag, cover_type, benefit_multiple, fields) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            to_insert,
        )
    if to_update:
        cur.executemany(
            "UPDATE category_config SET category_name=?, category_flag=?, cover_type=?, "
            "benefit_multiple=?, fields=? WHERE scheme_id=? AND product=? AND variant=? AND category_number=?",
            to_update,
        )
    return len(to_insert) + len(to_update)


SPC_FIELDS = ["rating", "pricing_basis", "fees_priced", "basic_fees",
              "profit_refund_loading_by_product", "rate_basis_by_product",
              "express_over_gla", "category_split", "scheme_codes"]


def _load_scheme_product_config(cur, scheme_id: str, ctr) -> int:
    from ctr_loader.products_registry import IN_SCOPE_PRODUCTS

    cur.execute(
        f"SELECT product, {', '.join(SPC_FIELDS)} FROM scheme_product_config WHERE scheme_id = ?",
        scheme_id,
    )
    existing = {}
    for row in cur.fetchall():
        existing[row[0]] = dict(zip(SPC_FIELDS, row[1:]))

    products = set(ctr.active_products)
    scheme_rec = ctr.scheme
    by_product: dict[str, dict] = {}

    for f in dataclasses.fields(scheme_rec):
        if f.name not in SPC_FIELDS:
            continue
        val = getattr(scheme_rec, f.name)
        if not isinstance(val, dict) or len(val) == 0:
            continue
        keys = list(val.keys())
        if not all(isinstance(k, str) for k in keys):
            continue
        if not any(k in products for k in keys):
            continue
        for product_key, leaf_val in val.items():
            by_product.setdefault(product_key, {})[f.name] = _encode_value(leaf_val)

    normalize_sc = lambda v: None if v == "0.0" else v

    to_insert = []
    to_update = []
    for product, field_map in by_product.items():
        is_priced = 1 if product in IN_SCOPE_PRODUCTS else 0
        if "scheme_codes" in field_map:
            field_map["scheme_codes"] = normalize_sc(field_map["scheme_codes"])

        if product not in existing:
            to_insert.append((scheme_id, product, is_priced, *[field_map.get(f) for f in SPC_FIELDS]))
        else:
            old = existing[product]
            new_vals = []
            changed = False
            for f in SPC_FIELDS:
                if old.get(f) is None and field_map.get(f) is not None:
                    new_vals.append(field_map[f])
                    changed = True
                else:
                    new_vals.append(old.get(f))
            if changed:
                to_update.append((*new_vals, scheme_id, product))

    if to_insert:
        cols = "scheme_id, product, is_priced_product, " + ", ".join(SPC_FIELDS)
        placeholders = ", ".join(["?"] * (3 + len(SPC_FIELDS)))
        _prev_fast = cur.fast_executemany
        cur.fast_executemany = False
        try:
            cur.executemany(
                f"INSERT INTO scheme_product_config ({cols}) VALUES ({placeholders})",
                to_insert,
            )
        finally:
            cur.fast_executemany = _prev_fast
    if to_update:
        set_clause = ", ".join(f"[{f}] = ?" for f in SPC_FIELDS)
        _prev_fast2 = cur.fast_executemany
        cur.fast_executemany = False
        try:
            cur.executemany(
                f"UPDATE scheme_product_config SET {set_clause} WHERE scheme_id = ? AND product = ?",
                to_update,
            )
        finally:
            cur.fast_executemany = _prev_fast2
    return len(to_insert) + len(to_update)


def _load_existing_identities_by_dob(cur, scheme_id, load_started_at):
    cur.execute(
        """
        SELECT member_id, member_name, date_of_birth, gender, annual_salary
        FROM member_identity WHERE scheme_id = ? AND created_at < ?
        """,
        scheme_id, load_started_at,
    )
    by_dob = defaultdict(list)
    for member_id, name, dob, gender, salary in cur.fetchall():
        by_dob[dob].append({
            "member_id": str(member_id), "name": name, "dob": dob,
            "gender": gender, "salary": salary,
        })
    return by_dob


def _resolve_member(name, dob, gender, salary, candidates_by_dob, claimed_ids: set):
    candidates = [c for c in candidates_by_dob.get(dob, []) if c["member_id"] not in claimed_ids]

    best_score, best = 0.0, None
    for cand in candidates:
        score = match_score(name, str(dob), gender, salary,
                             cand["name"], str(cand["dob"]), cand["gender"], cand["salary"])
        if score > best_score:
            best_score, best = score, cand

    if best_score >= MATCH_AUTO_LINK_THRESHOLD:
        claimed_ids.add(best["member_id"])
        return best["member_id"], "fuzzy_auto", best_score, False, None

    queue_entry = None
    if MATCH_QUEUE_THRESHOLD <= best_score < MATCH_AUTO_LINK_THRESHOLD:
        queue_entry = {"candidate_member_id": best["member_id"], "score": best_score}

    return None, "new", best_score, True, queue_entry


def _insert_pricing_results(cur, run_id, df_result, member_id_map):
    insert_sql = (
        f"INSERT INTO pricing_result (run_id, member_id, product, {','.join(FACT_COLUMNS)}) "
        f"VALUES ({','.join(['?'] * (3 + len(FACT_COLUMNS)))})"
    )

    missing_cols = [c for c in FACT_COLUMNS if c not in df_result.columns]
    if missing_cols:
        df_result = df_result.copy()
        for c in missing_cols:
            df_result[c] = None

    needed_cols = ["member_no", "product"] + FACT_COLUMNS
    sub = df_result[needed_cols]
    sub_obj = sub.astype(object).where(sub.notnull(), None)
    prepared_rows = sub_obj.values.tolist()

    batch, BATCH_SIZE = [], 10000
    for values in prepared_rows:
        member_no, product = values[0], values[1]
        if member_no is None:
            continue
        member_id = member_id_map.get(member_no - 1)
        if member_id is None:
            continue
        batch.append([run_id, member_id, product] + values[2:])
        if len(batch) >= BATCH_SIZE:
            cur.executemany(insert_sql, batch)
            batch = []
    if batch:
        cur.executemany(insert_sql, batch)


def _resolve_and_insert_member_identities(cur, scheme_id, gla_df, run_id):
    load_started_at = datetime.now(UTC)
    candidates_by_dob = _load_existing_identities_by_dob(cur, scheme_id, load_started_at)
    claimed_ids: set = set()
    new_identity_rows, queue_rows, member_id_map = [], [], {}

    for i, row in enumerate(gla_df.iter_rows(named=True)):
        mid, _method, score, needs_new, queue_entry = _resolve_member(
            row["member_name"], row["date_of_birth"], row["gender"], row["annual_salary"],
            candidates_by_dob, claimed_ids,
        )
        if needs_new:
            new_id = sql_seq_guid()
            new_identity_rows.append((
                new_id, scheme_id, row["member_name"], None, row["gender"],
                row["date_of_birth"], row["annual_salary"],
                "new" if queue_entry is None else "new_queued", score,
            ))
            member_id_map[i] = new_id
            if queue_entry:
                queue_rows.append((
                    sql_seq_guid(), run_id, queue_entry["candidate_member_id"],
                    row["member_name"], row["date_of_birth"], row["gender"], queue_entry["score"],
                ))
        else:
            member_id_map[i] = mid

    if new_identity_rows:
        cur.executemany(
            """
            INSERT INTO member_identity
                (member_id, scheme_id, member_name, id_number, gender, date_of_birth,
                 annual_salary, match_method, match_confidence)
            VALUES (?,?,?,?,?,?,?,?,?)
            """,
            new_identity_rows,
        )

    if queue_rows:
        cur.executemany(
            """
            INSERT INTO member_match_queue
                (queue_id, run_id, candidate_member_id, incoming_name, incoming_dob,
                 incoming_gender, similarity_score, status)
            VALUES (?,?,?,?,?,?,?,'pending')
            """,
            queue_rows,
        )
    return member_id_map


def load_run_to_db(conn_str: str, s3: LocalS3, ctr, df_result, parquet_local_path,
                    basis_snapshot: str = "", assumption_snapshot: str = "",
                    basis_snapshot_folder=None, assumption_snapshot_folder=None,
                    case_id: str | None = None, quote_seq: int | None = None) -> dict:
    result: dict[str, Any] = {"status": "failed", "run_id": None, "error": None, "timing": {}}
    conn = None
    try:
        import pyodbc
        conn = pyodbc.connect(conn_str, autocommit=False)
        cur = conn.cursor()
        cur.fast_executemany = True
        t_start = time.perf_counter()

        scheme_id = _get_or_create_scheme(cur, ctr, case_id=case_id, quote_seq=quote_seq)
        result["timing"]["scheme"] = time.perf_counter() - t_start

        t0 = time.perf_counter()
        _load_category_config(cur, scheme_id, ctr)
        _load_scheme_product_config(cur, scheme_id, ctr)
        result["timing"]["category_config"] = time.perf_counter() - t0

        t0 = time.perf_counter()
        run_id = sql_seq_guid()
        gla_df = ctr.members["gla_sheet"]
        member_id_map = _resolve_and_insert_member_identities(cur, scheme_id, gla_df, run_id)
        result["timing"]["member_resolution"] = time.perf_counter() - t0

        t0 = time.perf_counter()
        s3_folder_key = case_id or ctr.scheme.scheme_codes.get("gla", "UNKNOWN")
        quote_id = str(ctr.scheme.quote_number)
        s3_key = f"{s3_folder_key}/{quote_id}/{run_id}/pricing_result.parquet"
        s3.put_file(s3_key, parquet_local_path)
        result["timing"]["s3_write"] = time.perf_counter() - t0

        t0 = time.perf_counter()
        started_at = datetime.now(UTC)

        basis_bundle_hash = None
        assumption_bundle_hash = None
        engine_version_hash = None
        try:
            if basis_snapshot_folder:
                basis_bundle_hash = land_snapshot_bundle(
                    s3, basis_snapshot_folder, basis_snapshot, "basis")
            if assumption_snapshot_folder:
                assumption_bundle_hash = land_snapshot_bundle(
                    s3, assumption_snapshot_folder, assumption_snapshot, "assumption")
            engine_version_hash = land_engine_version(s3)
        except Exception as e:  # noqa: BLE001 - hashing is best-effort; DB load continues without it
            logger.warning(f"[db_loader] snapshot/engine hashing failed: {e}")

        cur.execute("SELECT COUNT(*) FROM run WHERE scheme_id = ?", scheme_id)
        rerun_number = cur.fetchone()[0] + 1

        cur.execute(
            """
            INSERT INTO run (run_id, scheme_id, ctr_fingerprint, basis_snapshot,
                              assumption_snapshot, s3_path, status, row_count, started_at,
                              basis_bundle_hash, assumption_bundle_hash, rerun_number,
                              engine_version_hash)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            run_id, scheme_id, ctr.ctr_fingerprint,
            basis_snapshot, assumption_snapshot,
            s3_key, "running", len(df_result), started_at,
            basis_bundle_hash, assumption_bundle_hash, rerun_number,
            engine_version_hash,
        )

        try:
            _load_scheme_snapshot(cur, s3, scheme_id, run_id, ctr)
        except Exception as e:  # noqa: BLE001 - scheme snapshot is best-effort; run load continues
            logger.warning(f"[db_loader] scheme snapshot load failed: {e}")

        snapshot_rows = []
        for i, row in enumerate(gla_df.iter_rows(named=True)):
            snapshot_rows.append((
                sql_seq_guid(), member_id_map[i], run_id,
                row["member_name"], row["date_of_birth"], row["annual_salary"],
                "gla_sheet",
            ))
        if snapshot_rows:
            cur.executemany(
                """
                INSERT INTO member_snapshot
                    (snapshot_id, member_id, run_id, raw_name, raw_dob, raw_salary, source_sheet)
                VALUES (?,?,?,?,?,?,?)
                """,
                snapshot_rows,
            )

        _insert_pricing_results(cur, run_id, df_result, member_id_map)

        cur.execute(
            "UPDATE run SET status='success', completed_at=? WHERE run_id=?",
            datetime.now(UTC), run_id,
        )
        result["timing"]["run_and_pricing_result"] = time.perf_counter() - t0

        conn.commit()
        result["timing"]["total"] = time.perf_counter() - t_start
        result.update(status="success", run_id=run_id, scheme_id=scheme_id, rows=len(df_result))

    except Exception as e:  # noqa: BLE001 - top-level handler records the failure in result
        if conn:
            conn.rollback()
        result["error"] = str(e)
    finally:
        if conn:
            conn.close()

    return result


def reconstruct_ctr_bundle(conn_str: str, s3, run_id: str):
    import polars as pl
    import pyodbc

    conn = pyodbc.connect(conn_str)
    cur = conn.cursor()

    cur.execute(
        "SELECT scheme_id, scheme_record_json, active_products_json, "
        "active_categories_json, warnings_json, ctr_loaded_at "
        "FROM scheme_snapshot WHERE run_id = ?",
        run_id,
    )
    row = cur.fetchone()
    if row is None:
        cur.close()
        conn.close()
        raise ValueError(
            f"No scheme_snapshot found for run_id={run_id!r} -- cannot "
            f"reconstruct. This run may predate snapshot capture, or "
            f"capture may have failed silently for it (fail-soft by design)."
        )
    scheme_id, scheme_json, products_json, categories_json, warnings_json, loaded_at = row

    scheme_rec = decode_scheme_record(scheme_json)
    active_products = tuple(json.loads(products_json))
    active_categories = {k: tuple(v) for k, v in json.loads(categories_json).items()}
    warnings = json.loads(warnings_json) if warnings_json else []

    cur.execute(
        """
        SELECT product, variant, category_number, category_name, category_flag,
               cover_type, benefit_multiple, fields
        FROM category_config WHERE scheme_id = ?
        """,
        scheme_id,
    )
    cat_rows = []
    for prod, variant, cat_num, cname, cflag, ctype, bmult, fields_json in cur.fetchall():
        row_fields = {"category_name": cname, "category_flag": cflag,
                      "cover_type": ctype, "benefit_multiple": bmult}
        if fields_json:
            row_fields.update(json.loads(fields_json))
        for field, value in row_fields.items():
            if value is None:
                continue
            cat_rows.append({
                "product": prod, "variant": variant, "category_number": cat_num,
                "field": field, "value": str(value),
            })
    categories_df = pl.DataFrame(cat_rows) if cat_rows else pl.DataFrame(
        schema={"product": pl.Utf8, "variant": pl.Utf8, "category_number": pl.Int64,
                "field": pl.Utf8, "value": pl.Utf8}
    )

    cur.execute(
        "SELECT sheet_name, s3_key FROM member_sheet_manifest WHERE run_id = ?",
        run_id,
    )
    members = {}
    for sheet_name, s3_key in cur.fetchall():
        local_path = s3.get_path(s3_key)
        members[sheet_name] = pl.read_parquet(local_path)

    cur.execute("SELECT ctr_path, ctr_fingerprint FROM scheme WHERE scheme_id = ?", scheme_id)
    ctr_path, ctr_fingerprint = cur.fetchone()

    cur.close()
    conn.close()

    from ctr_loader.ctr_schemas import CTRBundle
    return CTRBundle(
        ctr_path=Path(ctr_path) if ctr_path else None,
        ctr_fingerprint=ctr_fingerprint,
        loaded_at=loaded_at,
        scheme=scheme_rec,
        categories=categories_df,
        members=members,
        active_products=active_products,
        active_categories=active_categories,
        warnings=warnings,
    )
