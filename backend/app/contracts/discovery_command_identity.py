"""Identity of the user's discovery acceptance, without preparation data."""


def original_discovery_accept_identity(payload: dict) -> dict:
    return {
        key: payload[key]
        for key in ("opportunity_id", "selected_move_uci", "evidence_fingerprint")
    }
