"""Read-only one-game export for the installable observation/producer tools.

This owner supplies canonical source facts; it imports no Game Evidence runtime
or A bridge implementation and performs no admission, issuance or review.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

import chess
from tempo_game_evidence_contracts.legacy import fingerprint

from .services import legacy_game_authority as owner
from .services.legacy_game_capture import CaptureRequest, capture_source_scope
from .services.legacy_game_evidence_scope import LEGACY_WORKSPACE_ID


def capture_game(repertoire_id, game_id, decision_plies=None, *, reader=None, metadata=None, now=None):
    reader, metadata = reader or owner.read_rows, metadata or owner.metadata
    # This first-slice operator deliberately does not invent correction lineage.
    # Response-loss recovery uses the retained original preview, never recapture.
    def prior():
        return reader("SELECT game_revision FROM remediation_game_heads WHERE workspace_id=%s AND game_id=%s",
                      (LEGACY_WORKSPACE_ID, game_id))
    if prior(): raise ValueError("legacy_game_already_accepted_use_retained_preview")
    rows = reader("SELECT " + owner._GAME_COLUMNS + " FROM imported_games WHERE id=%s", (game_id,))
    if len(rows) != 1: raise ValueError("one_explicit_existing_game_required")
    game = rows[0]
    owner.completed_game_time(game)
    moves = json.loads(game['moves_json'])
    starting_turn = chess.Board(game['start_fen']).turn
    learner_turn = {'white': chess.WHITE, 'black': chess.BLACK}[game['color']]
    plies = (tuple(ply for ply in range(len(moves))
                   if (starting_turn if ply % 2 == 0 else not starting_turn) == learner_turn)
             if decision_plies is None else tuple(decision_plies))
    if (not 1 <= len(plies) <= 128 or plies != tuple(sorted(set(plies)))
            or any(type(ply) is not int or not 0 <= ply < len(moves) for ply in plies)):
        raise ValueError("explicit_decision_scope_exceeds_bound_or_is_invalid")
    captured = capture_source_scope(CaptureRequest(repertoire_id, game_id, plies), reader, metadata)
    if captured.game != game or prior(): raise ValueError("legacy_game_changed_or_accepted_during_export")
    authority = owner.current_token(captured, repertoire_id).model_dump(mode='json')
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None: raise ValueError("capture_time_requires_timezone")
    result = dict(kind='legacy_game_capture', schema_version=1, game=captured.game,
        tables=captured.tables, graphs=captured.graphs, authority=authority, observed_after=authority,
        captured_at=moment.isoformat(), catalogue_scope=captured.catalogue_proof.model_dump(mode='json'))
    return {**result, 'export_digest': fingerprint(result)}


def main(arguments=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repertoire-id', required=True)
    parser.add_argument('--game-id', required=True)
    parser.add_argument('--decision-ply', type=int, action='append')
    parser.add_argument('--output', required=True, type=Path)
    options = parser.parse_args(arguments)
    result = capture_game(options.repertoire_id, options.game_id, options.decision_ply)
    with options.output.open('x') as output: json.dump(result, output, indent=2)
    print(json.dumps(dict(output=str(options.output), export_digest=result['export_digest'], read_only=True)))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
