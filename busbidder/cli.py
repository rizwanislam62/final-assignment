"""Command-line interface for BusBidder Spain.

Run ``python main.py --help`` for the list of commands, or ``python main.py``
with no arguments for a guided interactive session.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from datetime import date, datetime
from pathlib import Path

from . import __version__
from .booking import MAX_SEATS_PER_BOOKING, NAME_RE, Booking, BookingService, BookingStore
from .config import Settings, load_settings
from .exceptions import BusBidderError, InvalidRequestError
from .explain import explain_top
from .filters import CONSTRAINTS, FilterResult, apply_hard_constraints
from .loader import Marketplace
from .logging_setup import configure_logging, get_logger
from .models import DeparturePreference, TravelRequest
from .profiles import TravellerProfile, load_profiles
from .ranking import TIE_BREAK_RULE, Ranking, rank
from .scoring import Criterion, parse_weights, validate_weights

log = get_logger("cli")

SHORT_LABEL = {"price": "Price", "duration": "Dur", "departure": "DepFit", "flexibility": "Flex",
               "transfers": "Trans", "rating": "Rating", "emissions": "CO2"}
_DURATION_RE = re.compile(r"^(?:(?P<h>\d{1,2})h(?P<m>[0-5]?\d)?|(?P<mins>\d{1,4}))$", re.I)


# ======================================================================
# Small helpers
# ======================================================================
def parse_date(text: str) -> date:
    try:
        return datetime.strptime(text.strip(), "%Y-%m-%d").date()
    except ValueError as exc:
        raise InvalidRequestError(f"'{text}' is not a valid date; use YYYY-MM-DD, e.g. 2027-07-12.") from exc


def parse_duration(text: str) -> int:
    """Accept '480' (minutes), '8h' or '7h30'."""
    match = _DURATION_RE.match(text.strip())
    if not match:
        raise InvalidRequestError(f"'{text}' is not a duration; use minutes (480) or hours (8h, 7h30).")
    if match["mins"]:
        return int(match["mins"])
    return int(match["h"]) * 60 + int(match["m"] or 0)


def hm(minutes: int) -> str:
    h, m = divmod(int(minutes), 60)
    return f"{h}h{m:02d}"


def fare_code(offer) -> str:
    """Compact fare label: C = free changes, C5 = changes for EUR 5, R = refundable, - = neither."""
    fare = offer.fare
    parts = []
    if fare.changeable:
        parts.append("C" if fare.change_fee_eur == 0 else f"C{fare.change_fee_eur:g}")
    if fare.refundable:
        parts.append("R")
    return "+".join(parts) or "-"


def table(headers: list[str], rows: list[list], align: str | None = None) -> str:
    """Render a plain-text table.  ``align`` has one char per column: l or r."""
    cells = [[str(c) for c in row] for row in rows]
    widths = [max(len(h), *(len(r[i]) for r in cells)) if cells else len(h) for i, h in enumerate(headers)]
    align = align or "l" * len(headers)

    def fmt(row):
        return "  ".join(c.rjust(w) if a == "r" else c.ljust(w) for c, w, a in zip(row, widths, align)).rstrip()

    out = [fmt(headers), "  ".join("-" * w for w in widths)]
    out.extend(fmt(r) for r in cells)
    return "\n".join(out)


def heading(text: str) -> None:
    print()
    print(text)
    print("=" * len(text))


# ======================================================================
# Application context
# ======================================================================
class App:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.marketplace = Marketplace.load(settings)
        self.profiles = load_profiles(settings.profiles_file)
        self.bookings = BookingService(self.marketplace, BookingStore(settings.bookings_file),
                                       clock=settings.now)

    # ------------------------------------------------------------- requests
    def build_request(self, args) -> TravelRequest:
        destination = self.marketplace.find_destination(args.to)
        date_from = parse_date(args.from_date)
        date_to = parse_date(args.to_date) if args.to_date else date_from
        if args.profile:
            profile = self._profile(args.profile)
            request = profile.build_request(destination, date_from, date_to, args.seats)
        else:
            request = TravelRequest(
                destination=destination, date_from=date_from, date_to=date_to, party_size=args.seats,
                weights=validate_weights(self.settings.default_weights),
            )
        # Command-line options override the profile.
        overrides = {
            "max_budget_eur": args.budget,
            "max_transfers": args.max_transfers,
            "min_luggage_kg": args.luggage,
            "max_duration_min": parse_duration(args.max_duration) if args.max_duration else None,
        }
        for name, value in overrides.items():
            if value is not None:
                setattr(request, name, value)
        if args.changeable:
            request.changeable_required = True
        if args.refundable:
            request.refundable_required = True
        if args.depart:
            request.preferred_departure = DeparturePreference.parse(args.depart)
        if args.weights:
            request.weights = validate_weights(parse_weights(args.weights))
        request.__post_init__()  # re-validate after overrides
        return request

    def _profile(self, key: str) -> TravellerProfile:
        if key not in self.profiles:
            raise InvalidRequestError(f"Unknown profile '{key}'. Available: {', '.join(self.profiles)}.")
        return self.profiles[key]

    def run_ranking(self, request: TravelRequest) -> Ranking:
        result = apply_hard_constraints(self.marketplace.offers, request, self.bookings.seats_left)
        return rank(result, request, request.weights, self.settings)


# ======================================================================
# Printing
# ======================================================================
def print_request(request: TravelRequest, ranking: Ranking) -> None:
    labels = {c.key: c.label for c in ranking.criteria}
    print("HARD CONSTRAINTS - an offer that breaks any of these is removed before ranking:")
    for line in request.hard_constraint_lines():
        print(f"  * {line}")
    print("SOFT PREFERENCES - these only change the score, they never remove an offer:")
    print(f"  * Preferred departure: {request.preferred_departure.describe()}")
    weights = ", ".join(f"{labels[k]} {w * 100:.0f}%" for k, w in sorted(request.weights.items(), key=lambda kv: -kv[1]))
    print(f"  * Weights: {weights}")


def print_filter_summary(result: FilterResult) -> None:
    print()
    print(f"{result.in_scope} offers match the journey and dates; "
          f"{len(result.removed)} removed by hard constraints; {len(result.accepted)} acceptable.")
    counts = result.counts_by_constraint()
    if counts:
        parts = ", ".join(f"{k}: {v}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1]))
        print(f"Removed because of -> {parts}  (one offer can break several constraints)")


def print_removed(result: FilterResult, limit: int) -> None:
    if not result.removed:
        return
    heading("Removed offers (hard-constraint violations)")
    rows = []
    for r in result.removed[:limit]:
        o = r.offer
        rows.append([o.offer_id, o.company.name, f"{o.departure:%a %d %H:%M}", f"{o.price_eur:.2f}",
                     "; ".join(reason for _, reason in r.reasons)])
    print(table(["Offer", "Company", "Departs", "EUR", "Reason(s)"], rows, "lllrl"))
    if len(result.removed) > limit:
        print(f"... and {len(result.removed) - limit} more")


def print_ranking(ranking: Ranking, top: int, seats_left) -> None:
    if not ranking.items:
        print("\nNo offer satisfies all hard constraints. Try relaxing a constraint "
              "(see the removal reasons above or use --show-removed).")
        return
    keys = [c.key for c in ranking.criteria]
    heading(f"Ranked offers (showing {min(top, len(ranking.items))} of {len(ranking.items)})")
    headers = ["#", "Offer", "Company", "Date", "Dep", "Arr", "Time", "EUR", "Tr", "Fare", "Kg", "Seats", "|"]
    headers += [SHORT_LABEL.get(k, k) for k in keys] + ["|", "Total"]
    rows = []
    for item in ranking.items[:top]:
        o = item.offer
        arr = f"{o.arrival:%H:%M}" + ("+1" if o.overnight else "")
        row = [f"{item.rank}{'=' if item.tied else ''}", o.offer_id, o.company.name, f"{o.departure:%a %d}",
               f"{o.departure:%H:%M}", arr, hm(o.duration_min), f"{o.price_eur:.2f}", o.transfers,
               fare_code(o), o.fare.luggage_kg, seats_left(o), "|"]
        row += [("n/a*" if k in item.missing else f"{item.scores[k]:.0f}") for k in keys]
        row += ["|", f"{item.total:.1f}"]
        rows.append(row)
    print(table(headers, rows, "rlllllrrrlrrl" + "r" * len(keys) + "lr"))
    print("Criterion scores are 0-100 (100 = best); Total = weighted sum of the scores. "
          "Fare: C = free changes, C5 = changes cost EUR 5, R = refundable, - = neither. +1 = arrives next day.")
    if any(i.missing for i in ranking.items[:top]):
        print("n/a* = value not published; a neutral score of "
              f"{ranking.criteria[0].settings.neutral_score_for_missing:.0f} was used.")
    if any(i.tied for i in ranking.items[:top]):
        print(f"'=' marks equal displayed totals; {TIE_BREAK_RULE}.")


def print_explanation(ranking: Ranking) -> None:
    heading("Recommendation")
    for line in explain_top(ranking):
        print(f"- {line}")


def print_booking(b: Booking) -> None:
    heading(f"Booking {b.reference}")
    dep = datetime.strptime(b.departure, "%Y-%m-%dT%H:%M")
    arr = datetime.strptime(b.arrival, "%Y-%m-%dT%H:%M")
    rows = [
        ["Status", b.status],
        ["Traveller", b.traveller_name],
        ["Journey", f"Madrid -> {b.destination} with {b.company} (offer {b.offer_id})"],
        ["Departure", f"{dep:%A %d %B %Y, %H:%M}"],
        ["Arrival", f"{arr:%A %d %B %Y, %H:%M}"],
        ["Seats", str(b.seats)],
        ["Price", f"EUR {b.price_per_seat_eur:.2f} x {b.seats} = EUR {b.total_price_eur:.2f}"],
        ["Fare conditions", b.fare.summary],
        ["Payment", b.payment_status],
        ["Booked at", b.created_at],
    ]
    if b.status == "CANCELLED":
        rows += [["Cancelled at", b.cancelled_at or ""],
                 ["Refund", f"EUR {b.refund_eur:.2f}"],
                 ["Travel credit", f"EUR {b.travel_credit_eur:.2f}"]]
    width = max(len(r[0]) for r in rows)
    for label, value in rows:
        print(f"  {label.ljust(width)} : {value}")
    print("  (Simulation only - no real payment was taken.)")


def export_ranking(ranking: Ranking, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    keys = [c.key for c in ranking.criteria]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["traveller", "rank", "offer_id", "company", "departure", "arrival", "duration_min",
                         "price_eur", "transfers", "fare", "luggage_kg", "rating", "co2_kg"]
                        + [f"weight_{k}" for k in keys] + [f"score_{k}" for k in keys] + ["total"])
        for item in ranking.items:
            o = item.offer
            writer.writerow([ranking.request.traveller_label, item.rank, o.offer_id, o.company.name,
                             f"{o.departure:%Y-%m-%d %H:%M}", f"{o.arrival:%Y-%m-%d %H:%M}", o.duration_min,
                             f"{o.price_eur:.2f}", o.transfers, fare_code(o), o.fare.luggage_kg,
                             "" if o.rating is None else o.rating, "" if o.co2_kg is None else o.co2_kg]
                            + [f"{ranking.weights[k]:.4f}" for k in keys]
                            + [f"{item.scores[k]:.2f}" for k in keys] + [f"{item.total:.2f}"])


# ======================================================================
# Commands
# ======================================================================
def cmd_search(app: App, args) -> int:
    request = app.build_request(args)
    ranking = app.run_ranking(request)
    heading(f"BusBidder Spain - offers for {request.traveller_label}")
    print_request(request, ranking)
    print_filter_summary(ranking.filter_result)
    if ranking.filter_result.in_scope == 0 and app.marketplace.date_range:
        first, last = app.marketplace.date_range
        print(f"Hint: the dataset contains departures from {first:%d %b %Y} to {last:%d %b %Y}.")
    if args.show_removed:
        print_removed(ranking.filter_result, args.show_removed)
    print_ranking(ranking, args.top, app.bookings.seats_left)
    if ranking.items:
        print_explanation(ranking)
    if args.export:
        export_ranking(ranking, Path(args.export))
        print(f"\nRanking exported to {args.export}")
    if args.book and ranking.items:
        interactive_booking(app, ranking, args.seats)
    return 0


def interactive_booking(app: App, ranking: Ranking, seats: int) -> None:
    def pick(choice: str):
        if not choice:
            return None
        if choice.isdigit():
            index = int(choice)
            if not 1 <= index <= len(ranking.items):
                raise InvalidRequestError(f"Rank {index} does not exist (1-{len(ranking.items)}).")
            return ranking.items[index - 1].offer
        offer = next((i.offer for i in ranking.items if i.offer.offer_id == choice.strip().upper()), None)
        if offer is None:
            raise InvalidRequestError(f"Offer '{choice}' is not in this ranking.")
        return offer

    print()
    offer = ask_until_valid("Enter a rank number or offer id to book (press Enter to skip)", "", pick)
    if offer is None:
        print("No booking made.")
        return
    name = ask_until_valid("Traveller name", "", _traveller_name)
    print(f"\nYou are booking {offer.offer_id}: {offer.company.name}, {offer.departure:%a %d %b %H:%M}, "
          f"{seats} seat(s) x EUR {offer.price_eur:.2f} = EUR {offer.price_eur * seats:.2f}; {offer.fare.summary}.")
    if not ask_until_valid("Confirm booking? (y/n)", "n", _yes_no):
        print("Booking cancelled by user; nothing was saved.")
        return
    booking = app.bookings.book(offer.offer_id, name, seats)
    print_booking(booking)
    print(f"  Seats left on {offer.offer_id}: {app.bookings.seats_left(offer)}")


def cmd_compare(app: App, args) -> int:
    destination = app.marketplace.find_destination(args.to)
    date_from = parse_date(args.from_date)
    date_to = parse_date(args.to_date) if args.to_date else date_from
    keys = [k.strip() for k in args.profiles.split(",")] if args.profiles else list(app.profiles)
    heading(f"Same offers, different travellers: Madrid -> {destination.name}, "
            f"{date_from:%d %b} to {date_to:%d %b %Y}")
    summary = []
    for key in keys:
        profile = app._profile(key)
        request = profile.build_request(destination, date_from, date_to, args.seats)
        ranking = app.run_ranking(request)
        labels = {c.key: c.label for c in ranking.criteria}
        print(f"\n--- {profile.name} [{key}] ---")
        print(f"    {profile.description}")
        print(f"    Hard: " + "; ".join(request.hard_constraint_lines()[3:] or ["no extra constraints"]))
        print(f"    Soft: departure {request.preferred_departure.describe()}; weights "
              + ", ".join(f"{labels[k]} {w * 100:.0f}%" for k, w in sorted(request.weights.items(), key=lambda kv: -kv[1])))
        fr = ranking.filter_result
        print(f"    {len(fr.accepted)} of {fr.in_scope} offers acceptable")
        rows = []
        for item in ranking.items[:args.top]:
            o = item.offer
            rows.append([item.rank, o.offer_id, o.company.name, f"{o.departure:%a %d %H:%M}", hm(o.duration_min),
                         f"{o.price_eur:.2f}", o.transfers, fare_code(o), f"{item.total:.1f}"])
        if rows:
            print("    " + table(["#", "Offer", "Company", "Departs", "Time", "EUR", "Tr", "Fare", "Total"],
                                  rows, "rllllrrlr").replace("\n", "\n    "))
        if args.explain and ranking.items:
            for line in explain_top(ranking):
                print(f"    - {line}")
        if args.export_dir:
            export_ranking(ranking, Path(args.export_dir) / f"ranking_{key}.csv")
        top = ranking.top
        if top:
            o = top.offer
            summary.append([profile.name, o.offer_id, o.company.name, f"{o.departure:%a %d %H:%M}",
                            hm(o.duration_min), f"{o.price_eur:.2f}", fare_code(o), f"{top.total:.1f}",
                            "yes" if ranking.is_robust else "no"])
        else:
            summary.append([profile.name, "-", "no acceptable offer", "", "", "", "", "", ""])
    heading("Summary of recommendations")
    print(table(["Traveller", "Top offer", "Company", "Departs", "Time", "EUR", "Fare", "Score", "Robust"],
                summary, "llllrrlrl"))
    print("Robust = the top offer stays first when any single weight moves by "
          f"+/-{app.settings.sensitivity_shift_pp:.0f} percentage points.")
    if args.export_dir:
        print(f"Rankings exported to {args.export_dir}")
    return 0


def cmd_book(app: App, args) -> int:
    booking = app.bookings.book(args.offer, args.name, args.seats)
    print_booking(booking)
    offer = app.marketplace.offer(booking.offer_id)
    print(f"  Seats left on {offer.offer_id}: {app.bookings.seats_left(offer)}")
    return 0


def cmd_bookings(app: App, args) -> int:
    heading("Bookings")
    if not app.bookings.bookings:
        print("No bookings yet.")
        return 0
    rows = [[b.reference, b.status, b.traveller_name, b.offer_id, b.destination, b.departure.replace("T", " "),
             b.seats, f"{b.total_price_eur:.2f}", b.payment_status] for b in app.bookings.bookings]
    print(table(["Reference", "Status", "Traveller", "Offer", "To", "Departs", "Seats", "EUR", "Payment"],
                rows, "lllllllrl"))
    return 0


def cmd_show(app: App, args) -> int:
    print_booking(app.bookings.get(args.reference))
    return 0


def cmd_cancel(app: App, args) -> int:
    booking = app.bookings.cancel(args.reference)
    print_booking(booking)
    return 0


def cmd_validate(app: App, args) -> int:
    report = app.marketplace.report
    heading("Data validation report")
    m = app.marketplace
    print(f"Companies: {len(m.companies)}   Destinations: {len(m.destinations)}")
    pairs = {(o.company.company_id, o.destination.destination_id) for o in m.offers}
    print(f"Company-destination combinations with offers: {len(pairs)} of {len(m.companies) * len(m.destinations)}")
    print(f"Offer records read: {report.offers_read}")
    print(f"  accepted           : {report.accepted}  (of which corrected: {report.corrected_records}, "
          f"sold out: {report.sold_out})")
    print(f"  rejected           : {len(report.rejected)}  -> {app.settings.output_dir / 'rejected_offers.csv'}")
    print(f"  placed for review  : {len(report.review)}  -> {app.settings.output_dir / 'review_queue.csv'}")
    for title, issues in (("Rejected", report.rejected), ("Placed aside for review", report.review)):
        if issues:
            print(f"\n{title}:")
            print(table(["Line", "Record", "Category", "Reason"],
                        [[i.line, i.record_id, i.category, i.message] for i in issues], "rlll"))
    if report.corrected:
        print("\nCorrections applied:")
        for note in report.corrected:
            print(f"  - {note}")
    print(f"\nDetails are logged in {app.settings.log_file}")
    return 0


def cmd_criteria(app: App, args) -> int:
    heading("Ranking criteria (soft preferences)")
    rows = [[key, cls.label, "core" if cls.core else "optional", cls.description]
            for key, cls in Criterion.registry.items()]
    print(table(["Key", "Label", "Type", "How it is scored (0-100, 100 = best)"], rows))
    print("\nDefault weights: " + ", ".join(f"{k}={v:g}" for k, v in app.settings.default_weights.items()))
    heading("Hard constraints")
    print(table(["Key", "Label", "Kind"],
                [[c.key, c.label, "journey scope" if c.scope else "traveller constraint"]
                 for c in CONSTRAINTS.values()]))
    return 0


def cmd_profiles(app: App, args) -> int:
    heading("Traveller profiles")
    for key, p in app.profiles.items():
        print(f"\n[{key}] {p.name}")
        print(f"  {p.description}")
        print(f"  constraints: {p.constraints or 'none'}")
        print(f"  departure: {p.departure}; weights: "
              + ", ".join(f"{k} {v * 100:.0f}%" for k, v in p.weights.items()))
    return 0


# ======================================================================
# Interactive mode
# ======================================================================
def _read(prompt: str, default: str | None) -> tuple[str, bool]:
    """Read one answer; returns (answer or default, True if input has ended)."""
    suffix = f" [{default}]" if default not in (None, "") else ""
    try:
        answer, ended = input(f"{prompt}{suffix}: ").strip(), False
    except EOFError:
        answer, ended = "", True
    return answer or (default or ""), ended


def ask(prompt: str, default: str | None = None) -> str:
    return _read(prompt, default)[0]


def ask_until_valid(prompt: str, default: str | None, check):
    """Ask until ``check(answer)`` succeeds and return its result.

    ``check`` raises a ``BusBidderError`` for an invalid answer; the message is
    shown and the question is asked again, so one typo does not end the
    session.  If the input stream ends (EOF) the error is raised instead of
    looping forever.
    """
    while True:
        answer, ended = _read(prompt, default)
        try:
            return check(answer)
        except BusBidderError as exc:
            if ended:
                raise
            print(f"  {exc} Please try again.")


def _number(text: str, kind, what: str):
    try:
        return kind(text)
    except ValueError as exc:
        raise InvalidRequestError(f"{what} must be a number, got '{text}'.") from exc


def _optional_number(kind, what: str, minimum: float, exclusive: bool = False):
    """Validator for an optional number (empty answer = no limit)."""
    def check(text: str):
        if not text:
            return None
        value = _number(text, kind, what)
        if value < minimum or (exclusive and value == minimum):
            bound = f"greater than {minimum:g}" if exclusive else f"at least {minimum:g}"
            raise InvalidRequestError(f"{what} must be {bound}, got '{text}'.")
        return value
    return check


def _yes_no(text: str) -> bool:
    answer = text.strip().lower()
    if answer in ("y", "yes"):
        return True
    if answer in ("n", "no"):
        return False
    raise InvalidRequestError(f"Please answer y or n, got '{text}'.")


def _seat_count(text: str) -> int:
    seats = _number(text, int, "Number of seats")
    if not 1 <= seats <= MAX_SEATS_PER_BOOKING:
        raise InvalidRequestError(f"Number of seats must be between 1 and {MAX_SEATS_PER_BOOKING}.")
    return seats


def _traveller_name(text: str) -> str:
    if not NAME_RE.match(text):
        raise InvalidRequestError(
            "Traveller name must be 2-60 letters (spaces, hyphens, apostrophes and dots allowed)."
        )
    return text


def cmd_interactive(app: App, args) -> int:
    heading("BusBidder Spain - guided search")
    print("Departures from Madrid. Destinations: " + ", ".join(sorted(d.name for d in app.marketplace.destinations.values())))
    # Each answer is checked as soon as it is given; an invalid answer is
    # explained and the question repeated (see ask_until_valid).
    def destination(text: str) -> str:
        app.marketplace.find_destination(text)
        return text

    def first_date(text: str) -> str:
        parse_date(text)
        return text

    def last_date(text: str) -> str:
        if parse_date(text) < parse_date(ns.from_date):
            raise InvalidRequestError(f"The last travel date must not be before {ns.from_date}.")
        return text

    def profile(text: str) -> str | None:
        if not text:
            return None
        app._profile(text)  # raises for an unknown profile key
        return text

    def departure(text: str) -> str:
        DeparturePreference.parse(text)
        return text

    def weights(text: str) -> str:
        validate_weights(parse_weights(text))
        return text

    ns = argparse.Namespace(
        to=None, from_date=None, to_date=None, profile=None, budget=None, max_transfers=None,
        luggage=None, max_duration=None, changeable=False, refundable=False, depart=None, weights=None,
        seats=1, top=10, show_removed=0, export=None, book=True,
    )
    ns.to = ask_until_valid("Destination", "Malaga", destination)
    ns.from_date = ask_until_valid("First travel date (YYYY-MM-DD)", "2027-07-12", first_date)
    ns.to_date = ask_until_valid("Last travel date (YYYY-MM-DD)", ns.from_date, last_date)
    print("Profiles: " + ", ".join(f"{k} ({p.name})" for k, p in app.profiles.items()))
    ns.profile = ask_until_valid("Profile to start from (Enter for custom)", "", profile)
    if not ns.profile:
        ns.budget = ask_until_valid("Maximum price per passenger in EUR (Enter = no limit)", "",
                                    _optional_number(float, "Maximum price", 0, exclusive=True))
        ns.max_transfers = ask_until_valid("Maximum transfers (Enter = no limit)", "",
                                           _optional_number(int, "Maximum transfers", 0))
        ns.changeable = ask_until_valid("Changeable ticket required? (y/n)", "n", _yes_no)
        ns.refundable = ask_until_valid("Refundable ticket required? (y/n)", "n", _yes_no)
        ns.depart = ask_until_valid("Preferred departure (morning/afternoon/evening/night/any or HH:MM)",
                                    "any", departure)
        ns.weights = ask_until_valid("Weights", "price=40,duration=25,departure=15,flexibility=20", weights)
    ns.seats = ask_until_valid("Number of seats", "1", _seat_count)
    return cmd_search(app, ns)


# ======================================================================
# Argument parsing
# ======================================================================
def _add_journey_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--to", required=True, help="destination name or code, e.g. Malaga or MAL")
    p.add_argument("--from-date", required=True, help="first travel date, YYYY-MM-DD")
    p.add_argument("--to-date", help="last travel date, YYYY-MM-DD (default: same as --from-date)")
    p.add_argument("--seats", type=int, default=1, help="number of seats needed (default 1)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python main.py",
        description="BusBidder Spain - filter and rank bus offers from Madrid, then book one (simulated).",
    )
    parser.add_argument("--config", help="path to settings.json (default: config/settings.json)")
    parser.add_argument("--version", action="version", version=f"BusBidder Spain {__version__}")
    sub = parser.add_subparsers(dest="command")

    s = sub.add_parser("search", help="filter and rank offers for one traveller")
    _add_journey_args(s)
    s.add_argument("--profile", help="start from a traveller profile (see 'profiles')")
    s.add_argument("--budget", type=float, help="HARD: maximum price per passenger in EUR")
    s.add_argument("--max-transfers", type=int, help="HARD: maximum number of transfers")
    s.add_argument("--changeable", action="store_true", help="HARD: ticket must be changeable")
    s.add_argument("--refundable", action="store_true", help="HARD: ticket must be refundable")
    s.add_argument("--luggage", type=int, help="HARD: minimum luggage allowance in kg")
    s.add_argument("--max-duration", help="HARD: maximum journey time, e.g. 480, 8h or 7h30")
    s.add_argument("--depart", help="SOFT: preferred departure: morning, afternoon, evening, night, any or HH:MM")
    s.add_argument("--weights", help="SOFT: e.g. price=40,duration=25,departure=15,flexibility=20 (must total 100)")
    s.add_argument("--top", type=int, default=10, help="number of ranked offers to show (default 10)")
    s.add_argument("--show-removed", type=int, nargs="?", const=25, default=0, metavar="N",
                   help="also list up to N removed offers with reasons (default 25)")
    s.add_argument("--export", help="write the full ranking to this CSV file")
    s.add_argument("--book", action="store_true", help="choose an offer to book after viewing the ranking")
    s.set_defaults(func=cmd_search)

    c = sub.add_parser("compare", help="rank the same offers for several traveller profiles")
    _add_journey_args(c)
    c.add_argument("--profiles", help="comma-separated profile keys (default: all)")
    c.add_argument("--top", type=int, default=3, help="offers to show per profile (default 3)")
    c.add_argument("--explain", action="store_true", help="print the explanation for each profile")
    c.add_argument("--export-dir", help="write each profile's ranking as CSV into this folder")
    c.set_defaults(func=cmd_compare)

    b = sub.add_parser("book", help="book an offer directly by offer id")
    b.add_argument("--offer", required=True, help="offer id, e.g. COS-MAL-0020")
    b.add_argument("--name", required=True, help="traveller name")
    b.add_argument("--seats", type=int, default=1)
    b.set_defaults(func=cmd_book)

    sub.add_parser("bookings", help="list all bookings").set_defaults(func=cmd_bookings)
    sh = sub.add_parser("show", help="show one booking")
    sh.add_argument("reference")
    sh.set_defaults(func=cmd_show)
    ca = sub.add_parser("cancel", help="cancel a booking (simulated refund / travel credit)")
    ca.add_argument("reference")
    ca.set_defaults(func=cmd_cancel)
    sub.add_parser("validate", help="show the data validation report").set_defaults(func=cmd_validate)
    sub.add_parser("criteria", help="list ranking criteria and hard constraints").set_defaults(func=cmd_criteria)
    sub.add_parser("profiles", help="list traveller profiles").set_defaults(func=cmd_profiles)
    sub.add_parser("interactive", help="guided search and booking").set_defaults(func=cmd_interactive)
    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        args.func = cmd_interactive
    try:
        settings = load_settings(args.config)
        configure_logging(settings.log_file)
        log.info("Command started: %s", " ".join(argv if argv is not None else sys.argv[1:]) or "interactive")
        app = App(settings)
        return args.func(app, args)
    except BusBidderError as exc:
        log.error("%s: %s", type(exc).__name__, exc)
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130
