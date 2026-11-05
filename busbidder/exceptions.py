"""Custom exception hierarchy for BusBidder Spain.

Every error the application raises on purpose derives from ``BusBidderError``,
so the command-line interface can catch one base class, print a clear message
and exit with a non-zero status instead of showing a traceback.
"""


class BusBidderError(Exception):
    """Base class for all expected application errors."""


# ---------------------------------------------------------------- data errors
class DataFileError(BusBidderError):
    """A required data or configuration file is missing or unreadable."""


class RecordValidationError(BusBidderError):
    """A single input record contains an impossible or invalid value.

    ``category`` is a short, stable error code (for example ``NEGATIVE_PRICE``)
    that is written to the log and the rejection file so that rejected records
    can be counted and grouped.
    """

    def __init__(self, category: str, message: str, field: str | None = None):
        super().__init__(message)
        self.category = category
        self.field = field


class RecordNeedsReviewError(RecordValidationError):
    """A record is internally inconsistent but not clearly impossible.

    Such records are not silently corrected or discarded.  They are placed in a
    review queue file so that a human can decide which value is wrong.
    """


class ConfigurationError(BusBidderError):
    """The configuration (settings or traveller profiles) is invalid."""


# ------------------------------------------------------------- request errors
class InvalidRequestError(BusBidderError):
    """The traveller's request contains an invalid value."""


class InvalidWeightsError(InvalidRequestError):
    """The criterion weights are unknown, negative or do not sum to 100 %."""


class UnknownDestinationError(InvalidRequestError):
    """The requested destination is not served by the platform."""


# ------------------------------------------------------------- booking errors
class BookingError(BusBidderError):
    """A booking could not be created, found or changed."""


class SeatUnavailableError(BookingError):
    """There are not enough seats left on the selected offer."""


class InvalidBookingReferenceError(BookingError):
    """A booking reference does not have the expected format."""
