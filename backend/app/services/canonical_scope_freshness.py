"""Version fences shared by canonical coverage and discovery publications."""

import json
from tempo_legacy_database.adapters.repertoire_scope.freshness import (
    coverage_scope_predicate, game_scope_generation, latest_coverage_attempt_predicate,
    latest_coverage_run_id, opportunity_scope_predicate,
)

from .canonical_prefix import read_prefix


def scope_identity(database, repertoire_id: str, *, lock: bool = False) -> dict:
    prefix = read_prefix(database, repertoire_id, lock=lock)
    return _identity_from_prefix(prefix)


def _identity_from_prefix(prefix: dict) -> dict:
    return {
        "canonical_prefix_revision": prefix["revision"],
        # Unrestricted analysis does not use canonical route certificates.
        "canonical_scope_source_revision": prefix["source_revision"] if prefix["moves"] else 0,
        "canonical_scope_preview_id": prefix["preview_id"] if prefix["moves"] else None,
    }


def coverage_run_is_current(database, run, repertoire_id: str, *, lock: bool = False) -> bool:
    if run is None:
        return False
    settings = json.loads(run["settings_json"])
    prefix = read_prefix(database, repertoire_id, lock=lock)
    identity = _identity_from_prefix(prefix)
    if prefix["moves"] and identity["canonical_scope_preview_id"] is None:
        return False
    return all(settings.get(field, 0 if field == "canonical_prefix_revision" else None) == value
               for field, value in identity.items()
               if field == "canonical_prefix_revision" or prefix["moves"])


def opportunity_is_current(database, opportunity, *, lock: bool = False) -> bool:
    opportunity = dict(opportunity)
    prefix = read_prefix(database, opportunity["repertoire_id"], lock=lock)
    identity = _identity_from_prefix(prefix)
    if prefix["moves"] and identity["canonical_scope_preview_id"] is None:
        return False
    return (all(opportunity.get(field, 0 if field == "canonical_prefix_revision" else None) == value for field, value in identity.items()
                if field == "canonical_prefix_revision" or prefix["moves"])
            and opportunity.get("game_scope_generation", 0) == game_scope_generation(database))
