from collections.abc import Mapping

from app.api.models import ConditionHeadline


def cleanliness_label(carbon_intensity: float) -> str:
    """Use broad, explicitly comparative bands until history supports percentiles."""

    if carbon_intensity < 100:
        return "Lower carbon"
    if carbon_intensity < 200:
        return "Typical carbon"
    return "Higher carbon"


def balance_label(frequency_hz: float | None, *, active_system_warning: bool = False) -> str:
    """Describe frequency only; never infer a formal system-balance condition."""

    if active_system_warning:
        return "System warning"
    if frequency_hz is None:
        return "Frequency unavailable"
    deviation = abs(frequency_hz - 50.0)
    if deviation <= 0.10:
        return "Frequency near 50 Hz"
    if deviation <= 0.20:
        return "Frequency away from 50 Hz"
    return "Frequency excursion"


def energy_position_label(net_import_mw: float) -> str:
    if net_import_mw <= -250:
        return "Net exporting"
    if net_import_mw < 250:
        return "Near-neutral flows"
    return "Net importing"


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
    leader = max(generation_mw, key=generation_mw.__getitem__) if generation_mw else "Supply"
    share = generation_mw.get(leader, 0.0) / total * 100 if total else 0
    flow_gw = abs(net_import_mw) / 1_000
    flow_phrase = (
        f"exporting {flow_gw:.1f} GW"
        if net_import_mw < -250
        else f"importing {flow_gw:.1f} GW"
        if net_import_mw > 250
        else "near neutral across the displayed interconnectors"
    )
    leader_label = leader.replace("_", " ").title()
    leader_verb = "are" if leader == "imports" else "is"
    interpretation = (
        f"{leader_label} {leader_verb} the largest displayed supply component "
        f"at {share:.0f}% of this partial mix. "
        f"Britain is {flow_phrase}; demand is {demand_mw / 1_000:.1f} GW."
    )
    return ConditionHeadline(
        cleanliness=clean,
        balance=balance,
        energy_position=position,
        interpretation=interpretation,
    )
