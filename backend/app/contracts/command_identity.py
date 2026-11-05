"""Pure legacy command receipt identity; this module does not register handlers."""

import hashlib
import json

from .analysis_paste_identity import original_paste_identity
from .discovery_command_identity import original_discovery_accept_identity
from .review_command_identity import original_review_identity


def request_digest(command_name: str, payload: dict) -> str:
    if command_name in {
        "opening_evidence.checkpoint",
        "cards.review",
        "cards.review.reconcile",
    }:
        identity_payload = original_review_identity(payload)
    elif command_name == "analysis.paste.commit":
        identity_payload = original_paste_identity(payload)
    elif command_name == "discovery.accept":
        identity_payload = original_discovery_accept_identity(payload)
    else:
        identity_payload = payload
    serialized = json.dumps(
        [command_name, identity_payload], sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(serialized.encode()).hexdigest()
