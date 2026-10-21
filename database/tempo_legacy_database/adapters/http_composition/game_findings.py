"""Fixed legacy Games projection/mutation queries; application decisions stay outside."""

from ..games.findings import defer_finding, ignore_finding, set_finding_decision
from ..games.mutations import (
    set_adaptive_exclusion, exclude_pending_findings, restore_excluded_findings,
    affected_repertoire_ids,
)


def findings_page(database, status, game_id, page_limit, offset):
    clauses = []
    parameters: list[object] = []
    if status:
        clauses.append("f.status=?")
        parameters.append(status)
    if game_id:
        clauses.append("f.game_id=?")
        parameters.append(game_id)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    total = database.execute(
        f"SELECT COUNT(*) FROM current_game_findings f JOIN imported_games g ON g.id=f.game_id {where}",
        parameters,
    ).fetchone()[0]
    rows = database.execute(
        f"""SELECT f.*,g.played_at,g.provider,g.opening_name,g.adaptive_excluded
                 FROM current_game_findings f JOIN imported_games g ON g.id=f.game_id
                 {where} ORDER BY g.played_at DESC,f.ply,f.kind,f.id
                 LIMIT ? OFFSET ?""",
        [*parameters, page_limit, offset],
    ).fetchall()
    return total, rows


def tactical_queue(database, now, motif):
    clauses = [
        "f.kind='tactical miss'", "f.status='pending'", "g.adaptive_excluded=0",
        "o.active=1", "o.analysis_version=g.analysis_version",
        "(f.review_after IS NULL OR f.review_after<=?)",
    ]
    parameters: list[str] = [now]
    if motif:
        clauses.append("o.motif=?")
        parameters.append(motif)
    remaining = database.execute(
        f"""SELECT COUNT(*) FROM current_game_findings f
                JOIN imported_games g ON g.id=f.game_id
                JOIN tactical_opportunities o ON o.id=f.source_opportunity_id
                WHERE {' AND '.join(clauses)}""",
        parameters,
    ).fetchone()[0]
    row = database.execute(
        f"""SELECT f.*,g.played_at,g.provider,g.speed,g.color,g.opening_name,
                       o.outcome,o.opportunity_value_cp,o.evaluation_loss_cp,o.accepted_moves_json,
                       o.evidence_json AS opportunity_evidence
                FROM current_game_findings f
                JOIN imported_games g ON g.id=f.game_id
                JOIN tactical_opportunities o ON o.id=f.source_opportunity_id
                WHERE {' AND '.join(clauses)}
                ORDER BY CASE WHEN instr(o.evidence_json,'"type": "mate"') > 0 THEN 0 ELSE 1 END,
                         o.confidence DESC,o.evaluation_loss_cp DESC,g.played_at DESC,f.id
                LIMIT 1""",
        parameters,
    ).fetchone()
    return remaining, row


def curation_finding(database, finding_id):
    return database.execute(
        "SELECT f.*,g.adaptive_excluded FROM current_game_findings f JOIN imported_games g ON g.id=f.game_id WHERE f.id=?",
        (finding_id,),
    ).fetchone()


def decision_finding(database, finding_id):
    return database.execute(
        """SELECT f.*,g.adaptive_excluded FROM current_game_findings f
               JOIN imported_games g ON g.id=f.game_id WHERE f.id=?""",
        (finding_id,),
    ).fetchone()


def linked_miss_event(database, game_id, repertoire_id, ply, card_id):
    return database.execute(
        """SELECT id FROM current_repertoire_decision_events repertoire_decision_events
                   WHERE game_id=? AND repertoire_id=? AND ply=? AND card_id=? AND outcome='miss'""",
        (game_id, repertoire_id, ply, card_id),
    ).fetchone()


def game_presence(database, game_id):
    return database.execute(
        "SELECT 1 FROM imported_games WHERE id=?",
        (game_id,),
    ).fetchone()
