from app.intelligence.models import EvidencePacket, GroundedExplanation


def deterministic_explanation(packet: EvidencePacket) -> GroundedExplanation:
    facts = {fact.metric: fact for fact in packet.facts}
    refs = list(packet.source_refs)[:3]
    if packet.event_type == "reported_unit_unavailability":
        subject = facts.get("reported_notice_subject") or facts.get("unavailable_asset")
        capacity = facts.get("unavailable_capacity_mw") or facts.get("unavailable_capacity")
        subject_text = str(subject.value) if subject else "A generating unit"
        headline_subject = subject_text[:68].rstrip()
        if capacity is not None:
            rendered_capacity = (
                f"{capacity.value} {capacity.unit}"
                if capacity.unit
                else str(capacity.value)
            )
            plain_language = (
                f"The published notice reports {rendered_capacity} unavailable for {subject_text}."
            )
        else:
            plain_language = f"The published notice reports an unavailability for {subject_text}."
        if cause := facts.get("reported_cause"):
            cause_text = str(cause.value)[:300].strip()
            if cause_text:
                plain_language += f" The notice reports the cause as: {cause_text}"
        return GroundedExplanation(
            headline=f"{headline_subject}: reported unavailability",
            plain_language=plain_language,
            caveat="; ".join(packet.unknowns[:2]) or None,
            evidence_refs=refs,
            suggested_questions=["Has a cause been reported?", "When is it due back?"],
        )
    if packet.event_type == "reported_system_warning":
        warning = facts.get("reported_warning_text")
        return GroundedExplanation(
            headline="System warning reported",
            plain_language=(
                str(warning.value)
                if warning is not None
                else "The system operator has published a system warning."
            ),
            caveat="This text reflects the published warning; 50Hz has not inferred a cause.",
            evidence_refs=refs,
            suggested_questions=["What does this warning mean?", "Show the source notice"],
        )

    primary = packet.facts[0]
    value = primary.value
    rendered = f"{value} {primary.unit}" if primary.unit else str(value)
    caveat = "; ".join(packet.unknowns[:2]) or None
    return GroundedExplanation(
        headline=primary.label.capitalize(),
        plain_language=f"The latest validated data reports {primary.label} as {rendered}.",
        why_it_matters=None,
        caveat=caveat,
        evidence_refs=refs,
        suggested_questions=["What changed before this?", "Show the source data"],
    )
