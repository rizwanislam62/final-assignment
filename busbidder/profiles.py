"""Traveller profiles loaded from ``config/profiles.json``.

A profile is a reusable bundle of hard constraints, soft preferences and
weights.  The journey itself (destination and travel window) is supplied
separately, so the *same offers* can be ranked for different travellers.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .exceptions import ConfigurationError, DataFileError, InvalidRequestError
from .models import DeparturePreference, Destination, TravelRequest
from .scoring import validate_weights

ALLOWED_CONSTRAINTS = {
    "max_budget_eur": float,
    "max_transfers": int,
    "changeable_required": bool,
    "refundable_required": bool,
    "min_luggage_kg": int,
    "max_duration_min": int,
}


@dataclass
class TravellerProfile:
    key: str
    name: str
    description: str
    constraints: dict = field(default_factory=dict)
    departure: str = "any"
    weights: dict[str, float] = field(default_factory=dict)   # validated fractions

    def build_request(
        self, destination: Destination, date_from: date, date_to: date, party_size: int = 1
    ) -> TravelRequest:
        return TravelRequest(
            destination=destination,
            date_from=date_from,
            date_to=date_to,
            party_size=party_size,
            preferred_departure=DeparturePreference.parse(self.departure),
            weights=dict(self.weights),
            traveller_label=self.name,
            **self.constraints,
        )


def load_profiles(path: Path) -> dict[str, TravellerProfile]:
    path = Path(path)
    if not path.is_file():
        raise DataFileError(f"Profiles file not found: {path}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigurationError(f"Profiles file {path} is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict) or not raw:
        raise ConfigurationError(f"Profiles file {path} must contain at least one profile")

    profiles: dict[str, TravellerProfile] = {}
    for key, spec in raw.items():
        try:
            constraints = {}
            for name, value in spec.get("constraints", {}).items():
                if name not in ALLOWED_CONSTRAINTS:
                    raise ConfigurationError(f"unknown constraint '{name}'")
                expected = ALLOWED_CONSTRAINTS[name]
                if expected is bool and not isinstance(value, bool):
                    raise ConfigurationError(f"'{name}' must be true or false")
                if expected is not bool and (isinstance(value, bool) or not isinstance(value, (int, float))):
                    raise ConfigurationError(f"'{name}' must be a number")
                constraints[name] = expected(value)
            departure = spec.get("preferences", {}).get("departure", "any")
            DeparturePreference.parse(departure)  # validate early
            weights = validate_weights({k: float(v) for k, v in spec["weights"].items()})
            profiles[key] = TravellerProfile(
                key=key,
                name=spec.get("name", key),
                description=spec.get("description", ""),
                constraints=constraints,
                departure=departure,
                weights=weights,
            )
        except (KeyError, TypeError, ValueError, InvalidRequestError, ConfigurationError) as exc:
            raise ConfigurationError(f"Profile '{key}' in {path} is invalid: {exc}") from exc
    return profiles
