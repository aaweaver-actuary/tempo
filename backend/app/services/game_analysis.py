from __future__ import annotations


def classify_swings(evaluations: list[dict], user_color: str, threshold: int = 100, starting_color: str = "white") -> dict[str, int | None]:
    """Classify engine evaluations already normalized to White's point of view."""
    sign = 1 if user_color == "white" else -1
    major = None
    missed = None
    for item in evaluations:
        ply = int(item["ply"])
        before = int(item["before_cp"]) * sign
        after = int(item["after_cp"]) * sign
        user_moved = (ply % 2 == 0) == (user_color == starting_color)
        loss = before - after
        if user_moved and loss >= threshold and major is None:
            major = ply
        if user_moved and bool(item.get("opponent_created_chance")) and loss >= threshold and missed is None:
            missed = ply
    return {"major_mistake_ply": major, "missed_punishment_ply": missed}
