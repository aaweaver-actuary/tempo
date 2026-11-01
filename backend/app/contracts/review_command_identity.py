"""Original review/checkpoint identity, excluding derived preparation."""


def original_review_identity(payload: dict) -> dict:
    return {key: value for key, value in payload.items() if key != "prepared_manifest"}
