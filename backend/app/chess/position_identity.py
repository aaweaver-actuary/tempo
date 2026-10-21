"""Existing canonical-scope chess position identity; no storage or workflows."""

from __future__ import annotations

import chess


def position_key(fen: str) -> str:
    return " ".join(chess.Board(fen).fen().split()[:4])
