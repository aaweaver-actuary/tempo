"""Clock-only compatibility for an explicitly associated recorded graph card.

The caller must already have the graph's exact card association and must retain
its move, learner-color and ordered-route/index checks. FEN equality never
establishes identity or a historical alias. Both raw FEN strings remain evidence;
this function returns the recorded source string for one stable NEW definition.
"""

import chess


_ERROR = "recorded_graph_starting_fen_incompatible"


def _validated_position_fields(fen):
    try:
        if type(fen) is not str:
            raise ValueError()
        fields = fen.split()
        if len(fields) != 6:
            raise ValueError()
        if any(not value.isascii() or not value.isdecimal() for value in fields[4:]):
            raise ValueError()
        if int(fields[5]) < 1:
            raise ValueError()
        if not chess.Board(fen).is_valid():
            raise ValueError()
        return fields[:4]
    except (ValueError, TypeError, OverflowError):
        raise ValueError(_ERROR) from None


def recorded_graph_starting_fen(source_fen, graph_fen):
    """Return exact ``source_fen`` when the only allowed difference is clocks.

    Validate complete legal FENs, then compare the supplied board, turn, castling
    and en-passant fields exactly. No position normalization or identity inference
    broadens that comparison. Halfmove/fullmove counters may differ; every repeated
    occurrence still uses the unchanged recorded source FEN as its definition.
    """
    if _validated_position_fields(source_fen) != _validated_position_fields(graph_fen):
        raise ValueError(_ERROR)
    return source_fen
