import copy
import io

import chess
import chess.pgn

from tempo_game_evidence import ComparisonSnapshot, normalize_pgn, replay_positions


def pgn(moves, *, fen=chess.STARTING_FEN, color="white", player="Learner"):
    game = chess.pgn.Game()
    game.setup(chess.Board(fen))
    game.headers.update(White=player if color=="white" else "Opponent",
                        Black=player if color=="black" else "Opponent",Result="1/2-1/2",Date="2026.10.15")
    node = game
    for uci in moves:
        node = node.add_variation(chess.Move.from_uci(uci))
    return (str(game)+"\n").encode()


def snapshot(*, moves=None, expected=None, fen=chess.STARTING_FEN, color="white", binding_ply=0,
             reentry=False, activation=True, workspace="fixture"):
    moves = ["e2e4","e7e5","g1f3"] if moves is None else moves
    board = chess.Board(fen)
    for uci in moves[:binding_ply]:
        board.push_uci(uci)
    expected = expected or [moves[binding_ply]]
    definition = {"card_id":"card-shared","decision_id":"decision-root","policy_id":"policy-main",
                  "source_revision":1,"publication_id":"publication-fixture","publication_generation":1,
                  "exercise_revision":1,"starting_fen":board.fen(en_passant="fen"),"prompt":"Play your repertoire move",
                  "specification":{"type":"move_line","accepted_lines":[[move] for move in expected],
                                   "mode":"single","grading_policy":"reference"}}
    publication = {"contract_version":1,"workspace_id":workspace,"publication_id":"publication-fixture",
                   "source_revision":1,"publication_generation":1,"built_at":"2026-10-15T12:00:00Z","definitions":[definition]}
    return {"schema_version":1,"snapshot_id":"snapshot-fixture","revision":1,"publication":publication,
            "activation":{"contract_version":1,"workspace_id":workspace,"activation_id":"activation-fixture",
                          "publication_id":"publication-fixture","publication_generation":1,"activation_revision":1,
                          "status":"activated","reason":None} if activation else None,
            "routes":[{"repertoire_id":"repertoire-main","route_id":"route-main","learner_color":color,
                       "starting_fen":fen,"moves":moves,"bindings":[{"ply":binding_ply,"card_id":"card-shared","reentry":reentry}]}]}


def observed(moves, **kwargs):
    game = normalize_pgn(pgn(moves,**kwargs),"Learner")
    game.update(workspace_id="fixture",game_id="game-fixture",revision=1)
    return game,replay_positions(game)
