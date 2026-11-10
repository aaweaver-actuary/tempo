"""Current repertoire source conflict validation for editing and import."""
from __future__ import annotations
import json
from app.contracts.product_v02.repertoire import RepertoireSnapshot
from app.repertoire import build_index
from .ports import AffectedOccurrence, ChangeError, ConflictReport, ResponseConflict

def conflict_report(snapshot: RepertoireSnapshot) -> ConflictReport:
    """Use the public exact-position index, retaining invalid-source diagnostics."""
    index = build_index(snapshot)
    conflicts = tuple(
        ResponseConflict(
            node.key,
            node.own_responses,
            tuple(
                AffectedOccurrence(
                    item.key.repertoire_id,
                    item.route_id,
                    item.ply,
                    item.next_move,
                    tuple(
                        sorted({binding.presentation_id for binding in item.bindings})
                    ),
                )
                for item in node.occurrences
                if item.learner_turn and item.next_move
            ),
        )
        for node in index.nodes.values()
        if node.conflict
    )
    return ConflictReport(
        index.snapshot_id,
        index.source_revision,
        index.revision,
        index.digest,
        conflicts,
        index.diagnostics,
        index.available,
        snapshot.membership.complete,
    )


def _source_rows_report(repertoire_id: str, rows: tuple[dict, ...]) -> ConflictReport:
    """Audit locked retained sources using the same public exact index.

    Caller owns current source read + repertoire row lock and supplies the
    proposed final source set. The guard does no I/O and creates no card mapping.
    """
    if len(rows) > 10000:
        raise ChangeError("source_exceeds_bound")
    routes = []
    colors = {row.get("trained_color") for row in rows}
    if len(colors) > 1:
        raise ChangeError("invalid_source")
    for row in rows:
        try:
            moves = json.loads(row["moves_json"])
            if not isinstance(moves, list) or row["trained_color"] not in {
                "white",
                "black",
            }:
                raise ValueError("invalid source")
            routes.append(
                dict(
                    route_id=row["source_id"],
                    repertoire_id=repertoire_id,
                    policy_id=repertoire_id,
                    learner_color=row["trained_color"],
                    starting_fen=row["start_fen"],
                    moves=moves,
                    reentries=[],
                )
            )
        except (ValueError, KeyError, TypeError) as error:
            raise ChangeError("invalid_source") from error
    try:
        value = RepertoireSnapshot.model_validate_json(
            json.dumps(
                dict(
                    membership=dict(
                        workspace_id="current-owner",
                        snapshot_id="mutation-guard",
                        revision=1,
                        digest="0" * 64,
                        source_revision="locked-current-source",
                        captured_at="2026-10-26T00:00:00Z",
                        scope_id=repertoire_id,
                        scope_repertoire_ids=[repertoire_id],
                        complete=True,
                        available=True,
                        routes=routes,
                    ),
                    variant="standard",
                    learner_color="white",
                    boundaries=[
                        dict(
                            route_id=item["route_id"],
                            terminal_ply=len(item["moves"]),
                            deliberate=True,
                        )
                        for item in routes
                    ],
                    decision_presentations=[],
                )
            )
        )
    except ValueError as error:
        raise ChangeError("invalid_source") from error
    return conflict_report(value)


def validate_source_rows(repertoire_id: str, rows: tuple[dict, ...]) -> None:
    report = _source_rows_report(repertoire_id, rows)
    if not report.clean:
        raise ChangeError(
            "conflict_pending_resolution" if report.conflicts else "invalid_source"
        )


def validate_repair_source_rows(
    repertoire_id: str, before: tuple[dict, ...], after: tuple[dict, ...]
) -> None:
    """Allow an exact conflict removal while rejecting every new or changed conflict."""
    original = _source_rows_report(repertoire_id, before)
    result = _source_rows_report(repertoire_id, after)
    if any(
        item.code != "own_response_conflict"
        for report in (original, result)
        for item in report.diagnostics
    ):
        raise ChangeError("invalid_source")
    old = {(item.position, item.responses_uci) for item in original.conflicts}
    new = {(item.position, item.responses_uci) for item in result.conflicts}
    if (old and not new < old) or (not old and new):
        raise ChangeError("conflict_pending_resolution")
