"""Product v0.2 value seams; no runtime composition or effect implementation."""
from datetime import datetime, timezone
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

INTERFACE_VERSION = "0.2.0"
Identifier = Annotated[str, Field(min_length=1, max_length=200)]
Revision = Annotated[int, Field(ge=1, le=2**53 - 1)]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
UciMove = Annotated[str, Field(pattern=r"^[a-h][1-8][a-h][1-8][qrbn]?$")]


def utc_timestamp(value: str) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if "T" not in value or parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError("timestamp must explicitly identify UTC")
    return value


UtcTimestamp = Annotated[str, AfterValidator(utc_timestamp)]


class Value(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, allow_inf_nan=False)
