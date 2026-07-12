from __future__ import annotations

from hashlib import sha256


def reported_notice_event_id(
    *,
    source_id: str,
    notice_kind: str,
    external_id: str,
) -> str:
    """Return the stable public identity for one reported notice lifecycle."""

    identity = f"reported-notice:v1:{source_id}:{notice_kind}:{external_id}"
    return f"evt_{sha256(identity.encode('utf-8')).hexdigest()[:20]}"
