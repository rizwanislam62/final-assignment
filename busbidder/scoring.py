"""Criteria, normalisation and weights for the weighted ranking model.

    Score(j) = sum over criteria c of  w_c * S_c(j)      with  sum w_c = 1

Each ``S_c`` is on a common 0-100 scale where 100 is always "best for the
traveller", so lower-is-better and higher-is-better criteria are combined in
the same direction.

Two normalisation strategies are used, chosen per criterion:

* ``RelativeCriterion`` (price, duration, emissions) - there is no natural
  "perfect" price, so values are compared with the other acceptable offers.
  Robust min-max scaling is used:
    - extreme values outside Tukey fences (Q1 - k*IQR, Q3 + k*IQR) do not
      stretch the scale; they are simply given 0 or 100;
    - the scale span is at least a *minimum meaningful range* (e.g. EUR 10,
      60 minutes) so that trivial differences such as EUR 30.00 vs 30.50 are
      not blown up to a full 100-point gap that would dominate the total.
* ``AbsoluteCriterion`` (departure convenience, flexibility, transfers,
  rating) - the criterion has a natural fixed scale, so an offer's score does
  not depend on which other offers happen to be present.

Missing values (rating or CO2 not published) receive a neutral score
(50 by default) and are flagged so the explanation can mention them.

Metaprogramming: every concrete subclass of ``Criterion`` registers itself in
``Criterion.registry`` through ``__init_subclass__``.  The weight parser, the
ranking engine and the CLI discover the available criteria from that registry,
so adding a criterion is a single new class.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from statistics import quantiles
from typing import ClassVar

from .config import Settings
from .exceptions import InvalidWeightsError
from .models import Offer, TravelRequest


class Criterion(ABC):
    key: ClassVar[str] = ""
    label: ClassVar[str] = ""
    core: ClassVar[bool] = False          # the four criteria the specification requires
    description: ClassVar[str] = ""
    registry: ClassVar[dict[str, type["Criterion"]]] = {}

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if cls.key:  # abstract intermediate classes have no key
            if cls.key in Criterion.registry:
                raise TypeError(f"Criterion key '{cls.key}' is already registered")
            Criterion.registry[cls.key] = cls

    def __init__(self, settings: Settings):
        self.settings = settings

    @abstractmethod
    def raw_value(self, offer: Offer, request: TravelRequest) -> float | None:
        """The measured value in natural units (EUR, minutes, ...), or None if unknown."""

    @abstractmethod
    def normalise(self, raws: list[float | None]) -> list[float]:
        """Map raw values to 0-100 scores (100 = best)."""

    def format_raw(self, value: float | None) -> str:
        return "n/a" if value is None else f"{value:g}"


# ------------------------------------------------------------- relative scale
class RelativeCriterion(Criterion):
    lower_is_better: ClassVar[bool] = True
    unit: ClassVar[str] = ""

    def normalise(self, raws: list[float | None]) -> list[float]:
        neutral = self.settings.neutral_score_for_missing
        values = [v for v in raws if v is not None]
        if not values:
            return [neutral for _ in raws]
        lo, hi = min(values), max(values)
        if len(values) >= 4:  # quartiles are meaningless for very small sets
            q1, _, q3 = quantiles(values, n=4, method="inclusive")
            k = self.settings.outlier_iqr_factor
            lo = max(lo, q1 - k * (q3 - q1))
            hi = min(hi, q3 + k * (q3 - q1))
        span = max(hi - lo, self.settings.min_meaningful_range.get(self.key, 0.0))
        scores = []
        for v in raws:
            if v is None:
                scores.append(neutral)
                continue
            if span <= 0:  # every non-outlier value is identical
                good = v <= lo if self.lower_is_better else v >= hi
                scores.append(100.0 if good else 0.0)
                continue
            if self.lower_is_better:
                s = 100.0 * (1 - (v - lo) / span)
            else:
                s = 100.0 * (1 - (hi - v) / span)
            scores.append(min(100.0, max(0.0, s)))
        return scores


class AbsoluteCriterion(Criterion):
    @abstractmethod
    def score_value(self, value: float) -> float:
        """Score one raw value on the criterion's fixed scale."""

    def normalise(self, raws: list[float | None]) -> list[float]:
        neutral = self.settings.neutral_score_for_missing
        return [neutral if v is None else min(100.0, max(0.0, self.score_value(v))) for v in raws]


# --------------------------------------------------------------- the criteria
class PriceCriterion(RelativeCriterion):
    key = "price"
    label = "Price"
    core = True
    unit = "EUR"
    description = "Ticket price; cheapest acceptable offer = 100 (robust min-max, lower is better)."

    def raw_value(self, offer, request):
        return offer.price_eur

    def format_raw(self, value):
        return f"EUR {value:.2f}"


class DurationCriterion(RelativeCriterion):
    key = "duration"
    label = "Duration"
    core = True
    unit = "min"
    description = "Journey time; fastest acceptable offer = 100 (robust min-max, lower is better)."

    def raw_value(self, offer, request):
        return float(offer.duration_min)

    def format_raw(self, value):
        h, m = divmod(int(value), 60)
        return f"{h}h{m:02d}"


