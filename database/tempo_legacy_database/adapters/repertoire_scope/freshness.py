"""Existing canonical coverage/discovery SQL predicates and generation facts."""


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


def latest_coverage_attempt_predicate(database, *, run_alias: str = "r", native: bool = False) -> str:
    """A failed source can continue only in the newest current-scope attempt."""
    current_scope = coverage_scope_predicate(database, settings="latest.settings_json",
                                            repertoire_id="latest.repertoire_id", native=native)
    return (f"{run_alias}.id=(SELECT latest.id FROM repertoire_coverage_runs latest "
            f"WHERE latest.repertoire_id={run_alias}.repertoire_id AND {current_scope} "
            "ORDER BY latest.created_at DESC,latest.id DESC LIMIT 1)")


def latest_coverage_run_id(database, repertoire_id: str) -> str | None:
    run = database.execute(
        f"SELECT r.id FROM repertoire_coverage_runs r WHERE r.repertoire_id=? "
        f"AND {latest_coverage_attempt_predicate(database)}", (repertoire_id,),
    ).fetchone()
    return run["id"] if run else None


def game_scope_generation(database, *, lock: bool = False) -> int:
    suffix = " FOR UPDATE" if lock and hasattr(database, "execute_native") else ""
    return int(database.execute("SELECT generation FROM repertoire_game_scope WHERE id=1" + suffix).fetchone()[0])


def opportunity_scope_predicate(alias: str = "opportunity") -> str:
    return (f"{alias}.canonical_prefix_revision=(SELECT canonical_prefix_revision FROM repertoires WHERE id={alias}.repertoire_id)"
            f" AND (EXISTS(SELECT 1 FROM repertoires scope WHERE scope.id={alias}.repertoire_id AND scope.canonical_prefix_moves_json='[]')"
            f" OR EXISTS(SELECT 1 FROM repertoires scope WHERE scope.id={alias}.repertoire_id AND scope.scope_source_revision={alias}.canonical_scope_source_revision AND scope.canonical_prefix_preview_id={alias}.canonical_scope_preview_id))"
            f" AND {alias}.game_scope_generation=(SELECT generation FROM repertoire_game_scope WHERE id=1)")
