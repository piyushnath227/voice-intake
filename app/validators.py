"""
Field-level validation + normalisation.

Single source of truth for the data-model rules. It is used by:
  * the REST API (via pydantic models in schemas.py)  -> server-side validation
  * the voice agent's `validate_field` tool           -> instant, field-specific re-prompts

Every function returns the normalised value (or None for a blank optional field)
and raises ValueError with a message that is safe to say out loud on a phone call.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from functools import partial

SEX_VALUES = ("Male", "Female", "Other", "Decline to Answer")
_SEX_ALIASES = {
    "male": "Male", "m": "Male", "man": "Male",
    "female": "Female", "f": "Female", "woman": "Female",
    "other": "Other", "non-binary": "Other", "nonbinary": "Other",
    "decline": "Decline to Answer", "decline to answer": "Decline to Answer",
    "prefer not to say": "Decline to Answer", "prefer not to answer": "Decline to Answer",
    "rather not say": "Decline to Answer",
}

US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa",
    "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire",
    "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York", "NC": "North Carolina",
    "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
    "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee",
    "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
    "DC": "District of Columbia", "PR": "Puerto Rico", "GU": "Guam", "VI": "Virgin Islands",
    "AS": "American Samoa", "MP": "Northern Mariana Islands",
}
_STATE_BY_NAME = {name.lower(): abbr for abbr, name in US_STATES.items()}

_CTRL = re.compile(r"[\x00-\x1f\x7f]")
_WS = re.compile(r"\s+")
_NAME_RE = re.compile(r"^[^\W\d_]+(?:[ '\-][^\W\d_]+)*$")          # letters, with inner - ' space
_CITY_RE = re.compile(r"^(?=.*[^\W\d_])[\w .'\-]+$")
_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}$")
_ZIP_RE = re.compile(r"^\d{5}(-\d{4})?$")
_MEMBER_RE = re.compile(r"^[A-Za-z0-9]{1,40}$")


# ---------- helpers ----------
def clean(v) -> str | None:
    """Sanitise free text: drop control chars + angle brackets, collapse whitespace."""
    if v is None:
        return None
    s = _CTRL.sub(" ", str(v)).replace("<", "").replace(">", "")
    return _WS.sub(" ", s).strip()


def _blank(v) -> bool:
    return v is None or clean(v) == ""


def _required(label: str) -> ValueError:
    return ValueError(f"The {label} is required.")


def _fix_case(s: str) -> str:
    # Speech-to-text often returns all-lower or all-upper names. Keep "McDonald" etc. untouched.
    return s.title() if (s.islower() or s.isupper()) else s


# ---------- rules ----------
def person_name(v, label: str, required: bool = True):
    if _blank(v):
        if required:
            raise _required(label)
        return None
    s = clean(v)
    if len(s) > 50:
        raise ValueError(f"The {label} is too long; it must be 50 characters or fewer.")
    if not _NAME_RE.match(s):
        raise ValueError(f"The {label} can only contain letters, hyphens and apostrophes.")
    return _fix_case(s)


def dob(v, required: bool = True):
    if _blank(v):
        if required:
            raise _required("date of birth")
        return None
    if isinstance(v, datetime):
        v = v.date()
    if isinstance(v, date):
        d = v
    else:
        s = clean(v)
        d = None
        for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y"):
            try:
                d = datetime.strptime(s, fmt).date()
                break
            except ValueError:
                continue
        if d is None:
            raise ValueError("That isn't a real calendar date. Please give month, day and four-digit year.")
    if d > date.today():
        raise ValueError("That date of birth is in the future.")
    if d.year < 1900:
        raise ValueError("That year seems too far back. Please check the four-digit birth year.")
    return d


def sex(v, required: bool = True):
    if _blank(v):
        if required:
            raise _required("sex")
        return None
    s = clean(v)
    if s in SEX_VALUES:
        return s
    mapped = _SEX_ALIASES.get(s.lower())
    if not mapped:
        raise ValueError("Sex must be one of: Male, Female, Other, or Decline to Answer.")
    return mapped


def phone(v, label: str = "phone number", required: bool = True):
    if _blank(v):
        if required:
            raise _required(label)
        return None
    digits = re.sub(r"\D", "", str(v))
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        raise ValueError(
            f"That {label} has {len(digits)} digits, but a U.S. number needs 10 including the area code."
        )
    if digits[0] in "01" or digits[3] in "01":
        raise ValueError(f"That {label} doesn't look like a valid U.S. number.")
    return digits


def email(v, required: bool = False):
    if _blank(v):
        if required:
            raise _required("email address")
        return None
    s = clean(v).lower().replace(" ", "")
    if len(s) > 254 or not _EMAIL_RE.match(s):
        raise ValueError("That email address doesn't look valid. It needs a name, an @ sign and a domain.")
    return s


def free_text(v, label: str, max_len: int, required: bool = True):
    if _blank(v):
        if required:
            raise _required(label)
        return None
    s = clean(v)
    if len(s) > max_len:
        raise ValueError(f"The {label} is too long; it must be {max_len} characters or fewer.")
    return s


def city(v, required: bool = True):
    s = free_text(v, "city", 100, required)
    if s and not _CITY_RE.match(s):
        raise ValueError("The city name contains characters I can't accept. Please say it again.")
    return _fix_case(s) if s else s


def state(v, required: bool = True):
    if _blank(v):
        if required:
            raise _required("state")
        return None
    s = clean(v)
    up = s.upper().rstrip(".")
    if up in US_STATES:
        return up
    abbr = _STATE_BY_NAME.get(s.lower())
    if abbr:
        return abbr
    raise ValueError("That isn't a U.S. state I recognise. Please say the state name or its two-letter code.")


def zip_code(v, required: bool = True):
    if _blank(v):
        if required:
            raise _required("ZIP code")
        return None
    s = clean(v).replace(" ", "")
    if re.fullmatch(r"\d{9}", s):
        s = f"{s[:5]}-{s[5:]}"
    if not _ZIP_RE.match(s):
        raise ValueError("A ZIP code needs 5 digits, or 9 digits for ZIP plus four.")
    return s


def member_id(v, required: bool = False):
    if _blank(v):
        return None
    s = re.sub(r"[\s\-]", "", clean(v))
    if not _MEMBER_RE.match(s):
        raise ValueError("A member ID can only contain letters and numbers, up to 40 characters.")
    return s.upper()


def language(v, required: bool = False):
    if _blank(v):
        return "English"
    s = clean(v)
    if len(s) > 30 or not _NAME_RE.match(s):
        raise ValueError("That language name doesn't look right. Please say it again.")
    return _fix_case(s)


# field name -> rule. `required` mirrors the data-model table.
FIELD_RULES = {
    "first_name": partial(person_name, label="first name", required=True),
    "last_name": partial(person_name, label="last name", required=True),
    "date_of_birth": partial(dob, required=True),
    "sex": partial(sex, required=True),
    "phone_number": partial(phone, label="phone number", required=True),
    "email": partial(email, required=False),
    "address_line_1": partial(free_text, label="street address", max_len=100, required=True),
    "address_line_2": partial(free_text, label="apartment or unit", max_len=100, required=False),
    "city": partial(city, required=True),
    "state": partial(state, required=True),
    "zip_code": partial(zip_code, required=True),
    "insurance_provider": partial(free_text, label="insurance provider", max_len=100, required=False),
    "insurance_member_id": partial(member_id, required=False),
    "preferred_language": partial(language, required=False),
    "emergency_contact_name": partial(free_text, label="emergency contact name", max_len=100, required=False),
    "emergency_contact_phone": partial(phone, label="emergency contact phone number", required=False),
}
