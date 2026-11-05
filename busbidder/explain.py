"""Data-driven explanation of the highest-ranked offer.

The explanation is generated only from the scored data:

* advantages   - weighted criteria where the offer scores >= 75 / 100;
* disadvantages - weighted criteria where it scores < 50 / 100;
* comparison with the runner-up - which weighted criteria won or lost points;
* missing data - criteria where a neutral score had to be used;
* robustness   - whether a +/-10 percentage-point change of any single weight
  would change the winner;
* a reminder that the result is "best" only under the chosen constraints,
  weights and scoring assumptions.
"""

from __future__ import annotations

from .ranking import Ranking, ScoredOffer

ADVANTAGE_AT = 75.0
DISADVANTAGE_BELOW = 50.0


def _hm(minutes: float) -> str:
    h, m = divmod(int(round(minutes)), 60)
    return f"{h}h{m:02d}"


def _phrase(key: str, item: ScoredOffer, ranking: Ranking, positive: bool) -> str:
    """One factual phrase about ``item`` on criterion ``key``.

    ``positive`` selects wording for an advantage or for a disadvantage.
    """
    offer = item.offer
    pool = [it.offer for it in ranking.items]
    if key == "price":
        cheapest = min(pool, key=lambda o: (o.price_eur, o.offer_id))
        gap = offer.price_eur - cheapest.price_eur
        if gap <= 0:
            return f"the lowest price among acceptable offers (EUR {offer.price_eur:.2f})"
        if positive:
            return f"a low price (EUR {offer.price_eur:.2f}, only EUR {gap:.2f} above the cheapest acceptable offer)"
        return (f"a higher price (EUR {offer.price_eur:.2f}, EUR {gap:.2f} more than the cheapest acceptable "
                f"offer from {cheapest.company.name})")
    if key == "duration":
        fastest = min(pool, key=lambda o: (o.duration_min, o.offer_id))
        gap = offer.duration_min - fastest.duration_min
        if gap <= 0:
            return f"the shortest journey time ({_hm(offer.duration_min)})"
        if positive:
            return f"a short journey ({_hm(offer.duration_min)}, only {gap} min longer than the fastest acceptable offer)"
        return f"a long journey ({_hm(offer.duration_min)}, {gap} min longer than the fastest acceptable offer)"
    if key == "departure":
        pref = ranking.request.preferred_departure
        off = int(item.raw["departure"] or 0)
        if off == 0:
            return f"a convenient departure at {offer.departure:%H:%M}, inside your preferred {pref.describe()} window"
        return f"a departure at {offer.departure:%H:%M}, {off} min outside your preferred {pref.describe()} window"
    if key == "flexibility":
        kind = "flexible" if positive else "restrictive"
        return f"a {kind} fare ({offer.fare.summary})"
    if key == "transfers":
        return "a direct route" if offer.transfers == 0 else f"{offer.transfers} transfer(s) on the way"
    if key == "rating":
        return f"a customer rating of {offer.rating:.1f}/5" if offer.rating is not None else "no published rating"
    if key == "emissions":
        if offer.co2_kg is None:
            return "no published CO2 estimate"
        known = [o for o in pool if o.co2_kg is not None]
        cleanest = min(known, key=lambda o: (o.co2_kg, o.offer_id))
        if offer.co2_kg <= cleanest.co2_kg:
            return f"the lowest estimated CO2 ({offer.co2_kg:.1f} kg per passenger)"
        return f"{offer.co2_kg:.1f} kg CO2 per passenger ({offer.co2_kg - cleanest.co2_kg:.1f} kg above the lowest)"
    return key


def _fact(key: str, item: ScoredOffer) -> str:
    """Short raw fact used in the 'moderate' sentence."""
    o = item.offer
    facts = {
        "price": f"EUR {o.price_eur:.2f}",
        "duration": _hm(o.duration_min),
        "departure": f"departs {o.departure:%H:%M}",
        "flexibility": o.fare.summary,
        "transfers": "direct" if o.transfers == 0 else f"{o.transfers} transfer(s)",
        "rating": "n/a" if o.rating is None else f"{o.rating:.1f}/5",
        "emissions": "n/a" if o.co2_kg is None else f"{o.co2_kg:.1f} kg CO2",
    }
    return facts.get(key, key)


