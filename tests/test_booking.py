"""Section 6 - simulated booking, seat consistency, persistence and cancellation."""

from __future__ import annotations

import json
import random
from datetime import datetime

import pytest

from busbidder.booking import BookingService, BookingStore
from busbidder.exceptions import (
    BookingError, DataFileError, InvalidBookingReferenceError, InvalidRequestError, SeatUnavailableError,
)
from busbidder.loader import Marketplace
from busbidder.validation import BOOKING_REF_RE

NOW = datetime(2027, 1, 1, 12, 0)


@pytest.fixture
def market(settings):
    return Marketplace.load(settings, write_issue_files=False)


def service(market, settings, seed=1, clock=lambda: NOW):
    return BookingService(market, BookingStore(settings.bookings_file), random.Random(seed), clock)


def an_offer(market, seats_at_least=3, refundable=None, changeable=None):
    for o in market.offers:
        if o.seats_available >= seats_at_least \
                and (refundable is None or o.fare.refundable == refundable) \
                and (changeable is None or o.fare.changeable == changeable):
            return o
    raise AssertionError("no suitable offer in dataset")


def test_booking_creates_reference_reduces_seats_and_saves(market, settings):
    svc = service(market, settings)
    offer = an_offer(market)
    before = svc.seats_left(offer)
    booking = svc.book(offer.offer_id, "Ana López", 2)
    assert BOOKING_REF_RE.match(booking.reference)
    assert booking.reference.startswith(f"BB-{offer.departure:%Y%m%d}-")
    assert svc.seats_left(offer) == before - 2
    assert booking.total_price_eur == pytest.approx(offer.price_eur * 2)
    assert booking.fare == offer.fare                       # fare conditions stored with the booking
    assert "simulated" in booking.payment_status
    saved = json.loads(settings.bookings_file.read_text(encoding="utf-8"))
    assert saved["bookings"][0]["reference"] == booking.reference
    assert saved["bookings"][0]["fare"]["refundable"] == offer.fare.refundable


def test_seat_count_survives_restart(market, settings):
    offer = an_offer(market)
    service(market, settings).book(offer.offer_id, "Ana Lopez", 1)
    reloaded = service(market, settings, seed=2)
    assert reloaded.seats_left(offer) == offer.seats_available - 1


def test_cannot_book_more_seats_than_left(market, settings):
    svc = service(market, settings)
    offer = next(o for o in market.offers if 1 <= o.seats_available <= 3)
    svc.book(offer.offer_id, "Last Seats", offer.seats_available)     # exactly the remaining seats: allowed
    assert svc.seats_left(offer) == 0
    with pytest.raises(SeatUnavailableError):
        svc.book(offer.offer_id, "Too Late", 1)


def test_sold_out_offer_cannot_be_booked(market, settings):
    sold_out = next(o for o in market.offers if o.seats_available == 0)
    with pytest.raises(SeatUnavailableError):
        service(market, settings).book(sold_out.offer_id, "Ana Lopez", 1)


@pytest.mark.parametrize("name, seats", [("", 1), ("A", 1), ("R2D2", 1), ("Ana Lopez", 0), ("Ana Lopez", 10)])
def test_invalid_booking_input(market, settings, name, seats):
    with pytest.raises(InvalidRequestError):
        service(market, settings).book(an_offer(market).offer_id, name, seats)


def test_unknown_offer_and_departed_offer(market, settings):
    with pytest.raises(BookingError):
        service(market, settings).book("SOL-MAL-9999", "Ana Lopez", 1)
    late = service(market, settings, clock=lambda: datetime(2030, 1, 1))
    with pytest.raises(BookingError, match="already departed"):
        late.book(an_offer(market).offer_id, "Ana Lopez", 1)


def test_reference_collision_is_retried(market, settings):
    offer = an_offer(market)
    first = service(market, settings, seed=7).book(offer.offer_id, "Ana Lopez", 1)
    second = service(market, settings, seed=7).book(offer.offer_id, "Ana Lopez", 1)  # same RNG sequence
    assert first.reference != second.reference


def test_cancel_refundable_fare_gives_full_refund_and_releases_seats(market, settings):
    svc = service(market, settings)
    offer = an_offer(market, refundable=True)
    booking = svc.book(offer.offer_id, "Ana Lopez", 2)
    cancelled = svc.cancel(booking.reference.lower())   # references are case-insensitive on input
    assert cancelled.status == "CANCELLED" and cancelled.refund_eur == booking.total_price_eur
    assert svc.seats_left(offer) == offer.seats_available
    with pytest.raises(BookingError, match="already cancelled"):
        svc.cancel(booking.reference)


def test_cancel_changeable_fare_gives_travel_credit_minus_fee(market, settings):
    svc = service(market, settings)
    offer = next(o for o in market.offers if o.fare.changeable and not o.fare.refundable
                 and o.fare.change_fee_eur > 0 and o.seats_available >= 1)
    booking = svc.cancel(svc.book(offer.offer_id, "Ana Lopez", 1).reference)
    assert booking.refund_eur == 0
    assert booking.travel_credit_eur == pytest.approx(offer.price_eur - offer.fare.change_fee_eur)


def test_cancel_rigid_fare_gives_nothing_back(market, settings):
    svc = service(market, settings)
    offer = an_offer(market, seats_at_least=1, refundable=False, changeable=False)
    booking = svc.cancel(svc.book(offer.offer_id, "Ana Lopez", 1).reference)
    assert booking.refund_eur == 0 and booking.travel_credit_eur == 0


@pytest.mark.parametrize("ref", ["BB-123", "XX-20270712-ABCDEF", "BB-20270712-ABCDE0", "BB-20270712-ABCDEFG"])
def test_malformed_reference_is_refused(market, settings, ref):
    with pytest.raises(InvalidBookingReferenceError):
        service(market, settings).get(ref)


def test_unknown_but_well_formed_reference(market, settings):
    with pytest.raises(BookingError, match="No booking"):
        service(market, settings).get("BB-20270712-ABCDEF")


def test_corrupt_booking_file_is_reported(market, settings):
    settings.bookings_file.parent.mkdir(parents=True, exist_ok=True)
    settings.bookings_file.write_text("{not json", encoding="utf-8")
    with pytest.raises(DataFileError):
        service(market, settings)
