"""Reproducible experiments used as evidence in the report.

    python scripts/run_experiments.py

Writes CSV files and a Markdown summary to ``output/experiments/``.  The
experiments use the library directly (no randomness), so they always produce
the same numbers for the same data and settings.

E1  Winners per profile for every destination (12-14 July 2027)
    -> which companies each profile tends to pick (fairness / bias check)
E2  Price-weight sweep for the specification example (Madrid -> Malaga)
E3  Robust vs plain min-max normalisation, all destinations, example profile
E4  Effective influence of each criterion (weight x spread of its scores)
E5  Outlier stress test: one extreme EUR 180 offer added, no budget limit
"""

from __future__ import annotations

import copy
import csv
import statistics
import sys
from collections import Counter
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from busbidder.config import load_settings  # noqa: E402
from busbidder.filters import apply_hard_constraints  # noqa: E402
from busbidder.loader import Marketplace  # noqa: E402
from busbidder.logging_setup import configure_logging  # noqa: E402
from busbidder.profiles import load_profiles  # noqa: E402
from busbidder.ranking import rank  # noqa: E402

OUT = ROOT / "output" / "experiments"
DATE_FROM, DATE_TO = date(2027, 7, 12), date(2027, 7, 14)


def ranking_for(market, settings, profile, destination, weights=None):
    request = profile.build_request(destination, DATE_FROM, DATE_TO)
    if weights:
        request.weights = weights
    result = apply_hard_constraints(market.offers, request, lambda o: o.seats_available)
    return rank(result, request, request.weights, settings)


def write_csv(name, header, rows):
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / name).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)


def md_table(header, rows):
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return "\n".join(lines)


def e1_winners(market, settings, profiles):
    rows, by_profile = [], {}
    for key, profile in profiles.items():
        counter = Counter()
        for dest in market.destinations.values():
            r = ranking_for(market, settings, profile, dest)
            top = r.top
            company = top.offer.company.name if top else "(none)"
            counter[company] += 1
            rows.append([key, dest.name, len(r.filter_result.accepted),
                         top.offer.offer_id if top else "", company,
                         f"{top.offer.price_eur:.2f}" if top else "", top.offer.duration_min if top else "",
                         f"{top.total:.2f}" if top else "", "yes" if r.is_robust else "no"])
        by_profile[key] = counter
    write_csv("e1_winners_by_destination.csv",
              ["profile", "destination", "acceptable_offers", "top_offer", "company", "price_eur",
               "duration_min", "score", "robust"], rows)
    companies = sorted({c for counter in by_profile.values() for c in counter})
    table_rows = [[profiles[k].name] + [by_profile[k].get(c, 0) for c in companies] for k in profiles]
    robust = Counter(r[0] for r in rows if r[-1] == "yes")
    summary = md_table(["Profile"] + companies, table_rows)
    summary += "\n\nRobust winners (unchanged under +/-10 pp weight shifts), out of 15 destinations: " + \
        ", ".join(f"{profiles[k].name} {robust.get(k, 0)}" for k in profiles)
    return summary


def e2_weight_sweep(market, settings, profiles):
    profile = profiles["example"]
    malaga = market.find_destination("Malaga")
    base = profile.weights
    rows = []
    for price_pct in range(10, 80, 10):
        w_price = price_pct / 100
        rest = 1 - base["price"]
        weights = {k: (w_price if k == "price" else v * (1 - w_price) / rest) for k, v in base.items()}
        r = ranking_for(market, settings, profile, malaga, weights)
        o = r.top.offer
        rows.append([f"{price_pct}%", o.offer_id, o.company.name, f"{o.departure:%a %d %H:%M}",
                     f"{o.price_eur:.2f}", o.duration_min, f"{r.top.total:.1f}"])
    header = ["price weight", "top offer", "company", "departs", "EUR", "minutes", "score"]
    write_csv("e2_price_weight_sweep.csv", header, rows)
    return md_table(header, rows)


