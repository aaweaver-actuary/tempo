"""Prospective Games boundary mapped only through the existing Study owner.

The ordinary selector supplies the complete authored frontier first. This root
prepares only that frontier's explicit logical mappings, closes every read before
owner chess work, then returns full scope facts for the same pure selector.
"""
from contextlib import contextmanager

from app.games.feedback.ports import PreparedFirstBoundaryScope

# CurrentOwnerDecisions accepts at most 64 explicit current-target bindings.
# Reject the entire oversized set; never select a prefix to create uniqueness.
MAX_CURRENT_TARGET_BINDINGS = 64


def prospective_first_boundary_candidates(selection,snapshots):
    if (selection.status != "own_unmapped" or selection.actor != "learner"
            or len(selection.expected_uci) != 1 or not selection.selected_scope
            or not all(item.complete and item.available for item in selection.selected_scope)
            or selection.diagnostic not in {"current_presentation_unavailable","missing_or_ambiguous_physical_target"}):
        return ()
    occurrences = {(item.repertoire_id,item.route_id,item.route_ply) for item in selection.surviving_routes}
    repertoires = {item[0] for item in occurrences}
    values = {mapping.model_dump_json():mapping for snapshot in snapshots
        for mapping in snapshot.decision_presentations
        if mapping.position_key == selection.position_key and mapping.repertoire_id in repertoires
        and (mapping.route_id is None or (mapping.repertoire_id,mapping.route_id,mapping.route_ply) in occurrences)}
    return tuple(values[key] for key in sorted(values))


def prepare_current_first_boundary(game,*,snapshots,selection,connections,target_prepare=None):
    snapshots = tuple(snapshots)
    candidates = prospective_first_boundary_candidates(selection,snapshots)
    if not candidates:
        return PreparedFirstBoundaryScope(snapshots=snapshots)
    if len(candidates) > MAX_CURRENT_TARGET_BINDINGS:
        return PreparedFirstBoundaryScope(snapshots=snapshots,diagnostic="current_target_candidates_exceed_owner_bound")
    if target_prepare is None:
        from tempo_legacy_database.adapters.study.recombination import prepare_game_feedback_target
        target_prepare = prepare_game_feedback_target
    from tempo_legacy_database.adapters.postgres_store import PostgresConnection
    @contextmanager
    def read_connection():
        with connections() as raw:
            yield PostgresConnection(raw,read_only=True)
    replacements = {}
    physical = {"presentation_id","presentation_revision","available","introduction","introduced_at",
        "introduction_matches_current_content","introduced_before_game","introduction_game"}
    try:
        for original in candidates:
            binding = target_prepare(original,read_connection=read_connection)
            if binding is None:
                continue
            if (binding.original != original or any(getattr(binding.current,field) != getattr(original,field)
                    for field in type(original).model_fields if field not in physical)):
                return PreparedFirstBoundaryScope(snapshots=snapshots,diagnostic="current_target_binding_unproved")
            replacements[original.model_dump_json()] = binding.current
    except Exception:
        return PreparedFirstBoundaryScope(snapshots=snapshots,diagnostic="current_target_authority_unavailable")
    return PreparedFirstBoundaryScope(snapshots=tuple(snapshot.model_copy(update={"decision_presentations":tuple(
        replacements.get(mapping.model_dump_json(),mapping) for mapping in snapshot.decision_presentations)})
        for snapshot in snapshots))
