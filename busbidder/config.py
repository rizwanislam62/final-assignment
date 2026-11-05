"""Loading and validating ``config/settings.json``.

All tunable numbers of the scoring model live in one JSON file instead of being
hard-coded, so that an experiment (for example a different outlier rule) can be
reproduced by pointing the program at a different settings file.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .exceptions import ConfigurationError, DataFileError

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SETTINGS_PATH = PROJECT_ROOT / "config" / "settings.json"


@dataclass
class Settings:
    # file locations (relative paths are resolved against the project root)
    companies_file: Path
    destinations_file: Path
    offers_file: Path
    profiles_file: Path
    bookings_file: Path
    log_file: Path
    output_dir: Path
    # validation
    rating_min: float = 1.0
    rating_max: float = 5.0
    duration_tolerance_min: int = 10
    min_plausible_speed_kmh: float = 20.0
    max_plausible_speed_kmh: float = 110.0
    # scoring
    departure_decay_min: int = 240
    min_meaningful_range: dict[str, float] = field(default_factory=dict)
    outlier_iqr_factor: float = 1.5
    neutral_score_for_missing: float = 50.0
    sensitivity_shift_pp: float = 10.0
    default_weights: dict[str, float] = field(default_factory=dict)
    # simulation clock: a fixed "now" makes booking runs reproducible (None = real clock)
    simulated_now: datetime | None = None

    def now(self) -> datetime:
        return self.simulated_now or datetime.now()


def _resolve(base: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (base / path)


def load_settings(path: Path | str | None = None) -> Settings:
    """Read the settings file and return a validated ``Settings`` object."""
    path = Path(path) if path else DEFAULT_SETTINGS_PATH
    if not path.is_file():
        raise DataFileError(f"Settings file not found: {path}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigurationError(f"Settings file {path} is not valid JSON: {exc}") from exc

    try:
        files = raw["files"]
        validation = raw.get("validation", {})
        scoring = raw.get("scoring", {})
        now_text = raw.get("simulation", {}).get("now")
        settings = Settings(
            companies_file=_resolve(PROJECT_ROOT, files["companies"]),
            destinations_file=_resolve(PROJECT_ROOT, files["destinations"]),
            offers_file=_resolve(PROJECT_ROOT, files["offers"]),
            profiles_file=_resolve(PROJECT_ROOT, files["profiles"]),
            bookings_file=_resolve(PROJECT_ROOT, files["bookings"]),
            log_file=_resolve(PROJECT_ROOT, files["log"]),
            output_dir=_resolve(PROJECT_ROOT, files["output_dir"]),
            rating_min=float(validation.get("rating_min", 1.0)),
            rating_max=float(validation.get("rating_max", 5.0)),
            duration_tolerance_min=int(validation.get("duration_tolerance_min", 10)),
            min_plausible_speed_kmh=float(validation.get("min_plausible_speed_kmh", 20.0)),
            max_plausible_speed_kmh=float(validation.get("max_plausible_speed_kmh", 110.0)),
            departure_decay_min=int(scoring.get("departure_decay_min", 240)),
            min_meaningful_range={k: float(v) for k, v in scoring.get("min_meaningful_range", {}).items()},
            outlier_iqr_factor=float(scoring.get("outlier_iqr_factor", 1.5)),
            neutral_score_for_missing=float(scoring.get("neutral_score_for_missing", 50.0)),
            sensitivity_shift_pp=float(scoring.get("sensitivity_shift_pp", 10.0)),
            default_weights={k: float(v) for k, v in scoring.get("default_weights", {}).items()},
            simulated_now=datetime.strptime(now_text, "%Y-%m-%dT%H:%M") if now_text else None,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ConfigurationError(f"Settings file {path} is missing or has an invalid entry: {exc}") from exc

    if settings.rating_min >= settings.rating_max:
        raise ConfigurationError("rating_min must be smaller than rating_max.")
    if settings.departure_decay_min <= 0:
        raise ConfigurationError("departure_decay_min must be positive.")
    if not 0 <= settings.neutral_score_for_missing <= 100:
        raise ConfigurationError("neutral_score_for_missing must be between 0 and 100.")
    if settings.outlier_iqr_factor <= 0:
        raise ConfigurationError("outlier_iqr_factor must be positive.")
    if any(v < 0 for v in settings.min_meaningful_range.values()):
        raise ConfigurationError("min_meaningful_range values cannot be negative.")
    return settings
