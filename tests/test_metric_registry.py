from fastapi.testclient import TestClient

from app.main import app
from app.metrics import (
    CURRENT_FAMILY_POLICIES,
    METRIC_DEFINITIONS,
    METRIC_REGISTRY_VERSION,
    MetricClassification,
)


def test_registry_definitions_are_versioned_and_covered_by_one_family_policy() -> None:
    metric_ids = [definition.metric_id for definition in METRIC_DEFINITIONS]
    policy_ids = [
        metric_id
        for policy in CURRENT_FAMILY_POLICIES
        for metric_id in policy.metric_ids
    ]

    assert METRIC_REGISTRY_VERSION
    assert len(metric_ids) == len(set(metric_ids))
    assert len(policy_ids) == len(set(policy_ids))
    assert set(metric_ids) == set(policy_ids)

    definitions = {definition.metric_id: definition for definition in METRIC_DEFINITIONS}
    for policy in CURRENT_FAMILY_POLICIES:
        assert policy.delivery_healthy_seconds < policy.delivery_stale_seconds
        assert policy.fact_live_seconds < policy.fact_stale_seconds
        assert all(
            definitions[metric_id].family is policy.family
            for metric_id in policy.metric_ids
        )

    for definition in METRIC_DEFINITIONS:
        assert definition.methodology_version
        assert definition.unit
        assert definition.boundary
        assert definition.source_datasets
        assert definition.methodology
        assert definition.exclusions


def test_registry_encodes_source_boundaries_and_classifications() -> None:
    metrics = {definition.metric_id: definition for definition in METRIC_DEFINITIONS}

    generation = metrics["generation.transmission_visible_by_fuel"]
    assert generation.classification is MetricClassification.OBSERVED
    assert "not total GB generation" in generation.boundary
    assert any("Embedded" in exclusion for exclusion in generation.exclusions)
    assert any("solar" in exclusion for exclusion in generation.exclusions)

    demand = metrics["demand.national_outturn"]
    assert demand.resolution_seconds == 1_800
    assert demand.expected_publication_lag_seconds == 900
    demand_exclusions = " ".join(demand.exclusions)
    assert "Interconnector flows" in demand_exclusions
    assert "Station transformer demand" in demand_exclusions
    assert "Pumped-storage demand" in demand_exclusions

    frequency = metrics["frequency.system"]
    assert "spot sample" in frequency.description
    assert "does not supply a reliable publication timestamp" in frequency.methodology

    carbon = metrics["carbon.intensity.national"]
    assert carbon.classification is MetricClassification.ESTIMATED
    assert "field named actual" in carbon.methodology

    flow = metrics["interconnector.flow"]
    assert flow.sign_convention == (
        "Positive MW imports into Great Britain; negative MW exports from Great Britain."
    )
    assert "storage.charging" not in metrics


def test_metric_registry_route_is_camel_case_and_conditionally_cached() -> None:
    with TestClient(app) as client:
        first = client.get("/v1/metadata/metrics")
        second = client.get(
            "/v1/metadata/metrics",
            headers={"If-None-Match": first.headers["etag"]},
        )

    assert first.status_code == 200
    assert first.headers["cache-control"].startswith("public, max-age=3600")
    payload = first.json()
    assert payload["schemaVersion"] == "1.0"
    assert payload["registryVersion"] == METRIC_REGISTRY_VERSION
    assert [metric["id"] for metric in payload["metrics"]] == [
        definition.metric_id for definition in METRIC_DEFINITIONS
    ]
    assert payload["metrics"][0]["methodologyVersion"]
    assert payload["metrics"][0]["sourceDatasets"]
    assert second.status_code == 304
    assert second.content == b""


def test_openapi_documents_additive_status_supply_and_metric_registry_contracts() -> None:
    schema = app.openapi()
    registry_response = schema["paths"]["/v1/metadata/metrics"]["get"]["responses"][
        "200"
    ]["content"]["application/json"]["schema"]
    assert registry_response == {
        "$ref": "#/components/schemas/MetricRegistryResponse"
    }

    components = schema["components"]["schemas"]
    snapshot = components["GridSnapshotResponse"]
    assert "dataStatus" in snapshot["properties"]
    assert "supply" in snapshot["properties"]
    # Both additions are optional in the schema so a stored v1 fixture remains valid.
    assert "dataStatus" not in snapshot["required"]
    assert "supply" not in snapshot["required"]

    status = components["DataFamilyStatus"]
    assert status["properties"]["evaluatedAt"]["format"] == "date-time"
    assert status["properties"]["deliveryState"]["$ref"].endswith(
        "/DeliveryState"
    )
    assert status["properties"]["factState"]["$ref"].endswith("/FactState")
    assert "validTo" in status["properties"]

    supply = components["SupplyAccounting"]
    assert "complete Great Britain supply balance" in supply["properties"][
        "isComplete"
    ]["description"]
    charging_types = supply["properties"]["storageChargingMW"]["anyOf"]
    assert {item.get("type") for item in charging_types} == {"number", "null"}

    definition = components["MetricDefinitionResponse"]
    assert definition["additionalProperties"] is False
    assert {
        "boundary",
        "classification",
        "unit",
        "methodology",
        "exclusions",
        "methodologyVersion",
    }.issubset(definition["properties"])
