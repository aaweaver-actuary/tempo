"""Immutable opaque JSON and shared boundary validation; standard library only."""
from collections.abc import Mapping
import json
from math import isfinite
import re

MAX_EXACT_JSON_INTEGER = 2**53 - 1


class EvidenceMathError(ValueError):
    def __init__(self, code, message, field_path=""):
        super().__init__(message)
        self.code, self.field_path = code, field_path


def identifier(value):
    if not isinstance(value, str) or not value.strip():
        raise EvidenceMathError("invalid_identity", "Expected nonempty identity")


def digest(value):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value):
        raise EvidenceMathError("invalid_digest", "Expected lowercase SHA-256 digest")


def number(value, *, probability=False):
    if type(value) not in (int, float):
        raise EvidenceMathError("invalid_numeric", "Expected a finite number, never a boolean")
    if type(value) is int and abs(value) > MAX_EXACT_JSON_INTEGER:
        raise EvidenceMathError("invalid_numeric", "Integer exceeds exact JSON range")
    if not isfinite(value) or (isinstance(value, float) and value.is_integer() and abs(value) > MAX_EXACT_JSON_INTEGER):
        raise EvidenceMathError("invalid_numeric", "Number exceeds finite exact JSON range")
    if probability and not 0 <= value <= 1:
        raise EvidenceMathError("invalid_numeric", "Probability must be in [0,1]")
    return value


def plain(value):
    if isinstance(value, CanonicalObject):
        return json.loads(value.json_text)
    if isinstance(value, Mapping):
        return {key: plain(child) for key, child in value.items()}
    if isinstance(value, (tuple, list)):
        return [plain(child) for child in value]
    return value


def validate_json(value):
    if type(value) in (float, int):
        number(value)
    elif isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str):
                raise EvidenceMathError("invalid_json", "JSON object keys must be strings")
            validate_json(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            validate_json(child)
    elif value is not None and type(value) not in (str, bool):
        raise EvidenceMathError("invalid_json", "Expected JSON data")


class CanonicalObject(Mapping):
    """Only immutable text is stored; nested objects/arrays expose frozen views."""
    __slots__ = ('json_text',)

    def __init__(self, value):
        if hasattr(self, 'json_text'):
            raise TypeError('CanonicalObject is immutable')
        if isinstance(value, CanonicalObject):
            object.__setattr__(self, 'json_text', value.json_text)
            return
        value = plain(value)
        if not isinstance(value, dict):
            raise ValueError('Expected JSON object')
        validate_json(value)
        object.__setattr__(self, 'json_text', json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False))

    def __setattr__(self, name, value):
        raise TypeError('CanonicalObject is immutable')

    def __delattr__(self, name):
        raise TypeError('CanonicalObject is immutable')

    def __getitem__(self, key):
        return freeze(json.loads(self.json_text)[key])

    def __iter__(self):
        return iter(json.loads(self.json_text))

    def __len__(self):
        return len(json.loads(self.json_text))

    def __eq__(self, other):
        try:
            return self.json_text == CanonicalObject(other).json_text
        except (ValueError, TypeError):
            return False

    def __hash__(self):
        return hash(self.json_text)

    def __deepcopy__(self, memo):
        return self

    def to_dict(self):
        return json.loads(self.json_text)

    @classmethod
    def __get_pydantic_core_schema__(cls, source_type, handler):
        from pydantic_core import core_schema
        type JsonData = dict[str, JsonData] | list[JsonData] | str | int | float | bool | None
        # Describe both wire directions without replacing our strict validator.
        json_object_schema = handler.generate_schema(dict[str, JsonData])
        return core_schema.no_info_plain_validator_function(cls,
            json_schema_input_schema=json_object_schema,
            serialization=core_schema.plain_serializer_function_ser_schema(
                lambda value: value.to_dict(), return_schema=json_object_schema))


def freeze(value):
    if isinstance(value, Mapping):
        return CanonicalObject(value)
    if isinstance(value, (tuple, list)):
        return tuple(freeze(child) for child in value)
    return value


def immutable_dataclass(_class=None, **options):
    """Detach nested input containers before the record's existing validation."""
    from dataclasses import dataclass, fields
    def decorate(cls):
        original = cls.__dict__.get('__post_init__')
        def post_init(self, *arguments):
            for field in fields(self):
                if field.init:
                    object.__setattr__(self, field.name, freeze(getattr(self, field.name)))
            if original is not None:
                original(self, *arguments)
        cls.__post_init__ = post_init
        return dataclass(cls, **options)
    return decorate(_class) if _class is not None else decorate
