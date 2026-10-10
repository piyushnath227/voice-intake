"""REST API for patient records. Every response uses the envelope {"data": ..., "error": ...}."""
from __future__ import annotations

import logging
import sys
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import db, validators as V
from .dashboard import DASHBOARD_HTML
from .schemas import PatientCreate, PatientUpdate, format_errors
from .vapi import router as vapi_router

logging.basicConfig(stream=sys.stdout, level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("patient-intake.api")

app = FastAPI(title="Patient Intake API", version="1.0.0")
app.include_router(vapi_router)


# ---------- envelope + errors ----------
def ok(data, status=200):
    return JSONResponse({"data": data, "error": None}, status_code=status)


def fail(status: int, code: str, message: str, **extra):
    return JSONResponse({"data": None, "error": {"code": code, "message": message, **extra}}, status_code=status)


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, **extra):
        self.status, self.code, self.message, self.extra = status, code, message, extra


@app.exception_handler(ApiError)
async def _api_error(_: Request, e: ApiError):
    return fail(e.status, e.code, e.message, **e.extra)


@app.exception_handler(RequestValidationError)
async def _validation_error(_: Request, e: RequestValidationError):
    errors = e.errors()
    if any(err["type"] == "json_invalid" for err in errors):
        return fail(400, "bad_request", "Request body is not valid JSON.")
    return fail(422, "validation_error", "One or more fields are invalid.", fields=format_errors(errors))


@app.exception_handler(StarletteHTTPException)
async def _http_error(_: Request, e: StarletteHTTPException):
    code = {404: "not_found", 405: "method_not_allowed"}.get(e.status_code, "http_error")
    return fail(e.status_code, code, str(e.detail))


@app.exception_handler(db.DatabaseError)
async def _db_error(_: Request, e: db.DatabaseError):
    return fail(500, "database_error", "The database is temporarily unavailable. Please try again.")


@app.exception_handler(Exception)
async def _unhandled(_: Request, e: Exception):
    log.exception("unhandled error")
    return fail(500, "internal_error", "Something went wrong on our side.")


def _uuid(value: str) -> str:
    try:
        return str(uuid.UUID(value))
    except ValueError:
        raise ApiError(400, "bad_request", "patient id must be a valid UUID.")


# ---------- routes ----------
@app.get("/health")
def health():
    return ok({"status": "ok"})


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def dashboard():
    return DASHBOARD_HTML


@app.get("/patients")
def list_patients(last_name: str | None = None, date_of_birth: str | None = None,
                  phone_number: str | None = None, limit: int = 100, offset: int = 0):
    try:
        dob = V.dob(date_of_birth, required=False) if date_of_birth else None
        phone = V.phone(phone_number, required=False) if phone_number else None
    except ValueError as e:
        raise ApiError(400, "bad_request", str(e))
    if not (1 <= limit <= 500) or offset < 0:
        raise ApiError(400, "bad_request", "limit must be 1-500 and offset >= 0.")
    return ok(db.list_patients(V.clean(last_name) or None, dob, phone, limit, offset))


@app.get("/patients/{patient_id}")
def get_patient(patient_id: str):
    row = db.get_patient(_uuid(patient_id))
    if not row:
        raise ApiError(404, "not_found", "Patient not found.")
    return ok(row)


@app.post("/patients")
def create_patient(payload: PatientCreate):
    try:
        row = db.create_patient(payload.to_db())
    except db.DuplicatePhone as dup:
        raise ApiError(409, "duplicate_phone", "A patient with this phone number already exists.",
                       existing_patient_id=(dup.existing or {}).get("patient_id"))
    log.info("patient_created payload=%s", row)
    return ok(row, 201)


@app.put("/patients/{patient_id}")
def update_patient(patient_id: str, payload: PatientUpdate):
    pid = _uuid(patient_id)
    changes = payload.to_db()
    if not changes:
        raise ApiError(400, "bad_request", "Provide at least one field to update.")
    try:
        row = db.update_patient(pid, changes)
    except db.DuplicatePhone as dup:
        raise ApiError(409, "duplicate_phone", "Another patient already uses this phone number.",
                       existing_patient_id=(dup.existing or {}).get("patient_id"))
    if not row:
        raise ApiError(404, "not_found", "Patient not found.")
    log.info("patient_updated id=%s fields=%s", pid, sorted(changes))
    return ok(row)


@app.delete("/patients/{patient_id}")
def delete_patient(patient_id: str):
    res = db.soft_delete_patient(_uuid(patient_id))
    if not res:
        raise ApiError(404, "not_found", "Patient not found.")
    return ok(res)


@app.get("/patients/{patient_id}/calls")
def patient_calls(patient_id: str):
    pid = _uuid(patient_id)
    if not db.get_patient(pid):
        raise ApiError(404, "not_found", "Patient not found.")
    return ok(db.list_calls(pid))
