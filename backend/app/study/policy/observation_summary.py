"""Pure per-observation contributions shared by incremental and rebuild writers."""


def observation_counts(observation: dict) -> tuple[int, int, int, int, int, int]:
    """Return responded, clean, incorrect, assisted, manual-failure, corrected.

    Preserve stored numeric conversion semantics: the flag-like fields
    clean, manual_failure and corrected deliberately use int, not bool.
    Incorrect requires exactly False; assistance requires an actual response.
    Distinct clean-day accounting remains with the callers.
    """
    responded = observation.get("first_response_uci") is not None
    return (
        int(responded),
        int(observation.get("clean", False)),
        int(observation.get("first_response_correct") is False),
        int(responded and bool(observation.get("assistance_before_response"))),
        int(observation.get("manual_failure", False)),
        int(observation.get("corrected", False)),
    )
