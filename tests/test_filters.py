"""Task 3 - hard constraints, including the boundaries of every limit."""

from __future__ import annotations

from datetime import date

import pytest

from conftest import VALENCIA, make_offer, make_request
from busbidder.exceptions import InvalidRequestError
from busbidder.filters import CONSTRAINTS, apply_hard_constraints


def run(offers, request):
    return apply_hard_constraints(offers, request, lambda o: o.seats_available)


def test_registry_contains_every_constraint():
    assert {"destination", "travel_window", "seats", "budget", "max_transfers", "changeable",
            "refundable", "luggage", "max_duration"} <= set(CONSTRAINTS)


def test_offers_outside_journey_scope_are_not_counted_as_rejections():
    offers = [make_offer(), make_offer("SOL-VAL-0001", destination=VALENCIA),
              make_offer("SOL-MAL-0002", dep="2027-07-20T08:00")]
    result = run(offers, make_request())
    assert len(result.accepted) == 1 and result.out_of_scope == 2 and not result.removed


@pytest.mark.parametrize("dep, inside", [
    ("2027-07-11T23:59", False), ("2027-07-12T00:00", True),
    ("2027-07-14T23:59", True), ("2027-07-15T00:00", False),
])
def test_travel_window_edges_are_inclusive(dep, inside):
    result = run([make_offer(dep=dep)], make_request())
    assert (len(result.accepted) == 1) is inside


@pytest.mark.parametrize("price, accepted", [(49.99, True), (50.0, True), (50.01, False)])
def test_budget_boundary(price, accepted):
    result = run([make_offer(price=price)], make_request(max_budget_eur=50))
    assert (len(result.accepted) == 1) is accepted


@pytest.mark.parametrize("transfers, accepted", [(0, True), (1, True), (2, False)])
def test_max_transfers_boundary(transfers, accepted):
    result = run([make_offer(transfers=transfers)], make_request(max_transfers=1))
    assert (len(result.accepted) == 1) is accepted


def test_max_transfers_zero_means_direct_only():
    result = run([make_offer(transfers=0), make_offer("SOL-MAL-0002", transfers=1)], make_request(max_transfers=0))
    assert [o.offer_id for o in result.accepted] == ["SOL-MAL-0001"]


@pytest.mark.parametrize("seats, party, accepted", [(0, 1, False), (1, 1, True), (2, 3, False), (3, 3, True)])
def test_seat_availability_boundary(seats, party, accepted):
    result = run([make_offer(seats=seats)], make_request(party_size=party))
    assert (len(result.accepted) == 1) is accepted


def test_changeable_and_refundable_requirements():
    offers = [make_offer("SOL-MAL-0001"), make_offer("SOL-MAL-0002", changeable=True, fee=5),
              make_offer("SOL-MAL-0003", changeable=True, refundable=True)]
    assert len(run(offers, make_request(changeable_required=True)).accepted) == 2
    assert len(run(offers, make_request(refundable_required=True)).accepted) == 1


@pytest.mark.parametrize("luggage, accepted", [(19, False), (20, True)])
def test_luggage_boundary(luggage, accepted):
    result = run([make_offer(luggage=luggage)], make_request(min_luggage_kg=20))
    assert (len(result.accepted) == 1) is accepted


@pytest.mark.parametrize("minutes, accepted", [(480, True), (481, False)])
def test_max_duration_boundary(minutes, accepted):
    result = run([make_offer(minutes=minutes)], make_request(max_duration_min=480))
    assert (len(result.accepted) == 1) is accepted


def test_all_violations_are_reported_not_just_the_first():
    offer = make_offer(price=80, transfers=2, seats=0)
    result = run([offer], make_request(max_budget_eur=50, max_transfers=1, changeable_required=True))
    labels = {label for label, _ in result.removed[0].reasons}
    assert labels == {"Seat availability", "Maximum budget", "Maximum transfers", "Changeable ticket"}
    assert result.counts_by_constraint()["Maximum budget"] == 1


def test_seats_left_function_is_used_instead_of_source_capacity():
    offer = make_offer(seats=5)
    result = apply_hard_constraints([offer], make_request(), lambda o: 0)  # all seats booked
    assert not result.accepted and result.removed[0].reasons[0][1] == "sold out"


@pytest.mark.parametrize("kwargs", [
    {"date_from": date(2027, 7, 14), "date_to": date(2027, 7, 12)},
    {"party_size": 0},
    {"max_budget_eur": 0},
    {"max_transfers": -1},
    {"min_luggage_kg": -5},
    {"max_duration_min": 0},
])
def test_invalid_requests_are_refused(kwargs):
    with pytest.raises(InvalidRequestError):
        make_request(**kwargs)
