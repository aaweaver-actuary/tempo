"""Exact value equivalence of validated immutable mapping facts."""
from typing import Iterable, Protocol, TypeVar


class _RouteMapping(Protocol):
    route_id: str | None
    route_ply: int | None

    def model_dump_json(self, *, exclude: set[str]) -> str: ...


MappingValue = TypeVar("MappingValue", bound=_RouteMapping)


def resolve_equivalent_mapping(mappings: Iterable[MappingValue]) -> MappingValue | None:
    """Return a canonical existing provenance member only after exact agreement.

    Every supplied fact other than route ID/ply must agree. This neither infers
    mapping from position equality nor establishes route coverage or authority.
    Callers retain all alternatives and must prove complete applicable coverage.
    The selected original member keeps its route provenance; no global binding
    is manufactured. Selection is deterministic under source row reordering.
    """
    values = tuple(mappings)
    if not values or len({item.model_dump_json(exclude={"route_id", "route_ply"})
                          for item in values}) != 1:
        return None
    return min(values, key=lambda item: (item.route_id or "", -1 if item.route_ply is None else item.route_ply))
