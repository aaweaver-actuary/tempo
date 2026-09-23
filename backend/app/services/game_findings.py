"""Durable, reviewable findings derived from repertoire and engine evidence."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import hashlib
import json

import chess

from ..database import connection
from .activity_gate import activity_gate
from .motif_detectors import (
    MotifEvidence,
    classify_candidate_lines,
    select_primary_motif,
)
from .tactical_catalog import catalog_status
from .tactical_opportunities import opportunity_id


def _finding_id(game_id: str, analysis_version: int, kind: str, ply: int) -> str:
    return hashlib.sha256(f"{game_id}\0{analysis_version}\0{kind}\0{ply}".encode()).hexdigest()


def _position_before_ply(start_fen: str, moves: list[str], ply: int) -> chess.Board:
    board = chess.Board(start_fen)
    for move_uci in moves[:ply]:
        board.push_uci(move_uci)
    return board


def _structured_motif_evidence(
    board: chess.Board,
    best_move_uci: str | None,
    principal_variation: list[str],
    *,
    candidate_lines: list[dict] | None = None,
    actual_move_uci: str | None = None,
    actual_after_cp: int | None = None,
    mover_color: str | None = None,
) -> list[MotifEvidence]:
    return classify_candidate_lines(
        board,
        actual_move_uci,
        candidate_lines or [],
        best_move_uci=best_move_uci,
        principal_variation=principal_variation,
        actual_after_cp=actual_after_cp,
        mover_color=mover_color,
    )


def classify_tactical_motif(
    board: chess.Board,
    best_move_uci: str | None,
    principal_variation: list[str],
    *,
    candidate_lines: list[dict] | None = None,
    concrete_gain_cp: int | None = None,
    actual_after_cp: int | None = None,
    mover_color: str | None = None,
    actual_move_uci: str | None = None,
) -> tuple[str, float, list[str]]:
    """Compatibility wrapper returning the legacy primary-label tuple.

    New code should consume :func:`_structured_motif_evidence` so every
    matching detector result remains available to callers.
    """

    del concrete_gain_cp
    evidence = _structured_motif_evidence(
        board,
        best_move_uci,
        principal_variation,
        candidate_lines=candidate_lines,
        actual_move_uci=actual_move_uci,
        actual_after_cp=actual_after_cp,
        mover_color=mover_color,
    )
    primary = select_primary_motif(evidence)
    if primary is None:
        return "unclassified", 0.0, []
    return primary.motif, primary.confidence, [item.motif for item in evidence]


def _upsert_finding(database, *, game_id: str, analysis_version: int, ply: int, kind: str,
                    confidence: float, evidence: dict, repertoire_id: str | None = None,
                    card_id: str | None = None, motif: str | None = None,
                    source_opportunity_id: str | None = None) -> None:
    now = datetime.now(timezone.utc).isoformat()
    database.execute(
        """INSERT INTO game_findings(id,game_id,analysis_version,ply,kind,confidence,evidence_json,repertoire_id,card_id,motif,source_opportunity_id,created_at,updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(id) DO UPDATE SET confidence=excluded.confidence,evidence_json=excluded.evidence_json,
           repertoire_id=excluded.repertoire_id,
           card_id=CASE WHEN excluded.kind='repertoire lapse' THEN excluded.card_id
                        ELSE COALESCE(excluded.card_id,game_findings.card_id) END,
           motif=excluded.motif,
           source_opportunity_id=excluded.source_opportunity_id,updated_at=excluded.updated_at""",
        (
            _finding_id(game_id, analysis_version, kind, ply), game_id, analysis_version, ply,
            kind, confidence, json.dumps(evidence), repertoire_id, card_id, motif, source_opportunity_id, now, now,
        ),
    )


def _unseen_card_for_position(
    unseen_cards: list[dict], fen: str, expected_move: str | None
) -> str | None:
    if not expected_move:
        return None
    target = " ".join(chess.Board(fen).fen().split()[:4])
    for row in unseen_cards:
        try:
            board = chess.Board(row["start_fen"])
            for move_uci in json.loads(row["moves_json"]):
                if " ".join(board.fen().split()[:4]) == target:
                    return row["id"] if move_uci == expected_move else None
                board.push_uci(move_uci)
        except (ValueError, TypeError, json.JSONDecodeError):
            continue
    return None


def refresh_game_findings(
    game_id: str | None = None, *, background: bool = False
) -> None:
    finding_writes: list[dict] = []
    opportunity_writes: list[dict] = []
    opportunity_games: dict[str, int] = {}
    priority_writes: list[tuple] = []
    with connection(background=background) as database:
        where = "WHERE g.id=?" if game_id else ""
        games = [dict(row) for row in database.execute(
            f"""SELECT g.*,m.repertoire_id,m.first_player_deviation_ply,m.first_player_deviation_fen,
                       m.first_player_deviation_expected_json,m.first_player_deviation_actual_uci,m.deviation_card_id
                       ,m.first_opponent_gap_ply,m.out_of_book_ply,m.timeline_json
                FROM imported_games g LEFT JOIN game_repertoire_matches m ON m.game_id=g.id AND m.is_primary=1 {where}""",
            (game_id,) if game_id else (),
        ).fetchall()]
        settings = dict(database.execute(
            "SELECT major_mistake_cp,engine_line_window_cp FROM settings WHERE id=1"
        ).fetchone())
        analysis_rows_by_game = {
            game["id"]: [
                dict(row)
                for row in database.execute(
                    "SELECT * FROM game_move_analysis WHERE game_id=? ORDER BY ply",
                    (game["id"],),
                ).fetchall()
            ]
            for game in games
        }
        candidate_rows_by_game = {
            game["id"]: [
                dict(row)
                for row in database.execute(
                    "SELECT * FROM game_move_analysis_candidates WHERE game_id=? ORDER BY ply,rank",
                    (game["id"],),
                ).fetchall()
            ]
            for game in games
        }
        unseen_cards = [
            dict(row)
            for row in database.execute(
                """SELECT c.id,c.start_fen,c.moves_json FROM cards c
                   WHERE c.content_type='opening' AND c.archived=0 AND c.introduced_at IS NULL
                     AND NOT EXISTS(SELECT 1 FROM reviews r WHERE r.card_id=c.id)"""
            ).fetchall()
        ]

    threshold = int(settings["major_mistake_cp"])
    acceptable_tolerance_cp = int(settings["engine_line_window_cp"])
    for game in games:
            version = max(1, int(game["analysis_version"]))
            moves = json.loads(game["moves_json"])
            deviation_ply = game["first_player_deviation_ply"]
            if deviation_ply is not None:
                finding_writes.append(dict(
                    game_id=game["id"], analysis_version=version, ply=deviation_ply,
                    kind="repertoire lapse", confidence=1.0,
                    evidence={
                        "fen": game["first_player_deviation_fen"],
                        "expected": json.loads(game["first_player_deviation_expected_json"] or "[]"),
                        "actual": game["first_player_deviation_actual_uci"],
                    }, repertoire_id=game["repertoire_id"], card_id=game["deviation_card_id"],
                ))
            analysis_rows = analysis_rows_by_game[game["id"]]
            candidate_rows = candidate_rows_by_game[game["id"]]
            candidate_lines_by_ply: dict[int, list[dict]] = {}
            for candidate in candidate_rows:
                candidate_lines_by_ply.setdefault(int(candidate["ply"]), []).append(
                    {
                        "uci": candidate["candidate_uci"],
                        "cp": candidate["score_cp"],
                        "mate": candidate["mate"],
                        "score": candidate["score_text"],
                        "pv": json.loads(candidate["principal_variation_json"] or "[]"),
                        "depth": candidate["depth"],
                        "engine_version": candidate["engine_version"],
                        "network_version": candidate["network_version"],
                    }
                )
            player_analysis_rows = [
                row for row in analysis_rows if bool(row["is_player_move"])
            ]
            gap_start = game["out_of_book_ply"]
            if game["first_opponent_gap_ply"] is not None:
                gap_start = min(
                    value
                    for value in (gap_start, game["first_opponent_gap_ply"] + 1)
                    if value is not None
                )
            if gap_start is None and game["repertoire_id"] is None:
                gap_start = player_analysis_rows[0]["ply"] if player_analysis_rows else None
            if gap_start is not None:
                decisions_after_gap = [
                    row for row in player_analysis_rows if row["ply"] >= gap_start
                ][:3]
                mistake = next(
                    (row for row in decisions_after_gap if row["loss_cp"] >= threshold),
                    None,
                )
                gap_analysis = decisions_after_gap[0] if decisions_after_gap else None
                if mistake and gap_analysis:
                    gap_board = _position_before_ply(
                        game["start_fen"], moves, gap_analysis["ply"]
                    )
                    best_move = gap_analysis["best_move_uci"]
                    linked_card = _unseen_card_for_position(
                        unseen_cards, gap_board.fen(), best_move
                    )
                    finding_id = _finding_id(
                        game["id"], version, "repertoire gap", gap_analysis["ply"]
                    )
                    finding_writes.append(dict(
                        game_id=game["id"],
                        analysis_version=version,
                        ply=gap_analysis["ply"],
                        kind="repertoire gap",
                        confidence=1.0,
                        evidence={
                            "fen": gap_board.fen(),
                            "best_move_uci": best_move,
                            "principal_variation": json.loads(
                                gap_analysis["principal_variation_json"] or "[]"
                            ),
                            "mistake_ply": mistake["ply"],
                            "mistake_loss_cp": mistake["loss_cp"],
                            "player_decisions_until_mistake": decisions_after_gap.index(mistake) + 1,
                        },
                        repertoire_id=game["repertoire_id"],
                        card_id=linked_card,
                    ))
                    if linked_card:
                        tomorrow = (date.today() + timedelta(days=1)).isoformat()
                        priority_writes.append(
                            (
                                linked_card,
                                game["id"],
                                finding_id,
                                tomorrow,
                                "Encountered repertoire gap followed by a mistake",
                                datetime.now(timezone.utc).isoformat(),
                            )
                        )
            # A tactical opportunity is durable evidence, including when the
            # player converted it: the raw candidate lines let later classifiers
            # distinguish exploited from missed without rerunning Stockfish.
            for row in player_analysis_rows:
                candidate_lines = candidate_lines_by_ply.get(int(row["ply"]), [])
                if not candidate_lines:
                    continue
                opportunity_board = _position_before_ply(game["start_fen"], moves, row["ply"])
                motif_evidence = _structured_motif_evidence(
                    opportunity_board,
                    row["best_move_uci"],
                    json.loads(row["principal_variation_json"] or "[]"),
                    candidate_lines=candidate_lines,
                    actual_move_uci=row["actual_move_uci"],
                    actual_after_cp=row["eval_after_cp"],
                    mover_color=row["mover_color"],
                )
                primary_motif = select_primary_motif(motif_evidence)
                if primary_motif is None:
                    continue
                def candidate_score(candidate: dict) -> int | None:
                    if candidate.get("mate") is not None:
                        return 1_000_000 if int(candidate["mate"]) > 0 else -1_000_000
                    return candidate.get("cp")

                candidate_scores = [candidate_score(candidate) for candidate in candidate_lines]
                scored_candidates = [score for score in candidate_scores if score is not None]
                best_score = max(scored_candidates) if scored_candidates else None
                acceptable_moves = {
                    candidate["uci"] for candidate, score in zip(candidate_lines, candidate_scores)
                    if score is not None and best_score is not None and best_score - score <= acceptable_tolerance_cp
                }
                acceptable = (
                    row["actual_move_uci"] in acceptable_moves
                    or int(row["loss_cp"] or 0) <= acceptable_tolerance_cp
                )
                loss_cp = int(row["loss_cp"] or 0)
                outcome = (
                    "exploited"
                    if acceptable
                    else "missed"
                    if loss_cp >= threshold
                    else "unconverted"
                )
                if outcome == "unconverted":
                    continue
                evidence = {
                            "fen": opportunity_board.fen(),
                            "motif": primary_motif.motif,
                            "candidate_motifs": [item.motif for item in motif_evidence],
                            "motif_evidence": [item.to_dict() for item in motif_evidence],
                            "outcome": outcome,
                            "actual_move_uci": row["actual_move_uci"],
                            "candidate_lines": candidate_lines,
                            "loss_cp": loss_cp,
                            "thresholds": {
                                "missed_opportunity_loss_cp": threshold,
                                "acceptable_move_tolerance_cp": acceptable_tolerance_cp,
                            },
                            "analysis_version": version,
                            "analysis_evidence_version": game["analysis_evidence_version"],
                            "accepted_moves": sorted(acceptable_moves),
                        }
                concrete_outcome = primary_motif.concrete_outcome
                opportunity_value_cp = max(
                    int(concrete_outcome.get("material_gain_cp", 0) or 0),
                    int(concrete_outcome.get("evaluation_swing_cp", 0) or 0),
                    1_000_000 if concrete_outcome.get("type") in {"mate", "forced_mate"} else 0,
                )
                stable_id = opportunity_id(game["id"], version, int(row["ply"]), primary_motif.motif)
                opportunity_games[game["id"]] = version
                opportunity_writes.append({
                    "id": stable_id, "game_id": game["id"], "analysis_version": version,
                    "ply": int(row["ply"]), "motif": primary_motif.motif, "outcome": outcome,
                    "confidence": primary_motif.confidence, "opportunity_value_cp": opportunity_value_cp,
                    "evaluation_loss_cp": loss_cp, "played_move_uci": row["actual_move_uci"],
                    "accepted_moves_json": json.dumps(sorted(acceptable_moves)),
                    "evidence_json": json.dumps(evidence),
                    "engine_version": candidate_lines[0].get("engine_version") if candidate_lines else None,
                    "network_version": candidate_lines[0].get("network_version") if candidate_lines else None,
                })
                if outcome == "missed" and primary_motif.confidence >= 0.8:
                    finding_writes.append(dict(
                        game_id=game["id"], analysis_version=version, ply=row["ply"],
                        kind="tactical miss", confidence=primary_motif.confidence,
                        evidence={**evidence, "opportunity_id": stable_id,
                                  "opportunity_value_cp": opportunity_value_cp},
                        motif=primary_motif.motif, source_opportunity_id=stable_id,
                    ))
            first_big_mistake = next((row for row in player_analysis_rows if row["loss_cp"] >= threshold), None)
            for row in player_analysis_rows:
                if row["loss_cp"] < threshold:
                    continue
                evidence = {
                    "fen": _position_before_ply(game["start_fen"], moves, row["ply"]).fen(),
                    "loss_cp": row["loss_cp"], "best_move_uci": row["best_move_uci"],
                    "principal_variation": json.loads(row["principal_variation_json"] or "[]"),
                    "eval_before_cp": row["eval_before_cp"], "eval_after_cp": row["eval_after_cp"],
                    "mate_before": row["mate_before"], "mate_after": row["mate_after"],
                    "candidate_lines": candidate_lines_by_ply.get(int(row["ply"]), []),
                }
                kind = "blunder" if row["loss_cp"] >= 250 or (row["mate_before"] is not None) != (row["mate_after"] is not None) else "major mistake"
                finding_writes.append(dict(game_id=game["id"], analysis_version=version, ply=row["ply"], kind=kind, confidence=1.0, evidence=evidence))
                if candidate_lines_by_ply.get(int(row["ply"])):
                    continue
                board = _position_before_ply(game["start_fen"], moves, row["ply"])
                motif_evidence = _structured_motif_evidence(
                    board,
                    row["best_move_uci"],
                    evidence["principal_variation"],
                    candidate_lines=evidence["candidate_lines"] or None,
                    actual_move_uci=row["actual_move_uci"],
                    actual_after_cp=row["eval_after_cp"],
                    mover_color=row["mover_color"],
                )
                primary_motif = select_primary_motif(motif_evidence)
                finding_writes.append(dict(
                    game_id=game["id"], analysis_version=version, ply=row["ply"],
                    kind="tactical miss", confidence=primary_motif.confidence if primary_motif else 0.0,
                    evidence={
                        **evidence,
                        "candidate_motifs": [item.motif for item in motif_evidence],
                        "motif_evidence": [item.to_dict() for item in motif_evidence],
                    },
                    motif=primary_motif.motif if primary_motif else "unclassified",
                ))
            if first_big_mistake:
                evidence = {
                    "fen": _position_before_ply(game["start_fen"], moves, first_big_mistake["ply"]).fen(),
                    "loss_cp": first_big_mistake["loss_cp"],
                    "best_move_uci": first_big_mistake["best_move_uci"],
                    "principal_variation": json.loads(first_big_mistake["principal_variation_json"] or "[]"),
                }
                finding_writes.append(dict(game_id=game["id"], analysis_version=version,
                                ply=first_big_mistake["ply"], kind="first big mistake",
                                confidence=1.0, evidence=evidence))
    if background:
        activity_gate.wait_for_foreground()
    with connection(background=background) as database:
        now = datetime.now(timezone.utc).isoformat()
        for opportunity_game_id, opportunity_version in opportunity_games.items():
            database.execute(
                "UPDATE tactical_opportunities SET active=0,superseded_at=?,updated_at=? WHERE game_id=? AND active=1 AND analysis_version!=?",
                (now, now, opportunity_game_id, opportunity_version),
            )
        for opportunity in opportunity_writes:
            database.execute(
                """INSERT INTO tactical_opportunities(
                    id,game_id,analysis_version,ply,motif,outcome,confidence,opportunity_value_cp,
                    evaluation_loss_cp,played_move_uci,accepted_moves_json,evidence_json,engine_version,
                    network_version,active,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?)
                ON CONFLICT(id) DO UPDATE SET outcome=excluded.outcome,confidence=excluded.confidence,
                    opportunity_value_cp=excluded.opportunity_value_cp,evaluation_loss_cp=excluded.evaluation_loss_cp,
                    played_move_uci=excluded.played_move_uci,accepted_moves_json=excluded.accepted_moves_json,
                    evidence_json=excluded.evidence_json,engine_version=excluded.engine_version,
                    network_version=excluded.network_version,active=1,superseded_at=NULL,updated_at=excluded.updated_at""",
                (*[opportunity[key] for key in (
                    "id","game_id","analysis_version","ply","motif","outcome","confidence",
                    "opportunity_value_cp","evaluation_loss_cp","played_move_uci","accepted_moves_json",
                    "evidence_json","engine_version","network_version")], now, now),
            )
        for finding in finding_writes:
            _upsert_finding(database, **finding)
        for priority in priority_writes:
            database.execute(
                """INSERT INTO gameplay_card_priorities(
                       card_id,source_game_id,finding_id,priority_date,reason,created_at
                   ) VALUES(?,?,?,?,?,?)
                   ON CONFLICT(card_id) DO UPDATE SET
                       source_game_id=excluded.source_game_id,
                       finding_id=excluded.finding_id,
                       priority_date=MIN(gameplay_card_priorities.priority_date,excluded.priority_date),
                       reason=excluded.reason""",
                priority,
            )


def motif_recommendations() -> list[dict]:
    with connection() as database:
        window_days = 30
        window_start = (
            datetime.now(timezone.utc) - timedelta(days=window_days)
        ).isoformat()
        rows = database.execute(
            """SELECT e.*,g.color AS player_color,g.played_at
                 FROM gameplay_events e
                 JOIN imported_games g ON g.id=e.game_id
                WHERE g.adaptive_excluded=0 AND g.played_at>=?
                  AND e.kind='tactical opportunity' AND e.confidence>=0.8
                  AND e.motif!='unclassified' AND e.beneficiary_color=g.color""",
            (window_start,),
        ).fetchall()
        grouped: dict[str, dict] = {}
        for row in rows:
            item = grouped.setdefault(row["motif"], {
                "motif": row["motif"], "miss_count": 0,
                "exploited_count": 0, "opportunity_count": 0, "total_loss_cp": 0,
                "supporting_games": [], "window_days": window_days,
            })
            item["opportunity_count"] += 1
            if row["outcome"] == "exploited":
                item["exploited_count"] += 1
                continue
            if row["outcome"] == "missed":
                item["miss_count"] += 1
                item["total_loss_cp"] += int(row["loss_cp"] or 0)
                if row["game_id"] not in item["supporting_games"]:
                    item["supporting_games"].append(row["game_id"])
        catalog = catalog_status(database)
        recommendations = []
        for item in grouped.values():
            if item["miss_count"] < 3:
                continue
            unfinished_packs = [
                pack for pack in catalog["packs"]
                if pack["id"].startswith(f"{item['motif']}-")
                and pack["introduced"] < pack["count"]
            ]
            unfinished_packs.sort(key=lambda pack: (not pack["active"], pack["id"]))
            pack = unfinished_packs[0] if unfinished_packs else None
            item["recommended_pack_id"] = pack["id"] if pack else None
            item["recommended_pack_active"] = bool(pack and pack["active"])
            item["miss_rate"] = item["miss_count"] / item["opportunity_count"]
            item["conversion_rate"] = item["exploited_count"] / item["opportunity_count"]
            recommendations.append(item)
        recommendations.sort(key=lambda item: (-item["miss_count"], -item["total_loss_cp"], item["motif"]))
        return recommendations
