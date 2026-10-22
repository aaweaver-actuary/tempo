"""Describe fixed Learning fixture profiles without executing them."""


def fixture_components(profile: str) -> tuple[str, ...]:
    """Return the ordered fixture components for an exact profile name."""
    if profile == "essential":
        return ("learning",)
    if profile == "full":
        return ("learning", "game-evidence", "content", "analysis-a-bridge")
    raise ValueError(f"Unknown Learning fixture profile: {profile!r}")
