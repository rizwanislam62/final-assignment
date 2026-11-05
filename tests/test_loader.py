"""Task 2 - loading and validating data (normal, invalid, missing and boundary cases)."""

from __future__ import annotations

import pytest

from conftest import valid_row, write_offers
from busbidder.exceptions import DataFileError, UnknownDestinationError
from busbidder.loader import Marketplace, load_companies, load_destinations, load_offers


def load_rows(settings, rows):
    path = write_offers(settings.offers_file, rows)
    companies = load_companies(settings.companies_file)
    destinations = load_destinations(settings.destinations_file)
    return load_offers(path, companies, destinations, settings)


# ------------------------------------------------------------------ normal
def test_valid_row_is_accepted_with_correct_types(settings):
    offers, report = load_rows(settings, [valid_row()])
    assert report.accepted == 1 and not report.rejected and not report.review
    o = offers[0]
    assert o.price_eur == 30.0 and isinstance(o.price_eur, float)
    assert o.transfers == 0 and o.duration_min == 420
    assert o.company.name == "SolRuta" and o.destination.name == "Malaga"
    assert o.fare.changeable is False and o.rating == 4.0


def test_overnight_offer_is_valid(settings):
    row = valid_row(departure="2027-07-12T23:00", arrival="2027-07-13T07:00", duration_min="480")
    offers, report = load_rows(settings, [row])
    assert report.accepted == 1 and offers[0].overnight


# ----------------------------------------------------------------- rejected
@pytest.mark.parametrize("override, category", [
    ({"price_eur": "-5.00"}, "NEGATIVE_PRICE"),
    ({"price_eur": "0"}, "ZERO_PRICE"),
    ({"price_eur": "cheap"}, "INVALID_NUMBER"),
    ({"destination_id": "IBZ", "offer_id": "SOL-IBZ-0001"}, "UNKNOWN_DESTINATION"),
    ({"company_id": "XYZ", "offer_id": "XYZ-MAL-0001"}, "UNKNOWN_COMPANY"),
    ({"departure": "2027-02-30T08:00"}, "INVALID_DATE"),
    ({"departure": "12/07/2027 08:00"}, "INVALID_DATE"),
    ({"departure": "2027-07-12T25:00"}, "INVALID_DATE"),
    ({"arrival": "2027-07-12T07:00"}, "ARRIVAL_BEFORE_DEPARTURE"),
    ({"arrival": "2027-07-12T08:00"}, "ARRIVAL_BEFORE_DEPARTURE"),   # equal is impossible too
    ({"rating": "5.1"}, "RATING_OUT_OF_SCALE"),
    ({"rating": "0.9"}, "RATING_OUT_OF_SCALE"),
    ({"seats_available": "-1"}, "NEGATIVE_SEATS"),
    ({"transfers": "-1"}, "NEGATIVE_VALUE"),
    ({"offer_id": "SOL_MAL_1"}, "MALFORMED_ID"),
    ({"company_id": "SOLR"}, "MALFORMED_ID"),
    ({"changeable": "maybe"}, "INVALID_BOOLEAN"),
    ({"fare_type": "premium"}, "INVALID_FARE_TYPE"),
])
def test_impossible_values_are_rejected(settings, override, category):
    offers, report = load_rows(settings, [valid_row(**override)])
    assert offers == []
    assert [i.category for i in report.rejected] == [category]


@pytest.mark.parametrize("field", ["offer_id", "price_eur", "departure", "seats_available", "duration_min"])
def test_missing_required_field_is_rejected(settings, field):
    offers, report = load_rows(settings, [valid_row(**{field: ""})])
    assert offers == [] and report.rejected[0].category == "MISSING_FIELD"


def test_duplicate_offer_id_keeps_first_and_rejects_second(settings):
    offers, report = load_rows(settings, [valid_row(), valid_row(price_eur="99.00")])
    assert len(offers) == 1 and offers[0].price_eur == 30.0
    assert report.rejected[0].category == "DUPLICATE_ID"


# ------------------------------------------------------------- review queue
@pytest.mark.parametrize("override, category", [
    ({"duration_min": "300"}, "DURATION_MISMATCH"),
    ({"company_id": "CAM"}, "ID_MISMATCH"),
    ({"arrival": "2027-07-12T09:30", "duration_min": "90"}, "IMPLAUSIBLE_SPEED"),
    ({"change_fee_eur": "5.00"}, "FARE_INCONSISTENT"),
])
def test_inconsistent_records_go_to_review(settings, override, category):
    offers, report = load_rows(settings, [valid_row(**override)])
    assert offers == [] and not report.rejected
    assert report.review[0].category == category


