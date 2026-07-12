"""Public presentation of the versioned metric registry."""

from app.api.models import MetricDefinitionResponse, MetricRegistryResponse
from app.metrics import METRIC_DEFINITIONS, METRIC_REGISTRY_VERSION


def present_metric_registry() -> MetricRegistryResponse:
    return MetricRegistryResponse(
        registry_version=METRIC_REGISTRY_VERSION,
        metrics=[
            MetricDefinitionResponse(
                id=definition.metric_id,
                methodology_version=definition.methodology_version,
                family=definition.family,
                display_name=definition.display_name,
                description=definition.description,
                unit=definition.unit,
                classification=definition.classification,
                boundary=definition.boundary,
                resolution_seconds=definition.resolution_seconds,
                expected_publication_lag_seconds=(
                    definition.expected_publication_lag_seconds
                ),
                source_datasets=list(definition.source_datasets),
                methodology=definition.methodology,
                exclusions=list(definition.exclusions),
                sign_convention=definition.sign_convention,
            )
            for definition in METRIC_DEFINITIONS
        ],
    )
