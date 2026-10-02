"""Version fences shared by canonical coverage and discovery publications."""

import json

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


def coverage_scope_predicate(database, settings: str = "r.settings_json", repertoire_id: str = "n.repertoire_id", *, native: bool = False) -> str:
    """One SQL equivalent of coverage_run_is_current for bounded item selection."""
    def value(field):
        if native and hasattr(database, "execute_native"):
            return f"({settings}::jsonb->>'{field}')"
        return f"json_extract({settings},'$.{field}')"
    prefix_revision = value("canonical_prefix_revision")
    source_revision = value("canonical_scope_source_revision")
    preview_id = value("canonical_scope_preview_id")
    return ("EXISTS(SELECT 1 FROM repertoires scope WHERE scope.id=" + repertoire_id
            + f" AND COALESCE(CAST({prefix_revision} AS BIGINT),0)=scope.canonical_prefix_revision"
            + " AND (scope.canonical_prefix_moves_json='[]' OR ("
            + f"CAST({source_revision} AS BIGINT)=scope.scope_source_revision"
            + f" AND {preview_id}=scope.canonical_prefix_preview_id)))")


def game_scope_generation(database, *, lock: bool = False) -> int:
    suffix = " FOR UPDATE" if lock and hasattr(database, "execute_native") else ""
    return int(database.execute("SELECT generation FROM repertoire_game_scope WHERE id=1" + suffix).fetchone()[0])


def opportunity_is_current(database, opportunity, *, lock: bool = False) -> bool:
    opportunity = dict(opportunity)
    prefix = read_prefix(database, opportunity["repertoire_id"], lock=lock)
    identity = _identity_from_prefix(prefix)
    if prefix["moves"] and identity["canonical_scope_preview_id"] is None:
        return False
    return (all(opportunity.get(field, 0 if field == "canonical_prefix_revision" else None) == value for field, value in identity.items()
                if field == "canonical_prefix_revision" or prefix["moves"])
            and opportunity.get("game_scope_generation", 0) == game_scope_generation(database))


def opportunity_scope_predicate(alias: str = "opportunity") -> str:
    return (f"{alias}.canonical_prefix_revision=(SELECT canonical_prefix_revision FROM repertoires WHERE id={alias}.repertoire_id)"
            f" AND (EXISTS(SELECT 1 FROM repertoires scope WHERE scope.id={alias}.repertoire_id AND scope.canonical_prefix_moves_json='[]')"
            f" OR EXISTS(SELECT 1 FROM repertoires scope WHERE scope.id={alias}.repertoire_id AND scope.scope_source_revision={alias}.canonical_scope_source_revision AND scope.canonical_prefix_preview_id={alias}.canonical_scope_preview_id))"
            f" AND {alias}.game_scope_generation=(SELECT generation FROM repertoire_game_scope WHERE id=1)")
