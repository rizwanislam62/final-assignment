"""Loading companies, destinations and offers from CSV files.

Invalid-record policy (explained in the report):

* **Corrected** - harmless formatting problems whose intended value is
  unambiguous (surrounding spaces, lower-case codes, a decimal comma in a price,
  "yes"/"no" instead of true/false).  The record is kept and the correction is
  logged as a WARNING.
* **Rejected** - impossible or unusable values (negative price, unknown
  destination, invalid date, arrival before departure, rating outside the
  scale, malformed or duplicate identifier, missing required field ...).
  The record is excluded and written to ``output/rejected_offers.csv``.
* **Placed aside for review** - values that are each plausible on their own
  but contradict each other (stated duration disagrees with the timestamps,
  offer-id prefix disagrees with the company column, implausible average
  speed, change fee on a non-changeable fare).  The program cannot know which
  value is wrong, so the record is excluded from ranking and written to
  ``output/review_queue.csv`` for a human to resolve.
* **Sold out** - an offer with 0 seats is *valid* (it is what every offer
  becomes after its last seat is booked).  It is loaded, counted and logged,
  and later removed by the seat-availability hard constraint.
"""

from __future__ import annotations

import csv
import unicodedata
from dataclasses import dataclass, field
from difflib import get_close_matches
from pathlib import Path

from .config import Settings
from .exceptions import (
    DataFileError,
    RecordNeedsReviewError,
    RecordValidationError,
    UnknownDestinationError,
)
from .logging_setup import get_logger
from .models import Company, Destination, FareConditions, Offer
from .validation import (
    FARE_TYPES,
    clean_text,
    parse_bool,
    parse_code,
    parse_datetime,
    parse_int,
    parse_money,
    parse_offer_id,
    parse_optional_decimal,
    require,
)

log = get_logger("loader")

COMPANY_COLUMNS = ["company_id", "name", "profile"]
DESTINATION_COLUMNS = ["destination_id", "name", "region", "distance_km", "coastal"]
OFFER_COLUMNS = [
    "offer_id", "company_id", "destination_id", "departure", "arrival", "duration_min",
    "price_eur", "transfers", "luggage_kg", "fare_type", "changeable", "change_fee_eur",
    "refundable", "seats_available", "rating", "co2_kg",
]


@dataclass
class RecordIssue:
    """A record that was rejected or placed aside, with the reason."""

    line: int
    record_id: str
    category: str
    message: str
    raw: dict


