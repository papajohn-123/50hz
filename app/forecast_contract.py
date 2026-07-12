"""Shared immutable contracts for persisted forecast-vintage semantics."""

NATIONAL_FORECAST_METHODOLOGY_VERSION = (
    "50hz.neso-carbon-intensity.national-forecast.v1"
)
SOURCE_ISSUE_TIME_UNAVAILABLE = "source_does_not_publish_issue_time"
CAPTURE_TIME_ISSUE_BASIS = "retrieved_at"
SOURCE_PUBLISHED_TIME_BASIS = "source_published_at"


__all__ = [
    "CAPTURE_TIME_ISSUE_BASIS",
    "NATIONAL_FORECAST_METHODOLOGY_VERSION",
    "SOURCE_ISSUE_TIME_UNAVAILABLE",
    "SOURCE_PUBLISHED_TIME_BASIS",
]
