"""Generate the simulated offer dataset ``data/offers.csv``.

The generator is deterministic: it uses a fixed random seed, so running it
again produces exactly the same file.  The committed ``data/offers.csv`` was
produced with:

    python scripts/generate_offers.py

Each of the five companies serves all fifteen destinations (75
company-destination combinations) on every day from 10 to 16 July 2027, with
one or more departures per day, and every departure is sold as a "basic" and a
"flex" fare.  Company behaviour follows the profiles in the specification:

* SolRuta (SOL)        - cheapest, daytime only, more transfers, rigid fares
* Camino Express (CAM) - fastest, always direct, more expensive
* CostaLink (COS)      - cheap and frequent to coastal cities, transfers inland
* VerdeBus (VER)       - low CO2 per passenger, high rating, mid price
* NocheVia (NOC)       - late-evening/overnight departures, slower

After the generated rows, a small block of deliberately invalid records is
appended so that every validation rule is exercised on the real data file.
"""

from __future__ import annotations

import csv
import random
from datetime import date, datetime, timedelta
from pathlib import Path

SEED = 4420
FIRST_DAY = date(2027, 7, 10)
DAYS = 7
OUT = Path(__file__).resolve().parent.parent / "data" / "offers.csv"

COLUMNS = [
    "offer_id", "company_id", "destination_id", "departure", "arrival", "duration_min",
    "price_eur", "transfers", "luggage_kg", "fare_type", "changeable", "change_fee_eur",
    "refundable", "seats_available", "rating", "co2_kg",
]

DESTINATIONS = {  # id: (distance_km, coastal, major_city)
    "BAR": (620, True, True), "VAL": (355, True, True), "SEV": (530, False, True),
    "MAL": (530, True, True), "GRA": (420, False, False), "COR": (400, False, False),
    "BIL": (400, True, True), "SSE": (450, True, False), "ZAR": (320, False, True),
    "ALI": (420, True, False), "MUR": (400, False, False), "SAL": (215, False, False),
    "TOL": (75, False, False), "SCQ": (600, False, False), "CAD": (650, True, False),
}

# Fare rules: (changeable, change_fee, refundable, luggage_kg, price multiplier)
COMPANIES = {
    "SOL": dict(rate=0.050, speed=68, slots=["07:00", "09:30", "12:00", "15:00", "17:00"],
                per_day=(2, 2), rating=3.6, co2=0.032,
                basic=(False, 0.0, False, 15, 1.00), flex=(True, 5.0, False, 20, 1.18)),
    "CAM": dict(rate=0.080, speed=85, slots=["07:30", "11:00", "15:30", "19:00"],
                per_day=(1, 3), rating=4.4, co2=0.030,
                basic=(True, 10.0, False, 20, 1.00), flex=(True, 0.0, True, 30, 1.30)),
    "COS": dict(rate=0.055, speed=72, slots=["06:45", "10:15", "14:00", "18:30"],
                per_day=(1, 3), rating=4.0, co2=0.031,
                basic=(False, 0.0, False, 20, 1.00), flex=(True, 0.0, False, 25, 1.22)),
    "VER": dict(rate=0.070, speed=74, slots=["08:00", "10:30", "13:30", "16:00"],
                per_day=(2, 2), rating=4.5, co2=0.012,
                basic=(True, 5.0, False, 20, 1.00), flex=(True, 0.0, True, 25, 1.25)),
    "NOC": dict(rate=0.058, speed=62, slots=["21:30", "22:45", "23:55"],
                per_day=(1, 2), rating=3.8, co2=0.029,
                basic=(False, 0.0, False, 20, 1.00), flex=(True, 0.0, True, 20, 1.20)),
}

TRANSFER_MINUTES = 40


