from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from app.assets.linking import (
    AssetLinkConfidence,
    AssetLinkDisposition,
    AssetLinkEvidenceKind,
    AssetLinkMethod,
    AssetLinkReason,
    link_repd_site,
    link_repd_sites,
)
from app.assets.models import AssetReference, EvidenceKind, Provenance
from app.geography.repd import (
    REPDProvenance,
    REPDSite,
    REPDStatus,
)


NOW = datetime(2026, 7, 15, 10, 0, tzinfo=UTC)


def _site(
    *,
    source_id: str = "R-1",
    name: str = "A'Chruach Wind Farm",
    technology: str = "Wind Onshore",
    capacity_mw: float | None = 42.6,
    storage: bool = False,
    operator_name: str | None = "Mobius Renewables Ltd",
) -> REPDSite:
    return REPDSite(
        source_id=source_id,
        project_name=name,
        operator_name=operator_name,
        technology=technology,
        capacity_mw=capacity_mw,
        status=REPDStatus.OPERATIONAL,
        source_status="Operational",
        storage_type="Stand-alone Storage" if storage else None,
        is_storage=storage,
        region=None,
        country="England",
        planning_authority=None,
        record_last_updated="15/07/2026",
        coordinates=None,
        provenance=REPDProvenance(
            publisher="Department for Energy Security and Net Zero",
            dataset="Renewable Energy Planning Database (REPD)",
            source_url="https://assets.publishing.service.gov.uk/repd.csv",
            licence_name="Open Government Licence v3.0",
            licence_url="https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/",
            retrieved_at=NOW,
        ),
    )


def _unit(
    *,
    asset_id: str = "ACHRW-1",
    source_asset_id: str | None = "T_ACHRW-1",
    display_name: str | None = "AChruach Wind-Farm",
    lead_party_name: str | None = "Mobius Renewables",
    fuel_type: str | None = "WIND",
    generation_capacity_mw: float | None = 42.6,
    demand_capacity_mw: float | None = 0.0,
) -> AssetReference:
    return AssetReference(
        asset_id=asset_id,
        source_asset_id=source_asset_id,
        display_name=display_name,
        fuel_type=fuel_type,
        lead_party_name=lead_party_name,
        lead_party_id="PARTY",
        asset_type="T",
        production_or_consumption="P",
        submits_physical_notifications=True,
        generation_capacity_mw=generation_capacity_mw,
        demand_capacity_mw=demand_capacity_mw,
        gsp_group_id="_N",
        gsp_group_name="North Scotland",
        interconnector_id=None,
        eic=None,
        location=None,
        provenance=Provenance(
            source_id="elexon",
            dataset="BM_UNIT_REFERENCE",
            endpoint="/reference/bmunits/all",
            retrieved_at=NOW,
            evidence_kind=EvidenceKind.REFERENCE,
        ),
    )


def test_unique_exact_identity_capacity_and_fuel_link_with_audit_evidence() -> None:
    decision = link_repd_site(_site(), [_unit()])

    assert decision.disposition is AssetLinkDisposition.LINKED
    assert decision.method is AssetLinkMethod.EXACT_SITE_IDENTITY
    assert decision.confidence is AssetLinkConfidence.HIGH
    assert decision.reason is AssetLinkReason.UNIQUE_STRONG_CANDIDATE
    assert decision.elexon_asset_id == "ACHRW-1"
    assert decision.elexon_source_asset_id == "T_ACHRW-1"
    assert decision.is_linked is True
    assert {item.kind for item in decision.evidence} == {
        AssetLinkEvidenceKind.NAME_EXACT,
        AssetLinkEvidenceKind.CAPACITY_COMPATIBLE,
        AssetLinkEvidenceKind.FUEL_COMPATIBLE,
        AssetLinkEvidenceKind.OPERATOR_COMPATIBLE,
    }


def test_source_attribution_identifies_both_upstream_records() -> None:
    decision = link_repd_site(_site(source_id="REPD-17"), [_unit(asset_id="BM-7")])

    assert decision.repd_source.authority == (
        "Department for Energy Security and Net Zero"
    )
    assert decision.repd_source.dataset == "Renewable Energy Planning Database (REPD)"
    assert decision.repd_source.record_id == "REPD-17"
    assert decision.repd_source.locator.endswith("repd.csv")
    assert decision.elexon_source is not None
    assert decision.elexon_source.authority == "elexon"
    assert decision.elexon_source.dataset == "BM_UNIT_REFERENCE"
    assert decision.elexon_source.record_id == "BM-7"
    assert decision.elexon_source.locator == "/reference/bmunits/all"