def e3_normalisation(market, settings, profiles):
    plain = copy.deepcopy(settings)
    plain.min_meaningful_range = {}
    plain.outlier_iqr_factor = 1e9          # fences never bind -> classic min-max
    profile = profiles["example"]
    rows, changed = [], 0
    for dest in market.destinations.values():
        robust_r = ranking_for(market, settings, profile, dest)
        plain_r = ranking_for(market, plain, profile, dest)
        if not robust_r.top:
            continue
        a, b = robust_r.top.offer, plain_r.top.offer
        same = a.offer_id == b.offer_id
        changed += not same
        top5_overlap = len({i.offer.offer_id for i in robust_r.items[:5]} & {i.offer.offer_id for i in plain_r.items[:5]})
        rows.append([dest.name, a.offer_id, f"{a.price_eur:.2f}", b.offer_id, f"{b.price_eur:.2f}",
                     "same" if same else "DIFFERENT", top5_overlap])
    header = ["destination", "robust winner", "EUR", "plain min-max winner", "EUR", "winner", "top-5 overlap"]
    write_csv("e3_normalisation_comparison.csv", header, rows)
    return md_table(header, rows) + f"\n\nWinner differs for {changed} of {len(rows)} destinations."


def e4_influence(market, settings, profiles):
    plain = copy.deepcopy(settings)
    plain.min_meaningful_range = {}
    plain.outlier_iqr_factor = 1e9
    profile = profiles["example"]
    rows = []
    for label, s in (("robust", settings), ("plain min-max", plain)):
        per_crit = {k: [] for k in profile.weights}
        for dest in market.destinations.values():
            r = ranking_for(market, s, profile, dest)
            if len(r.items) < 2:
                continue
            for k in profile.weights:
                per_crit[k].append(statistics.pstdev(i.contributions[k] for i in r.items))
        rows.append([label] + [f"{statistics.mean(v):.2f}" for v in per_crit.values()])
    header = ["normalisation"] + [f"{k} ({profile.weights[k] * 100:.0f}%)" for k in profile.weights]
    write_csv("e4_effective_influence.csv", header, rows)
    return md_table(header, rows)


def e5_outlier(market, settings, profiles):
    from dataclasses import replace
    plain = copy.deepcopy(settings)
    plain.min_meaningful_range = {}
    plain.outlier_iqr_factor = 1e9
    profile = profiles["example"]
    malaga = market.find_destination("Malaga")
    request = profile.build_request(malaga, DATE_FROM, DATE_TO)
    request.max_budget_eur = None                      # no budget, so the outlier survives filtering
    base = apply_hard_constraints(market.offers, request, lambda o: o.seats_available)
    template = max(base.accepted, key=lambda o: o.price_eur)
    outlier = replace(template, offer_id="CAM-MAL-9999", price_eur=180.0)
    rows = []
    for label, s, extra in (("robust, no outlier", settings, []), ("robust, with outlier", settings, [outlier]),
                            ("plain, no outlier", plain, []), ("plain, with outlier", plain, [outlier])):
        fr = copy.copy(base)
        fr.accepted = base.accepted + extra
        r = rank(fr, request, request.weights, s)
        normal = [i.scores["price"] for i in r.items if i.offer.offer_id != "CAM-MAL-9999"]
        rows.append([label, r.top.offer.offer_id, f"{min(normal):.0f}-{max(normal):.0f}",
                     f"{statistics.pstdev(normal):.1f}",
                     " ".join(i.offer.offer_id for i in r.items[:3])])
    header = ["setting", "winner", "price-score range of normal offers", "std dev", "top 3"]
    write_csv("e5_outlier_stress_test.csv", header, rows)
    return md_table(header, rows)


def main():
    settings = load_settings()
    settings.output_dir = OUT  # keep rejected/review files of this run with the experiments
    configure_logging(settings.log_file)
    market = Marketplace.load(settings, write_issue_files=False)
    profiles = load_profiles(settings.profiles_file)
    sections = [
        ("E1 Winning company per profile across all 15 destinations (12-14 July 2027)", e1_winners),
        ("E2 Price-weight sweep, example traveller, Madrid -> Malaga", e2_weight_sweep),
        ("E3 Robust vs plain min-max normalisation, example traveller", e3_normalisation),
        ("E4 Effective influence: mean standard deviation of weighted contribution (points)", e4_influence),
        ("E5 Outlier stress test: Madrid -> Malaga without budget, one EUR 180 offer injected", e5_outlier),
    ]
    parts = ["# BusBidder Spain - experiment results", ""]
    for title, fn in sections:
        parts += [f"## {title}", "", fn(market, settings, profiles), ""]
    (OUT / "summary.md").write_text("\n".join(parts), encoding="utf-8")
    print("\n".join(parts))


if __name__ == "__main__":
    main()
