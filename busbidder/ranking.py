"""Weighted ranking of the offers that survived the hard constraints.

The ranking is fully deterministic (no randomness, stable tie-breaking), so
the same data, request and weights always give the same order.

Tie-breaking: offers are ordered by total score; when two totals are equal
(to 6 decimals) the cheaper offer wins, then the shorter journey, then the
earlier departure and finally the offer id.  These rules are shown to the user
whenever displayed totals are equal.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .config import Settings
from .filters import FilterResult
from .logging_setup import get_logger
from .models import Offer, TravelRequest
from .scoring import Criterion

log = get_logger("ranking")

TIE_BREAK_RULE = "equal totals are ordered by lower price, then shorter duration, then earlier departure"


@dataclass
class ScoredOffer:
    offer: Offer
    raw: dict[str, float | None]
    scores: dict[str, float]
    contributions: dict[str, float]   # weight * score, in points of the total
    total: float
    missing: list[str] = field(default_factory=list)
    rank: int = 0
    tied: bool = False


@dataclass
class SensitivityFlip:
    criterion: str
    shift_pp: float
    new_winner: Offer


@dataclass
class Ranking:
    request: TravelRequest
    weights: dict[str, float]
    criteria: list[Criterion]
    items: list[ScoredOffer]
    filter_result: FilterResult | None = None
    flips: list[SensitivityFlip] = field(default_factory=list)
    shift_pp: float = 10.0

    @property
    def top(self) -> ScoredOffer | None:
        return self.items[0] if self.items else None

    @property
    def is_robust(self) -> bool:
        return not self.flips


def _sort_key(item: ScoredOffer, total: float | None = None):
    o = item.offer
    t = item.total if total is None else total
    return (-round(t, 6), o.price_eur, o.duration_min, o.departure, o.offer_id)


def score_offers(
    offers: list[Offer], request: TravelRequest, weights: dict[str, float], settings: Settings
) -> tuple[list[Criterion], list[ScoredOffer]]:
    """Compute raw values, 0-100 scores and weighted totals for every offer."""
    criteria = [cls(settings) for key, cls in Criterion.registry.items() if key in weights]
    raw_by_key = {c.key: [c.raw_value(o, request) for o in offers] for c in criteria}
    score_by_key = {c.key: c.normalise(raw_by_key[c.key]) for c in criteria}
    scored: list[ScoredOffer] = []
    for i, offer in enumerate(offers):
        raw = {c.key: raw_by_key[c.key][i] for c in criteria}
        scores = {c.key: score_by_key[c.key][i] for c in criteria}
        contributions = {k: weights[k] * scores[k] for k in scores}
        scored.append(ScoredOffer(
            offer=offer,
            raw=raw,
            scores=scores,
            contributions=contributions,
            total=sum(contributions.values()),
            missing=[k for k, v in raw.items() if v is None],
        ))
    return criteria, scored


def _order(items: list[ScoredOffer]) -> list[ScoredOffer]:
    items = sorted(items, key=_sort_key)
    for position, item in enumerate(items, start=1):
        item.rank = position
    for a, b in zip(items, items[1:]):
        if round(a.total, 2) == round(b.total, 2):
            a.tied = b.tied = True
    return items


def _shifted_weights(weights: dict[str, float], key: str, delta: float) -> dict[str, float] | None:
    """Move ``key`` by ``delta`` and rescale the others proportionally (total stays 1)."""
    old = weights[key]
    new = min(1.0, max(0.0, old + delta))
    rest = 1.0 - old
    if new == old or rest <= 0:
        return None
    factor = (1.0 - new) / rest
    return {k: (new if k == key else w * factor) for k, w in weights.items()}


def sensitivity(items: list[ScoredOffer], weights: dict[str, float], shift_pp: float) -> list[SensitivityFlip]:
    """Report every +/- ``shift_pp`` single-weight change that would change the winner.

    Normalised scores do not depend on the weights, so only totals are recomputed.
    """
    if len(items) < 2:
        return []
    winner = items[0].offer.offer_id
    flips: list[SensitivityFlip] = []
    for key in weights:
        for delta in (+shift_pp / 100.0, -shift_pp / 100.0):
            shifted = _shifted_weights(weights, key, delta)
            if shifted is None:
                continue
            best = min(items, key=lambda it: _sort_key(
                it, sum(shifted[k] * it.scores[k] for k in shifted)))
            if best.offer.offer_id != winner:
                flips.append(SensitivityFlip(key, delta * 100.0, best.offer))
    return flips


def rank(
    filter_result: FilterResult, request: TravelRequest, weights: dict[str, float], settings: Settings
) -> Ranking:
    criteria, scored = score_offers(filter_result.accepted, request, weights, settings)
    items = _order(scored)
    ranking = Ranking(
        request=request, weights=weights, criteria=criteria, items=items,
        filter_result=filter_result, shift_pp=settings.sensitivity_shift_pp,
    )
    ranking.flips = sensitivity(items, weights, settings.sensitivity_shift_pp)
    if ranking.top:
        log.info(
            "Ranked %d offers for %s; top %s (%.2f); weights %s; robust=%s",
            len(items), request.traveller_label, ranking.top.offer.offer_id, ranking.top.total,
            {k: round(v, 3) for k, v in weights.items()}, ranking.is_robust,
        )
    else:
        log.info("No acceptable offers to rank for %s", request.traveller_label)
    return ranking