def test_exact_name_alone_never_links_without_capacity_evidence() -> None:
    decision = link_repd_site(_site(capacity_mw=None), [_unit()])

    assert decision.disposition is AssetLinkDisposition.UNLINKED
    assert decision.confidence is AssetLinkConfidence.NONE
    assert decision.method is AssetLinkMethod.NONE
    assert decision.evidence == ()
    assert decision.candidates == ()


@pytest.mark.parametrize("unit_capacity", [40.0, None, 0.0, float("nan")])
def test_capacity_mismatch_or_absence_rejects_false_positive(
    unit_capacity: float | None,
) -> None:
    decision = link_repd_site(
        _site(capacity_mw=42.6),
        [_unit(generation_capacity_mw=unit_capacity)],
    )

    assert decision.disposition is AssetLinkDisposition.UNLINKED
    assert decision.reason is AssetLinkReason.NO_STRONG_CANDIDATE


def test_capacity_tolerance_is_narrow_and_auditable() -> None:
    inside = link_repd_site(
        _site(capacity_mw=50.0),
        [_unit(generation_capacity_mw=50.9)],
    )
    outside = link_repd_site(
        _site(capacity_mw=50.0),
        [_unit(generation_capacity_mw=51.1)],
    )

    assert inside.is_linked is True
    capacity = next(
        item
        for item in inside.evidence
        if item.kind is AssetLinkEvidenceKind.CAPACITY_COMPATIBLE
    )
    assert "difference 0.9 MW" in capacity.detail
    assert outside.disposition is AssetLinkDisposition.UNLINKED


def test_absolute_capacity_floor_does_not_swamp_small_sites() -> None:
    decision = link_repd_site(
        _site(capacity_mw=0.2),
        [_unit(generation_capacity_mw=0.6)],
    )

    assert decision.disposition is AssetLinkDisposition.UNLINKED


def test_known_fuel_contradiction_rejects_exact_name_and_capacity() -> None:
    decision = link_repd_site(_site(), [_unit(fuel_type="CCGT")])

    assert decision.disposition is AssetLinkDisposition.UNLINKED
    assert decision.candidates == ()


def test_different_source_roles_do_not_count_as_operator_corroboration() -> None:
    decision = link_repd_site(
        _site(operator_name="Independent Wind Holdings Ltd"),
        [_unit(lead_party_name="Unrelated Power plc")],
    )

    assert decision.is_linked is True
    assert AssetLinkEvidenceKind.OPERATOR_COMPATIBLE not in {
        item.kind for item in decision.evidence
    }


def test_unknown_fuel_is_not_positive_evidence_but_exact_display_can_link() -> None:
    decision = link_repd_site(_site(), [_unit(fuel_type=None)])

    assert decision.is_linked is True
    assert {item.kind for item in decision.evidence} == {
        AssetLinkEvidenceKind.NAME_EXACT,
        AssetLinkEvidenceKind.CAPACITY_COMPATIBLE,
        AssetLinkEvidenceKind.OPERATOR_COMPATIBLE,
    }


def test_contained_lead_party_identity_requires_positive_fuel_evidence() -> None:
    site = _site(
        name="Afton Wind Farm",
        capacity_mw=50.0,
        operator_name="Afton Wind Farm Ltd",
    )
    candidate = _unit(
        display_name="AFTOW-1",
        lead_party_name="Afton Wind Farm Limited",
        generation_capacity_mw=50.0,
    )

    linked = link_repd_site(site, [candidate])
    unlinked = link_repd_site(
        replace(site, operator_name=None),
        [replace(candidate, fuel_type=None)],
    )

    assert linked.is_linked is True
    assert linked.method is AssetLinkMethod.CONTAINED_SITE_IDENTITY
    assert linked.evidence[0].elexon_field == "lead_party_name"
    assert unlinked.disposition is AssetLinkDisposition.UNLINKED


