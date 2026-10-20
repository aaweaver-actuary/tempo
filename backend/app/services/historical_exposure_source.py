"""Read-only encounter projection using the canonical legacy binding owner.

No admission, queue, review, FEN normalization, or GE/A implementation imports.
The complete source traversal happens in command preparation, outside its writer.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
import json

from tempo_learning_review import historical_exposure as policy
from ..historical_exposure_contracts import ExposureSelection
from . import legacy_game_authority as owner
from .legacy_game_capture import CaptureRequest, capture_source_scope


@dataclass(frozen=True)
class VerifiedExposure:
    request: policy.Request
    authority: dict
    integrity_certificate: dict
    game_header: dict
    card_source: dict
    catalogue_proof: dict
    selection: dict


def project_encounter(selection: ExposureSelection, captured: owner.Capture) -> VerifiedExposure:
    token=owner.current_token(captured,selection.repertoire_id)
    game=captured.game
    proof=captured.catalogue_proof
    if (not captured.metadata.get('integrity_current') or proof is None
            or selection.ply not in proof.scope.decision_plies
            or (token.target_id,token.workspace_id,token.storage_identity()[0],game['id'],game['content_hash']) !=
            (selection.target_id,selection.workspace_id,selection.authority_id,selection.game_id,selection.source_digest)
            or game['adaptive_excluded']):
        raise policy.ExposureRejected('current_exact_encounter_required')
    repertoire=next(row for row in captured.tables['repertoires'] if row['id']==selection.repertoire_id)
    if json.loads(repertoire.get('canonical_prefix_moves_json','[]')):
        raise policy.ExposureRejected('canonical_prefix_unsupported')
    moves=json.loads(game['moves_json']);ply=selection.ply
    boards=owner.positions(game['start_fen'],moves)
    if (ply>=len(moves) or ('white' if boards[ply].turn else 'black')!=game['color']):
        raise policy.ExposureRejected('learner_decision_required')
    routes=owner.matching_routes(captured,selection.repertoire_id,ply,boards)
    candidates=[owner.physical_route_candidates(captured,selection.repertoire_id,row,line,ply,
        owner.positions(row['start_fen'],line)) for row,line in routes]
    if not candidates or any(len(group)!=1 for group in candidates) or len(set.union(*candidates))!=1:
        raise policy.ExposureRejected('unambiguous_physical_binding_required')
    card_id,offset=next(iter(candidates[0]))
    card=next(row for row in captured.tables['cards'] if row['id']==card_id)
    physical_moves=json.loads(card['moves_json'])
    executable_digest=owner.fingerprint(dict(starting_fen=card['start_fen'],moves=physical_moves,
        trained_color=card['trained_color']))
    route_ids=tuple(sorted(row['id'] for row,_ in routes))
    expected=tuple(sorted({line[ply] for _,line in routes}))
    if ((card_id,card['revision'],executable_digest,route_ids,expected)!=
            (selection.card_id,selection.card_revision,selection.executable_digest,
             tuple(selection.route_ids),tuple(selection.expected_moves))
            or set(expected)!={physical_moves[offset]} or moves[ply] in expected):
        raise policy.ExposureRejected('selected_departure_changed')
    identity=policy.EncounterIdentity(token.storage_identity()[0],token.target_id,token.workspace_id,
        selection.repertoire_id,game['id'],game['content_hash'],owner.completed_game_time(game),ply,
        card_id,card['revision'],executable_digest,route_ids,moves[ply],expected)
    return VerifiedExposure(policy.Request(identity,True,selection.accepts_unknown_chronology),
        token.model_dump(mode='json'),captured.metadata['integrity_certificate'],dict(game),
        dict(card),proof.model_dump(mode='json'),selection.model_dump(mode='json'))


def prepare_selection(selection: ExposureSelection) -> VerifiedExposure:
    captured=capture_source_scope(CaptureRequest(selection.repertoire_id,selection.game_id,(selection.ply,)),
        owner.read_rows,owner.metadata)
    return project_encounter(selection,captured)


def chronology(introduced_at: str | None, occurred_at: datetime) -> str:
    if not introduced_at: return 'unknown'
    if owner.introduced_before_game(introduced_at,occurred_at): return 'introduced_before_game'
    try:
        if len(introduced_at)==10:
            # Date-only introduction evidence uses the owner's UTC day bounds.
            earliest=datetime.fromisoformat(introduced_at).replace(tzinfo=timezone.utc)
            return 'introduced_after_game' if earliest>occurred_at else 'unknown'
        value=datetime.fromisoformat(introduced_at.replace('Z','+00:00'))
        return 'introduced_after_game' if value.tzinfo is not None and value>occurred_at else 'unknown'
    except (ValueError,TypeError):
        return 'unknown'
