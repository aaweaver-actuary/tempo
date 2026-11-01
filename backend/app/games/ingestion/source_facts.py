"""Pure facts projected from retained Games source records."""

from datetime import datetime, timezone
import re


def retained_time_precision(game) -> str:
    """Conservative projection of the retained import normalizer's source fact.

    Its date-only fallback is exactly UTC midnight; preserve that ambiguity.
    Non-midnight explicit HH:MM:SS cannot come from that fallback. Partial or
    absent time is never upgraded to precise chronology.
    """
    value = game.get("played_at", "")
    if not isinstance(value, str) or not re.search(r"T\d{2}:\d{2}:\d{2}", value):
        return "unknown"
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if moment.utcoffset() is None:
            return "unknown"
        moment = moment.astimezone(timezone.utc)
        return "second" if (moment.hour, moment.minute, moment.second, moment.microsecond) != (0, 0, 0, 0) else "unknown"
    except ValueError:
        return "unknown"


