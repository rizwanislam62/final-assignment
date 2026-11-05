"""Draw the architecture diagram and the program flowchart used in the report.

    python scripts/draw_diagrams.py

Writes docs/architecture.png and docs/flowchart.png.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch  # noqa: E402

DOCS = Path(__file__).resolve().parent.parent / "docs"

FILE = "#FFF4D6"
CODE = "#DCEBFA"
STORE = "#E3F4E1"
UI = "#F6E1F0"
DECISION = "#FFE9D6"
EDGE = "#33475B"


def box(ax, x, y, w, h, title, body="", color=CODE, title_size=10):
    ax.add_patch(FancyBboxPatch((x - w / 2, y - h / 2), w, h, boxstyle="round,pad=0.02,rounding_size=0.08",
                                fc=color, ec=EDGE, lw=1.2))
    if body:
        ax.text(x, y + h / 2 - 0.15, title, ha="center", va="top", fontsize=title_size, weight="bold",
                color="#1B2A38")
        ax.text(x, y + h / 2 - 0.45, body, ha="center", va="top", fontsize=7.8, color="#22313F",
                linespacing=1.35)
    else:
        ax.text(x, y, title, ha="center", va="center", fontsize=title_size, weight="bold", color="#1B2A38")


def arrow(ax, start, end, label="", rad=0.0, ls="-"):
    ax.add_patch(FancyArrowPatch(start, end, arrowstyle="-|>", mutation_scale=13, lw=1.2, color=EDGE,
                                 connectionstyle=f"arc3,rad={rad}", linestyle=ls, shrinkA=2, shrinkB=2))
    if label:
        mx, my = (start[0] + end[0]) / 2, (start[1] + end[1]) / 2
        ax.text(mx, my, label, fontsize=7.4, ha="center", va="center", color="#22313F",
                bbox=dict(fc="white", ec="none", pad=1.2))


def architecture():
    fig, ax = plt.subplots(figsize=(11.6, 8.6))
    ax.set_xlim(0, 11.4)
    ax.set_ylim(-0.35, 8.4)
    ax.axis("off")
    ax.text(5.7, 8.25, "BusBidder Spain - main components and flow of information", ha="center",
            fontsize=13, weight="bold", color="#1B2A38")
    A, B, C = 1.6, 5.6, 9.7

    # column A: inputs, configuration, interface, booking
    box(ax, A, 7.3, 2.8, 1.5, "Input files",
        "data/companies.csv\ndata/destinations.csv\ndata/offers.csv\nconfig/settings.json, profiles.json", FILE)
    box(ax, A, 5.65, 2.8, 0.9, "config.py + profiles.py", "Settings, TravellerProfile", CODE, 9.5)
    box(ax, A, 4.0, 2.8, 1.7, "cli.py  (user interface)",
        "search, compare, book, bookings,\nshow, cancel, validate,\ncriteria, profiles, interactive", UI)
    box(ax, A, 1.95, 2.8, 1.2, "booking.py",
        "seats left = published - booked\nreference BB-YYYYMMDD-XXXXXX\ncancel: refund / travel credit", CODE, 9.5)
    box(ax, A, 0.5, 2.8, 0.75, "state/bookings.json", "atomic write (temp + replace)", STORE, 9)

    # column B: the processing pipeline
    box(ax, B, 7.3, 3.2, 1.5, "loader.py + validation.py",
        "type conversion, regex checks\ncorrected / rejected / review\nbuilds the Marketplace", CODE)
    box(ax, B, 5.2, 3.2, 1.4, "filters.py",
        "@hard_constraint registry\ndestination, window, seats, budget,\ntransfers, flexibility, luggage, duration", CODE)
    box(ax, B, 3.35, 3.2, 1.2, "ranking.py", "weighted total, deterministic\ntie-break, +/-10 pp sensitivity", CODE)
    box(ax, B, 1.75, 3.2, 1.0, "explain.py", "advantages, disadvantages,\nrunner-up comparison, caveat", CODE, 9.5)

    # column C: outputs and shared modules
    box(ax, C, 7.3, 3.0, 1.5, "Data-quality outputs",
        "output/rejected_offers.csv\noutput/review_queue.csv\nlogs/busbidder.log", STORE)
    box(ax, C, 5.2, 3.0, 1.4, "models.py  (shared)",
        "Company, Destination, Offer,\nFareConditions, TravelRequest,\nDeparturePreference", CODE, 9.5)
    box(ax, C, 3.35, 3.0, 1.5, "scoring.py",
        "Criterion registry (__init_subclass__)\nrelative: price, duration, CO2\n"
        "fixed: departure, flexibility,\ntransfers, rating;  weight checks", CODE, 9.5)
    box(ax, C, 1.4, 3.0, 1.3, "exceptions.py + logging_setup.py",
        "BusBidderError hierarchy;\none log file for all modules", CODE, 9)

    arrow(ax, (3.0, 7.3), (4.0, 7.3), "read")
    arrow(ax, (7.2, 7.3), (8.2, 7.3), "invalid")
    arrow(ax, (A, 6.55), (A, 6.1))
    arrow(ax, (A, 5.2), (A, 4.85))
    arrow(ax, (B, 6.55), (B, 5.9), "validated offers")
    arrow(ax, (3.0, 4.55), (4.0, 4.75), "TravelRequest")
    arrow(ax, (B, 4.5), (B, 3.95), "acceptable offers")
    arrow(ax, (8.2, 3.35), (7.2, 3.35), "0-100 scores")
    arrow(ax, (B, 2.75), (B, 2.25), "Ranking")
    arrow(ax, (4.0, 3.2), (3.0, 3.55), "ranked offers")
    arrow(ax, (4.0, 1.6), (3.0, 3.2), "explanation", rad=-0.25)
    arrow(ax, (A, 3.15), (A, 2.55), "selected offer")
    arrow(ax, (A, 1.35), (A, 0.88))
    ax.text(0.2, -0.25, "Yellow = input files, blue = Python modules, green = files written, pink = user interface. "
            "filters.py asks booking.py for seats left, so capacity always reflects saved bookings.",
            fontsize=7.6, color="#4A5B6B")
    DOCS.mkdir(exist_ok=True)
    fig.savefig(DOCS / "architecture.png", dpi=170, bbox_inches="tight")
    plt.close(fig)


def flowchart():
    fig, ax = plt.subplots(figsize=(8.6, 12))
    ax.set_xlim(0, 8.6)
    ax.set_ylim(0, 12)
    ax.axis("off")
    ax.text(4.3, 11.75, "Program flow: search, rank and book", ha="center", fontsize=13, weight="bold")
    steps = [
        (11.0, "Load settings.json, start log file", UI),
        (10.15, "Load companies, destinations, offers\nvalidate every record", CODE),
        (9.2, "Write rejected_offers.csv and review_queue.csv\n(corrected records kept, logged)", STORE),
        (8.3, "Read traveller request\n(profile and/or command-line options)", UI),
        (7.4, "Validate request: dates, destination,\nweights total 100% (else clear error)", CODE),
        (6.5, "Apply hard constraints to every offer\n(record all violated constraints)", CODE),
    ]
    for y, text, color in steps:
        box(ax, 3.3, y, 4.6, 0.68, text, color=color, title_size=8.8)
    for (y1, *_), (y2, *_) in zip(steps, steps[1:]):
        arrow(ax, (3.3, y1 - 0.34), (3.3, y2 + 0.34))

    ax.add_patch(plt.Polygon([[3.3, 5.95], [4.75, 5.45], [3.3, 4.95], [1.85, 5.45]], fc=DECISION, ec=EDGE))
    ax.text(3.3, 5.45, "Any acceptable\noffer?", ha="center", va="center", fontsize=8.8, weight="bold")
    arrow(ax, (3.3, 6.16), (3.3, 5.95))
    box(ax, 6.9, 5.45, 2.6, 0.9, "Show removal reasons,\nsuggest relaxing a\nconstraint, then end", color=UI,
        title_size=8.3)
    arrow(ax, (4.75, 5.45), (5.6, 5.45), "no")

    later = [
        (4.35, "Normalise each criterion to 0-100\n(robust min-max or fixed scale)", CODE),
        (3.45, "Weighted total, sort, deterministic tie-break,\n+/-10 pp sensitivity check", CODE),
        (2.55, "Display ranked table + explanation\nof the top offer (with caveats)", UI),
    ]
    for y, text, color in later:
        box(ax, 3.3, y, 4.6, 0.68, text, color=color, title_size=8.8)
    arrow(ax, (3.3, 4.95), (3.3, 4.69), "yes")
    for (y1, *_), (y2, *_) in zip(later, later[1:]):
        arrow(ax, (3.3, y1 - 0.34), (3.3, y2 + 0.34))

    ax.add_patch(plt.Polygon([[3.3, 1.95], [4.6, 1.5], [3.3, 1.05], [2.0, 1.5]], fc=DECISION, ec=EDGE))
    ax.text(3.3, 1.5, "Book an\noffer?", ha="center", va="center", fontsize=8.8, weight="bold")
    arrow(ax, (3.3, 2.21), (3.3, 1.95))
    box(ax, 6.9, 1.5, 2.6, 1.05, "Re-check seats left;\ncreate BB- reference;\nsave booking (atomic);\nconfirm",
        color=STORE, title_size=8.3)
    arrow(ax, (4.6, 1.5), (5.6, 1.5), "yes")
    box(ax, 3.3, 0.35, 2.0, 0.45, "End", color=UI, title_size=9)
    arrow(ax, (3.3, 1.05), (3.3, 0.58), "no")
    arrow(ax, (6.9, 0.97), (4.3, 0.35), "", rad=-0.2)
    fig.savefig(DOCS / "flowchart.png", dpi=170, bbox_inches="tight")
    plt.close(fig)


if __name__ == "__main__":
    architecture()
    flowchart()
    print(f"Diagrams written to {DOCS}")