@dataclass
class LoadReport:
    offers_read: int = 0
    accepted: int = 0
    corrected_records: int = 0
    corrected: list[str] = field(default_factory=list)
    rejected: list[RecordIssue] = field(default_factory=list)
    review: list[RecordIssue] = field(default_factory=list)
    sold_out: int = 0

    def category_counts(self, issues: list[RecordIssue]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for issue in issues:
            counts[issue.category] = counts.get(issue.category, 0) + 1
        return dict(sorted(counts.items()))


# ---------------------------------------------------------------- csv reading
def _read_csv(path: Path, required_columns: list[str]) -> list[tuple[int, dict]]:
    """Return ``(line_number, row)`` pairs; fail loudly on a missing file or header."""
    path = Path(path)
    if not path.is_file():
        raise DataFileError(f"Data file not found: {path}")
    try:
        with path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            header = [clean_text(h) for h in (reader.fieldnames or [])]
            missing = [c for c in required_columns if c not in header]
            if missing:
                raise DataFileError(f"{path.name} is missing required column(s): {', '.join(missing)}")
            reader.fieldnames = header
            # Line 1 is the header, so the first data row is line 2.
            return [(i + 2, row) for i, row in enumerate(reader)]
    except UnicodeDecodeError as exc:
        raise DataFileError(f"{path} is not UTF-8 text: {exc}") from exc
    except csv.Error as exc:
        raise DataFileError(f"{path} is not a valid CSV file: {exc}") from exc


def load_companies(path: Path) -> dict[str, Company]:
    companies: dict[str, Company] = {}
    for line, row in _read_csv(path, COMPANY_COLUMNS):
        try:
            company_id = parse_code(require(row, "company_id"), "company_id", [])
            if company_id in companies:
                raise RecordValidationError("DUPLICATE_ID", f"duplicate company_id {company_id}")
            companies[company_id] = Company(company_id, require(row, "name"), clean_text(row.get("profile")))
        except RecordValidationError as exc:
            log.warning("companies line %d rejected [%s]: %s", line, exc.category, exc)
    if not companies:
        raise DataFileError(f"No valid companies found in {path}")
    log.info("Loaded %d companies from %s", len(companies), path)
    return companies


def load_destinations(path: Path) -> dict[str, Destination]:
    destinations: dict[str, Destination] = {}
    for line, row in _read_csv(path, DESTINATION_COLUMNS):
        try:
            dest_id = parse_code(require(row, "destination_id"), "destination_id", [])
            if dest_id in destinations:
                raise RecordValidationError("DUPLICATE_ID", f"duplicate destination_id {dest_id}")
            distance = parse_int(require(row, "distance_km"), "distance_km")
            if distance <= 0:
                raise RecordValidationError("INVALID_NUMBER", "distance_km must be positive")
            destinations[dest_id] = Destination(
                dest_id,
                require(row, "name"),
                clean_text(row.get("region")),
                distance,
                parse_bool(require(row, "coastal"), "coastal"),
            )
        except RecordValidationError as exc:
            log.warning("destinations line %d rejected [%s]: %s", line, exc.category, exc)
    if not destinations:
        raise DataFileError(f"No valid destinations found in {path}")
    log.info("Loaded %d destinations from %s", len(destinations), path)
    return destinations


# --------------------------------------------------------------- offer parsing
def parse_offer(
    row: dict,
    companies: dict[str, Company],
    destinations: dict[str, Destination],
    settings: Settings,
    corrections: list[str],
) -> Offer:
    """Convert one CSV row into an ``Offer`` or raise a validation error."""
    for key, value in row.items():
        if key and value is not None and value != value.strip():
            corrections.append(f"surrounding spaces removed from {key}")

    raw_id = require(row, "offer_id")
    offer_id = raw_id.upper()
    if offer_id != raw_id:
        corrections.append(f"offer_id '{raw_id}' upper-cased")
    id_match = parse_offer_id(offer_id)

    company_id = parse_code(require(row, "company_id"), "company_id", corrections)
    if company_id not in companies:
        raise RecordValidationError("UNKNOWN_COMPANY", f"company '{company_id}' does not exist", "company_id")
    destination_id = parse_code(require(row, "destination_id"), "destination_id", corrections)
    if destination_id not in destinations:
        raise RecordValidationError(
            "UNKNOWN_DESTINATION", f"destination '{destination_id}' is not served", "destination_id"
        )

    departure = parse_datetime(require(row, "departure"), "departure")
    arrival = parse_datetime(require(row, "arrival"), "arrival")
    if arrival <= departure:
        raise RecordValidationError(
            "ARRIVAL_BEFORE_DEPARTURE", f"arrival {arrival:%Y-%m-%d %H:%M} is not after departure "
            f"{departure:%Y-%m-%d %H:%M}", "arrival"
        )

    price = parse_money(require(row, "price_eur"), "price_eur", corrections)
    if price < 0:
        raise RecordValidationError("NEGATIVE_PRICE", f"price {price:.2f} is negative", "price_eur")
    if price == 0:
        raise RecordValidationError("ZERO_PRICE", "price is zero", "price_eur")

    transfers = parse_int(require(row, "transfers"), "transfers")
    if transfers < 0:
        raise RecordValidationError("NEGATIVE_VALUE", "transfers cannot be negative", "transfers")
    luggage = parse_int(require(row, "luggage_kg"), "luggage_kg")
    if luggage < 0:
        raise RecordValidationError("NEGATIVE_VALUE", "luggage_kg cannot be negative", "luggage_kg")
    seats = parse_int(require(row, "seats_available"), "seats_available")
    if seats < 0:
        raise RecordValidationError("NEGATIVE_SEATS", f"seats_available {seats} is negative", "seats_available")

    fare_type = require(row, "fare_type").lower()
    if fare_type not in FARE_TYPES:
        raise RecordValidationError("INVALID_FARE_TYPE", f"fare_type '{fare_type}' is unknown", "fare_type")
    changeable = parse_bool(require(row, "changeable"), "changeable")
    refundable = parse_bool(require(row, "refundable"), "refundable")
    change_fee = parse_money(require(row, "change_fee_eur"), "change_fee_eur", corrections)
    if change_fee < 0:
        raise RecordValidationError("NEGATIVE_VALUE", "change_fee_eur cannot be negative", "change_fee_eur")

    rating = parse_optional_decimal(clean_text(row.get("rating")), "rating")
    if rating is not None and not settings.rating_min <= rating <= settings.rating_max:
        raise RecordValidationError(
            "RATING_OUT_OF_SCALE",
            f"rating {rating} is outside {settings.rating_min}-{settings.rating_max}", "rating",
        )
    co2 = parse_optional_decimal(clean_text(row.get("co2_kg")), "co2_kg")
    if co2 is not None and co2 < 0:
        raise RecordValidationError("NEGATIVE_VALUE", "co2_kg cannot be negative", "co2_kg")

    stated_duration = parse_int(require(row, "duration_min"), "duration_min")

    # ---- cross-field consistency checks -> review queue, not silent fixes
    if id_match["company"] != company_id or id_match["destination"] != destination_id:
        raise RecordNeedsReviewError(
            "ID_MISMATCH",
            f"offer_id prefix {id_match['company']}-{id_match['destination']} disagrees with "
            f"company_id {company_id} / destination_id {destination_id}",
        )
    actual_duration = int((arrival - departure).total_seconds() // 60)
    if abs(actual_duration - stated_duration) > settings.duration_tolerance_min:
        raise RecordNeedsReviewError(
            "DURATION_MISMATCH",
            f"duration_min {stated_duration} disagrees with timestamps ({actual_duration} min)",
        )
    destination = destinations[destination_id]
    speed = destination.distance_km / (actual_duration / 60)
    if not settings.min_plausible_speed_kmh <= speed <= settings.max_plausible_speed_kmh:
        raise RecordNeedsReviewError(
            "IMPLAUSIBLE_SPEED",
            f"{destination.distance_km} km in {actual_duration} min is {speed:.0f} km/h",
        )
    if not changeable and change_fee > 0:
        raise RecordNeedsReviewError(
            "FARE_INCONSISTENT", f"change fee {change_fee:.2f} given for a non-changeable fare"
        )

    return Offer(
        offer_id=offer_id,
        company=companies[company_id],
        destination=destination,
        departure=departure,
        arrival=arrival,
        price_eur=round(price, 2),
        transfers=transfers,
        fare=FareConditions(fare_type, changeable, round(change_fee, 2), refundable, luggage),
        seats_available=seats,
        rating=rating,
        co2_kg=co2,
    )


def load_offers(
    path: Path,
    companies: dict[str, Company],
    destinations: dict[str, Destination],
    settings: Settings,
) -> tuple[list[Offer], LoadReport]:
    report = LoadReport()
    offers: list[Offer] = []
    seen_ids: set[str] = set()
    for line, row in _read_csv(path, OFFER_COLUMNS):
        report.offers_read += 1
        record_id = clean_text(row.get("offer_id")) or f"<line {line}>"
        corrections: list[str] = []
        try:
            offer = parse_offer(row, companies, destinations, settings, corrections)
            if offer.offer_id in seen_ids:
                raise RecordValidationError("DUPLICATE_ID", f"offer_id {offer.offer_id} appears more than once")
        except RecordNeedsReviewError as exc:
            report.review.append(RecordIssue(line, record_id, exc.category, str(exc), row))
            log.warning("offers line %d (%s) placed in review queue [%s]: %s", line, record_id, exc.category, exc)
            continue
        except RecordValidationError as exc:
            report.rejected.append(RecordIssue(line, record_id, exc.category, str(exc), row))
            log.warning("offers line %d (%s) rejected [%s]: %s", line, record_id, exc.category, exc)
            continue
        seen_ids.add(offer.offer_id)
        offers.append(offer)
        report.accepted += 1
        if corrections:
            report.corrected_records += 1
        for note in dict.fromkeys(corrections):  # de-duplicate, keep order
            report.corrected.append(f"line {line} ({offer.offer_id}): {note}")
            log.warning("offers line %d (%s) corrected: %s", line, offer.offer_id, note)
        if offer.seats_available == 0:
            report.sold_out += 1
            log.info("offers line %d (%s) is sold out (0 seats)", line, offer.offer_id)
    log.info(
        "Offers file %s: %d read, %d accepted (%d corrected), %d rejected, %d placed for review, %d sold out",
        path, report.offers_read, report.accepted, report.corrected_records,
        len(report.rejected), len(report.review), report.sold_out,
    )
    return offers, report


def write_issue_file(path: Path, issues: list[RecordIssue]) -> None:
    """Write rejected / review records with their reason so they can be inspected."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["line", "record_id", "category", "message", *OFFER_COLUMNS])
        for issue in issues:
            writer.writerow(
                [issue.line, issue.record_id, issue.category, issue.message,
                 *[issue.raw.get(c, "") for c in OFFER_COLUMNS]]
            )


# ---------------------------------------------------------------- marketplace
def _fold(text: str) -> str:
    """Lower-case and strip accents so 'Málaga', 'MALAGA' and 'malaga' match."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).lower().strip()


class Marketplace:
    """All validated reference data and offers, loaded once per run."""

    def __init__(self, companies, destinations, offers, report: LoadReport):
        self.companies: dict[str, Company] = companies
        self.destinations: dict[str, Destination] = destinations
        self.offers: list[Offer] = offers
        self.report = report
        self._by_id = {o.offer_id: o for o in offers}

    @classmethod
    def load(cls, settings: Settings, write_issue_files: bool = True) -> "Marketplace":
        companies = load_companies(settings.companies_file)
        destinations = load_destinations(settings.destinations_file)
        offers, report = load_offers(settings.offers_file, companies, destinations, settings)
        if write_issue_files:
            write_issue_file(settings.output_dir / "rejected_offers.csv", report.rejected)
            write_issue_file(settings.output_dir / "review_queue.csv", report.review)
        return cls(companies, destinations, offers, report)

    @property
    def date_range(self) -> tuple | None:
        """First and last departure date in the data (None if there are no offers)."""
        if not self.offers:
            return None
        dates = [o.travel_date for o in self.offers]
        return min(dates), max(dates)

    def offer(self, offer_id: str) -> Offer | None:
        return self._by_id.get(offer_id.strip().upper())

    def find_destination(self, text: str) -> Destination:
        """Look up a destination by code or by name, ignoring case and accents."""
        wanted = _fold(text)
        for dest in self.destinations.values():
            if wanted in (_fold(dest.name), _fold(dest.destination_id)):
                return dest
        names = [d.name for d in self.destinations.values()]
        folded = {_fold(n): n for n in names}
        suggestions = [folded[m] for m in get_close_matches(wanted, list(folded), n=3, cutoff=0.6)]
        hint = f" Did you mean: {', '.join(suggestions)}?" if suggestions else ""
        raise UnknownDestinationError(
            f"'{text}' is not a BusBidder destination.{hint} Served destinations: {', '.join(sorted(names))}."
        )
