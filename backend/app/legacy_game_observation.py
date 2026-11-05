"""Capture read-only game and repertoire facts for Game Review and Study feedback."""

from datetime import datetime, timezone
import json

import chess
from tempo_game_evidence_contracts.legacy import fingerprint
from tempo_legacy_database.adapters.games.authority import headers as storage

from .services import legacy_game_authority as owner
from .services.legacy_game_capture import CaptureRequest, capture_source_scope
from .services.legacy_game_evidence_scope import LEGACY_WORKSPACE_ID


def capture_game(
    repertoire_id, game_id, decision_plies=None, *, reader=None, metadata=None, now=None
):
    reader, metadata = reader or owner.read_rows, metadata or owner.metadata

    # This first-slice operator deliberately does not invent correction lineage.
    # Response-loss recovery uses the retained original preview, never recapture.
    def prior():
        return storage.accepted_game_head(reader, LEGACY_WORKSPACE_ID, game_id)

    if prior():
        raise ValueError("legacy_game_already_accepted_use_retained_preview")
    rows = storage.imported_game(reader, game_id)
    if len(rows) != 1:
        raise ValueError("one_explicit_existing_game_required")
    game = rows[0]
    owner.completed_game_time(game)
    moves = json.loads(game["moves_json"])
    starting_turn = chess.Board(game["start_fen"]).turn
    learner_turn = {"white": chess.WHITE, "black": chess.BLACK}[game["color"]]
    plies = (
        tuple(
            ply
            for ply in range(len(moves))
            if (starting_turn if ply % 2 == 0 else not starting_turn) == learner_turn
        )
        if decision_plies is None
        else tuple(decision_plies)
    )
    if (
        not 1 <= len(plies) <= 128
        or plies != tuple(sorted(set(plies)))
        or any(type(ply) is not int or not 0 <= ply < len(moves) for ply in plies)
    ):
        raise ValueError("explicit_decision_scope_exceeds_bound_or_is_invalid")
    captured = capture_source_scope(
        CaptureRequest(repertoire_id, game_id, plies), reader, metadata
    )
    if captured.game != game or prior():
        raise ValueError("legacy_game_changed_or_accepted_during_export")
    authority = owner.current_token(captured, repertoire_id).model_dump(mode="json")
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        raise ValueError("capture_time_requires_timezone")
    result = dict(
        kind="legacy_game_capture",
        schema_version=1,
        game=captured.game,
        tables=captured.tables,
        graphs=captured.graphs,
        authority=authority,
        observed_after=authority,
        captured_at=moment.isoformat(),
        catalogue_scope=captured.catalogue_proof.model_dump(mode="json"),
    )
    return {**result, "export_digest": fingerprint(result)}
