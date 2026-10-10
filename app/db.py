"""
Data layer. All SQL lives here; the API and the voice webhook both go through these functions,
so there is exactly one write path to the database (no logic duplicated between voice and REST).

Serverless-friendly: one short-lived connection per operation (works with Supabase's
transaction pooler; prepared statements are disabled for pgbouncer compatibility).
"""
from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from datetime import date, datetime, timezone

import psycopg
from psycopg import errors as pgerr
from psycopg.rows import dict_row

log = logging.getLogger("patient-intake.db")

PUBLIC_COLS = (
    "patient_id, first_name, last_name, date_of_birth, sex, phone_number, email, address_line_1, "
    "address_line_2, city, state, zip_code, insurance_provider, insurance_member_id, "
    "preferred_language, emergency_contact_name, emergency_contact_phone, created_at, updated_at"
)
WRITABLE = {
    "first_name", "last_name", "date_of_birth", "sex", "phone_number", "email", "address_line_1",
    "address_line_2", "city", "state", "zip_code", "insurance_provider", "insurance_member_id",
    "preferred_language", "emergency_contact_name", "emergency_contact_phone",
}


class DatabaseError(Exception):
    """Any infrastructure failure. Details are logged, never shown to callers/clients."""


class DuplicatePhone(Exception):
    def __init__(self, existing: dict | None):
        super().__init__("phone number already registered")
        self.existing = existing


@contextmanager
def _conn():
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise DatabaseError("DATABASE_URL is not configured")
    try:
        conn = psycopg.connect(url, row_factory=dict_row, autocommit=True,
                               prepare_threshold=None, connect_timeout=8)
    except psycopg.Error as e:
        log.exception("db connect failed")
        raise DatabaseError("cannot connect") from e
    try:
        yield conn
    finally:
        conn.close()


def _exec(sql: str, params=(), one=False, many=False):
    try:
        with _conn() as c:
            cur = c.execute(sql, params)
            if one:
                return cur.fetchone()
            if many:
                return cur.fetchall()
    except (pgerr.UniqueViolation, DatabaseError):
        raise
    except psycopg.Error as e:
        log.exception("db query failed")
        raise DatabaseError("query failed") from e


def _ts(v: datetime | None):
    return v.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if v else None


def serialize(row: dict | None) -> dict | None:
    if row is None:
        return None
    out = dict(row)
    out["patient_id"] = str(out["patient_id"])
    out["date_of_birth"] = out["date_of_birth"].strftime("%m/%d/%Y")
    for k in ("created_at", "updated_at"):
        out[k] = _ts(out.get(k))
    for k in ("phone_number", "emergency_contact_phone", "state"):
        if out.get(k):
            out[k] = out[k].strip()     # char(n) columns
    return out


def _ph(col: str) -> str:
    return "%s::sex_enum" if col == "sex" else "%s"


# ---------------- patients ----------------
def list_patients(last_name=None, date_of_birth: date | None = None, phone_number=None,
                  limit: int = 100, offset: int = 0) -> list[dict]:
    where, params = ["deleted_at is null"], []
    if last_name:
        where.append("lower(last_name) = lower(%s)"); params.append(last_name)
    if date_of_birth:
        where.append("date_of_birth = %s"); params.append(date_of_birth)
    if phone_number:
        where.append("phone_number = %s"); params.append(phone_number)
    params += [limit, offset]
    rows = _exec(f"select {PUBLIC_COLS} from patients where {' and '.join(where)} "
                 "order by created_at desc limit %s offset %s", params, many=True)
    return [serialize(r) for r in rows]


def get_patient(patient_id: str) -> dict | None:
    return serialize(_exec(f"select {PUBLIC_COLS} from patients where patient_id = %s and deleted_at is null",
                           (patient_id,), one=True))


def find_by_phone(phone: str) -> dict | None:
    return serialize(_exec(f"select {PUBLIC_COLS} from patients where phone_number = %s and deleted_at is null",
                           (phone,), one=True))


def create_patient(data: dict) -> dict:
    cols = [c for c in data if c in WRITABLE]
    sql = (f"insert into patients ({', '.join(cols)}) values ({', '.join(_ph(c) for c in cols)}) "
           f"returning {PUBLIC_COLS}")
    try:
        return serialize(_exec(sql, [data[c] for c in cols], one=True))
    except pgerr.UniqueViolation:
        raise DuplicatePhone(find_by_phone(data.get("phone_number", "")))


def update_patient(patient_id: str, data: dict) -> dict | None:
    cols = [c for c in data if c in WRITABLE]
    if not cols:
        return get_patient(patient_id)
    sql = (f"update patients set {', '.join(f'{c} = {_ph(c)}' for c in cols)} "
           f"where patient_id = %s and deleted_at is null returning {PUBLIC_COLS}")
    try:
        return serialize(_exec(sql, [data[c] for c in cols] + [patient_id], one=True))
    except pgerr.UniqueViolation:
        raise DuplicatePhone(find_by_phone(data.get("phone_number", "")))


def soft_delete_patient(patient_id: str) -> dict | None:
    row = _exec("update patients set deleted_at = now() where patient_id = %s and deleted_at is null "
                "returning patient_id, deleted_at", (patient_id,), one=True)
    if not row:
        return None
    return {"patient_id": str(row["patient_id"]), "deleted_at": _ts(row["deleted_at"])}


# ---------------- call logs (bonus) ----------------
def link_call(call_id: str, caller_number: str | None, patient_id: str) -> None:
    _exec("insert into call_logs (call_id, caller_number, patient_id) values (%s, %s, %s) "
          "on conflict (call_id) do update set patient_id = excluded.patient_id", (call_id, caller_number, patient_id))


def save_call_report(call_id: str, caller_number, started_at, ended_at, ended_reason, summary, transcript) -> None:
    _exec("insert into call_logs (call_id, caller_number, started_at, ended_at, ended_reason, summary, transcript) "
          "values (%s, %s, %s, %s, %s, %s, %s) on conflict (call_id) do update set "
          "caller_number = coalesce(call_logs.caller_number, excluded.caller_number), "
          "started_at = excluded.started_at, ended_at = excluded.ended_at, ended_reason = excluded.ended_reason, "
          "summary = excluded.summary, transcript = excluded.transcript",
          (call_id, caller_number, started_at, ended_at, ended_reason, summary, transcript))


def list_calls(patient_id: str) -> list[dict]:
    rows = _exec("select call_id, started_at, ended_at, ended_reason, summary, transcript from call_logs "
                 "where patient_id = %s order by coalesce(started_at, created_at) desc", (patient_id,), many=True)
    return [{**r, "started_at": _ts(r["started_at"]), "ended_at": _ts(r["ended_at"])} for r in rows]
