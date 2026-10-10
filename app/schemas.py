"""Pydantic request models. All rule logic lives in validators.py."""
from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from . import validators as V


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")  # unknown / read-only fields (patient_id, created_at...) -> 422

    @field_validator("*", mode="before")
    @classmethod
    def _normalise(cls, v, info):
        return V.FIELD_RULES[info.field_name](v)


class PatientCreate(_Base):
    first_name: str
    last_name: str
    date_of_birth: date
    sex: str
    phone_number: str
    email: str | None = None
    address_line_1: str
    address_line_2: str | None = None
    city: str
    state: str
    zip_code: str
    insurance_provider: str | None = None
    insurance_member_id: str | None = None
    preferred_language: str = "English"
    emergency_contact_name: str | None = None
    emergency_contact_phone: str | None = None

    def to_db(self) -> dict:
        return self.model_dump(exclude_none=True)


class PatientUpdate(_Base):
    """Partial update: only fields present in the body are changed."""
    first_name: str | None = None
    last_name: str | None = None
    date_of_birth: date | None = None
    sex: str | None = None
    phone_number: str | None = None
    email: str | None = None
    address_line_1: str | None = None
    address_line_2: str | None = None
    city: str | None = None
    state: str | None = None
    zip_code: str | None = None
    insurance_provider: str | None = None
    insurance_member_id: str | None = None
    preferred_language: str | None = None
    emergency_contact_name: str | None = None
    emergency_contact_phone: str | None = None

    def to_db(self) -> dict:
        return self.model_dump(exclude_unset=True)


def format_errors(errors: list[dict]) -> list[dict]:
    """Turn pydantic errors into [{field, message}] with caller-friendly wording."""
    out = []
    for e in errors:
        field = ".".join(str(p) for p in e["loc"] if p != "body") or "body"
        msg = e["msg"]
        if e["type"] == "missing":
            msg = f"The {field.replace('_', ' ')} is required."
        elif e["type"] == "extra_forbidden":
            msg = f"'{field}' is not an accepted field."
        elif msg.startswith("Value error, "):
            msg = msg[len("Value error, "):]
        out.append({"field": field, "message": msg})
    return out


__all__ = ["PatientCreate", "PatientUpdate", "ValidationError", "format_errors"]
