"""Public JSON value conversion for Intelligence response envelopes."""
from datetime import datetime


def public(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: public(item) for key, item in value.items()}
    if isinstance(value, list):
        return [public(item) for item in value]
    return value
