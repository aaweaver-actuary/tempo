"""Original paste request identity, independent of a derived preview."""


def original_paste_identity(payload: dict):
    return payload["request"]