def test_duration_tolerance_boundary(settings):
    # Timestamps say 420 min; a stated duration within 10 min is accepted, 11 min is not.
    ok, _ = load_rows(settings, [valid_row(duration_min="430")])
    assert len(ok) == 1
    bad, report = load_rows(settings, [valid_row(duration_min="431")])
    assert bad == [] and report.review[0].category == "DURATION_MISMATCH"


# ---------------------------------------------------------------- corrected
def test_harmless_formatting_is_corrected(settings):
    row = valid_row(price_eur="30,50", destination_id="mal", changeable=" yes", change_fee_eur="0")
    offers, report = load_rows(settings, [row])
    assert len(offers) == 1 and report.corrected_records == 1
    assert offers[0].price_eur == 30.5
    assert offers[0].destination.destination_id == "MAL"
    assert offers[0].fare.changeable is True


# ------------------------------------------------------- missing / boundary
def test_optional_rating_and_co2_may_be_missing(settings):
    offers, report = load_rows(settings, [valid_row(rating="", co2_kg="")])
    assert offers[0].rating is None and offers[0].co2_kg is None


@pytest.mark.parametrize("rating", ["1.0", "5.0"])
def test_rating_scale_edges_are_accepted(settings, rating):
    offers, _ = load_rows(settings, [valid_row(rating=rating)])
    assert len(offers) == 1


def test_zero_seats_is_valid_but_counted_as_sold_out(settings):
    offers, report = load_rows(settings, [valid_row(seats_available="0")])
    assert len(offers) == 1 and report.sold_out == 1


def test_missing_offers_file_raises(settings):
    companies = load_companies(settings.companies_file)
    destinations = load_destinations(settings.destinations_file)
    with pytest.raises(DataFileError):
        load_offers(settings.offers_file.with_name("nope.csv"), companies, destinations, settings)


def test_missing_column_raises(settings):
    settings.offers_file.write_text("offer_id,company_id\nSOL-MAL-0001,SOL\n", encoding="utf-8")
    companies = load_companies(settings.companies_file)
    destinations = load_destinations(settings.destinations_file)
    with pytest.raises(DataFileError, match="missing required column"):
        load_offers(settings.offers_file, companies, destinations, settings)


def test_empty_companies_file_raises(settings):
    settings.companies_file.write_text("company_id,name,profile\n", encoding="utf-8")
    with pytest.raises(DataFileError):
        load_companies(settings.companies_file)


def test_rejections_are_logged_and_written_to_file(settings):
    from busbidder.logging_setup import configure_logging
    configure_logging(settings.log_file)
    write_offers(settings.offers_file, [valid_row(), valid_row(offer_id="SOL-MAL-0002", price_eur="-1")])
    Marketplace.load(settings)
    rejected = (settings.output_dir / "rejected_offers.csv").read_text(encoding="utf-8")
    assert "NEGATIVE_PRICE" in rejected
    assert "NEGATIVE_PRICE" in settings.log_file.read_text(encoding="utf-8")


# ------------------------------------------------------- destination lookup
@pytest.mark.parametrize("text", ["Malaga", "málaga", "MALAGA", "mal", "  Malaga "])
def test_destination_lookup_ignores_case_and_accents(settings, text):
    m = Marketplace.load(settings, write_issue_files=False)
    assert m.find_destination(text).destination_id == "MAL"


def test_unknown_destination_suggests_close_match(settings):
    m = Marketplace.load(settings, write_issue_files=False)
    with pytest.raises(UnknownDestinationError, match="Did you mean: Malaga"):
        m.find_destination("Malga")


# ------------------------------------------------------------ real dataset
def test_real_dataset_covers_all_75_combinations_and_flags_every_anomaly(settings):
    m = Marketplace.load(settings, write_issue_files=False)
    pairs = {(o.company.company_id, o.destination.destination_id) for o in m.offers}
    assert len(pairs) == 75
    r = m.report
    assert len(r.rejected) == 11 and len(r.review) == 4 and r.corrected_records == 1
    assert r.accepted + len(r.rejected) + len(r.review) == r.offers_read
