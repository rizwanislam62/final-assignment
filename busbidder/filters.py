"""Hard-constraint filtering.

Every hard constraint is a small function registered with the
``@hard_constraint`` decorator.  The decorator stores the function in a
registry together with a user-facing label, so:

* the filter engine discovers constraints dynamically instead of a long chain
  of ``if`` statements;
* adding a constraint is one decorated function, nothing else changes;
* every removed offer can be explained by the label(s) of the constraint(s)
  it violated.

A constraint function returns ``None`` if the offer is acceptable, or a short
reason string if it violates the constraint.  All constraints are evaluated
(not just the first failing one) so the user sees every reason.

"Scope" constraints (destination and travel window) define *which journey*
the traveller is looking at.  Offers outside the scope are counted but not
listed as rejections, because they were never candidates.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from .logging_setup import get_logger
from .models import Offer, TravelRequest

log = get_logger("filters")

ConstraintFn = Callable[[Offer, TravelRequest, int], "str | None"]


@dataclass(frozen=True)
class ConstraintSpec:
    key: str
    label: str
    scope: bool
    func: ConstraintFn


CONSTRAINTS: dict[str, ConstraintSpec] = {}


def hard_constraint(key: str, label: str, scope: bool = False):
    """Decorator that registers a hard-constraint check under ``key``."""

    def decorator(func: ConstraintFn) -> ConstraintFn:
        if key in CONSTRAINTS:
            raise ValueError(f"Hard constraint '{key}' registered twice")
        CONSTRAINTS[key] = ConstraintSpec(key, label, scope, func)
        return func

    return decorator


# ------------------------------------------------------------- the constraints
# ``seats_left`` is passed in because availability is derived from bookings
# (see booking.py), not read from the immutable source data.

@hard_constraint("destination", "Destination", scope=True)
def _destination(offer: Offer, req: TravelRequest, seats_left: int) -> str | None:
    if offer.destination.destination_id != req.destination.destination_id:
        return f"goes to {offer.destination.name}"
    return None


@hard_constraint("travel_window", "Travel window", scope=True)
def _travel_window(offer: Offer, req: TravelRequest, seats_left: int) -> str | None:
    if not req.date_from <= offer.travel_date <= req.date_to:
        return f"departs {offer.travel_date:%d %b}"
    return None


@hard_constraint("seats", "Seat availability")
def _seats(offer: Offer, req: TravelRequest, seats_left: int) -> str | None:
    if seats_left < req.party_size:
        return "sold out" if seats_left == 0 else f"only {seats_left} seat(s) left, {req.party_size} needed"
    return None


@hard_constraint("budget", "Maximum budget")
def _budget(offer: Offer, req: TravelRequest, seats_left: int) -> str | None:
    # Inclusive: an offer priced exactly at the budget is acceptable.
    if req.max_budget_eur is not None and offer.price_eur > req.max_budget_eur + 1e-9:
        return f"EUR {offer.price_eur:.2f} > budget EUR {req.max_budget_eur:.2f}"
    return None


@hard_constraint("max_transfers", "Maximum transfers")
def _transfers(offer: Offer, req: TravelRequest, seats_left: int) -> str | None:
    if req.max_transfers is not None and offer.transfers > req.max_transfers:
        return f"{offer.transfers} transfers > {req.max_transfers}"
    return None


@hard_constraint("changeable", "Changeable ticket")
def _changeable(offer: Offer, req: TravelRequest, seats_left: int) -> str | None:
    if req.changeable_required and not offer.fare.changeable:
        return "ticket cannot be changed"
    return None


@hard_constraint("refundable", "Refundable ticket")
def _refundable(offer: Offer, req: TravelRequest, seats_left: int) -> str | None:
    if req.refundable_required and not offer.fare.refundable:
        return "ticket is not refundable"
    return None


@hard_constraint("luggage", "Luggage allowance")
def _luggage(offer: Offer, req: TravelRequest, seats_left: int) -> str | None:
    if req.min_luggage_kg is not None and offer.fare.luggage_kg < req.min_luggage_kg:
        return f"{offer.fare.luggage_kg} kg luggage < {req.min_luggage_kg} kg"
    return None


@hard_constraint("max_duration", "Maximum journey time")
def _duration(offer: Offer, req: TravelRequest, seats_left: int) -> str | None:
    if req.max_duration_min is not None and offer.duration_min > req.max_duration_min:
        return f"{offer.duration_min} min > {req.max_duration_min} min"
    return None


# ------------------------------------------------------------------ the engine
@dataclass
class RemovedOffer:
    offer: Offer
    reasons: list[tuple[str, str]]  # (constraint label, reason)


@dataclass
class FilterResult:
    accepted: list[Offer] = field(default_factory=list)
    removed: list[RemovedOffer] = field(default_factory=list)
    out_of_scope: int = 0

    @property
    def in_scope(self) -> int:
        return len(self.accepted) + len(self.removed)

    def counts_by_constraint(self) -> dict[str, int]:
        """How many in-scope offers violate each constraint (an offer may count twice)."""
        counts: dict[str, int] = {}
        for removed in self.removed:
            for label, _ in removed.reasons:
                counts[label] = counts.get(label, 0) + 1
        return counts


def apply_hard_constraints(
    offers: list[Offer],
    request: TravelRequest,
    seats_left: Callable[[Offer], int],
) -> FilterResult:
    """Split ``offers`` into acceptable offers and removed offers with reasons."""
    result = FilterResult()
    scope_specs = [c for c in CONSTRAINTS.values() if c.scope]
    other_specs = [c for c in CONSTRAINTS.values() if not c.scope]
    for offer in offers:
        left = seats_left(offer)
        if any(spec.func(offer, request, left) for spec in scope_specs):
            result.out_of_scope += 1
            continue
        reasons = []
        for spec in other_specs:
            reason = spec.func(offer, request, left)
            if reason:
                reasons.append((spec.label, reason))
        if reasons:
            result.removed.append(RemovedOffer(offer, reasons))
        else:
            result.accepted.append(offer)
    log.info(
        "Filter for %s (%s): %d in scope, %d acceptable, %d removed by hard constraints %s",
        request.traveller_label, request.destination.name, result.in_scope,
        len(result.accepted), len(result.removed), result.counts_by_constraint(),
    )
    return result
