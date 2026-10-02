"""Shared validated primitives for the public diagnostic contract."""
from datetime import datetime
from typing import Annotated, Literal
from pydantic import AfterValidator, Field
from .background_metric_kinds import KINDS


def _timestamp_with_timezone(value: str) -> str:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError('Diagnostic timestamps require a timezone')
    return value


DiagnosticTimestamp = Annotated[str, AfterValidator(_timestamp_with_timezone), Field(json_schema_extra={'format':'date-time'})]
WorkKind = Literal[tuple(sorted(KINDS))]
WorkState = Literal['queued','retrying','delayed','paused','leased','failed','complete','blocked','superseded','publishing']
