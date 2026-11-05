"""Section 5 - normalisation, direction, missing values, outliers, ties and weights."""

from __future__ import annotations

from datetime import time

import pytest

from conftest import make_offer, make_request
from busbidder.exceptions import InvalidRequestError, InvalidWeightsError
from busbidder.models import DeparturePreference
from busbidder.scoring import (
    Criterion, DepartureCriterion, DurationCriterion, EmissionsCriterion, FlexibilityCriterion,
    PriceCriterion, RatingCriterion, TransfersCriterion, core_criteria, parse_weights, validate_weights,
)


# ------------------------------------------------------------------ registry
def test_registry_discovers_all_criteria_and_core_four():
    assert list(Criterion.registry) == ["price", "duration", "departure", "flexibility",
                                        "transfers", "rating", "emissions"]
    assert core_criteria() == ["price", "duration", "departure", "flexibility"]


def test_new_criterion_registers_itself_and_duplicates_are_refused():
    class LuggageCriterion(TransfersCriterion.__mro__[1]):  # AbsoluteCriterion
        key = "luggage_test"
        label = "Luggage"
        def raw_value(self, offer, request): return offer.fare.luggage_kg
        def score_value(self, value): return value
    try:
        assert Criterion.registry["luggage_test"] is LuggageCriterion
        with pytest.raises(TypeError):
            class Duplicate(LuggageCriterion):  # noqa: F841
                key = "luggage_test"
    finally:
        Criterion.registry.pop("luggage_test", None)


# ------------------------------------------------- relative (min-max) scale
def test_price_lower_is_better_and_scaled_0_to_100(settings):
    scores = PriceCriterion(settings).normalise([20.0, 30.0, 40.0])
    assert scores == [100.0, 50.0, 0.0]


def test_duration_uses_same_direction(settings):
    scores = DurationCriterion(settings).normalise([300.0, 360.0, 420.0])
    assert scores == [100.0, 50.0, 0.0]


def test_identical_values_all_get_full_score(settings):
    assert PriceCriterion(settings).normalise([30.0, 30.0, 30.0]) == [100.0, 100.0, 100.0]


def test_single_offer_gets_full_score(settings):
    assert PriceCriterion(settings).normalise([42.0]) == [100.0]


def test_minimum_meaningful_range_prevents_tiny_differences_from_dominating(settings):
    # EUR 30.00 vs 30.50 must not become a 100-point gap (min range is EUR 10).
    scores = PriceCriterion(settings).normalise([30.0, 30.5])
    assert scores[0] == 100.0 and scores[1] == pytest.approx(95.0)


def test_outlier_does_not_compress_the_other_scores(settings):
    prices = [20.0, 22.0, 24.0, 26.0, 28.0, 300.0]
    scores = PriceCriterion(settings).normalise(prices)
    assert scores[-1] == 0.0                      # extreme value gets the minimum score
    assert scores[0] == 100.0
    # Without clipping, EUR 28 would score ~97; with clipping the normal range keeps its spread.
    assert scores[4] < 60.0


def test_outlier_with_zero_iqr_is_still_worst(settings):
    scores = PriceCriterion(settings).normalise([30.0, 30.0, 30.0, 30.0, 45.0])
    assert scores[:4] == [100.0] * 4 and scores[4] == 0.0


def test_missing_values_receive_neutral_score(settings):
    scores = EmissionsCriterion(settings).normalise([10.0, None, 20.0])
    assert scores[1] == settings.neutral_score_for_missing
    assert EmissionsCriterion(settings).normalise([None, None]) == [50.0, 50.0]


def test_scores_always_within_0_and_100(settings):
    for values in ([1.0, 1000.0], [5.0, 5.0, 6.0, 7.0, -50.0], [0.0, 0.0001]):
        assert all(0.0 <= s <= 100.0 for s in PriceCriterion(settings).normalise(values))


# ------------------------------------------------------------ absolute scales
@pytest.mark.parametrize("dep, expected", [
    ("2027-07-12T06:00", 100.0),   # window start (inclusive)
    ("2027-07-12T11:59", 100.0),
    ("2027-07-12T12:00", 100.0),   # 0 min outside the half-open window end
    ("2027-07-12T14:00", 50.0),    # 120 min outside, decay 240 -> 50
    ("2027-07-12T16:00", 0.0),     # 240 min outside -> 0
    ("2027-07-12T22:00", 0.0),     # far away -> clamped at 0
    ("2027-07-12T04:00", 50.0),    # 120 min before the window
])
def test_departure_convenience(settings, dep, expected):
    crit = DepartureCriterion(settings)
    request = make_request()
    raw = crit.raw_value(make_offer(dep=dep), request)
    assert crit.normalise([raw]) == [pytest.approx(expected)]


