"""Simulated booking: references, seat inventory, persistence and cancellation.

Design decisions
----------------
* The published offer file is treated as immutable source data.  Seats left
  are *derived*: ``published seats - seats held by confirmed bookings``.  This
  keeps capacity consistent by construction (there is no second counter that
  can drift), makes runs reproducible, and lets a cancellation release seats
  automatically.
* Bookings are stored in a JSON file that is written atomically (write to a
  temporary file, then ``os.replace``), so a crash cannot leave half a file.
* A booking stores a *copy* of the fare conditions at booking time, so later
  changes to the offer data cannot change what the traveller bought.
* No payment data is collected.  ``payment_status`` is simulated.
"""

from __future__ import annotations

import json
import os
import random
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from .exceptions import (
    BookingError,
    DataFileError,
    InvalidBookingReferenceError,
    InvalidRequestError,
    SeatUnavailableError,
)
from .loader import Marketplace
from .logging_setup import get_logger
from .models import FareConditions, Offer
from .validation import BOOKING_REF_ALPHABET, is_valid_booking_reference

log = get_logger("booking")

MAX_SEATS_PER_BOOKING = 9
NAME_RE = re.compile(r"^[^\W\d_](?:[^\W\d_]|[ '.-]){1,59}$")

CONFIRMED = "CONFIRMED"
CANCELLED = "CANCELLED"


@dataclass
class Booking:
    reference: str
    offer_id: str
    traveller_name: str
    seats: int
    company: str
    destination: str
    departure: str          # ISO "YYYY-MM-DDTHH:MM"
    arrival: str
    price_per_seat_eur: float
    total_price_eur: float
    fare: FareConditions
    status: str
    payment_status: str
    created_at: str
    cancelled_at: str | None = None
    refund_eur: float = 0.0
    travel_credit_eur: float = 0.0

    def to_dict(self) -> dict:
        data = asdict(self)
        data["fare"] = self.fare.to_dict()
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "Booking":
        data = dict(data)
        data["fare"] = FareConditions.from_dict(data["fare"])
        return cls(**data)