def _transfers(rng: random.Random, company: str, distance: int, coastal: bool, major: bool) -> int:
    if company == "CAM" or distance < 150:
        return 0
    if company == "SOL":
        return rng.choices([0, 1, 2], weights=[3, 5, 2] if distance > 450 else [6, 4, 0])[0]
    if company == "COS":
        return 0 if coastal else rng.choice([1, 1, 2])
    if company == "VER":
        return 1 if distance > 500 and rng.random() < 0.4 else 0
    return rng.choice([0, 0, 1])  # NOC


def _departures_per_day(rng: random.Random, company: str, coastal: bool, major: bool) -> int:
    low, high = COMPANIES[company]["per_day"]
    if company == "CAM":
        return rng.randint(2, 3) if major else 1
    if company == "COS":
        return rng.randint(2, 3) if coastal else 1
    return rng.randint(low, high)


def _round_to(value: float, step: float) -> float:
    return round(round(value / step) * step, 2)


def generate(rng: random.Random) -> list[dict]:
    rows: list[dict] = []
    for company, cfg in COMPANIES.items():
        for dest, (distance, coastal, major) in DESTINATIONS.items():
            seq = 0
            for day_index in range(DAYS):
                day = FIRST_DAY + timedelta(days=day_index)
                count = _departures_per_day(rng, company, coastal, major)
                slots = sorted(rng.sample(cfg["slots"], min(count, len(cfg["slots"]))))
                for slot in slots:
                    hh, mm = map(int, slot.split(":"))
                    jitter = rng.choice([-20, -10, 0, 0, 10, 20])
                    departure = datetime(day.year, day.month, day.day, hh, mm) + timedelta(minutes=jitter)
                    if departure.date() != day:  # keep 23:55 + jitter on the same service day
                        departure = datetime(day.year, day.month, day.day, 23, 50)
                    transfers = _transfers(rng, company, distance, coastal, major)
                    speed = cfg["speed"] * (0.9 if company == "COS" and not coastal else 1.0)
                    minutes = distance / speed * 60 + transfers * TRANSFER_MINUTES
                    minutes = int(_round_to(minutes * rng.uniform(0.95, 1.08), 5))
                    arrival = departure + timedelta(minutes=minutes)
                    rate = cfg["rate"] * (1.25 if company == "COS" and not coastal else 1.0)
                    base = distance * rate * rng.uniform(0.92, 1.10)
                    if day.weekday() in (4, 6):  # Friday and Sunday demand
                        base *= 1.08
                    base = max(base, 6.0)
                    rating = None if rng.random() < 0.04 else round(
                        min(5.0, max(1.0, rng.gauss(cfg["rating"], 0.2))), 1)
                    co2 = None if rng.random() < 0.05 else round(
                        distance * cfg["co2"] * (1 + 0.15 * transfers), 1)
                    for fare_type in ("basic", "flex"):
                        changeable, fee, refundable, luggage, mult = cfg[fare_type]
                        seq += 1
                        roll = rng.random()
                        seats = 0 if roll < 0.06 else rng.randint(1, 3) if roll < 0.16 else rng.randint(4, 45)
                        rows.append({
                            "offer_id": f"{company}-{dest}-{seq:04d}",
                            "company_id": company,
                            "destination_id": dest,
                            "departure": departure.strftime("%Y-%m-%dT%H:%M"),
                            "arrival": arrival.strftime("%Y-%m-%dT%H:%M"),
                            "duration_min": minutes,
                            "price_eur": f"{_round_to(base * mult, 0.05):.2f}",
                            "transfers": transfers,
                            "luggage_kg": luggage,
                            "fare_type": fare_type,
                            "changeable": "true" if changeable else "false",
                            "change_fee_eur": f"{fee:.2f}",
                            "refundable": "true" if refundable else "false",
                            "seats_available": seats,
                            "rating": "" if rating is None else f"{rating:.1f}",
                            "co2_kg": "" if co2 is None else f"{co2:.1f}",
                        })
    return rows


