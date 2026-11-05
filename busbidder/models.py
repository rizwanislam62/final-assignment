"""Domain model for BusBidder Spain.

The classes are small, mostly immutable dataclasses.  Behaviour that belongs to
a single concept lives on that class (for example ``Offer.duration_min`` or
``DeparturePreference.minutes_outside``); behaviour that combines several
concepts (filtering, scoring, booking) lives in its own module.

Composition is used throughout: an ``Offer`` *has a* ``Company``, a
``Destination`` and a ``FareConditions`` object rather than copying their
fields.  This keeps one source of truth for, e.g., a company's name.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time

from .exceptions import InvalidRequestError

ORIGIN = "Madrid"  # Madrid is the single departure hub of the marketplace.

MINUTES_PER_DAY = 24 * 60


@dataclass(frozen=True)
class Company:
    company_id: str
    name: str
    profile: str


@dataclass(frozen=True)
class Destination:
    destination_id: str
    name: str
    region: str
    distance_km: int
    coastal: bool


@dataclass(frozen=True)
class FareConditions:
    """Ticket conditions attached to an offer (and copied into a booking)."""

    fare_type: str          # "basic" or "flex"
    changeable: bool
    change_fee_eur: float   # 0.0 means free changes (only meaningful if changeable)
    refundable: bool
    luggage_kg: int

    @property
    def summary(self) -> str:
        if self.changeable:
            change = "free changes" if self.change_fee_eur == 0 else f"changes EUR {self.change_fee_eur:.2f}"
        else:
            change = "no changes"
        refund = "refundable" if self.refundable else "non-refundable"
        return f"{self.fare_type}: {change}, {refund}, {self.luggage_kg} kg luggage"

    def to_dict(self) -> dict:
        return {
            "fare_type": self.fare_type,
            "changeable": self.changeable,
            "change_fee_eur": self.change_fee_eur,
            "refundable": self.refundable,
            "luggage_kg": self.luggage_kg,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "FareConditions":
        return cls(
            fare_type=str(data["fare_type"]),
            changeable=bool(data["changeable"]),
            change_fee_eur=float(data["change_fee_eur"]),
            refundable=bool(data["refundable"]),
            luggage_kg=int(data["luggage_kg"]),
        )


@dataclass(frozen=True)
class Offer:
    """One bookable departure from Madrid, as published by a company."""

    offer_id: str
    company: Company
    destination: Destination
    departure: datetime
    arrival: datetime
    price_eur: float
    transfers: int
    fare: FareConditions
    seats_available: int          # capacity in the published source data
    rating: float | None = None   # customer rating, 1.0-5.0, optional
    co2_kg: float | None = None   # estimated CO2 per passenger, optional

    @property
    def duration_min(self) -> int:
        return int((self.arrival - self.departure).total_seconds() // 60)

    @property
    def travel_date(self) -> date:
        return self.departure.date()

    @property
    def is_direct(self) -> bool:
        return self.transfers == 0

    @property
    def overnight(self) -> bool:
        return self.arrival.date() > self.departure.date()


# --------------------------------------------------------------------------
# Departure-time preference
# --------------------------------------------------------------------------
_TIME_RE = re.compile(r"^(?P<h>[01]\d|2[0-3]):(?P<m>[0-5]\d)$")

# Named periods as (start, end) in minutes after midnight.  "night" wraps
# around midnight, which is why distances are computed on a 24-hour circle.
NAMED_PERIODS: dict[str, tuple[int, int]] = {
    "morning": (6 * 60, 12 * 60),
    "afternoon": (12 * 60, 18 * 60),
    "evening": (18 * 60, 22 * 60),
    "night": (22 * 60, 6 * 60),
}


def _circular_distance(a: int, b: int) -> int:
    d = abs(a - b) % MINUTES_PER_DAY
    return min(d, MINUTES_PER_DAY - d)


def _fmt_minutes(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"


@dataclass(frozen=True)
class DeparturePreference:
    """A preferred departure period such as "morning" or an exact "09:30".

    A specific clock time is treated as a +/-30 minute window, because a bus
    leaving at 09:20 is, for practical purposes, as convenient as one at 09:30.
    The window is half-open: "morning" means 06:00 <= t < 12:00.
    """

    label: str
    start_min: int | None   # None means "any time is fine"
    end_min: int | None

    @classmethod
    def parse(cls, text: str | None) -> "DeparturePreference":
        value = (text or "any").strip().lower()
        if value == "any":
            return cls("any", None, None)
        if value in NAMED_PERIODS:
            start, end = NAMED_PERIODS[value]
            return cls(value, start, end)
        match = _TIME_RE.match(value)
        if match:
            centre = int(match["h"]) * 60 + int(match["m"])
            return cls(value, (centre - 30) % MINUTES_PER_DAY, (centre + 30) % MINUTES_PER_DAY)
        raise InvalidRequestError(
            f"Invalid departure preference '{text}'. Use morning, afternoon, evening, "
            "night, any, or a 24-hour time such as 09:30."
        )

    @property
    def is_any(self) -> bool:
        return self.start_min is None

    def contains(self, minute_of_day: int) -> bool:
        if self.start_min is None:
            return True
        if self.start_min <= self.end_min:
            return self.start_min <= minute_of_day < self.end_min
        return minute_of_day >= self.start_min or minute_of_day < self.end_min  # wraps midnight

    def minutes_outside(self, t: time) -> int:
        """Minutes between ``t`` and the nearest edge of the window (0 if inside)."""
        minute = t.hour * 60 + t.minute
        if self.contains(minute):
            return 0
        return min(_circular_distance(minute, self.start_min), _circular_distance(minute, self.end_min))

    def describe(self) -> str:
        if self.start_min is None:
            return "any time"
        return f"{self.label} ({_fmt_minutes(self.start_min)}-{_fmt_minutes(self.end_min)})"


# --------------------------------------------------------------------------
# Traveller request
# --------------------------------------------------------------------------
@dataclass
class TravelRequest:
    """What a traveller asks for.

    Fields are split into two groups on purpose:

    * hard constraints - an offer that violates any of them is removed before
      ranking (destination, travel window, seats and the optional limits);
    * soft preferences - the preferred departure period and the criterion
      weights.  They only change the score, never remove an offer.
    """

    destination: Destination
    date_from: date
    date_to: date
    party_size: int = 1
    # ---- optional hard constraints (None / False = not required)
    max_budget_eur: float | None = None      # per passenger
    max_transfers: int | None = None
    changeable_required: bool = False
    refundable_required: bool = False
    min_luggage_kg: int | None = None
    max_duration_min: int | None = None
    # ---- soft preferences
    preferred_departure: DeparturePreference = field(
        default_factory=lambda: DeparturePreference("any", None, None)
    )
    weights: dict[str, float] = field(default_factory=dict)
    traveller_label: str = "Custom traveller"

    def __post_init__(self) -> None:
        if self.date_to < self.date_from:
            raise InvalidRequestError(
                f"Travel window ends ({self.date_to}) before it starts ({self.date_from})."
            )
        if self.party_size < 1:
            raise InvalidRequestError("Party size must be at least 1.")
        if self.max_budget_eur is not None and self.max_budget_eur <= 0:
            raise InvalidRequestError("Maximum budget must be a positive amount.")
        if self.max_transfers is not None and self.max_transfers < 0:
            raise InvalidRequestError("Maximum transfers cannot be negative.")
        if self.min_luggage_kg is not None and self.min_luggage_kg < 0:
            raise InvalidRequestError("Required luggage cannot be negative.")
        if self.max_duration_min is not None and self.max_duration_min <= 0:
            raise InvalidRequestError("Maximum duration must be positive.")

    def hard_constraint_lines(self) -> list[str]:
        """Human-readable list of the hard constraints in force."""
        lines = [
            f"Journey: Madrid -> {self.destination.name}",
            f"Travel window: {self.date_from:%d %b %Y} to {self.date_to:%d %b %Y}",
            f"Seats needed: {self.party_size}",
        ]
        if self.max_budget_eur is not None:
            lines.append(f"Maximum price: EUR {self.max_budget_eur:.2f} per passenger")
        if self.max_transfers is not None:
            lines.append(f"Maximum transfers: {self.max_transfers}")
        if self.changeable_required:
            lines.append("Changeable ticket: required")
        if self.refundable_required:
            lines.append("Refundable ticket: required")
        if self.min_luggage_kg is not None:
            lines.append(f"Luggage allowance: at least {self.min_luggage_kg} kg")
        if self.max_duration_min is not None:
            h, m = divmod(self.max_duration_min, 60)
            lines.append(f"Maximum journey time: {h}h{m:02d}")
        return lines
