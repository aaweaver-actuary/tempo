"""Repertoire-owned strict import boundary over the existing pure conflict owner."""
import json
import chess
from fastapi import HTTPException
from app.studies.repertoire_conflicts import conflicts_from_snapshot

MAX_CONFLICT_DIAGNOSTIC_BYTES = 8 * 1024


def _conflict_detail(conflicts):
    """Bound only diagnostics; complete request/source evidence stays retained.

    Keep whole conflict objects/identities when they fit, never a partial route
    list that could look complete. This owner budget stays below the existing
    command-receipt structured-error limit; the boundary regression checks it.
    """
    detail = {'code': 'repertoire_conflict',
              'message': 'Competing own responses need manual resolution; no content was saved.',
              'conflicts': list(conflicts)}
    if len(json.dumps(detail, allow_nan=False).encode('utf-8')) <= MAX_CONFLICT_DIAGNOSTIC_BYTES:
        return detail
    detail.update(conflicts=[], conflict_count=len(conflicts),
        affected_route_count=len({line_id for conflict in conflicts
            for move in conflict['moves'] for line_id in move['line_ids']}),
        conflicts_truncated=True)
    for conflict in conflicts:
        detail['conflicts'].append(conflict)
        if len(json.dumps(detail, allow_nan=False).encode('utf-8')) > MAX_CONFLICT_DIAGNOSTIC_BYTES:
            detail['conflicts'].pop()
            break
    return detail


def validate_sources(repertoire_id, sources):
    if len(sources)>10000:
        raise HTTPException(422,'PGN import exceeds the bounded source limit')
    snapshot=[]
    total=0
    for row in sources:
        try:
            moves=json.loads(row['moves_json'])
            total += len(moves)
            if total>100000: raise ValueError('Source move bound exceeded')
            board=chess.Board(row['start_fen'])
            if not board.is_valid() or row['trained_color'] not in {'white','black'} or not isinstance(moves,list) or not moves or len(moves)>1000:
                raise ValueError('Invalid repertoire source')
            for value in moves:
                move=chess.Move.from_uci(value)
                if move not in board.legal_moves: raise ValueError('Illegal repertoire move')
                board.push(move)
        except (ValueError,TypeError,KeyError) as error:
            raise HTTPException(422,{'code':'invalid_pgn_source','message':'Import contains an invalid source; no content was saved.','source_id':row.get('id')}) from error
        snapshot.append((row['id'],repertoire_id,'',row['trained_color'],row['start_fen'],row['moves_json']))
    conflicts=conflicts_from_snapshot(tuple(snapshot))
    if conflicts:
        raise HTTPException(409, _conflict_detail(conflicts))
