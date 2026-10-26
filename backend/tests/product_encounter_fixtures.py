"""Synthetic retained-history fixtures for the next normal-Study integration.

These are test facts, not product commands or scheduling policy. The owning
PostgreSQL fixture supplies the transaction and disposes its capped container.
"""
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta
import hashlib
import json
import uuid

import chess
from fsrs import Card

from app.legacy_game_observation import capture_game
from app.services.postgres_integrity import current_integrity_certificate
from test_product_game_lapse import NOW


@dataclass(frozen=True)
class HistoryCase:
    transaction: object
    repertoire_id: str
    card_id: str
    protected_id: str
    game_ids: tuple[str, ...]
    state: str

    def command(self, capture):
        """Shared factual command from actual synthetic owner rows, no effect."""
        from app.contracts.product_v02.learning import GameEffectKey, GameEncounter
        from app.contracts.product_v02.repertoire import IntroductionGameScope
        from app.product_composition.retained_game import retained_canonical_game
        from app.repertoire import project_capture
        from app.study.encounter_feedback import encounter_digest
        canonical = retained_canonical_game(capture)
        mapping, = project_capture(capture, comparison_game=canonical).decision_presentations
        played = canonical.moves[0]
        value = GameEncounter(key=GameEffectKey(workspace_id=canonical.workspace_id,
            provider="lichess", game_id=canonical.game_id, decision_id=mapping.decision_id, occurrence_ply=1),
            source=IntroductionGameScope(provider="lichess", game_id=canonical.game_id,
                source_revision=canonical.source_revision, source_digest=canonical.source_digest),
            decision=mapping, expected_uci=mapping.selected_response_uci, played_uci=played,
            observation="matched" if played == mapping.selected_response_uci else "missed",
            detected_at=NOW.isoformat(), occurred_at=canonical.played_at, payload_digest="0"*64)
        return value.model_copy(update={"payload_digest": encounter_digest(value)})

    def capture(self, game_id):
        assert game_id in self.game_ids
        with self.transaction() as database:
            def reader(statement, parameters=()):
                return [dict(row) for row in database.execute_native(statement, parameters).fetchall()]

            def metadata(repertoire_id):
                row = database.execute_native("""SELECT repertoire.scope_source_revision,
                    repertoire.canonical_prefix_revision,scope.generation catalogue_generation,
                    publication.generation graph_generation,publication.state graph_state,
                    integrity.status integrity_status,integrity.scan_status,integrity.scan_generation
                    FROM repertoires repertoire CROSS JOIN repertoire_game_scope scope
                    JOIN opening_graph_publications publication ON publication.repertoire_id=repertoire.id
                    JOIN repertoire_integrity_state integrity ON integrity.repertoire_id=repertoire.id
                    WHERE repertoire.id=%s AND scope.id=1""", (repertoire_id,)).fetchone()
                certificate = current_integrity_certificate(database, repertoire_id, 1)
                return {**dict(row), "integrity_current": certificate is not None,
                        "integrity_certificate": certificate}

            return capture_game(self.repertoire_id, game_id, (0,), reader=reader,
                                metadata=metadata, now=NOW)

    def readback(self):
        with self.transaction() as database:
            cards = database.execute_native("""SELECT id,revision,introduced_at,state,due_date,
                interval_days,fsrs_card_json,first_correct_at FROM cards WHERE id=ANY(%s) ORDER BY id""",
                ([self.card_id, self.protected_id],)).fetchall()
            reviews = database.execute_native("""SELECT id,card_id,rating,reviewed_at,source_kind,source_ref
                FROM reviews WHERE card_id=ANY(%s) ORDER BY id""",
                ([self.card_id, self.protected_id],)).fetchall()
            queue = database.execute_native("""SELECT id,card_id,position,status,attempt_state
                FROM daily_queue WHERE card_id=ANY(%s) ORDER BY position,id""",
                ([self.card_id, self.protected_id],)).fetchall()
            origins = database.execute_native("""SELECT origin.* FROM queue_attempt_origins origin
                JOIN daily_queue queue ON queue.id=origin.queue_entry_id
                WHERE queue.card_id=ANY(%s) ORDER BY origin.queue_entry_id""",
                ([self.card_id, self.protected_id],)).fetchall()
        return dict(cards=tuple(tuple(row) for row in cards), reviews=tuple(tuple(row) for row in reviews),
                    queue=tuple(tuple(row) for row in queue), origins=tuple(tuple(row) for row in origins))


