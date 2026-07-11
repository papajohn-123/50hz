from collections.abc import Mapping

from app.api.models import ConditionHeadline


def cleanliness_label(carbon_intensity: float) -> str:
    """Bootstrap bands until rolling GB percentiles have enough stored history."""

    if carbon_intensity < 50:
        return "Exceptionally clean"
    if carbon_intensity < 100:
        return "Clean"
    if carbon_intensity < 180:
        return "Typical"
    if carbon_intensity < 260:
        return "Carbon intensive"
    return "Exceptionally carbon intensive"


def balance_label(frequency_hz: float | None, *, active_system_warning: bool = False) -> str:
    if active_system_warning:
        return "System event"
    if frequency_hz is None:
        return "Unknown"
    deviation = abs(frequency_hz - 50.0)
    if deviation <= 0.05:
        return "Comfortable"
    if deviation <= 0.10:
        return "Balanced"
    if deviation <= 0.15:
        return "Tightening"
    if deviation <= 0.20:
        return "Stretched"
    return "System event"


def energy_position_label(net_import_mw: float) -> str:
    if net_import_mw <= -2_000:
        return "Exporting strongly"
    if net_import_mw <= -250:
        return "Exporting"
    if net_import_mw < 250:
        return "Broadly balanced"
    if net_import_mw < 3_000:
        return "Importing"
    return "Import-dependent"


def build_headline(
    *,
    carbon_intensity: float,
    frequency_hz: float | None,
    net_import_mw: float,
    generation_mw: Mapping[str, float],
    demand_mw: float,
    active_system_warning: bool = False,
) -> ConditionHeadline:
    clean = cleanliness_label(carbon_intensity)
    balance = balance_label(frequency_hz, active_system_warning=active_system_warning)
    position = energy_position_label(net_import_mw)
    total = sum(max(0.0, value) for value in generation_mw.values())
    leader = max(generation_mw, key=generation_mw.__getitem__) if generation_mw else "Generation"
    share = generation_mw.get(leader, 0.0) / total * 100 if total else 0
    flow_gw = abs(net_import_mw) / 1_000
    flow_phrase = (
        f"exporting {flow_gw:.1f} GW"
        if net_import_mw < -250
        else f"importing {flow_gw:.1f} GW"
        if net_import_mw > 250
        else "broadly balanced with neighbouring systems"
    )
    leader_label = leader.replace("_", " ").title()
    leader_verb = "are" if leader == "imports" else "is"
    interpretation = (
        f"{leader_label} {leader_verb} the largest source at {share:.0f}% of generation. "
        f"Britain is {flow_phrase}; demand is {demand_mw / 1_000:.1f} GW."
    )
    return ConditionHeadline(
        cleanliness=clean,
        balance=balance,
        energy_position=position,
        interpretation=interpretation,
    )
