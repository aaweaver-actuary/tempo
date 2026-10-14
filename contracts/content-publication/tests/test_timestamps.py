"""Own installed public envelope validates timestamps without changing wire spelling."""

import importlib
import json
from pathlib import Path
import pytest
from pydantic import ValidationError
from tempo_content_publication_contracts import v1
from .conftest import load_fixture

TIMESTAMP_CASES = [
    ("no_fraction", "2026-10-14T12:00:00Z", True),
    ("fraction_1", "2026-10-14T12:00:00.1Z", True),
    ("fraction_2", "2026-10-14T12:00:00.12Z", True),
    ("fraction_3", "2026-10-14T12:00:00.123Z", True),
    ("fraction_4", "2026-10-14T12:00:00.1234Z", True),
    ("fraction_5", "2026-10-14T12:00:00.12345Z", True),
    ("fraction_6", "2026-10-14T12:00:00.123456Z", True),
    ("positive_offset", "2026-10-14T12:00:00.000001+02:30", True),
    ("negative_offset", "2026-10-14T12:00:00.120000-05:45", True),
    ("positive_zero_offset", "2026-10-14T12:00:00+00:00", True),
    ("negative_zero_offset", "2026-10-14T12:00:00-00:00", True),
    ("leap_year_2000", "2000-02-29T00:00:00Z", True),
    ("year_0001", "0001-01-01T00:00:00Z", True),
    ("year_0099", "0099-12-31T23:59:59Z", True),
    ("year_9999", "9999-12-31T23:59:59.999999Z", True),
    ("offset_before_year_0001", "0001-01-01T00:00:00+23:59", True),
    ("offset_after_year_9999", "9999-12-31T23:59:59-23:59", True),
    ("invalid_leap_1900", "1900-02-29T00:00:00Z", False),
    ("invalid_leap_2026", "2026-02-29T00:00:00Z", False),
    ("invalid_month", "2026-13-01T00:00:00Z", False),
    ("invalid_day", "2026-04-31T00:00:00Z", False),
    ("year_0000", "0000-01-01T00:00:00Z", False),
    ("offset_plus_minutes_60", "2026-10-14T12:00:00+00:60", False),
    ("offset_minus_minutes_60", "2026-10-14T12:00:00-00:60", False),
    ("offset_minutes_99", "2026-10-14T12:00:00+01:99", False),
    ("offset_hours_24", "2026-10-14T12:00:00+24:00", False),
    ("fraction_7", "2026-10-14T12:00:00.1234567Z", False),
    ("missing_timezone", "2026-10-14T12:00:00", False),
    ("hour_24", "2026-10-14T24:00:00Z", False),
    ("minute_60", "2026-10-14T12:60:00Z", False),
    ("second_60", "2026-10-14T12:00:60Z", False),
    ("lowercase_t", "2026-10-14t12:00:00Z", False),
    ("lowercase_z", "2026-10-14T12:00:00z", False),
    ("trailing_junk", "2026-10-14T12:00:00Zjunk", False),
    ("trailing_newline", "2026-10-14T12:00:00Z\n", False),
    ("leading_space", " 2026-10-14T12:00:00Z", False),
    ("trailing_space", "2026-10-14T12:00:00Z ", False),
]


@pytest.mark.parametrize(
    "case_name,timestamp,accepted",
    TIMESTAMP_CASES,
    ids=[case[0] for case in TIMESTAMP_CASES],
)
def test_installed_timestamp_validity_and_original_wire_spelling(
    case_name, timestamp, accepted
):
    installed = Path(
        importlib.import_module("tempo_content_publication_contracts").__file__
    ).resolve()
    assert "site-packages" in installed.parts
    payload = load_fixture("publication")
    field_path = ["built_at"]
    target = payload
    for field in field_path[:-1]:
        target = target[field]
    target[field_path[-1]] = timestamp
    if not accepted:
        with pytest.raises(ValidationError):
            v1.validate_contract(v1.Publication, json.dumps(payload))
        return
    value = v1.validate_contract(v1.Publication, json.dumps(payload))
    serialized = json.loads(v1.serialize_contract(value))
    for field in field_path:
        serialized = serialized[field]
    assert serialized == timestamp
