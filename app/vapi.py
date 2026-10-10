"""
Vapi webhook: the bridge between the voice agent (LLM) and the data layer.

One endpoint, POST /vapi/webhook, receives two kinds of Vapi server messages:
  * "tool-calls"         -> the LLM wants to run one of our tools; we reply {"results": [...]}
  * "end-of-call-report" -> call finished (also fires if the caller hangs up / the line drops);
                            we persist transcript + summary and log it.

Design rule: tools never raise. Every outcome (success, validation error, duplicate, outage) comes back
as a small JSON result with a `say` hint, so the caller always hears something and never silence.
"""
from __future__ import annotations

import hmac
import json
import logging
import os

from fastapi import APIRouter, Header, HTTPException, Request

from . import db, validators as V
from .schemas import PatientCreate, PatientUpdate, ValidationError, format_errors

log = logging.getLogger("patient-intake.vapi")
router = APIRouter(prefix="/vapi", tags=["vapi"])

OUTAGE_SAY = ("I'm sorry, I'm having trouble reaching our system right now, and I haven't been able to save "
              "your information. Please call back in a few minutes.")


def _check_secret(x_vapi_secret: str | None, authorization: str | None) -> None:
    expected = os.environ.get("VAPI_SECRET")
    if not expected:
        return  # secret not configured (local dev)
    supplied = x_vapi_secret or (authorization or "").removeprefix("Bearer ").strip()
    if not supplied or not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="invalid webhook secret")


# ---------------- tools ----------------
def validate_field(args: dict, ctx: dict) -> dict:
    """Check ONE field the moment the caller says it, so we can re-prompt for exactly that field."""
    field, value = args.get("field"), args.get("value")
    rule = V.FIELD_RULES.get(field)
    if not rule:
        return {"valid": False, "problem": f"Unknown field '{field}'."}
    try:
        norm = rule(value)
    except ValueError as e:
        return {"valid": False, "field": field, "problem": str(e)}
    return {"valid": True, "field": field, "normalized": norm.strftime("%m/%d/%Y") if hasattr(norm, "strftime") else norm}


def find_patient_by_phone(args: dict, ctx: dict) -> dict:
    try:
        phone = V.phone(args.get("phone_number"))
    except ValueError as e:
        return {"found": False, "valid": False, "problem": str(e)}
    p = db.find_by_phone(phone)
    if not p:
        return {"found": False, "valid": True}
    return {"found": True, "patient_id": p["patient_id"], "first_name": p["first_name"], "last_name": p["last_name"]}


def register_patient(args: dict, ctx: dict) -> dict:
    data = {k: v for k, v in args.items() if k in PatientCreate.model_fields and v not in (None, "")}
    try:
        model = PatientCreate(**data)
    except ValidationError as e:
        return {"success": False, "errors": format_errors(e.errors()),
                "say": "Ask the caller again, specifically, for each field listed in errors."}
    try:
        row = db.create_patient(model.to_db())
    except db.DuplicatePhone as dup:
        ex = dup.existing or {}
        return {"success": False, "duplicate": True, "patient_id": ex.get("patient_id"),
                "existing_first_name": ex.get("first_name"), "existing_last_name": ex.get("last_name")}
    except db.DatabaseError:
        return {"success": False, "system_error": True, "say": OUTAGE_SAY}
    log.info("registration_complete payload=%s", json.dumps(row, default=str))
    _link(ctx, row["patient_id"])
    return {"success": True, "patient_id": row["patient_id"], "first_name": row["first_name"]}


