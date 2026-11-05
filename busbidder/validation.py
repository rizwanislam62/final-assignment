"""Field-level parsing and validation helpers.

Regular expressions are used where the input has a fixed *structure*
(identifiers, booking references, timestamps, money amounts, weight lists).
Semantic checks that a regex cannot express well (is 2027-02-30 a real date?
is a rating inside the scale?) are done with ordinary Python code.

Each parser either returns a clean, typed value or raises
``RecordValidationError`` with a stable category code.  Harmless formatting
problems are *corrected* and reported through the ``corrections`` list.
"""

from __future__ import annotations

import re
from datetime import datetime

from .exceptions import RecordValidationError

# ------------------------------------------------------------------ patterns
CODE_RE = re.compile(r"^[A-Z]{3}$")
OFFER_ID_RE = re.compile(r"^(?P<company>[A-Z]{3})-(?P<destination>[A-Z]{3})-(?P<seq>\d{4})$")
# Booking reference: BB-<travel date YYYYMMDD>-<6 chars>.  The alphabet leaves
# out I, O, 0 and 1 so references can be read aloud without confusion.
BOOKING_REF_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
BOOKING_REF_RE = re.compile(r"^BB-(?P<date>\d{8})-(?P<code>[A-HJ-NP-Z2-9]{6})$")
DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$")
MONEY_RE = re.compile(r"^(?P<sign>-?)(?P<int>\d+)(?:(?P<sep>[.,])(?P<dec>\d{1,2}))?$")
INT_RE = re.compile(r"^-?\d+$")
DECIMAL_RE = re.compile(r"^-?\d+(?:\.\d+)?$")

TRUE_WORDS = {"true", "yes", "y", "1"}
FALSE_WORDS = {"false", "no", "n", "0"}
FARE_TYPES = {"basic", "flex"}


def clean_text(value: str | None) -> str:
    return (value or "").strip()


def require(row: dict, name: str) -> str:
    value = clean_text(row.get(name))
    if not value:
        raise RecordValidationError("MISSING_FIELD", f"required field '{name}' is empty", name)
    return value


def parse_code(value: str, name: str, corrections: list[str]) -> str:
    """Three-letter upper-case code; lower case is corrected, anything else rejected."""
    upper = value.upper()
    if upper != value:
        corrections.append(f"{name} '{value}' upper-cased to '{upper}'")
    if not CODE_RE.match(upper):
        raise RecordValidationError("MALFORMED_ID", f"{name} '{value}' is not a three-letter code", name)
    return upper


def parse_offer_id(value: str) -> re.Match:
    match = OFFER_ID_RE.match(value)
    if not match:
        raise RecordValidationError(
            "MALFORMED_ID", f"offer_id '{value}' does not match the pattern AAA-BBB-0000", "offer_id"
        )
    return match


def parse_datetime(value: str, name: str) -> datetime:
    if not DATETIME_RE.match(value):
        raise RecordValidationError(
            "INVALID_DATE", f"{name} '{value}' is not in the format YYYY-MM-DDTHH:MM", name
        )
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M")
    except ValueError as exc:  # e.g. 2027-02-30 or 25:00 passes the regex but is impossible
        raise RecordValidationError("INVALID_DATE", f"{name} '{value}' is not a real date/time", name) from exc


def parse_money(value: str, name: str, corrections: list[str]) -> float:
    match = MONEY_RE.match(value)
    if not match:
        raise RecordValidationError("INVALID_NUMBER", f"{name} '{value}' is not a money amount", name)
    if match["sep"] == ",":
        corrections.append(f"{name} '{value}' used a decimal comma; read as {value.replace(',', '.')}")
    amount = float(f"{match['sign']}{match['int']}.{match['dec'] or '0'}")
    return amount


def parse_int(value: str, name: str) -> int:
    if not INT_RE.match(value):
        raise RecordValidationError("INVALID_NUMBER", f"{name} '{value}' is not a whole number", name)
    return int(value)


def parse_optional_decimal(value: str, name: str) -> float | None:
    if value == "":
        return None
    if not DECIMAL_RE.match(value):
        raise RecordValidationError("INVALID_NUMBER", f"{name} '{value}' is not a number", name)
    return float(value)


def parse_bool(value: str, name: str) -> bool:
    lowered = value.lower()
    if lowered in TRUE_WORDS:
        return True
    if lowered in FALSE_WORDS:
        return False
    raise RecordValidationError("INVALID_BOOLEAN", f"{name} '{value}' is not yes/no or true/false", name)


def is_valid_booking_reference(reference: str) -> bool:
    return bool(BOOKING_REF_RE.match(reference or ""))