def _join(parts: list[str]) -> str:
    if len(parts) <= 1:
        return "".join(parts)
    return ", ".join(parts[:-1]) + " and " + parts[-1]


def explain_top(ranking: Ranking) -> list[str]:
    """Return the explanation for the top offer as a list of sentences."""
    top = ranking.top
    if top is None:
        return ["No offer satisfies all hard constraints, so nothing can be recommended."]
    offer = top.offer
    labels = {c.key: c.label for c in ranking.criteria}
    # Order criteria by how much weight the traveller gave them.
    keys = sorted(ranking.weights, key=lambda k: -ranking.weights[k])

    lines = [
        f"{offer.company.name} {offer.offer_id} on {offer.departure:%a %d %b} ranks first with "
        f"{top.total:.1f}/100 under your preferences."
    ]
    # Criteria with a missing value, or on which the traveller expressed no
    # preference (departure "any"), say nothing about this offer specifically.
    informative = [k for k in keys if k not in top.missing]
    if ranking.request.preferred_departure.is_any and "departure" in informative:
        informative.remove("departure")
    advantages = [_phrase(k, top, ranking, True) for k in informative if top.scores[k] >= ADVANTAGE_AT]
    disadvantages = [_phrase(k, top, ranking, False) for k in informative if top.scores[k] < DISADVANTAGE_BELOW]
    moderate = [f"{labels[k].lower()} ({_fact(k, top)}; {top.scores[k]:.0f}/100)" for k in informative
                if DISADVANTAGE_BELOW <= top.scores[k] < ADVANTAGE_AT]
    if "transfers" not in ranking.weights:  # route type is always worth stating, even if not scored
        if offer.is_direct:
            advantages.append("a direct route")
        else:
            disadvantages.append(f"{offer.transfers} transfer(s) on the way")
    if advantages:
        lines.append("Why it ranks highest: it offers " + _join(advantages) + ".")
    else:
        lines.append("Why it ranks highest: no single criterion stands out; it has the best balance "
                     "across your weighted criteria.")
    if disadvantages:
        lines.append("Remaining disadvantages: " + _join(disadvantages) + ".")
    else:
        lines.append(f"Remaining disadvantages: no weighted criterion scores below {DISADVANTAGE_BELOW:.0f}/100.")
    if moderate:
        lines.append("Moderate on: " + _join(moderate) + ".")
    if top.missing:
        lines.append(
            "Missing data: " + ", ".join(labels[k] for k in top.missing)
            + " not published, so a neutral score was used."
        )

    if len(ranking.items) > 1:
        second = ranking.items[1]
        diff = {k: top.contributions[k] - second.contributions[k] for k in ranking.weights}
        gains = sorted((k for k in diff if diff[k] > 0.05), key=lambda k: -diff[k])
        losses = sorted((k for k in diff if diff[k] < -0.05), key=lambda k: diff[k])
        sentence = (f"Compared with #2 ({second.offer.company.name} {second.offer.offer_id}, "
                    f"{second.total:.1f}), it gains points on "
                    + (_join([f"{labels[k].lower()} (+{diff[k]:.1f})" for k in gains]) or "no criterion"))
        if losses:
            sentence += " but loses on " + _join([f"{labels[k].lower()} ({diff[k]:.1f})" for k in losses])
        sentence += f"; margin {top.total - second.total:.2f} points."
        if top.tied:
            sentence += " The displayed totals are tied, so the tie-break rule decided the order."
        lines.append(sentence)
        if ranking.is_robust:
            lines.append(
                f"Robustness: the winner stays the same if any single weight is moved by "
                f"+/-{ranking.shift_pp:.0f} percentage points."
            )
        else:
            flips = "; ".join(
                f"{labels[f.criterion].lower()} {f.shift_pp:+.0f} pp -> {f.new_winner.company.name} "
                f"{f.new_winner.offer_id}" for f in ranking.flips
            )
            lines.append(f"Sensitivity: the winner changes if a weight moves by {ranking.shift_pp:.0f} pp ({flips}).")
    weights_text = ", ".join(f"{labels[k].lower()} {ranking.weights[k] * 100:.0f}%" for k in keys)
    lines.append(
        "Note: this is the best match only under your hard constraints, your weights "
        f"({weights_text}) and the scoring assumptions - not an objectively best bus."
    )
    return lines
