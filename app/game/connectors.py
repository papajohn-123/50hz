"""Versioned connector membership used by auditable game resolution."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from app.sources.elexon import INTERCONNECTOR_NAMES


@dataclass(frozen=True, slots=True)
class ConnectorRegistry:
    version: str
    effective_from: date
    expected_connector_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.version.strip():
            raise ValueError("connector registry version cannot be blank")
        normalized = tuple(sorted({code.strip().upper() for code in self.expected_connector_ids}))
        if not normalized:
            raise ValueError("connector registry cannot be empty")
        unknown = set(normalized).difference(INTERCONNECTOR_NAMES)
        if unknown:
            raise ValueError(
                "connector registry contains unknown Elexon codes: "
                + ", ".join(sorted(unknown))
            )
        object.__setattr__(self, "expected_connector_ids", normalized)


# Registry versions and their literal memberships are append-only. Do not derive
# an old version from the adapter's current map: adding a future interconnector
# there must not retroactively change a historical prediction rule.
_ELEXON_FUELINST_CONNECTORS_V1 = (
    "INTELEC",
    "INTEW",
    "INTFR",
    "INTGRNL",
    "INTIFA2",
    "INTIRL",
    "INTNED",
    "INTNEM",
    "INTNSL",
    "INTVKL",
)


CONNECTOR_REGISTRIES = (
    ConnectorRegistry(
        version="elexon-fuelinst-connectors-v1",
        effective_from=date(2026, 1, 1),
        expected_connector_ids=_ELEXON_FUELINST_CONNECTORS_V1,
    ),
)


def connector_registry_for_date(day: date) -> ConnectorRegistry:
    applicable = tuple(
        registry for registry in CONNECTOR_REGISTRIES if registry.effective_from <= day
    )
    if not applicable:
        raise ValueError(f"no connector registry covers {day.isoformat()}")
    return max(applicable, key=lambda registry: registry.effective_from)
