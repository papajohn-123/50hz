from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.assets.api_models import (
    AssetCoordinateResponse,
    AssetFeedState,
    AssetFeedStatusResponse,
    AssetLifecycle,
    AssetMapItemResponse,
    AssetMapResponse,
    AssetOperatingEvidenceResponse,
    AssetPlanEvidenceResponse,
    AssetSettledEvidenceResponse,
    AssetSourceResponse,
)


NOW = datetime(2026, 7, 15, 11, 0, tzinfo=UTC)


def _source() -> AssetSourceResponse:
    return AssetSourceResponse(
        source_id="desnz.repd",
        publisher="Department for Energy Security and Net Zero",
        dataset="Renewable Energy Planning Database (REPD)",
        source_record_id="6935",
        retrieved_at=NOW,
        canonical_url="https://www.gov.uk/government/publications/renewable-energy-planning-database-quarterly-extract",
        licence="Open Government Licence v3.0",
        attribution="Contains public sector information licensed under OGL v3.0.",
    )


def _item() -> AssetMapItemResponse:
    return AssetMapItemResponse(
        id="site_abc",
        name="Hornsea Project Two",
        operator_name="Orsted",
        technology="Wind Offshore",
        fuel_type="wind",
        lifecycle=AssetLifecycle.OPERATIONAL,
        capacity_mw=1_386,
        region="Yorkshire and Humber",
        country="England",
        coordinate=AssetCoordinateResponse(
            latitude=53.9,
            longitude=1.8,
            precision="repd_site_point",
            source=_source(),
        ),
        linked_bm_unit_count=1,
        operating_evidence=AssetOperatingEvidenceResponse(
            participant_submitted_plan=AssetPlanEvidenceResponse(
                level_mw=900,
                at=NOW,
                direction="export",
                source_id="elexon.pn",
                retrieved_at=NOW,
                settlement_date="2026-07-15",
                settlement_period=23,
                caveat="Participant-submitted plan, not actual output.",
            ),
            has_live_metered_output=False,
        ),
    )


def test_mobile_json_keeps_acronym_aliases_and_evidence_semantics() -> None:
    response = AssetMapResponse(
        evaluated_at=NOW,
        source_status=AssetFeedStatusResponse(
            state=AssetFeedState.CURRENT,
            last_successful_at=NOW,
            asset_reference_count=3_026,
            located_asset_count=3_100,
        ),
        total_count=1,
        returned_count=1,
        is_truncated=False,
        assets=[_item()],
        boundary="Source-located renewable and storage sites in Great Britain",
        disclaimer="Elexon does not publish generator coordinates.",
    )

    payload = response.model_dump(mode="json", by_alias=True)

    source = payload["assets"][0]["coordinate"]["source"]
    evidence = payload["assets"][0]["operatingEvidence"]
    assert source["sourceID"] == "desnz.repd"
    assert source["sourceRecordID"] == "6935"
    assert payload["assets"][0]["capacityMW"] == 1_386
    assert payload["assets"][0]["linkedBMUnitCount"] == 1
    assert evidence["participantSubmittedPlan"]["levelMW"] == 900
    assert evidence["participantSubmittedPlan"]["evidenceKind"] == "reported_plan"
    assert evidence["hasLiveMeteredOutput"] is False


def test_response_count_contract_fails_closed() -> None:
    with pytest.raises(ValidationError, match="returned_count"):
        AssetMapResponse(
            evaluated_at=NOW,
            source_status=AssetFeedStatusResponse(
                state=AssetFeedState.CURRENT,
                asset_reference_count=0,
                located_asset_count=0,
            ),
            total_count=2,
            returned_count=2,
            is_truncated=False,
            assets=[_item()],
            boundary="Great Britain",
            disclaimer="Reference data only.",
        )


def test_settled_interval_must_be_ordered() -> None:
    with pytest.raises(ValidationError, match="interval end"):
        AssetSettledEvidenceResponse(
            energy_mwh=10,
            average_mw=20,
            interval_start=NOW,
            interval_end=NOW,
            direction="export",
            source_id="elexon.b1610",
            retrieved_at=NOW,
            settlement_date="2026-07-15",
            settlement_period=23,
            caveat="Delayed settled metered energy.",
        )
