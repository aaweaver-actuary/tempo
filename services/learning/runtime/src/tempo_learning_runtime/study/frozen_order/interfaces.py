"""Public facts and semantic capabilities for frozen Study preference."""
from __future__ import annotations
from typing import AbstractSet, Callable, Mapping, NamedTuple, Sequence


class FrozenRankFacts(NamedTuple):
    ordinal: int
    stored_fingerprint: str
    current_definition: Mapping[str, object] | None
    memberships: Sequence[str]


class FrozenAnnotationFacts(NamedTuple):
    token: tuple[str | None, int]
    ranks: Mapping[str, FrozenRankFacts]
    legacy_card_ids: AbstractSet[str]
    blocked_memberships: AbstractSet[tuple[str, str]]


ContentFingerprint = Callable[[Mapping[str, object], Sequence[str]], str]
CardPredicate = Callable[[str], bool]
