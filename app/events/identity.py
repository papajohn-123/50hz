from __future__ import annotations

from hashlib import sha256
import re


_STABLE_EVENT_ID = re.compile(r"evt_[0-9a-f]{20}")


def reported_notice_event_id(
    *,
    source_id: str,
    notice_kind: str,
    external_id: str,
) -> str:
    """Return the stable public identity for one reported notice lifecycle."""

    identity = f"reported-notice:v1:{source_id}:{notice_kind}:{external_id}"
    return f"evt_{sha256(identity.encode('utf-8')).hexdigest()[:20]}"


def is_stable_event_id(value: object) -> bool:
    """Return whether ``value`` has the non-reversible public event-ID shape."""

    return isinstance(value, str) and _STABLE_EVENT_ID.fullmatch(value) is not None
