import copy, uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import app


class FakeDB:
    """In-memory stand-in with the same contract as app.db, so API/voice logic is testable without Postgres."""
    def __init__(self): self.rows = {}; self.fail = False
    def _chk(self):
        if self.fail: raise db.DatabaseError("down")
    def _out(self, r):
        o = copy.deepcopy(r); o["date_of_birth"] = r["date_of_birth"].strftime("%m/%d/%Y")
        o.pop("deleted_at", None); return o
    def find_by_phone(self, phone):
        self._chk()
        return next((self._out(r) for r in self.rows.values() if r["phone_number"] == phone and not r.get("deleted_at")), None)
    def create_patient(self, data):
        self._chk()
        if self.find_by_phone(data["phone_number"]): raise db.DuplicatePhone(self.find_by_phone(data["phone_number"]))
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        row = {"patient_id": str(uuid.uuid4()), "address_line_2": None, "email": None, "insurance_provider": None,
               "insurance_member_id": None, "emergency_contact_name": None, "emergency_contact_phone": None,
               "preferred_language": "English", "created_at": now, "updated_at": now, **data}
        self.rows[row["patient_id"]] = row; return self._out(row)
    def get_patient(self, pid):
        self._chk(); r = self.rows.get(pid)
        return self._out(r) if r and not r.get("deleted_at") else None
    def list_patients(self, last_name=None, date_of_birth=None, phone_number=None, limit=100, offset=0):
        self._chk(); out = [self._out(r) for r in self.rows.values() if not r.get("deleted_at")]
        if last_name: out = [r for r in out if r["last_name"].lower() == last_name.lower()]
        if phone_number: out = [r for r in out if r["phone_number"] == phone_number]
        if date_of_birth: out = [r for r in out if r["date_of_birth"] == date_of_birth.strftime("%m/%d/%Y")]
        return out
    def update_patient(self, pid, data):
        self._chk(); r = self.rows.get(pid)
        if not r or r.get("deleted_at"): return None
        r.update(data); return self._out(r)
    def soft_delete_patient(self, pid):
        self._chk(); r = self.rows.get(pid)
        if not r or r.get("deleted_at"): return None
        r["deleted_at"] = "now"; return {"patient_id": pid, "deleted_at": "now"}
    def link_call(self, *a): pass
    def save_call_report(self, *a): self.saved = a
    def list_calls(self, pid): return []


@pytest.fixture
def fake(monkeypatch):
    f = FakeDB()
    for name in ("find_by_phone", "create_patient", "get_patient", "list_patients", "update_patient",
                 "soft_delete_patient", "link_call", "save_call_report", "list_calls"):
        monkeypatch.setattr(db, name, getattr(f, name))
    monkeypatch.delenv("VAPI_SECRET", raising=False)
    return f


@pytest.fixture
def client(fake):
    return TestClient(app, raise_server_exceptions=False)


GOOD = {"first_name": "jane", "last_name": "o'brien", "date_of_birth": "04/12/1990", "sex": "female",
        "phone_number": "(212) 555-0142", "address_line_1": "123 Main St", "city": "new york",
        "state": "New York", "zip_code": "10001"}