class BookingStore:
    """Reads and writes the bookings JSON file."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def load(self) -> list[Booking]:
        if not self.path.exists():
            return []
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            bookings = [Booking.from_dict(item) for item in raw.get("bookings", [])]
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise DataFileError(f"Booking file {self.path} is corrupt: {exc}") from exc
        for b in bookings:
            if not is_valid_booking_reference(b.reference):
                raise DataFileError(f"Booking file {self.path} contains an invalid reference '{b.reference}'")
        return bookings

    def save(self, bookings: list[Booking]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        payload = {"bookings": [b.to_dict() for b in bookings]}
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.path)


class BookingService:
    def __init__(
        self,
        marketplace: Marketplace,
        store: BookingStore,
        rng: random.Random | None = None,
        clock: Callable[[], datetime] = datetime.now,
    ):
        self.marketplace = marketplace
        self.store = store
        self.rng = rng or random.SystemRandom()
        self.clock = clock
        self.bookings: list[Booking] = store.load()
        for b in self.bookings:
            if marketplace.offer(b.offer_id) is None:
                log.warning("Booking %s refers to offer %s which is not in the current data",
                            b.reference, b.offer_id)

    # ------------------------------------------------------------ inventory
    def seats_held(self, offer_id: str) -> int:
        return sum(b.seats for b in self.bookings if b.offer_id == offer_id and b.status == CONFIRMED)

    def seats_left(self, offer: Offer) -> int:
        return max(0, offer.seats_available - self.seats_held(offer.offer_id))

    # -------------------------------------------------------------- booking
    def _new_reference(self, offer: Offer) -> str:
        existing = {b.reference for b in self.bookings}
        for _ in range(50):
            code = "".join(self.rng.choice(BOOKING_REF_ALPHABET) for _ in range(6))
            reference = f"BB-{offer.departure:%Y%m%d}-{code}"
            if reference not in existing:
                return reference
        raise BookingError("Could not generate a unique booking reference")

    def book(self, offer_id: str, traveller_name: str, seats: int = 1) -> Booking:
        name = (traveller_name or "").strip()
        if not NAME_RE.match(name):
            raise InvalidRequestError(
                "Traveller name must be 2-60 letters (spaces, hyphens, apostrophes and dots allowed)."
            )
        if not 1 <= seats <= MAX_SEATS_PER_BOOKING:
            raise InvalidRequestError(f"Seats per booking must be between 1 and {MAX_SEATS_PER_BOOKING}.")
        offer = self.marketplace.offer(offer_id)
        if offer is None:
            raise BookingError(f"Offer '{offer_id}' does not exist.")
        if offer.departure <= self.clock():
            raise BookingError(f"Offer {offer.offer_id} has already departed.")
        left = self.seats_left(offer)  # re-checked at booking time, not taken from the ranking
        if left < seats:
            log.warning("Booking refused for %s: %d seat(s) requested, %d left", offer.offer_id, seats, left)
            raise SeatUnavailableError(
                f"Offer {offer.offer_id} has {left} seat(s) left; {seats} requested."
            )
        booking = Booking(
            reference=self._new_reference(offer),
            offer_id=offer.offer_id,
            traveller_name=name,
            seats=seats,
            company=offer.company.name,
            destination=offer.destination.name,
            departure=offer.departure.strftime("%Y-%m-%dT%H:%M"),
            arrival=offer.arrival.strftime("%Y-%m-%dT%H:%M"),
            price_per_seat_eur=offer.price_eur,
            total_price_eur=round(offer.price_eur * seats, 2),
            fare=offer.fare,
            status=CONFIRMED,
            payment_status="PAID (simulated)",
            created_at=self.clock().isoformat(timespec="seconds"),
        )
        self.bookings.append(booking)
        self.store.save(self.bookings)
        log.info("Booking %s created: %s, %d seat(s), EUR %.2f, %d seat(s) left",
                 booking.reference, offer.offer_id, seats, booking.total_price_eur, self.seats_left(offer))
        return booking

    def get(self, reference: str) -> Booking:
        reference = (reference or "").strip().upper()
        if not is_valid_booking_reference(reference):
            raise InvalidBookingReferenceError(
                f"'{reference}' is not a valid booking reference (expected BB-YYYYMMDD-XXXXXX)."
            )
        for b in self.bookings:
            if b.reference == reference:
                return b
        raise BookingError(f"No booking with reference {reference}.")

    def cancel(self, reference: str) -> Booking:
        """Cancel a booking and apply the stored fare conditions (simulated refund).

        refundable fare               -> full refund
        changeable, non-refundable    -> travel credit = price - change fee per seat
        neither                       -> no money back
        """
        booking = self.get(reference)
        if booking.status == CANCELLED:
            raise BookingError(f"Booking {booking.reference} is already cancelled.")
        if datetime.strptime(booking.departure, "%Y-%m-%dT%H:%M") <= self.clock():
            raise BookingError(f"Booking {booking.reference} has already departed and cannot be cancelled.")
        fare = booking.fare
        if fare.refundable:
            booking.refund_eur = booking.total_price_eur
            booking.payment_status = "REFUNDED (simulated)"
        elif fare.changeable:
            credit = max(0.0, booking.price_per_seat_eur - fare.change_fee_eur) * booking.seats
            booking.travel_credit_eur = round(credit, 2)
            booking.payment_status = "TRAVEL CREDIT ISSUED (simulated)"
        else:
            booking.payment_status = "NOT REFUNDED (non-refundable fare)"
        booking.status = CANCELLED
        booking.cancelled_at = self.clock().isoformat(timespec="seconds")
        self.store.save(self.bookings)
        log.info("Booking %s cancelled: refund EUR %.2f, credit EUR %.2f",
                 booking.reference, booking.refund_eur, booking.travel_credit_eur)
        return booking
