from app.intelligence.models import EvidencePacket, GroundedExplanation


def deterministic_explanation(packet: EvidencePacket) -> GroundedExplanation:
    primary = packet.facts[0]
    value = primary.value
    rendered = f"{value} {primary.unit}" if primary.unit else str(value)
    caveat = "; ".join(packet.unknowns[:2]) or None
    refs = list(packet.source_refs)[:3]
    return GroundedExplanation(
        headline=primary.label.capitalize(),
        plain_language=f"The latest validated data reports {primary.label} as {rendered}.",
        why_it_matters=None,
        caveat=caveat,
        evidence_refs=refs,
        suggested_questions=["What changed before this?", "Show the source data"],
    )

