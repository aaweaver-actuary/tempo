"""Package-local wire primitives; no application or provider imports."""

from __future__ import annotations

from datetime import datetime
import json
import re
from typing import Annotated, Literal, TypeVar
from pydantic import BaseModel, ConfigDict, Field, AfterValidator, model_validator

Identifier = Annotated[str, Field(min_length=1, max_length=200)]
Revision = Annotated[int, Field(ge=1, le=2**53 - 1)]
Count = Annotated[int, Field(ge=0, le=2**53 - 1)]
Probability = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
UciMove = Annotated[str, Field(pattern=r"^[a-h][1-8][a-h][1-8][qrbn]?$")]
Square = Annotated[str, Field(pattern=r"^[a-h][1-8]$")]


def check_timestamp(value: str) -> str:
    matches = re.fullmatch(
        r"([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})"
        r"(?:\.([0-9]{1,6}))?(?:Z|([+-])([0-9]{2}):([0-9]{2}))",
        value,
    )
    if matches is None:
        raise ValueError("timestamp must match the complete timezone-aware wire form")
    year, month, day, hour, minute, second = map(int, matches.groups()[:6])
    if not (
        1 <= year <= 9999
        and 1 <= month <= 12
        and 1 <= day <= 31
        and hour <= 23
        and minute <= 59
        and second <= 59
    ):
        raise ValueError("timestamp calendar or clock components are out of bounds")
    if matches[8] is not None and (int(matches[9]) > 23 or int(matches[10]) > 59):
        raise ValueError("timestamp offset components are out of bounds")
    # fromisoformat validates the Gregorian day within its month; explicit
    # bounds above prevent its normalization of malformed offsets such as +00:60.
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise ValueError("timestamp must include timezone")
    return value


Timestamp = Annotated[
    str,
    Field(
        pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$"
    ),
    AfterValidator(check_timestamp),
]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    @model_validator(mode="before")
    @classmethod
    def validate_wire_numbers(cls, value):
        def visit(item):
            if isinstance(item, dict):
                for child in item.values():
                    visit(child)
            elif isinstance(item, (list, tuple)):
                for child in item:
                    visit(child)
            elif type(item) is int and abs(item) > 2**53 - 1:
                raise ValueError("integer exceeds interoperable JSON range")

        visit(value)
        if (
            isinstance(value, dict)
            and "contract_version" in value
            and type(value["contract_version"]) is not int
        ):
            raise ValueError("contract_version must be an integer")
        return value


class WireModel(Model):
    contract_version: Literal[1]


ContractType = TypeVar("ContractType", bound=Model)


def validate_contract(model: type[ContractType], payload: str | bytes) -> ContractType:
    return model.model_validate_json(payload)


def serialize_contract(value: Model) -> str:
    """Canonical validated payload, including explicit defaults and nulls."""
    return json.dumps(
        value.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
