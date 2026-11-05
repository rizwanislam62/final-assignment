"""Traveller profiles, configuration errors and end-to-end command-line runs."""

from __future__ import annotations

import json
from datetime import date

import pytest

from busbidder.cli import main, parse_duration
from busbidder.config import load_settings
from busbidder.exceptions import ConfigurationError, DataFileError, InvalidRequestError
from busbidder.filters import apply_hard_constraints
from busbidder.loader import Marketplace
from busbidder.profiles import load_profiles
from busbidder.ranking import rank


# ------------------------------------------------------------------ profiles
def test_profiles_load_with_normalised_weights(settings):
    profiles = load_profiles(settings.profiles_file)
    assert {"budget", "business", "flexible", "eco", "example"} <= set(profiles)
    for p in profiles.values():
        assert sum(p.weights.values()) == pytest.approx(1.0)


def test_same_offers_give_different_recommendations_per_profile(settings):
    m = Marketplace.load(settings, write_issue_files=False)
    profiles = load_profiles(settings.profiles_file)
    malaga = m.find_destination("Malaga")
    winners = {}
    for key in ("budget", "business", "flexible"):
        request = profiles[key].build_request(malaga, date(2027, 7, 12), date(2027, 7, 14))
        result = apply_hard_constraints(m.offers, request, lambda o: o.seats_available)
        winners[key] = rank(result, request, request.weights, settings).top.offer
    assert len({o.offer_id for o in winners.values()}) == 3
    assert winners["budget"].price_eur < winners["business"].price_eur
    assert winners["business"].is_direct and winners["business"].fare.changeable


@pytest.mark.parametrize("bad_profile, message", [
    ({"weights": {"price": 50, "duration": 50}}, "core criteria"),
    ({"weights": {"price": 40, "duration": 25, "departure": 15, "flexibility": 20},
      "constraints": {"max_speed": 3}}, "unknown constraint"),
    ({"weights": {"price": 40, "duration": 25, "departure": 15, "flexibility": 20},
      "constraints": {"changeable_required": "yes"}}, "true or false"),
    ({"weights": {"price": 40, "duration": 25, "departure": 15, "flexibility": 20},
      "preferences": {"departure": "lunchtime"}}, "departure preference"),
    ({"name": "no weights"}, "weights"),
])
def test_invalid_profiles_are_reported(tmp_path, bad_profile, message):
    path = tmp_path / "profiles.json"
    path.write_text(json.dumps({"bad": bad_profile}), encoding="utf-8")
    with pytest.raises(ConfigurationError, match=message):
        load_profiles(path)


def test_missing_profiles_and_settings_files(tmp_path):
    with pytest.raises(DataFileError):
        load_profiles(tmp_path / "missing.json")
    with pytest.raises(DataFileError):
        load_settings(tmp_path / "missing.json")


def test_simulated_clock_is_read_from_settings(tmp_path):
    from datetime import datetime
    from conftest import ROOT
    raw = json.loads((ROOT / "config" / "settings.json").read_text(encoding="utf-8"))
    raw["simulation"] = {"now": "2027-07-01T09:00"}
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    assert load_settings(path).now() == datetime(2027, 7, 1, 9, 0)
    raw["simulation"] = {"now": "first of July"}
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ConfigurationError):
        load_settings(path)


def test_settings_with_invalid_values(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"files": {}}), encoding="utf-8")
    with pytest.raises(ConfigurationError):
        load_settings(path)


@pytest.mark.parametrize("text, minutes", [("480", 480), ("8h", 480), ("7h30", 450), ("0h45", 45)])
def test_parse_duration(text, minutes):
    assert parse_duration(text) == minutes


@pytest.mark.parametrize("text", ["8 hours", "7:30", "h30", "7h75"])
def test_parse_duration_rejects_bad_input(text):
    with pytest.raises(InvalidRequestError):
        parse_duration(text)


# ----------------------------------------------------------------- CLI runs
def run_cli(settings_file, *args):
    return main(["--config", str(settings_file), *args])


def test_cli_search_prints_constraints_ranking_and_explanation(settings_file, capsys, tmp_path):
    export = tmp_path / "ranking.csv"
    code = run_cli(settings_file, "search", "--to", "Malaga", "--from-date", "2027-07-12",
                   "--to-date", "2027-07-14", "--profile", "example", "--export", str(export))
    out = capsys.readouterr().out
    assert code == 0
    assert "HARD CONSTRAINTS" in out and "SOFT PREFERENCES" in out
    assert "Ranked offers" in out and "Recommendation" in out
    assert "not an objectively best bus" in out
    assert export.read_text(encoding="utf-8").startswith("traveller,rank,offer_id")


def test_cli_compare_shows_summary(settings_file, capsys):
    code = run_cli(settings_file, "compare", "--to", "MAL", "--from-date", "2027-07-12", "--to-date", "2027-07-14")
    out = capsys.readouterr().out
    assert code == 0 and "Summary of recommendations" in out and "Business traveller" in out


def test_cli_book_list_show_cancel_cycle(settings_file, settings, capsys):
    m = Marketplace.load(settings, write_issue_files=False)
    offer = next(o for o in m.offers if o.seats_available >= 2)
    assert run_cli(settings_file, "book", "--offer", offer.offer_id, "--name", "Ana Lopez") == 0
    reference = json.loads(settings.bookings_file.read_text(encoding="utf-8"))["bookings"][0]["reference"]
    assert run_cli(settings_file, "bookings") == 0
    assert run_cli(settings_file, "show", reference) == 0
    assert run_cli(settings_file, "cancel", reference) == 0
    out = capsys.readouterr().out
    assert reference in out and "CANCELLED" in out


def test_cli_interactive_booking_after_ranking(settings_file, settings, monkeypatch, capsys):
    answers = iter(["1", "Kari Hansen", "y"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    code = run_cli(settings_file, "search", "--to", "Malaga", "--from-date", "2027-07-12",
                   "--to-date", "2027-07-14", "--profile", "business", "--book")
    assert code == 0
    saved = json.loads(settings.bookings_file.read_text(encoding="utf-8"))["bookings"]
    assert len(saved) == 1 and saved[0]["traveller_name"] == "Kari Hansen"


@pytest.mark.parametrize("args, message", [
    (["search", "--to", "Ibiza", "--from-date", "2027-07-12"], "not a BusBidder destination"),
    (["search", "--to", "Malaga", "--from-date", "12-07-2027"], "not a valid date"),
    (["search", "--to", "Malaga", "--from-date", "2027-07-12", "--weights", "price=90"], "core criteria"),
    (["search", "--to", "Malaga", "--from-date", "2027-07-12", "--profile", "pirate"], "Unknown profile"),
    (["show", "nonsense"], "not a valid booking reference"),
])
def test_cli_errors_exit_with_code_2_and_clear_message(settings_file, capsys, args, message):
    assert run_cli(settings_file, *args) == 2
    assert message in capsys.readouterr().err


def test_cli_validate_and_info_commands(settings_file, capsys):
    for command in ("validate", "criteria", "profiles"):
        assert run_cli(settings_file, command) == 0
    out = capsys.readouterr().out
    assert "NEGATIVE_PRICE" in out and "Ranking criteria" in out and "Business traveller" in out