def test_night_window_wraps_around_midnight():
    night = DeparturePreference.parse("night")
    assert night.minutes_outside(time(23, 30)) == 0
    assert night.minutes_outside(time(1, 0)) == 0
    assert night.minutes_outside(time(21, 0)) == 60
    assert night.minutes_outside(time(7, 0)) == 60


def test_specific_time_is_a_30_minute_window():
    pref = DeparturePreference.parse("07:30")
    assert pref.minutes_outside(time(7, 0)) == 0
    assert pref.minutes_outside(time(8, 30)) == 30
    assert DeparturePreference.parse("any").minutes_outside(time(3, 0)) == 0


@pytest.mark.parametrize("text", ["25:00", "7:30", "dawn", "12:60"])
def test_invalid_departure_preference(text):
    with pytest.raises(InvalidRequestError):
        DeparturePreference.parse(text)


@pytest.mark.parametrize("changeable, fee, refundable, expected", [
    (False, 0, False, 0), (True, 5, False, 40), (True, 0, False, 60),
    (False, 0, True, 40), (True, 0, True, 100), (True, 10, True, 80),
])
def test_flexibility_categories(settings, changeable, fee, refundable, expected):
    crit = FlexibilityCriterion(settings)
    raw = crit.raw_value(make_offer(changeable=changeable, fee=fee, refundable=refundable), make_request())
    assert crit.normalise([raw]) == [expected]


@pytest.mark.parametrize("transfers, expected", [(0, 100), (1, 50), (2, 0), (3, 0)])
def test_transfers_scale(settings, transfers, expected):
    crit = TransfersCriterion(settings)
    assert crit.normalise([float(transfers)]) == [expected]


@pytest.mark.parametrize("rating, expected", [(1.0, 0.0), (3.0, 50.0), (5.0, 100.0), (None, 50.0)])
def test_rating_scale_and_missing(settings, rating, expected):
    assert RatingCriterion(settings).normalise([rating]) == [pytest.approx(expected)]


# -------------------------------------------------------------------- weights
def test_parse_weights_accepts_percent_signs_and_spaces():
    assert parse_weights("price=40%, duration = 25 ,departure:15,flexibility=20") == {
        "price": 40.0, "duration": 25.0, "departure": 15.0, "flexibility": 20.0}


@pytest.mark.parametrize("text", ["price40", "price=forty", "", "price=10,price=90"])
def test_parse_weights_rejects_bad_syntax(text):
    with pytest.raises(InvalidWeightsError):
        parse_weights(text)


def test_validate_weights_percent_and_fraction_give_same_result():
    pct = validate_weights({"price": 40, "duration": 25, "departure": 15, "flexibility": 20})
    frac = validate_weights({"price": 0.4, "duration": 0.25, "departure": 0.15, "flexibility": 0.2})
    assert pct == pytest.approx(frac)
    assert sum(pct.values()) == pytest.approx(1.0)


def test_validate_weights_tolerates_rounding():
    w = validate_weights({"price": 33.333, "duration": 33.333, "departure": 16.667, "flexibility": 16.667})
    assert sum(w.values()) == pytest.approx(1.0)


@pytest.mark.parametrize("weights, message", [
    ({"price": 50, "duration": 25, "departure": 15, "flexibility": 20}, "total 110"),
    ({"price": 30, "duration": 25, "departure": 15, "flexibility": 20}, "total 90"),
    ({"price": 40, "duration": 25, "departure": 15, "comfort": 20}, "Unknown"),
    ({"price": 70, "duration": 25, "departure": 15, "flexibility": -10}, "negative"),
    ({"price": 60, "duration": 25, "departure": 15, "flexibility": 0}, "flexibility"),
    ({"price": 75, "duration": 25}, "departure"),
])
def test_validate_weights_rejects_invalid(weights, message):
    with pytest.raises(InvalidWeightsError, match=message):
        validate_weights(weights)


def test_optional_criterion_with_zero_weight_is_dropped():
    w = validate_weights({"price": 40, "duration": 25, "departure": 15, "flexibility": 20, "rating": 0})
    assert "rating" not in w
