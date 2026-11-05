"""Shared fixtures: isolated settings, small CSV files and object builders."""

from __future__ import annotations

import csv
import json
import shutil
import sys
from datetime import date, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from busbidder.config import Settings, load_settings  # noqa: E402
from busbidder.loader import OFFER_COLUMNS  # noqa: E402
from busbidder.models import (  # noqa: E402
    Company, DeparturePreference, Destination, FareConditions, Offer, TravelRequest,
)

COMPANY = Company("SOL", "SolRuta", "Low-cost daytime services")
COMPANY_B = Company("CAM", "Camino Express", "Fast direct connections")
MALAGA = Destination("MAL", "Malaga", "Andalusia", 530, True)
VALENCIA = Destination("VAL", "Valencia", "Valencian Community", 355, True)


@pytest.fixture
def settings(tmp_path) -> Settings:
    """Real settings, but every writable path points into a temporary folder."""
    s = load_settings(ROOT / "config" / "settings.json")
    data = tmp_path / "data"
    data.mkdir()
    shutil.copy(ROOT / "data" / "companies.csv", data / "companies.csv")
    shutil.copy(ROOT / "data" / "destinations.csv", data / "destinations.csv")
    shutil.copy(ROOT / "data" / "offers.csv", data / "offers.csv")
    shutil.copy(ROOT / "config" / "profiles.json", tmp_path / "profiles.json")
    s.companies_file = data / "companies.csv"
    s.destinations_file = data / "destinations.csv"
    s.offers_file = data / "offers.csv"
    s.profiles_file = tmp_path / "profiles.json"
    s.bookings_file = tmp_path / "state" / "bookings.json"
    s.log_file = tmp_path / "logs" / "test.log"
    s.output_dir = tmp_path / "output"
    return s


@pytest.fixture
def settings_file(tmp_path, settings) -> Path:
    """A settings.json on disk that matches the ``settings`` fixture (for CLI tests)."""
    raw = json.loads((ROOT / "config" / "settings.json").read_text(encoding="utf-8"))
    raw["files"] = {
        "companies": str(settings.companies_file),
        "destinations": str(settings.destinations_file),
        "offers": str(settings.offers_file),
        "profiles": str(settings.profiles_file),
        "bookings": str(settings.bookings_file),
        "log": str(settings.log_file),
        "output_dir": str(settings.output_dir),
    }
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    return path


def valid_row(**overrides) -> dict:
    """A CSV row (strings) that passes validation; override fields to break it."""
    row = {
        "offer_id": "SOL-MAL-0001", "company_id": "SOL", "destination_id": "MAL",
        "departure": "2027-07-12T08:00", "arrival": "2027-07-12T15:00", "duration_min": "420",
        "price_eur": "30.00", "transfers": "0", "luggage_kg": "20", "fare_type": "basic",
        "changeable": "false", "change_fee_eur": "0.00", "refundable": "false",
        "seats_available": "10", "rating": "4.0", "co2_kg": "15.0",
    }
    row.update({k: str(v) for k, v in overrides.items()})
    return row


def write_offers(path: Path, rows: list[dict]) -> Path:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=OFFER_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    return path


def make_offer(
    offer_id="SOL-MAL-0001", price=30.0, dep="2027-07-12T08:00", minutes=420, transfers=0,
    changeable=False, fee=0.0, refundable=False, luggage=20, seats=10, rating=4.0, co2=15.0,
    company=COMPANY, destination=MALAGA, fare_type="basic",
) -> Offer:
    departure = datetime.strptime(dep, "%Y-%m-%dT%H:%M")
    from datetime import timedelta
    return Offer(
        offer_id=offer_id, company=company, destination=destination,
        departure=departure, arrival=departure + timedelta(minutes=minutes),
        price_eur=price, transfers=transfers,
        fare=FareConditions(fare_type, changeable, fee, refundable, luggage),
        seats_available=seats, rating=rating, co2_kg=co2,
    )


def make_request(**overrides) -> TravelRequest:
    params = dict(
        destination=MALAGA, date_from=date(2027, 7, 12), date_to=date(2027, 7, 14),
        preferred_departure=DeparturePreference.parse("morning"),
        weights={"price": 0.4, "duration": 0.25, "departure": 0.15, "flexibility": 0.2},
    )
    params.update(overrides)
    return TravelRequest(**params)