def test_generic_containment_does_not_create_candidate() -> None:
    decision = link_repd_site(
        _site(name="Hill Wind Farm", capacity_mw=30.0),
        [
            _unit(
                display_name="Blue Hill Wind Farm Holdings",
                generation_capacity_mw=30.0,
            )
        ],
    )

    assert decision.disposition is AssetLinkDisposition.UNLINKED


def test_multiple_strong_candidates_remain_explicitly_ambiguous() -> None:
    decision = link_repd_site(
        _site(),
        [
            _unit(asset_id="WIND-B"),
            _unit(asset_id="WIND-A", source_asset_id="T_WIND-A"),
        ],
    )

    assert decision.disposition is AssetLinkDisposition.AMBIGUOUS
    assert decision.confidence is AssetLinkConfidence.NONE
    assert decision.elexon_asset_id is None
    assert decision.elexon_source is None
    assert decision.evidence == ()
    assert decision.reason is AssetLinkReason.MULTIPLE_STRONG_CANDIDATES
    assert [candidate.elexon_asset_id for candidate in decision.candidates] == [
        "WIND-A",
        "WIND-B",
    ]
    assert all(candidate.evidence for candidate in decision.candidates)


def test_repeated_copy_of_same_source_record_is_not_false_ambiguity() -> None:
    unit = _unit()

    decision = link_repd_site(_site(), [unit, unit])

    assert decision.is_linked is True
    assert decision.elexon_asset_id == unit.asset_id
    assert len(decision.candidates) == 1


def test_exact_candidate_does_not_silently_override_other_strong_candidate() -> None:
    site = _site(
        name="Afton Wind Farm",
        capacity_mw=50.0,
        operator_name="Afton Wind Farm Ltd",
    )
    exact = _unit(
        asset_id="EXACT",
        display_name="Afton Wind Farm",
        lead_party_name="Afton Wind Farm Limited",
        generation_capacity_mw=50.0,
    )
    contained = _unit(
        asset_id="CONTAINED",
        display_name="AFTOW-1",
        lead_party_name="Afton Wind Farm Limited",
        generation_capacity_mw=50.0,
    )

    decision = link_repd_site(site, [exact, contained])

    assert decision.disposition is AssetLinkDisposition.AMBIGUOUS
    assert {candidate.method for candidate in decision.candidates} == {
        AssetLinkMethod.EXACT_SITE_IDENTITY,
        AssetLinkMethod.CONTAINED_SITE_IDENTITY,
    }


def test_storage_capacity_can_be_corroborated_by_demand_capability() -> None:
    site = _site(
        name="North Store Battery",
        technology="Battery",
        capacity_mw=20.0,
        storage=True,
    )
    unit = _unit(
        display_name="North Store Battery",
        fuel_type=None,
        generation_capacity_mw=0.0,
        demand_capacity_mw=-20.0,
    )

    decision = link_repd_site(site, [unit])

    assert decision.is_linked is True
    capacity = next(
        item
        for item in decision.evidence
        if item.kind is AssetLinkEvidenceKind.CAPACITY_COMPATIBLE
    )
    assert capacity.elexon_field == "demand_capacity_mw"
    assert capacity.elexon_value == "20"


def test_same_bm_unit_selected_by_two_repd_sites_is_unlinked_for_both() -> None:
    sites = [
        _site(source_id="R-1"),
        _site(source_id="R-2"),
    ]

    decisions = link_repd_sites(sites, [_unit()])

    assert all(
        decision.disposition is AssetLinkDisposition.AMBIGUOUS
        for decision in decisions
    )
    assert all(
        decision.reason is AssetLinkReason.CROSS_SITE_COLLISION
        for decision in decisions
    )
    assert decisions[0].conflicting_repd_source_ids == ("R-2",)
    assert decisions[1].conflicting_repd_source_ids == ("R-1",)
    assert all(decision.elexon_asset_id is None for decision in decisions)


def test_empty_reference_set_returns_source_attributed_unlinked_decision() -> None:
    decision = link_repd_site(_site(source_id="R-empty"), [])

    assert decision.disposition is AssetLinkDisposition.UNLINKED
    assert decision.repd_source.record_id == "R-empty"
    assert decision.reason is AssetLinkReason.NO_STRONG_CANDIDATE
