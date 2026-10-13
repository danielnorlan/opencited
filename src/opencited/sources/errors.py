"""Errors that a source client raises when a source gives no usable answer."""

from __future__ import annotations


class SourceError(Exception):
    """A request to a source that did not give a usable answer."""


class TableNotFoundError(SourceError):
    """The source has no table with the requested ID."""


class SourceUnavailableError(SourceError):
    """The source could not be reached, did not answer in time, or answered without data."""


class RateLimitedError(SourceUnavailableError):
    """The source refused the request because the client sent too many."""


class RejectedRequestError(SourceError):
    """The source rejected the request, for example an unknown dimension or code."""


class InvalidResponseError(SourceError):
    """The source answered with JSON in a shape that the client does not understand."""