@contextmanager
def retained_history_case(transaction, *, state, played="d2d4"):
    """Two retained games and existing current cards, with no inferred admission.

    unintroduced: no introduction, FSRS state or prior Study review exists.
    introduced: the miss follows prior introduction and the last saved review.
    later_learning: both historical games precede actual introduction/review.
    The companion game plays the expected move and remains frequency evidence.
    """
    if state not in {"unintroduced", "introduced", "later_learning"} or played not in {"e2e4", "d2d4"}:
        raise ValueError("explicit bounded fixture scenario required")
    identity = "encounter-integration-" + uuid.uuid4().hex
    repertoire, protected_rep = identity + "-repertoire", identity + "-protected-repertoire"
    target, protected = identity + "-target", identity + "-protected"
    games = ("lichess:" + identity + "-one", "lichess:" + identity + "-two")
    board = chess.Board()
    board.remove_piece_at(chess.D8)
    target_fen = board.fen(en_passant="legal")
    game_day = NOW - timedelta(days=14 if state == "later_learning" else 1)
    case = HistoryCase(transaction, repertoire, target, protected, games, state)
    try:
        with transaction() as database:
            for rep, card_id, fen, move in (
                (repertoire, target, target_fen, "e2e4"),
                (protected_rep, protected, chess.STARTING_FEN, "d2d4"),
            ):
                fresh = card_id == target and state == "unintroduced"
                introduced = None if fresh else (NOW-timedelta(
                    days=4 if card_id == target and state == "later_learning" else 20)).date().isoformat()
                last_review = NOW-timedelta(days=1 if state == "later_learning" and card_id == target else 3)
                scheduling = None if fresh else Card(card_id=100, due=NOW+timedelta(days=20),
                    last_review=last_review, stability=20, difficulty=5).to_json()
                route, scan = rep + "-route", rep + "-scan"
                position = " ".join(fen.split()[:4])
                database.execute_native("""INSERT INTO repertoires(id,name,source_name,created_at,new_cards_per_day)
                    VALUES(%s,%s,'synthetic',%s,2)""", (rep, "Encounter target" if card_id == target
                    else "Protected unrelated", (NOW-timedelta(days=90)).isoformat()))
                database.execute_native("""INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,
                    start_fen,moves_json,created_at) VALUES(%s,%s,'Approved fixture route','white',%s,%s,%s)""",
                    (route, rep, fen, json.dumps([move]), (NOW-timedelta(days=90)).isoformat()))
                database.execute_native("""INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,
                    due_date,introduced_at,revision,interval_days,fsrs_card_json,first_correct_at,stability,
                    content_type,trained_color) VALUES(%s,%s,'prefix',%s,%s,%s,%s,%s,1,%s,%s,%s,%s,'opening','white')""",
                    (card_id, rep, fen, json.dumps([move]), "new" if fresh else "mature",
                     (NOW if fresh else NOW+timedelta(days=20)).date().isoformat(), introduced,
                     0 if fresh else 20, scheduling, None if fresh else last_review.isoformat(),
                     0 if fresh else 20))
                database.execute_native("""INSERT INTO repertoire_cards(repertoire_id,card_id,canonical_route_source)
                    VALUES(%s,%s,1)""", (rep, card_id))
                if not fresh:
                    database.execute_native("""INSERT INTO reviews(card_id,rating,reviewed_at,previous_interval,
                        next_interval,internal_rating,guided,source_kind,source_ref)
                        VALUES(%s,'correct',%s,10,20,'good',0,'study',%s)""",
                        (card_id, last_review.isoformat(), "retained:" + card_id))
                database.execute_native("""INSERT INTO opening_graph_publications(repertoire_id,generation,state,published_at)
                    VALUES(%s,1,'ready',%s)""", (rep, NOW.isoformat()))
                database.execute_native("""INSERT INTO opening_graph_steps(repertoire_id,generation,line_id,
                    decision_index,card_id,decision_fen_key,starting_fen,moves_json,trained_color,segment_kind,
                    first_decision_index,last_decision_index,decision_fen_keys_json)
                    VALUES(%s,1,%s,0,%s,%s,%s,%s,'white','prefix',0,0,%s)""",
                    (rep, route, card_id, position, fen, json.dumps([move]), json.dumps([position])))
                for kind, task_id in (("opening_graph_rebuild", rep+"-graph"), ("integrity_scan", scan)):
                    database.execute_native("""INSERT INTO background_tasks(id,kind,deduplication_key,generation,state,
                        payload_json,next_attempt_at,created_at,updated_at) VALUES(%s,%s,%s,1,'complete',%s,%s,%s,%s)""",
                        (task_id, kind, rep, json.dumps(dict(repertoire_id=rep, graph_generation=1)),
                         NOW.isoformat(), NOW.isoformat(), NOW.isoformat()))
                database.execute_native("""INSERT INTO repertoire_integrity_state(repertoire_id,status,checked_at,
                    scan_status,scan_generation,scan_completed_sources,scan_total_sources)
                    VALUES(%s,'clean',%s,'idle',%s,1,1)""", (rep, NOW.isoformat(), scan+":1"))
            for game_id, actual in zip(games, (played, "e2e4")):
                digest = hashlib.sha256((game_id + actual).encode()).hexdigest()
                database.execute_native("""INSERT INTO imported_games(id,provider,provider_game_id,username,
                    played_at,speed,rated,color,result,start_fen,moves_json,content_hash)
                    VALUES(%s,'lichess',%s,'synthetic',%s,'rapid',0,'white','1/2-1/2',%s,%s,%s)""",
                    (game_id, game_id.removeprefix("lichess:"), game_day.isoformat(), target_fen,
                     json.dumps([actual]), digest))
            database.execute_native("""INSERT INTO daily_queue(queue_date,card_id,position,admission_kind)
                VALUES(%s,%s,0,'review')""", (NOW.date().isoformat(), protected))
        yield case
    finally:
        with transaction() as database:
            database.execute_native("DELETE FROM study_game_feedback WHERE card_id=ANY(%s)", ([target, protected],))
            database.execute_native("DELETE FROM imported_games WHERE id=ANY(%s)", (list(games),))
            database.execute_native("DELETE FROM cards WHERE id=ANY(%s)", ([target, protected],))
            database.execute_native("DELETE FROM background_tasks WHERE deduplication_key=ANY(%s)", ([repertoire, protected_rep],))
            database.execute_native("DELETE FROM repertoires WHERE id=ANY(%s)", ([repertoire, protected_rep],))
