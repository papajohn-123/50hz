"""Typed source failures used by adapters and the ingestion worker."""

from __future__ import annotations


class SourceError(RuntimeError):
    """Base class for an upstream source failure."""


class SourceUnavailableError(SourceError):
    """The source could not be reached after retrying transient failures."""


class SourceHTTPStatusError(SourceError):
    """The source returned a non-success response."""

    def __init__(self, status_code: int, url: str, body_preview: str) -> None:
        self.status_code = status_code
        self.url = url
        self.body_preview = body_preview
        super().__init__(f"source returned HTTP {status_code} for {url}")


class SourcePayloadError(SourceError):
    """The response was successful but was not usable JSON."""


class SourceSchemaError(SourceError):
    """The JSON envelope or all of its records no longer match the contract."""