def _row(offer_id, company, dest, dep, arr, dur, price, transfers=0, luggage=20, fare="basic",
         changeable="false", fee="0.00", refundable="false", seats=10, rating="4.0", co2="10.0"):
    return dict(zip(COLUMNS, [offer_id, company, dest, dep, arr, dur, price, transfers, luggage,
                              fare, changeable, fee, refundable, seats, rating, co2]))


# Deliberately invalid or messy records.  Each one exercises one validation rule.
ANOMALIES = [
    # ---- rejected
    _row("SOL-MAL-9001", "SOL", "MAL", "2027-07-12T08:00", "2027-07-12T16:00", 480, "-12.50"),        # NEGATIVE_PRICE
    _row("CAM-IBZ-9001", "CAM", "IBZ", "2027-07-12T08:00", "2027-07-12T14:00", 360, "40.00"),         # UNKNOWN_DESTINATION
    _row("XYZ-VAL-9001", "XYZ", "VAL", "2027-07-12T08:00", "2027-07-12T12:30", 270, "20.00"),         # UNKNOWN_COMPANY
    _row("COS-MAL-9001", "COS", "MAL", "2027-02-30T08:00", "2027-02-30T15:00", 420, "30.00"),         # INVALID_DATE
    _row("VER-MAL-9001", "VER", "MAL", "2027-07-13T15:00", "2027-07-13T09:00", 360, "38.00"),         # ARRIVAL_BEFORE_DEPARTURE
    _row("NOC-SEV-9001", "NOC", "SEV", "2027-07-12T22:00", "2027-07-13T06:30", 510, "31.00", rating="6.2"),  # RATING_OUT_OF_SCALE
    _row("SOL-GRA-9001", "SOL", "GRA", "2027-07-12T09:00", "2027-07-12T15:20", 380, "21.00", seats=-3),     # NEGATIVE_SEATS
    _row("CAM-BAR-9001", "CAM", "BAR", "2027-07-12T08:00", "2027-07-12T15:20", 440, ""),              # MISSING_FIELD
    _row("SOL_MAL_77", "SOL", "MAL", "2027-07-12T08:00", "2027-07-12T16:00", 480, "25.00"),           # MALFORMED_ID
    _row("CAM-MAL-0001", "CAM", "MAL", "2027-07-10T07:30", "2027-07-10T13:45", 375, "39.00"),         # DUPLICATE_ID
    _row("VER-ZAR-9001", "VER", "ZAR", "2027-07-12T08:00", "2027-07-12T12:20", 260, "free"),          # INVALID_NUMBER
    # ---- placed aside for review
    _row("VER-BIL-9001", "VER", "BIL", "2027-07-12T08:00", "2027-07-12T13:30", 240, "29.00"),         # DURATION_MISMATCH
    _row("COS-ALI-9001", "SOL", "ALI", "2027-07-12T08:00", "2027-07-12T14:00", 360, "22.00"),         # ID_MISMATCH
    _row("CAM-BAR-9002", "CAM", "BAR", "2027-07-12T08:00", "2027-07-12T10:00", 120, "49.00"),         # IMPLAUSIBLE_SPEED
    _row("SOL-TOL-9001", "SOL", "TOL", "2027-07-12T09:00", "2027-07-12T10:15", 75, "6.50", fee="5.00"),  # FARE_INCONSISTENT
    # ---- corrected and accepted (decimal comma, lower-case code, spaces, "yes"/"no")
    _row("COS-VAL-9001", "COS", "val", "2027-07-14T10:00", "2027-07-14T15:05", 305, "19,50",
         fare="flex", changeable=" yes", refundable="no", seats=12, rating="4.1", co2="11.0"),
]


def main() -> None:
    rng = random.Random(SEED)
    rows = generate(rng) + ANOMALIES
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} rows ({len(ANOMALIES)} deliberate anomalies) to {OUT}")


if __name__ == "__main__":
    main()
