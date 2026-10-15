from .opening_segmentation import stable_key

VERSION = "frozen-maia-v1"
READER_VERSION = "frozen-maia-v2"


def content_fingerprint(card, memberships):
    return stable_key(
        VERSION,
        card["id"],
        card["revision"],
        card["start_fen"],
        card["moves_json"],
        card.get("trained_color"),
        card.get("kind"),
        card.get("content_type"),
        card.get("source_ref"),
        card.get("source_fen"),
        sorted(set(memberships)),
    )
