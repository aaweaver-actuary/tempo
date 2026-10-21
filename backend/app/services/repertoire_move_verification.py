"""Bounded foreground membership checks against current saved opening sources."""

import hashlib
import json
import sqlite3
import time

import chess
from fastapi import HTTPException

from tempo_legacy_database.connections import read_connection
from .repertoire_comparison import canonical_fen

SOURCE_PAGE_SIZE = 64
SOURCE_REPLAY_SECONDS = 0.15
SOURCE_LINE_MOVE_LIMIT = 2048


class _ReplayBudgetExhausted(Exception):
    """An unfinished line must be retried from the last completed source."""


class _SourceDigest:
    def __init__(self):
        self.digest = hashlib.sha256()

    def step(self, *values):
        self.digest.update(json.dumps(values, separators=(',', ':')).encode())

    def finalize(self):
        return self.digest.hexdigest()


def _source_token(database, repertoire_id):
    if isinstance(database, sqlite3.Connection):
        database.create_aggregate('tempo_source_digest', 4, _SourceDigest)
        return database.execute(
            'SELECT tempo_source_digest(id,trained_color,start_fen,moves_json) FROM '
            '(SELECT id,trained_color,start_fen,moves_json FROM repertoire_lines '
            'WHERE repertoire_id=? ORDER BY id)', (repertoire_id,),
        ).fetchone()[0]
    return database.execute_native(
        "SELECT md5(COALESCE(string_agg(md5(row_to_json(item)::text),'' ORDER BY item.id),'')) "
        "FROM (SELECT id,trained_color,start_fen,moves_json FROM repertoire_lines "
        "WHERE repertoire_id=%s) item", (repertoire_id,),
    ).fetchone()[0]


def _line_contains_move(source_line, position_key, move_uci, deadline):
    try:
        board = chess.Board(source_line['start_fen'])
        moves = json.loads(source_line['moves_json'])
        if not isinstance(moves, list) or not moves or len(moves) > SOURCE_LINE_MOVE_LIMIT:
            raise ValueError('Invalid saved line length')
        matched = False
        for source_move in moves:
            if time.monotonic() >= deadline:
                raise _ReplayBudgetExhausted()
            parsed_move = chess.Move.from_uci(source_move)
            if parsed_move not in board.legal_moves:
                raise ValueError('Illegal saved source move')
            # Board.fen() already normalizes legal en passant and castling rights.
            matched |= ' '.join(board.fen().split()[:4]) == position_key and source_move == move_uci
            board.push(parsed_move)
        return matched
    except (ValueError, TypeError, KeyError) as error:
        raise HTTPException(503, 'Saved repertoire evidence is invalid. Inspect or repair its lines before grading this move.') from error


def verify_repertoire_move(repertoire_id, fen, move_uci, cursor='', source_token=None):
    try:
        requested_board = chess.Board(fen)
        requested_move = chess.Move.from_uci(move_uci)
        if requested_move not in requested_board.legal_moves:
            raise ValueError('Illegal requested move')
        position_key = canonical_fen(fen)
    except (ValueError, TypeError) as error:
        raise HTTPException(422, 'Supply a valid position and legal move to verify.') from error
    with read_connection() as database:
        current_token = _source_token(database, repertoire_id)
        if source_token is not None and source_token != current_token:
            raise HTTPException(409, 'Saved repertoire lines changed. Retry verification from the current position.')
        source_lines = [dict(row) for row in database.execute(
            'SELECT id,start_fen,moves_json FROM repertoire_lines '
            'WHERE repertoire_id=? AND id>? ORDER BY id LIMIT ?',
            (repertoire_id, cursor, SOURCE_PAGE_SIZE + 1),
        )]
    if not source_lines:
        raise HTTPException(503, 'Saved repertoire evidence is unavailable. Reload or inspect its lines before grading this move.')
    deadline = time.monotonic() + SOURCE_REPLAY_SECONDS
    verified = None
    next_cursor = cursor
    for source_line in source_lines[:SOURCE_PAGE_SIZE]:
        if time.monotonic() >= deadline:
            break
        try:
            matched = _line_contains_move(source_line, position_key, move_uci, deadline)
        except _ReplayBudgetExhausted:
            break
        if matched:
            verified = True
            next_cursor = None
            break
        next_cursor = source_line['id']
    else:
        if len(source_lines) <= SOURCE_PAGE_SIZE:
            verified = False
            next_cursor = None
    if verified is None and next_cursor == cursor:
        raise HTTPException(503, 'Saved repertoire verification could not complete a bounded item. Retry or inspect its lines.')
    # A result cannot authorize grading against sources changed during replay.
    with read_connection() as database:
        if current_token != _source_token(database, repertoire_id):
            raise HTTPException(409, 'Saved repertoire lines changed. Retry verification from the current position.')
    return {'verified': verified, 'next_cursor': next_cursor, 'source_token': current_token}
