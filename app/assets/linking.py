"""Conservative, source-attributed links between REPD sites and Elexon BM units.

This module intentionally optimises for precision rather than coverage.  A BM
unit is linked only when a distinctive site-name relationship is corroborated
by compatible capacity, and weak name relations additionally require positive
fuel or operator evidence.  Unknown data never becomes positive evidence,
known fuel contradictions reject a candidate, and ties remain ambiguities.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum

from app.assets.models import AssetReference
from app.geography.repd import REPDSite


class AssetLinkDisposition(StrEnum):
    LINKED = "linked"
    AMBIGUOUS = "ambiguous"
    UNLINKED = "unlinked"


class AssetLinkMethod(StrEnum):
    EXACT_SITE_IDENTITY = "exact_site_identity"
    CONTAINED_SITE_IDENTITY = "contained_site_identity"
    NONE = "none"


class AssetLinkConfidence(StrEnum):
    HIGH = "high"
    NONE = "none"


class AssetLinkReason(StrEnum):
    UNIQUE_STRONG_CANDIDATE = "unique_strong_candidate"
    NO_STRONG_CANDIDATE = "no_strong_candidate"
    MULTIPLE_STRONG_CANDIDATES = "multiple_strong_candidates"
    CROSS_SITE_COLLISION = "cross_site_collision"


class AssetLinkEvidenceKind(StrEnum):
    NAME_EXACT = "name_exact"
    NAME_CONTAINED = "name_contained"
    CAPACITY_COMPATIBLE = "capacity_compatible"
    FUEL_COMPATIBLE = "fuel_compatible"
    OPERATOR_COMPATIBLE = "operator_compatible"


@dataclass(frozen=True, slots=True)
class AssetLinkSource:
    """The immutable upstream record that participated in a decision."""

    authority: str
    dataset: str
    record_id: str
    locator: str
    retrieved_at: datetime


@dataclass(frozen=True, slots=True)
class AssetLinkEvidence:
    kind: AssetLinkEvidenceKind
    repd_field: str
    repd_value: str
    elexon_field: str
    elexon_value: str
    detail: str


@dataclass(frozen=True, slots=True)
class AssetLinkCandidate:
    elexon_asset_id: str
    elexon_source_asset_id: str | None
    source: AssetLinkSource
    method: AssetLinkMethod
    evidence: tuple[AssetLinkEvidence, ...]


@dataclass(frozen=True, slots=True)
class AssetLinkDecision:
    repd_source: AssetLinkSource
    disposition: AssetLinkDisposition
    method: AssetLinkMethod
    confidence: AssetLinkConfidence
    elexon_asset_id: str | None
    elexon_source_asset_id: str | None
    elexon_source: AssetLinkSource | None
    evidence: tuple[AssetLinkEvidence, ...]
    candidates: tuple[AssetLinkCandidate, ...]
    reason: AssetLinkReason
    conflicting_repd_source_ids: tuple[str, ...] = ()

    @property
    def is_linked(self) -> bool:
        return self.disposition is AssetLinkDisposition.LINKED

    def __post_init__(self) -> None:
        if self.disposition is AssetLinkDisposition.LINKED:
            if (
                self.confidence is not AssetLinkConfidence.HIGH
                or self.method is AssetLinkMethod.NONE
                or self.elexon_asset_id is None
                or self.elexon_source is None
                or not self.evidence
                or len(self.candidates) != 1
            ):
                raise ValueError("linked decisions require one fully evidenced source")
            return
        if (
            self.confidence is not AssetLinkConfidence.NONE
            or self.elexon_asset_id is not None
            or self.elexon_source_asset_id is not None
            or self.elexon_source is not None
            or self.evidence
        ):
            raise ValueError("non-linked decisions cannot expose a selected source")
        if self.disposition is AssetLinkDisposition.UNLINKED and (
            self.method is not AssetLinkMethod.NONE or self.candidates
        ):
            raise ValueError("unlinked decisions cannot expose candidates")
        if self.disposition is AssetLinkDisposition.AMBIGUOUS and (
            self.method is AssetLinkMethod.NONE or not self.candidates
        ):
            raise ValueError("ambiguous decisions require evidenced candidates")


@dataclass(frozen=True, slots=True)
class _RankedCandidate:
    candidate: AssetLinkCandidate
    rank: tuple[int, int]


@dataclass(frozen=True, slots=True)
class _NameMatch:
    method: AssetLinkMethod
    elexon_field: str
    elexon_value: str
    rank: tuple[int, int]


_GENERIC_NAME_TOKENS = {
    "and",
    "battery",
    "centre",
    "development",
    "energy",
    "extension",
    "farm",
    "generator",
    "hydro",
    "limited",
    "ltd",
    "offshore",
    "onshore",
    "park",
    "phase",
    "plant",
    "power",
    "project",
    "solar",
    "station",
    "storage",
    "turbine",
    "unit",
    "wind",
}

_LOW_SPECIFICITY_TOKENS = {
    "bank",
    "black",
    "east",
    "green",
    "hill",
    "lower",
    "new",
    "north",
    "old",
    "south",
    "upper",
    "west",
    "white",
    "wood",
}

_COMPOUND_NAME_TOKENS: dict[str, tuple[str, ...]] = {
    "energycentre": ("energy", "centre"),
    "powerstation": ("power", "station"),
    "solarfarm": ("solar", "farm"),
    "solarpark": ("solar", "park"),
    "windfarm": ("wind", "farm"),
}

_LEGAL_ENTITY_SUFFIXES = {
    "co",
    "company",
    "inc",
    "incorporated",
    "limited",
    "llp",
    "ltd",
    "plc",
}

_TECHNOLOGY_FUELS: dict[str, frozenset[str]] = {
    "biomass co firing": frozenset({"BIOMASS"}),
    "biomass dedicated": frozenset({"BIOMASS"}),
    "large hydro": frozenset({"NPSHYD"}),
    "pumped storage hydroelectricity": frozenset({"PS"}),
    "small hydro": frozenset({"NPSHYD"}),
    "wind offshore": frozenset({"WIND"}),
    "wind onshore": frozenset({"WIND"}),
}


def link_repd_site(
    site: REPDSite,
    bm_units: Iterable[AssetReference],
) -> AssetLinkDecision:
    """Return one auditable link decision for a REPD site.

    Only candidates satisfying the complete evidence policy are exposed.  A
    caller therefore cannot mistake a fuzzy near-match for a proposed link.
    """

    repd_source = _repd_source(site)
    ranked_by_asset_id: dict[str, _RankedCandidate] = {}
    for unit in bm_units:
        candidate = _candidate(site, unit)
        if candidate is None:
            continue
        existing = ranked_by_asset_id.get(candidate.candidate.elexon_asset_id)
        if existing is None or candidate.rank > existing.rank:
            ranked_by_asset_id[candidate.candidate.elexon_asset_id] = candidate
    ranked = tuple(ranked_by_asset_id.values())
    if not ranked:
        return AssetLinkDecision(
            repd_source=repd_source,
            disposition=AssetLinkDisposition.UNLINKED,
            method=AssetLinkMethod.NONE,
            confidence=AssetLinkConfidence.NONE,
            elexon_asset_id=None,
            elexon_source_asset_id=None,
            elexon_source=None,
            evidence=(),
            candidates=(),
            reason=AssetLinkReason.NO_STRONG_CANDIDATE,
        )

    finalists = tuple(
        sorted(
            (ranked_candidate.candidate for ranked_candidate in ranked),
            key=lambda candidate: candidate.elexon_asset_id,
        )
    )
    method = max(ranked, key=lambda candidate: candidate.rank).candidate.method
    if len(finalists) != 1:
        return AssetLinkDecision(
            repd_source=repd_source,
            disposition=AssetLinkDisposition.AMBIGUOUS,
            method=method,
            confidence=AssetLinkConfidence.NONE,
            elexon_asset_id=None,
            elexon_source_asset_id=None,
            elexon_source=None,
            evidence=(),
            candidates=finalists,
            reason=AssetLinkReason.MULTIPLE_STRONG_CANDIDATES,
        )

    selected = finalists[0]
    return AssetLinkDecision(
        repd_source=repd_source,
        disposition=AssetLinkDisposition.LINKED,
        method=selected.method,
        confidence=AssetLinkConfidence.HIGH,
        elexon_asset_id=selected.elexon_asset_id,
        elexon_source_asset_id=selected.elexon_source_asset_id,
        elexon_source=selected.source,
        evidence=selected.evidence,
        candidates=finalists,
        reason=AssetLinkReason.UNIQUE_STRONG_CANDIDATE,
    )


def link_repd_sites(
    sites: Sequence[REPDSite],
    bm_units: Sequence[AssetReference],
) -> tuple[AssetLinkDecision, ...]:
    """Link a batch while enforcing one-to-one uniqueness across REPD sites."""

    token_index: dict[str, set[int]] = defaultdict(set)
    for index, unit in enumerate(bm_units):
        for value in (unit.display_name, unit.lead_party_name):
            if value is None:
                continue
            for token in set(_name_tokens(value)) - _GENERIC_NAME_TOKENS:
                if not token.isdigit():
                    token_index[token].add(index)

    decisions: list[AssetLinkDecision] = []
    for site in sites:
        possible_indexes: set[int] = set()
        for token in set(_name_tokens(site.project_name)) - _GENERIC_NAME_TOKENS:
            if not token.isdigit():
                possible_indexes.update(token_index.get(token, ()))
        possible_units = tuple(bm_units[index] for index in sorted(possible_indexes))
        decisions.append(link_repd_site(site, possible_units))
    by_elexon_id: dict[str, list[int]] = defaultdict(list)
    for index, decision in enumerate(decisions):
        if decision.elexon_asset_id is not None:
            by_elexon_id[decision.elexon_asset_id].append(index)

    for indexes in by_elexon_id.values():
        if len(indexes) < 2:
            continue
        conflicting_ids = tuple(
            sorted(decisions[index].repd_source.record_id for index in indexes)
        )
        for index in indexes:
            decision = decisions[index]
            decisions[index] = replace(
                decision,
                disposition=AssetLinkDisposition.AMBIGUOUS,
                confidence=AssetLinkConfidence.NONE,
                elexon_asset_id=None,
                elexon_source_asset_id=None,
                elexon_source=None,
                evidence=(),
                reason=AssetLinkReason.CROSS_SITE_COLLISION,
                conflicting_repd_source_ids=tuple(
                    source_id
                    for source_id in conflicting_ids
                    if source_id != decision.repd_source.record_id
                ),
            )
    return tuple(decisions)


def _candidate(site: REPDSite, unit: AssetReference) -> _RankedCandidate | None:
    name_match = _best_name_match(site.project_name, unit)
    if name_match is None:
        return None

    capacity_evidence = _capacity_evidence(site, unit)
    if capacity_evidence is None:
        return None

    fuel_compatible, fuel_evidence = _fuel_evidence(site, unit)
    if fuel_compatible is False:
        return None

    operator_evidence = _operator_evidence(site, unit)

    # Containment and lead-party names are weaker than an exact BM-unit display
    # name.  They must be corroborated by populated compatible fuel or operator
    # evidence; missing values do not satisfy that requirement.
    if (
        name_match.method is AssetLinkMethod.CONTAINED_SITE_IDENTITY
        or name_match.elexon_field == "lead_party_name"
    ) and fuel_evidence is None and operator_evidence is None:
        return None

    name_kind = (
        AssetLinkEvidenceKind.NAME_EXACT
        if name_match.method is AssetLinkMethod.EXACT_SITE_IDENTITY
        else AssetLinkEvidenceKind.NAME_CONTAINED
    )
    name_evidence = AssetLinkEvidence(
        kind=name_kind,
        repd_field="project_name",
        repd_value=site.project_name,
        elexon_field=name_match.elexon_field,
        elexon_value=name_match.elexon_value,
        detail=(
            "distinctive names are equal after conservative normalization"
            if name_kind is AssetLinkEvidenceKind.NAME_EXACT
            else "one distinctive normalized name is a high-coverage token phrase"
        ),
    )
    evidence = [name_evidence, capacity_evidence]
    if fuel_evidence is not None:
        evidence.append(fuel_evidence)
    if operator_evidence is not None:
        evidence.append(operator_evidence)
    candidate = AssetLinkCandidate(
        elexon_asset_id=unit.asset_id,
        elexon_source_asset_id=unit.source_asset_id,
        source=_elexon_source(unit),
        method=name_match.method,
        evidence=tuple(evidence),
    )
    return _RankedCandidate(candidate=candidate, rank=name_match.rank)


def _best_name_match(project_name: str, unit: AssetReference) -> _NameMatch | None:
    project_tokens = _name_tokens(project_name)
    if not _is_distinctive(project_tokens, contained=False):
        return None

    matches: list[_NameMatch] = []
    for field_name, value, field_rank in (
        ("display_name", unit.display_name, 2),
        ("lead_party_name", unit.lead_party_name, 1),
    ):
        if value is None:
            continue
        candidate_tokens = _name_tokens(value)
        if project_tokens == candidate_tokens:
            matches.append(
                _NameMatch(
                    method=AssetLinkMethod.EXACT_SITE_IDENTITY,
                    elexon_field=field_name,
                    elexon_value=value,
                    rank=(2, field_rank),
                )
            )
            continue
        if _strong_containment(project_tokens, candidate_tokens):
            matches.append(
                _NameMatch(
                    method=AssetLinkMethod.CONTAINED_SITE_IDENTITY,
                    elexon_field=field_name,
                    elexon_value=value,
                    rank=(1, field_rank),
                )
            )
    return max(matches, key=lambda match: match.rank) if matches else None


def _capacity_evidence(
    site: REPDSite,
    unit: AssetReference,
) -> AssetLinkEvidence | None:
    if site.capacity_mw is None or not math.isfinite(site.capacity_mw):
        return None
    possible: list[tuple[str, float]] = []
    if unit.generation_capacity_mw is not None:
        possible.append(("generation_capacity_mw", unit.generation_capacity_mw))
    if site.is_storage and unit.demand_capacity_mw is not None:
        possible.append(("demand_capacity_mw", abs(unit.demand_capacity_mw)))

    compatible: list[tuple[float, str, float, float]] = []
    for field_name, value in possible:
        if not math.isfinite(value) or value <= 0:
            continue
        difference = abs(site.capacity_mw - value)
        tolerance = max(0.1, 0.02 * max(site.capacity_mw, value))
        if difference <= tolerance:
            compatible.append((difference, field_name, value, tolerance))
    if not compatible:
        return None
    difference, field_name, value, tolerance = min(compatible)
    return AssetLinkEvidence(
        kind=AssetLinkEvidenceKind.CAPACITY_COMPATIBLE,
        repd_field="capacity_mw",
        repd_value=f"{site.capacity_mw:g}",
        elexon_field=field_name,
        elexon_value=f"{value:g}",
        detail=f"absolute difference {difference:g} MW within {tolerance:g} MW tolerance",
    )


def _fuel_evidence(
    site: REPDSite,
    unit: AssetReference,
) -> tuple[bool | None, AssetLinkEvidence | None]:
    expected = _TECHNOLOGY_FUELS.get(_word_key(site.technology))
    if expected is None or unit.fuel_type is None:
        return None, None
    actual = re.sub(r"[^A-Z0-9]", "", unit.fuel_type.upper())
    if actual not in expected:
        return False, None
    return True, AssetLinkEvidence(
        kind=AssetLinkEvidenceKind.FUEL_COMPATIBLE,
        repd_field="technology",
        repd_value=site.technology,
        elexon_field="fuel_type",
        elexon_value=unit.fuel_type,
        detail=f"REPD technology maps conservatively to Elexon fuel {actual}",
    )


def _operator_evidence(
    site: REPDSite,
    unit: AssetReference,
) -> AssetLinkEvidence | None:
    if site.operator_name is None or unit.lead_party_name is None:
        return None
    repd_tokens = _operator_tokens(site.operator_name)
    elexon_tokens = _operator_tokens(unit.lead_party_name)
    if not repd_tokens or not elexon_tokens:
        return None
    if repd_tokens != elexon_tokens:
        # REPD's operator/applicant and Elexon's current lead trading party are
        # different roles.  A match corroborates identity; a difference is not
        # a contradiction because owners commonly use optimisers or suppliers.
        return None
    return AssetLinkEvidence(
        kind=AssetLinkEvidenceKind.OPERATOR_COMPATIBLE,
        repd_field="operator_name",
        repd_value=site.operator_name,
        elexon_field="lead_party_name",
        elexon_value=unit.lead_party_name,
        detail="operator names are equal after punctuation and legal-suffix normalization",
    )


def _strong_containment(left: tuple[str, ...], right: tuple[str, ...]) -> bool:
    shorter, longer = (left, right) if len(left) <= len(right) else (right, left)
    if len(shorter) < 3 or len(shorter) / len(longer) < 0.6:
        return False
    if not _is_distinctive(shorter, contained=True):
        return False
    return any(
        longer[index : index + len(shorter)] == shorter
        for index in range(len(longer) - len(shorter) + 1)
    )


def _is_distinctive(tokens: tuple[str, ...], *, contained: bool) -> bool:
    distinctive = [
        token
        for token in tokens
        if token not in _GENERIC_NAME_TOKENS and not token.isdigit()
    ]
    if not distinctive:
        return False
    if not contained:
        return len(tokens) >= 2 or len(distinctive[0]) >= 6
    strong = [
        token
        for token in distinctive
        if token not in _LOW_SPECIFICITY_TOKENS and not token.isdigit()
    ]
    return len(strong) >= 2 or any(len(token) >= 5 for token in strong)


def _name_tokens(value: str) -> tuple[str, ...]:
    normalized = unicodedata.normalize("NFKD", value.casefold())
    normalized = "".join(
        character for character in normalized if not unicodedata.combining(character)
    )
    normalized = normalized.replace("&", " and ").replace("'", "").replace("’", "")
    raw_tokens = re.findall(r"[a-z0-9]+", normalized)
    tokens: list[str] = []
    for token in raw_tokens:
        tokens.extend(_COMPOUND_NAME_TOKENS.get(token, (token,)))
    return tuple(tokens)


def _operator_tokens(value: str) -> tuple[str, ...]:
    tokens = list(_name_tokens(value))
    while tokens and tokens[-1] in _LEGAL_ENTITY_SUFFIXES:
        tokens.pop()
    return tuple(tokens)


def _word_key(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))


def _repd_source(site: REPDSite) -> AssetLinkSource:
    return AssetLinkSource(
        authority=site.provenance.publisher,
        dataset=site.provenance.dataset,
        record_id=site.source_id,
        locator=site.provenance.source_url,
        retrieved_at=site.provenance.retrieved_at,
    )


def _elexon_source(unit: AssetReference) -> AssetLinkSource:
    return AssetLinkSource(
        authority=unit.provenance.source_id,
        dataset=unit.provenance.dataset,
        record_id=unit.asset_id,
        locator=unit.provenance.endpoint,
        retrieved_at=unit.provenance.retrieved_at,
    )