def update_patient(args: dict, ctx: dict) -> dict:
    pid = args.get("patient_id")
    try:
        existing = db.get_patient(pid) if pid else None
    except db.DatabaseError:
        return {"success": False, "system_error": True, "say": OUTAGE_SAY}
    except Exception:
        existing = None
    if not existing:
        return {"success": False, "problem": "No matching patient record was found."}
    try:  # light identity check before changing anything
        supplied = V.dob(args.get("verify_date_of_birth"))
    except ValueError as e:
        return {"success": False, "verification_failed": True, "problem": str(e)}
    if supplied.strftime("%m/%d/%Y") != existing["date_of_birth"]:
        return {"success": False, "verification_failed": True,
                "problem": "That date of birth doesn't match our records, so I can't change this record."}
    changes = {k: v for k, v in args.items() if k in PatientUpdate.model_fields and v not in (None, "")}
    if not changes:
        return {"success": False, "problem": "No fields to update were provided."}
    try:
        model = PatientUpdate(**changes)
    except ValidationError as e:
        return {"success": False, "errors": format_errors(e.errors())}
    try:
        row = db.update_patient(pid, model.to_db())
    except db.DuplicatePhone:
        return {"success": False, "problem": "Another patient already uses that phone number."}
    except db.DatabaseError:
        return {"success": False, "system_error": True, "say": OUTAGE_SAY}
    log.info("update_complete id=%s fields=%s", pid, sorted(changes))
    _link(ctx, pid)
    return {"success": True, "first_name": row["first_name"] if row else None}


TOOLS = {
    "validate_field": validate_field,
    "find_patient_by_phone": find_patient_by_phone,
    "register_patient": register_patient,
    "update_patient": update_patient,
}


def _link(ctx: dict, patient_id: str) -> None:
    """Best effort: attach this call to the patient so transcript + summary can be linked later."""
    try:
        if ctx.get("call_id"):
            db.link_call(ctx["call_id"], ctx.get("caller"), patient_id)
    except Exception:
        log.exception("could not link call to patient")


# ---------------- webhook ----------------
def _parse_tool_calls(message: dict) -> list[tuple[str, str, dict]]:
    calls = []
    for tc in message.get("toolCallList") or message.get("toolCalls") or []:
        fn = tc.get("function") or {}
        name = tc.get("name") or fn.get("name")
        args = tc.get("arguments", fn.get("arguments", tc.get("parameters", {})))
        if isinstance(args, str):
            try:
                args = json.loads(args or "{}")
            except json.JSONDecodeError:
                args = {}
        calls.append((tc.get("id", ""), name, args or {}))
    return calls


@router.post("/webhook")
async def webhook(request: Request, x_vapi_secret: str | None = Header(default=None),
                  authorization: str | None = Header(default=None)):
    _check_secret(x_vapi_secret, authorization)
    try:
        message = (await request.json()).get("message") or {}
    except Exception:
        raise HTTPException(status_code=400, detail="invalid JSON")

    call = message.get("call") or {}
    ctx = {"call_id": call.get("id"), "caller": (call.get("customer") or {}).get("number")}
    mtype = message.get("type")

    if mtype == "tool-calls":
        results = []
        for call_id, name, args in _parse_tool_calls(message):
            handler = TOOLS.get(name)
            try:
                result = handler(args, ctx) if handler else {"error": f"unknown tool {name}"}
            except Exception:
                log.exception("tool %s crashed", name)
                result = {"success": False, "system_error": True, "say": OUTAGE_SAY}
            results.append({"name": name, "toolCallId": call_id, "result": json.dumps(result)})
        return {"results": results}

    if mtype == "end-of-call-report":
        art = message.get("artifact") or {}
        transcript = message.get("transcript") or art.get("transcript")
        summary = message.get("summary") or (message.get("analysis") or {}).get("summary")
        log.info("call_ended call_id=%s reason=%s summary=%s", ctx["call_id"], message.get("endedReason"), summary)
        log.info("call_transcript call_id=%s transcript=%s", ctx["call_id"], transcript)
        if ctx["call_id"]:
            try:
                db.save_call_report(ctx["call_id"], ctx["caller"], call.get("startedAt") or message.get("startedAt"),
                                    call.get("endedAt") or message.get("endedAt"), message.get("endedReason"),
                                    summary, transcript)
            except Exception:
                log.exception("could not save call report")
        return {"ok": True}

    return {"ok": True}  # status-update, speech-update, etc.: acknowledged and ignored
