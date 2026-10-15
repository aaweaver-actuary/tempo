from __future__ import annotations
import chess
from .threat_models import ExerciseAnchor,ForkGeometry,ForkTarget,PositionContext,SourceLine,ThreatSeed
from .threat_validation import AnalysisRequest,AnalysisLine,AnalysisReport,EngineScore

def _request_from_json(raw: dict) -> AnalysisRequest:
    return AnalysisRequest(**{**raw, "position_prefix_uci": tuple(raw["position_prefix_uci"])})

def report_from_json(raw: dict) -> AnalysisReport:
    return AnalysisReport(
        request=_request_from_json(raw["request"]),
        lines=tuple(AnalysisLine(
            root_move_uci=line["root_move_uci"],
            pv_uci=tuple(line["pv_uci"]),
            score=EngineScore(**line["score"]),
            depth=line["depth"],
        ) for line in raw["lines"]),
        complete=bool(raw["complete"]),
    )

def validate_analysis_report(request: AnalysisRequest, report: AnalysisReport) -> None:
    """Reject incomplete or misattributed engine output before it enters durable evidence."""
    if report.request != request or not report.complete or not report.lines:
        raise ValueError("Engine report is incomplete or belongs to another request")
    board = chess.Board(request.position_start_fen)
    for move_uci in request.position_prefix_uci:
        move = chess.Move.from_uci(move_uci)
        if move not in board.legal_moves:
            raise ValueError("Engine request has an illegal position history")
        board.push(move)
    if request.root_move_uci and len(report.lines) != 1:
        raise ValueError("Restricted search returned multiple root moves")
    for line in report.lines:
        if (line.depth < request.depth or not line.pv_uci
                or line.root_move_uci != line.pv_uci[0]
                or request.root_move_uci and line.root_move_uci != request.root_move_uci):
            raise ValueError("Engine report has insufficient depth or the wrong root move")
        position = board.copy(stack=False)
        for move_uci in line.pv_uci:
            move = chess.Move.from_uci(move_uci)
            if move not in position.legal_moves:
                raise ValueError("Engine report contains an illegal principal variation")
            position.push(move)

def _seed_from_json(raw: dict) -> ThreatSeed:
    geometry = raw["geometry"]
    return ThreatSeed(
        raw["seed_id"], raw["game_id"], raw["analysis_version"],
        SourceLine(**{**raw["source_line"],
                      "moves_uci": tuple(raw["source_line"]["moves_uci"])}),
        raw["fork_line_index"],
        ForkGeometry(**{**geometry,
                        "king": ForkTarget(**geometry["king"]),
                        "major": ForkTarget(**geometry["major"]),
                        "other_majors": tuple(ForkTarget(**target)
                                              for target in geometry.get("other_majors", ())) }),
    )

def _anchor_from_json(raw: dict) -> ExerciseAnchor:
    position = raw["position"]
    return ExerciseAnchor(
        raw["seed_id"], raw["player_ply"],
        PositionContext(position["start_fen"], tuple(position["prefix_uci"]),
                        position["learner_color"]),
        raw["historical_move_uci"], raw["decisions_before_event"],
    )