class DepartureCriterion(AbsoluteCriterion):
    key = "departure"
    label = "Departure fit"
    core = True
    description = ("Minutes outside the preferred departure window; 100 inside the window, "
                   "falling linearly to 0 at the decay limit (default 4 hours).")

    def raw_value(self, offer, request):
        return float(request.preferred_departure.minutes_outside(offer.departure.time()))

    def score_value(self, value):
        return 100.0 * (1 - value / self.settings.departure_decay_min)

    def format_raw(self, value):
        return "in window" if value == 0 else f"{int(value)} min off"


class FlexibilityCriterion(AbsoluteCriterion):
    key = "flexibility"
    label = "Flexibility"
    core = True
    description = ("Fare flexibility points: free changes 60, changes with a fee 40, "
                   "no changes 0; refundable adds 40 (max 100).")

    def raw_value(self, offer, request):
        fare = offer.fare
        points = 0.0
        if fare.changeable:
            points += 60.0 if fare.change_fee_eur == 0 else 40.0
        if fare.refundable:
            points += 40.0
        return points

    def score_value(self, value):
        return value

    def format_raw(self, value):
        return f"{int(value)} pts"


class TransfersCriterion(AbsoluteCriterion):
    key = "transfers"
    label = "Transfers"
    description = "Direct = 100, one transfer = 50, two or more = 0."

    def raw_value(self, offer, request):
        return float(offer.transfers)

    def score_value(self, value):
        return 100.0 - 50.0 * value

    def format_raw(self, value):
        return "direct" if value == 0 else f"{int(value)} transfer(s)"


class RatingCriterion(AbsoluteCriterion):
    key = "rating"
    label = "Rating"
    description = "Customer rating mapped linearly from the rating scale (1-5) to 0-100."

    def raw_value(self, offer, request):
        return offer.rating

    def score_value(self, value):
        lo, hi = self.settings.rating_min, self.settings.rating_max
        return 100.0 * (value - lo) / (hi - lo)

    def format_raw(self, value):
        return "n/a" if value is None else f"{value:.1f}/5"


class EmissionsCriterion(RelativeCriterion):
    key = "emissions"
    label = "CO2"
    unit = "kg"
    description = "Estimated CO2 per passenger; lowest acceptable offer = 100 (robust min-max)."

    def raw_value(self, offer, request):
        return offer.co2_kg

    def format_raw(self, value):
        return "n/a" if value is None else f"{value:.1f} kg"


def core_criteria() -> list[str]:
    return [k for k, cls in Criterion.registry.items() if cls.core]


# -------------------------------------------------------------------- weights
_WEIGHT_ITEM_RE = re.compile(r"^\s*(?P<name>[a-z_]+)\s*[=:]\s*(?P<value>-?\d+(?:\.\d+)?)\s*%?\s*$", re.I)


def parse_weights(text: str) -> dict[str, float]:
    """Parse ``"price=40, duration=25, departure=15, flexibility=20"``."""
    weights: dict[str, float] = {}
    for item in filter(None, (part.strip() for part in text.split(","))):
        match = _WEIGHT_ITEM_RE.match(item)
        if not match:
            raise InvalidWeightsError(
                f"Cannot read weight '{item}'. Use name=value pairs, e.g. price=40,duration=25."
            )
        name = match["name"].lower()
        if name in weights:
            raise InvalidWeightsError(f"Weight for '{name}' is given twice.")
        weights[name] = float(match["value"])
    if not weights:
        raise InvalidWeightsError("No weights were given.")
    return weights


def validate_weights(weights: dict[str, float]) -> dict[str, float]:
    """Check the weights and return them as fractions that sum to exactly 1.0.

    Rules: only known criteria; no negative weights; each of the four core
    criteria (price, duration, departure, flexibility) must have a positive
    weight; the weights must total 100 (percent) or 1.0 (fractions).  Weights
    that total anything else are rejected rather than silently rescaled,
    because a silent rescale would hide a typing mistake from the traveller.
    """
    known = Criterion.registry
    unknown = sorted(set(weights) - set(known))
    if unknown:
        raise InvalidWeightsError(
            f"Unknown criterion/criteria: {', '.join(unknown)}. Valid: {', '.join(known)}."
        )
    negative = sorted(k for k, v in weights.items() if v < 0)
    if negative:
        raise InvalidWeightsError(f"Weights cannot be negative: {', '.join(negative)}.")
    missing_core = [k for k in core_criteria() if weights.get(k, 0) <= 0]
    if missing_core:
        raise InvalidWeightsError(
            "The ranking must use the four core criteria; give a positive weight to: "
            + ", ".join(missing_core) + "."
        )
    total = sum(weights.values())
    if abs(total - 100.0) <= 0.01:
        scale = 100.0
    elif abs(total - 1.0) <= 0.0001:
        scale = 1.0
    else:
        raise InvalidWeightsError(
            f"Weights total {total:g}; they must total 100 (percent) or 1.0 (fractions)."
        )
    fractions = {k: v / scale for k, v in weights.items() if v > 0}
    # Remove floating-point residue so the weights sum to exactly 1.
    norm = sum(fractions.values())
    return {k: v / norm for k, v in fractions.items()}
