"""Versioned definitions for every metric exposed by the current grid view."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


METRIC_REGISTRY_VERSION = "2026-07-12.1"


class MetricClassification(StrEnum):
    OBSERVED = "observed"
    ESTIMATED = "estimated"
    DERIVED = "derived"
    FORECAST = "forecast"
    REPORTED = "reported"


class MetricFamily(StrEnum):
    GENERATION = "generation"
    DEMAND = "demand"
    FREQUENCY = "frequency"
    INTERCONNECTORS = "interconnectors"
    CARBON = "carbon"


@dataclass(frozen=True, slots=True)
class MetricDefinition:
    metric_id: str
    methodology_version: str
    family: MetricFamily
    display_name: str
    description: str
    unit: str
    classification: MetricClassification
    boundary: str
    resolution_seconds: int
    expected_publication_lag_seconds: int
    source_datasets: tuple[str, ...]
    methodology: str
    exclusions: tuple[str, ...]
    sign_convention: str | None = None

    def __post_init__(self) -> None:
        if not self.metric_id or not self.methodology_version:
            raise ValueError("metric definitions require stable IDs and versions")
        if self.resolution_seconds <= 0 or self.expected_publication_lag_seconds < 0:
            raise ValueError("metric timing values must be non-negative")
        if not self.source_datasets or not self.exclusions:
            raise ValueError("metric definitions require sources and explicit exclusions")


@dataclass(frozen=True, slots=True)
class MetricFreshnessPolicy:
    family: MetricFamily
    metric_ids: tuple[str, ...]
    required_for_snapshot: bool
    expected_cadence_seconds: int
    valid_interval_seconds: int | None
    delivery_healthy_seconds: int
    delivery_stale_seconds: int
    fact_live_seconds: int
    fact_stale_seconds: int

    def __post_init__(self) -> None:
        if self.expected_cadence_seconds <= 0:
            raise ValueError("expected cadence must be positive")
        if not 0 < self.delivery_healthy_seconds < self.delivery_stale_seconds:
            raise ValueError("delivery thresholds must be ordered")
        if not 0 < self.fact_live_seconds < self.fact_stale_seconds:
            raise ValueError("fact thresholds must be ordered")
        if self.valid_interval_seconds is not None and self.valid_interval_seconds <= 0:
            raise ValueError("valid interval must be positive")


METRIC_DEFINITIONS = (
    MetricDefinition(
        metric_id="generation.transmission_visible_by_fuel",
        methodology_version="fuelinst-generation-v1",
        family=MetricFamily.GENERATION,
        display_name="Transmission-visible generation by fuel",
        description=(
            "Five-minute average operational-metered output for Elexon "
            "FUELINST units, grouped by the unit's primary fuel."
        ),
        unit="MW",
        classification=MetricClassification.OBSERVED,
        boundary=(
            "Great Britain transmission-connected units represented by "
            "operational meters in Elexon FUELINST; this is not total GB generation."
        ),
        resolution_seconds=300,
        expected_publication_lag_seconds=300,
        source_datasets=("Elexon FUELINST",),
        methodology=(
            "Interconnector pseudo-fuels are separated into signed flow metrics; "
            "positive primary-fuel outputs are grouped into 50Hz display categories."
        ),
        exclusions=(
            "Embedded and other unmetered generation are omitted.",
            "FUELINST has no dedicated solar category, so it does not measure total solar output.",
            "Interconnector flows are excluded and reported separately.",
            "Negative pumped-storage values are excluded from the legacy generation mix and are not treated as a complete charging measure.",
        ),
    ),
    MetricDefinition(
        metric_id="demand.national_outturn",
        methodology_version="indo-national-demand-v1",
        family=MetricFamily.DEMAND,
        display_name="Initial National Demand outturn",
        description=(
            "Initial half-hour average National Demand outturn published through "
            "Elexon INDO, normally within 15 minutes after the period ends."
        ),
        unit="MW",
        classification=MetricClassification.OBSERVED,
        boundary="Great Britain National Demand under the Elexon/NESO INDO definition.",
        resolution_seconds=1_800,
        expected_publication_lag_seconds=900,
        source_datasets=("Elexon INDO",),
        methodology=(
            "The latest initial outturn is retained for each settlement period; "
            "comparisons should use the National Demand Forecast (NDF) boundary."
        ),
        exclusions=(
            "Interconnector flows are excluded.",
            "Station transformer demand is excluded.",
            "Pumped-storage demand is excluded.",
            "Not household, smart-meter, regional, or distribution-network demand.",
            "Not directly comparable with demand series other than NDF without boundary reconciliation.",
            "Later settled revisions are not represented by the initial outturn feed.",
        ),
    ),
    MetricDefinition(
        metric_id="frequency.system",
        methodology_version="freq-system-v1",
        family=MetricFamily.FREQUENCY,
        display_name="GB transmission system frequency",
        description="Near-real-time spot samples of Great Britain transmission system frequency.",
        unit="Hz",
        classification=MetricClassification.OBSERVED,
        boundary="The synchronous Great Britain transmission system measurement published by Elexon.",
        resolution_seconds=15,
        expected_publication_lag_seconds=120,
        source_datasets=("Elexon FREQ",),
        methodology=(
            "The latest valid 40–60 Hz spot sample is displayed without "
            "interpolation. Samples are delivered in files approximately every "
            "two minutes; the source does not supply a reliable publication timestamp."
        ),
        exclusions=(
            "Not a regional or premises-level frequency measurement.",
            "Not an interval average.",
            "No frequency forecast is produced by 50Hz.",
        ),
    ),
    MetricDefinition(
        metric_id="interconnector.flow",
        methodology_version="fuelinst-interconnector-flow-v1",
        family=MetricFamily.INTERCONNECTORS,
        display_name="Interconnector flow",
        description="Signed near-live flow for each interconnector represented in FUELINST.",
        unit="MW",
        classification=MetricClassification.OBSERVED,
        boundary="Electricity flow between Great Britain and connected neighbouring systems.",
        resolution_seconds=300,
        expected_publication_lag_seconds=300,
        source_datasets=("Elexon FUELINST",),
        methodology="Interconnector pseudo-fuels are retained as individual signed flows.",
        exclusions=(
            "Not available capacity, a commercial schedule, or a physical network-flow model.",
            "Only interconnectors represented in the source response are included.",
        ),
        sign_convention="Positive MW imports into Great Britain; negative MW exports from Great Britain.",
    ),
    MetricDefinition(
        metric_id="carbon.intensity.national",
        methodology_version="neso-national-carbon-v1",
        family=MetricFamily.CARBON,
        display_name="National carbon intensity",
        description="Half-hour national electricity carbon-intensity estimate from NESO.",
        unit="gCO2/kWh",
        classification=MetricClassification.ESTIMATED,
        boundary="Great Britain national electricity carbon intensity under the NESO methodology.",
        resolution_seconds=1_800,
        expected_publication_lag_seconds=1_800,
        source_datasets=("NESO Carbon Intensity national",),
        methodology=(
            "50Hz uses the source field named actual when supplied, but classifies "
            "it as estimated because carbon intensity remains a modelled system value."
        ),
        exclusions=(
            "Not marginal carbon intensity or a household-specific meter reading.",
            "Not a price, tariff, or guarantee of emissions avoided by one action.",
            "Regional current values are separate forecast metrics and are not this national estimate.",
        ),
    ),
    MetricDefinition(
        metric_id="supply.domestic_generation",
        methodology_version="supply-accounting-v1",
        family=MetricFamily.GENERATION,
        display_name="Transmission-visible domestic generation",
        description="Sum of positive non-interconnector FUELINST generation values.",
        unit="MW",
        classification=MetricClassification.DERIVED,
        boundary="The same transmission-visible GB boundary as the FUELINST generation metric.",
        resolution_seconds=300,
        expected_publication_lag_seconds=300,
        source_datasets=("Elexon FUELINST",),
        methodology="Positive generation categories are summed before any interconnector position is added.",
        exclusions=(
            "Excludes imports, exports, and negative pumped-storage values.",
            "Inherits the coverage limits of FUELINST generation.",
        ),
    ),
    MetricDefinition(
        metric_id="supply.gross_imports",
        methodology_version="supply-accounting-v1",
        family=MetricFamily.INTERCONNECTORS,
        display_name="Gross imports",
        description="Sum of positive individual interconnector flows into Great Britain.",
        unit="MW",
        classification=MetricClassification.DERIVED,
        boundary="Interconnectors represented in the current Elexon FUELINST response.",
        resolution_seconds=300,
        expected_publication_lag_seconds=300,
        source_datasets=("Elexon FUELINST",),
        methodology="All positive signed interconnector values are summed without netting exports.",
        exclusions=(
            "Does not represent contracted capacity or include absent source series.",
        ),
        sign_convention="Reported as a non-negative magnitude into Great Britain.",
    ),
    MetricDefinition(
        metric_id="supply.gross_exports",
        methodology_version="supply-accounting-v1",
        family=MetricFamily.INTERCONNECTORS,
        display_name="Gross exports",
        description="Absolute sum of negative individual interconnector flows from Great Britain.",
        unit="MW",
        classification=MetricClassification.DERIVED,
        boundary="Interconnectors represented in the current Elexon FUELINST response.",
        resolution_seconds=300,
        expected_publication_lag_seconds=300,
        source_datasets=("Elexon FUELINST",),
        methodology="Negative signed interconnector values are converted to non-negative export magnitudes and summed.",
        exclusions=(
            "Does not represent contracted capacity or include absent source series.",
        ),
        sign_convention="Reported as a non-negative magnitude leaving Great Britain.",
    ),
    MetricDefinition(
        metric_id="supply.net_imports",
        methodology_version="supply-accounting-v1",
        family=MetricFamily.INTERCONNECTORS,
        display_name="Net interconnector position",
        description="Algebraic sum of all represented signed interconnector flows.",
        unit="MW",
        classification=MetricClassification.DERIVED,
        boundary="Interconnectors represented in the current Elexon FUELINST response.",
        resolution_seconds=300,
        expected_publication_lag_seconds=300,
        source_datasets=("Elexon FUELINST",),
        methodology="Gross imports minus gross exports.",
        exclusions=(
            "Does not show simultaneous gross import and export volumes on its own.",
            "Does not represent contracted capacity or include absent source series.",
        ),
        sign_convention="Positive MW is net import; negative MW is net export.",
    ),
    MetricDefinition(
        metric_id="storage.generation",
        methodology_version="supply-accounting-v1",
        family=MetricFamily.GENERATION,
        display_name="Storage generation",
        description="Sum of positive FUELINST pumped-storage output represented as generation.",
        unit="MW",
        classification=MetricClassification.DERIVED,
        boundary="Transmission-visible storage represented in FUELINST fuel categories.",
        resolution_seconds=300,
        expected_publication_lag_seconds=300,
        source_datasets=("Elexon FUELINST",),
        methodology="Positive FUELINST pumped-storage generating values are summed.",
        exclusions=(
            "Not a complete measure of all battery or distribution-connected storage.",
            "Does not measure charging demand; negative values are not interpreted as a complete charging balance.",
            "Does not describe stored energy or state of charge.",
        ),
    ),
    MetricDefinition(
        metric_id="supply.legacy_display_mix",
        methodology_version="supply-accounting-v1",
        family=MetricFamily.GENERATION,
        display_name="Legacy displayed generation mix",
        description="The additive mix retained for compatibility with existing 50Hz clients.",
        unit="MW",
        classification=MetricClassification.DERIVED,
        boundary="Transmission-visible positive generation plus a positive net interconnector position.",
        resolution_seconds=300,
        expected_publication_lag_seconds=300,
        source_datasets=("Elexon FUELINST",),
        methodology=(
            "Positive domestic categories are displayed; when the algebraic "
            "interconnector position is importing, one imports category is added."
        ),
        exclusions=(
            "It is a display composition, not a physical energy-balance equation.",
            "Embedded and unmetered generation are omitted by the source.",
            "Simultaneous gross imports and exports are netted into one optional imports category.",
            "When Britain is net exporting, exports are not subtracted from the legacy generation list.",
        ),
    ),
)


CURRENT_FAMILY_POLICIES = (
    MetricFreshnessPolicy(
        family=MetricFamily.GENERATION,
        metric_ids=(
            "generation.transmission_visible_by_fuel",
            "supply.domestic_generation",
            "storage.generation",
            "supply.legacy_display_mix",
        ),
        required_for_snapshot=True,
        expected_cadence_seconds=300,
        valid_interval_seconds=300,
        delivery_healthy_seconds=300,
        delivery_stale_seconds=600,
        fact_live_seconds=600,
        fact_stale_seconds=900,
    ),
    MetricFreshnessPolicy(
        family=MetricFamily.DEMAND,
        metric_ids=("demand.national_outturn",),
        required_for_snapshot=True,
        expected_cadence_seconds=1_800,
        valid_interval_seconds=1_800,
        delivery_healthy_seconds=600,
        delivery_stale_seconds=1_200,
        fact_live_seconds=2_700,
        fact_stale_seconds=3_900,
    ),
    MetricFreshnessPolicy(
        family=MetricFamily.FREQUENCY,
        metric_ids=("frequency.system",),
        required_for_snapshot=False,
        expected_cadence_seconds=60,
        valid_interval_seconds=None,
        delivery_healthy_seconds=180,
        delivery_stale_seconds=600,
        fact_live_seconds=180,
        fact_stale_seconds=600,
    ),
    MetricFreshnessPolicy(
        family=MetricFamily.INTERCONNECTORS,
        metric_ids=(
            "interconnector.flow",
            "supply.gross_imports",
            "supply.gross_exports",
            "supply.net_imports",
        ),
        required_for_snapshot=False,
        expected_cadence_seconds=300,
        valid_interval_seconds=300,
        delivery_healthy_seconds=300,
        delivery_stale_seconds=600,
        fact_live_seconds=600,
        fact_stale_seconds=900,
    ),
    MetricFreshnessPolicy(
        family=MetricFamily.CARBON,
        metric_ids=("carbon.intensity.national",),
        required_for_snapshot=True,
        expected_cadence_seconds=1_800,
        valid_interval_seconds=1_800,
        delivery_healthy_seconds=600,
        delivery_stale_seconds=1_200,
        fact_live_seconds=1_800,
        fact_stale_seconds=3_900,
    ),
)


_metric_ids = [definition.metric_id for definition in METRIC_DEFINITIONS]
if len(_metric_ids) != len(set(_metric_ids)):
    raise RuntimeError("metric registry IDs must be unique")

_defined = set(_metric_ids)
_policy_metric_ids = [
    metric_id
    for policy in CURRENT_FAMILY_POLICIES
    for metric_id in policy.metric_ids
]
if len(_policy_metric_ids) != len(set(_policy_metric_ids)):
    raise RuntimeError("each metric must belong to only one current-family policy")

_referenced = set(_policy_metric_ids)
if _referenced != _defined:
    raise RuntimeError("every metric must have exactly one current-family policy")
