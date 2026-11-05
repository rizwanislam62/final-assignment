"""Ranking order, reproducibility, tie-breaking, sensitivity and explanations."""

from __future__ import annotations

import pytest

from conftest import make_offer, make_request
from busbidder.explain import explain_top
from busbidder.filters import FilterResult
from busbidder.ranking import _shifted_weights, rank


def ranking_for(offers, settings, **request_kwargs):
    request = make_request(**request_kwargs)
    return rank(FilterResult(accepted=list(offers)), request, request.weights, settings)


def test_total_is_weighted_sum_of_criterion_scores(settings):
    r = ranking_for([make_offer(price=20), make_offer("SOL-MAL-0002", price=40, minutes=300)], settings)
    for item in r.items:
        assert item.total == pytest.approx(sum(r.weights[k] * item.scores[k] for k in r.weights))
        assert 0 <= item.total <= 100


def test_cheaper_offer_wins_when_everything_else_is_equal(settings):
    r = ranking_for([make_offer("SOL-MAL-0001", price=40), make_offer("SOL-MAL-0002", price=25)], settings)
    assert [i.offer.offer_id for i in r.items] == ["SOL-MAL-0002", "SOL-MAL-0001"]
    assert [i.rank for i in r.items] == [1, 2]


def test_weights_change_the_winner(settings):
    cheap_slow = make_offer("SOL-MAL-0001", price=25, minutes=540)
    fast_dear = make_offer("SOL-MAL-0002", price=45, minutes=360)
    price_first = ranking_for([cheap_slow, fast_dear], settings,
                              weights={"price": 0.7, "duration": 0.1, "departure": 0.1, "flexibility": 0.1})
    speed_first = ranking_for([cheap_slow, fast_dear], settings,
                              weights={"price": 0.1, "duration": 0.7, "departure": 0.1, "flexibility": 0.1})
    assert price_first.top.offer.offer_id == "SOL-MAL-0001"
    assert speed_first.top.offer.offer_id == "SOL-MAL-0002"


def test_ties_are_broken_deterministically(settings):
    # Identical offers except id -> same total; order must be by id, every time.
    offers = [make_offer("SOL-MAL-0003"), make_offer("SOL-MAL-0001"), make_offer("SOL-MAL-0002")]
    first = ranking_for(offers, settings)
    second = ranking_for(list(reversed(offers)), settings)
    ids = [i.offer.offer_id for i in first.items]
    assert ids == ["SOL-MAL-0001", "SOL-MAL-0002", "SOL-MAL-0003"]
    assert ids == [i.offer.offer_id for i in second.items]
    assert all(i.tied for i in first.items)


def test_ranking_is_reproducible_on_real_data(settings):
    from busbidder.filters import apply_hard_constraints
    from busbidder.loader import Marketplace
    m = Marketplace.load(settings, write_issue_files=False)
    request = make_request(destination=m.find_destination("Malaga"), max_budget_eur=50)
    results = []
    for _ in range(3):
        fr = apply_hard_constraints(m.offers, request, lambda o: o.seats_available)
        r = rank(fr, request, request.weights, settings)
        results.append([(i.offer.offer_id, round(i.total, 9)) for i in r.items])
    assert results[0] == results[1] == results[2]


def test_empty_ranking_is_handled(settings):
    r = ranking_for([], settings)
    assert r.top is None and r.items == []
    assert "No offer" in explain_top(r)[0]


def test_shifted_weights_still_sum_to_one():
    w = {"price": 0.4, "duration": 0.25, "departure": 0.15, "flexibility": 0.2}
    for key in w:
        for delta in (0.1, -0.1):
            shifted = _shifted_weights(w, key, delta)
            assert sum(shifted.values()) == pytest.approx(1.0)
            assert shifted[key] == pytest.approx(w[key] + delta)


def test_sensitivity_detects_a_close_call(settings):
    # Near-identical totals: a small change in the price weight flips the winner.
    a = make_offer("SOL-MAL-0001", price=30, minutes=420)
    b = make_offer("SOL-MAL-0002", price=34, minutes=360)
    r = ranking_for([a, b], settings)
    assert not r.is_robust and r.flips


def test_sensitivity_robust_for_dominant_offer(settings):
    best = make_offer("SOL-MAL-0001", price=20, minutes=300, changeable=True, refundable=True)
    worse = make_offer("SOL-MAL-0002", price=45, minutes=500, dep="2027-07-12T20:00")
    r = ranking_for([best, worse], settings)
    assert r.is_robust


def test_explanation_mentions_advantages_disadvantages_and_disclaimer(settings):
    winner = make_offer("SOL-MAL-0001", price=38, minutes=360, changeable=True, refundable=True)
    cheaper = make_offer("SOL-MAL-0002", price=25, minutes=480, dep="2027-07-12T15:00")
    r = ranking_for([winner, cheaper], settings)
    assert r.top.offer.offer_id == "SOL-MAL-0001"
    text = " ".join(explain_top(r))
    assert "direct route" in text
    assert "inside your preferred morning" in text
    assert "refundable" in text
    assert "higher price" in text and "EUR 13.00 more" in text
    assert "not an objectively best bus" in text


def test_explanation_reports_missing_data(settings):
    a = make_offer("SOL-MAL-0001", rating=None)
    b = make_offer("SOL-MAL-0002", price=40)
    request = make_request(weights={"price": 0.4, "duration": 0.2, "departure": 0.1,
                                    "flexibility": 0.2, "rating": 0.1})
    r = rank(FilterResult(accepted=[a, b]), request, request.weights, settings)
    top = r.top
    assert top.offer.offer_id == "SOL-MAL-0001" and top.missing == ["rating"]
    assert any("Missing data: Rating" in line for line in explain_top(r))
