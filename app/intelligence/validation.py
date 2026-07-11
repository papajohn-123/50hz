import re
from decimal import Decimal, InvalidOperation

from app.intelligence.models import EvidencePacket, GroundedExplanation


class ExplanationValidationError(ValueError):
    pass


_NUMBER_RE = re.compile(r"(?<![\w])[-+]?\d+(?:,\d{3})*(?:\.\d+)?")
_FACT_NUMBER_RE = re.compile(r"[-+]?\d+(?:,\d{3})*(?:\.\d+)?")
_CAUSAL_PHRASES = (" caused ", " because ", " led to ", " as a result ", " triggered ")


def _normalise_number(value: str | int | float) -> Decimal | None:
    try:
        return Decimal(str(value).replace(",", "")).normalize()
    except (InvalidOperation, ValueError):
        return None


def validate_explanation(explanation: GroundedExplanation, packet: EvidencePacket) -> None:
    supplied_refs = set(packet.source_refs)
    used_refs = set(explanation.evidence_refs)
    if not used_refs or not used_refs.issubset(supplied_refs):
        raise ExplanationValidationError("Explanation contains missing or unknown evidence references")

    allowed_numbers: set[Decimal] = set()
    for fact in packet.facts:
        for raw in _FACT_NUMBER_RE.findall(str(fact.value)):
            number = _normalise_number(raw.replace(",", ""))
            if number is not None:
                allowed_numbers.update((number, abs(number)))
    if any(fact.metric == "frequency_hz" for fact in packet.facts):
        allowed_numbers.add(Decimal("50"))
    text = " ".join(
        part
        for part in (
            explanation.headline,
            explanation.plain_language,
            explanation.why_it_matters,
            explanation.caveat,
        )
        if part
    )
    for match in _NUMBER_RE.findall(text):
        number = _normalise_number(match)
        if number not in allowed_numbers:
            raise ExplanationValidationError(f"Unsupported numerical claim: {match}")

    lowered = f" {text.lower()} "
    if not packet.cause_reported and any(phrase in lowered for phrase in _CAUSAL_PHRASES):
        raise ExplanationValidationError("Causal language is not supported by the evidence packet")
