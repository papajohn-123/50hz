from __future__ import annotations

from enum import StrEnum

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from app.forecast_verification.core import VerificationMetric


def _camel(value: str) -> str:
    first, *rest = value.split("_")
    return first + "".join(part.capitalize() for part in rest)


class VerificationAPIModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=_camel,
        populate_by_name=True,
        extra="forbid",
    )


class PublicVerificationStatus(StrEnum):
    AVAILABLE = "available"
    INSUFFICIENT_DATA = "insufficient_data"


class HorizonDefinition(VerificationAPIModel):
    id: str
    minimum_hours: int = Field(ge=0, le=48)
    maximum_hours: int = Field(gt=0, le=48)


class VerificationWindow(VerificationAPIModel):
    start: AwareDatetime
    end: AwareDatetime


class VerificationSource(VerificationAPIModel):
    source_id: str = Field(alias="sourceID")
    dataset: str
    methodology_version: str
    fact_class: str


class ForecastVerificationItem(VerificationAPIModel):
    metric: VerificationMetric
    display_name: str
    unit: str
    expected_interval_minutes: int = Field(gt=0, le=60)
    horizon: HorizonDefinition
    status: PublicVerificationStatus
    reason: str
    mae: float | None = Field(default=None, ge=0)
    bias: float | None = None
    wape_percent: float | None = Field(default=None, ge=0)
    verified_samples: int = Field(ge=0)
    expected_samples: int = Field(ge=0)
    coverage: float = Field(ge=0, le=1)
    verification_window: VerificationWindow | None = None
    issue_time_basis: str
    effective_vintage_time_basis: str
    forecast: VerificationSource
    outturn: VerificationSource
    registry_version: str
    verification_methodology_version: str
    evidence_checksum: str | None = Field(default=None, min_length=64, max_length=64)
    revision: int | None = Field(default=None, ge=0)
    computed_at: AwareDatetime | None = None
    source_watermark_at: AwareDatetime | None = None


class ForecastVerificationResponse(VerificationAPIModel):
    schema_version: str = "1.0"
    generated_at: AwareDatetime | None = None
    minimum_verified_samples: int = 100
    minimum_coverage: float = 0.90
    results: list[ForecastVerificationItem]
    methodology: dict[str, str] = Field(
        default_factory=lambda: {
            "pairing": (
                "Pair every distinct stored source issue/capture with the latest "
                "immutable exact-timestamp outturn revision. Only the latest correction "
                "of one vintage is selected; nothing is interpolated or synthesized."
            ),
            "vintageTime": (
                "Horizon uses the source publication time when supplied. NESO's "
                "national carbon source does not publish an issue time, so its exact "
                "retrieval/capture time is the declared effective vintage instead."
            ),
            "error": (
                "Signed error is forecast minus outturn; MAE and bias retain "
                "the source unit."
            ),
            "wape": (
                "WAPE is 100 times total absolute error divided by total absolute "
                "outturn, and is omitted when that denominator is not safely positive."
            ),
            "eligibility": (
                "Error statistics are displayed only with at least 100 verified "
                "samples and compatible outturns for at least 90% of the stored "
                "reviewed forecast vintages in that horizon bucket."
            ),
            "scope": "Every result is national; regional forecast accuracy is not claimed.",
        }
    )
