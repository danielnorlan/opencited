"""Errors that a source client raises when a source gives no usable answer."""

from __future__ import annotations


class SourceError(Exception):
    """A request to a source that did not give a usable answer."""


class TableNotFoundError(SourceError):
    """The source has no table with the requested ID."""


class SourceUnavailableError(SourceError):
    """The source could not be reached, timed out, failed, or answered without JSON."""


class RateLimitedError(SourceUnavailableError):
    """The source refused the request because the client sent too many.

    ``retry_after`` holds the seconds that the source asked the client to wait, when it
    said so.
    """

    def __init__(self, message: str, retry_after: int | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class RejectedRequestError(SourceError):
    """The source rejected the request, for example an unknown dimension or code."""


class InvalidResponseError(SourceError):
    """The source answered with a body that is not valid JSON, or not in a known shape."""


class InvalidSelectionError(SourceError):
    """A selection that does not fit the table: an unknown dimension or code, a missing
    dimension, or no code selected. Raised before any data request."""


class TooManyCellsError(InvalidSelectionError):
    """A selection that covers more cells than the limit allows. Raised before any data
    request."""

    def __init__(self, message: str, cells: int, limit: int) -> None:
        super().__init__(message)
        self.cells = cells
        self.limit = limit


class RevisionPeriodError(SourceUnavailableError):
    """A data request in the period when SSB shows figures under revision as 0 or a dot
    (ADR 0007). Raised before any request."""
