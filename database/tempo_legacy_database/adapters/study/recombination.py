"""T22 PostgreSQL storage and concrete retained Study boundary, borrowed writer.

Import/construction runs no DDL, connection or commit. The private store owns its
six tables; CurrentStudyRecombination also initializes fresh generated card/queue
state through Study. Original cards, reviews, attempts, memberships, source moves
and #487 logical effect/deadline/provenance remain intact.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
import json
from zoneinfo import ZoneInfo

import chess
import psycopg
from tempo_evidence_math import opening_position_key
from tempo_game_evidence_contracts.legacy import decision_identity, fingerprint
from tempo_game_evidence_contracts.legacy_scope import LegacyIntegrityCertificate

from app.contracts.product_v02.presentation import DecisionComponent, OpponentCue, Presentation, PresentationChangeReceipt
from app.study.recombination.models import CleanStreak, JoinMember, ReviewEvent, RouteContext, RouteUse
from app.study.recombination.ports import CurrentRoute, Obligation


SCHEMA = """
CREATE TABLE study_recombination_streaks (
    presentation_id text NOT NULL, revision bigint NOT NULL CHECK (revision > 0),
    evidence_epoch text NOT NULL, state_json jsonb NOT NULL,
    PRIMARY KEY (presentation_id, revision, evidence_epoch)
);
CREATE TABLE study_recombination_events (
    event_id text PRIMARY KEY, event_json jsonb NOT NULL
);
CREATE TABLE study_recombination_completions (
    sequence bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    attempt_id text NOT NULL UNIQUE, review_id text NOT NULL UNIQUE,
    presentation_id text NOT NULL, revision bigint NOT NULL, evidence_epoch text NOT NULL,
    completed_at timestamptz NOT NULL, event_json jsonb NOT NULL
);
CREATE INDEX study_recombination_recent ON study_recombination_completions
    (presentation_id, revision, evidence_epoch, completed_at DESC, sequence DESC);
CREATE TABLE study_recombination_changes (
    command_id text PRIMARY KEY, digest text NOT NULL,
    change_json jsonb NOT NULL, receipt_json jsonb NOT NULL
);
CREATE TABLE study_recombination_presentations (
    presentation_id text PRIMARY KEY,
    command_id text NOT NULL REFERENCES study_recombination_changes(command_id),
    result_json jsonb NOT NULL
);
CREATE TABLE study_recombination_current (
    route_id text NOT NULL, decision_id text NOT NULL,
    presentation_id text NOT NULL REFERENCES study_recombination_presentations(presentation_id),
    PRIMARY KEY (route_id, decision_id)
);
"""


def _json(value):
    def encode(item):
        if isinstance(item, datetime):
            return item.isoformat()
        if isinstance(item, (set, frozenset)):
            return sorted(item)
        if hasattr(item, "model_dump"):
            return item.model_dump(mode="json")
        raise TypeError(type(item).__name__)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=encode)


def _event(value):
    data = dict(value)
    data["occurred_at"] = datetime.fromisoformat(data["occurred_at"])
    return ReviewEvent(**data)


class PostgresRecombinationStore:
    def __init__(self, database, *, validate_reviews=False):
        self.database = database
        self.validate_reviews = validate_reviews

    def _writer(self):
        if self.database.read_only or self.database.raw.info.transaction_status.name != "INTRANS":
            raise RuntimeError("recombination requires a borrowed writer transaction")

    def try_lock_route(self, route_id):
        self._writer()
        # Nonblocking: saved review/card/day locks must never wait on a route.
        return bool(self.database.execute_native(
            "SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0))",
            ("tempo:recombination-route:" + route_id,)).fetchone()[0])

    def streak(self, identity):
        row = self.database.execute_native("""SELECT state_json FROM study_recombination_streaks
            WHERE presentation_id=%s AND revision=%s AND evidence_epoch=%s""",
            (identity.presentation_id, identity.presentation_revision, identity.evidence_epoch)).fetchone()
        if not row:
            # No inference from the caller's aggregate count or imported history.
            return CleanStreak(identity.presentation_id, identity.presentation_revision, identity.evidence_epoch)
        value = dict(row[0])
        if value["last_event_at"] is not None:
            value["last_event_at"] = datetime.fromisoformat(value["last_event_at"])
        state = CleanStreak(**value)
        if self.validate_reviews and state.count:
            evidence = self.recent_completed(state)[:state.count]
            valid = len(evidence)==state.count
            for event in evidence:
                valid = valid and event.outcome=="correct" and event.assistance=="unassisted"
                row = self.database.execute_native("""SELECT r.id FROM reviews r JOIN review_attempt_receipts receipt
                    ON receipt.review_id=r.id AND receipt.card_id=r.card_id
                    JOIN queue_attempt_origins o ON o.queue_entry_id=receipt.queue_entry_id AND o.card_id=r.card_id
                    WHERE r.id=%s AND r.card_id=%s AND receipt.attempt_id=%s AND o.revision=%s
                      AND r.invalidated_at IS NULL AND r.rating='correct' AND r.guided=0 AND o.attempt_failed=0
                    FOR SHARE OF r,receipt,o SKIP LOCKED""", (int(event.review_id),event.presentation_id,event.attempt_id,event.presentation_revision)).fetchone()
                valid = valid and row is not None
            if not valid:
                return replace(state,count=0)
        return state

    def try_lock_evidence(self, identities):
        self._writer()
        keys = sorted({(s.presentation_id, s.presentation_revision, s.evidence_epoch) for s in identities})
        for key in keys:
            if not self.database.execute_native(
                "SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0))",
                ("tempo:recombination-evidence:" + _json(key),)).fetchone()[0]:
                return False
        return True

    def save_streak(self, state):
        self._writer()
        self.database.execute_native("""INSERT INTO study_recombination_streaks
            (presentation_id,revision,evidence_epoch,state_json) VALUES(%s,%s,%s,%s::jsonb)
            ON CONFLICT(presentation_id,revision,evidence_epoch)
            DO UPDATE SET state_json=EXCLUDED.state_json""",
            (state.presentation_id, state.presentation_revision, state.evidence_epoch, _json(asdict(state))))

    def event(self, event_id):
        row = self.database.execute_native("SELECT event_json FROM study_recombination_events WHERE event_id=%s",
                                            (event_id,)).fetchone()
        return _event(row[0]) if row else None

    def save_event(self, event):
        self._writer()
        payload = _json(asdict(event))
        self.database.execute_native("INSERT INTO study_recombination_events(event_id,event_json) VALUES(%s,%s::jsonb)",
                                     (event.event_id, payload))
        if event.complete and event.source == "study":
            self.database.execute_native("""INSERT INTO study_recombination_completions
                (attempt_id,review_id,presentation_id,revision,evidence_epoch,completed_at,event_json)
                VALUES(%s,%s,%s,%s,%s,%s,%s::jsonb) ON CONFLICT DO NOTHING""",
                (event.attempt_id, event.review_id, event.presentation_id, event.presentation_revision,
                 event.evidence_epoch, event.occurred_at, payload))

    def recent_completed(self, identity):
        rows = self.database.execute_native("""SELECT event_json FROM study_recombination_completions
            WHERE presentation_id=%s AND revision=%s AND evidence_epoch=%s
            ORDER BY completed_at DESC,sequence DESC LIMIT 5""",
            (identity.presentation_id, identity.presentation_revision, identity.evidence_epoch)).fetchall()
        return tuple(_event(row[0]) for row in rows)

    def receipt(self, command_id):
        row = self.database.execute_native("SELECT digest,receipt_json FROM study_recombination_changes WHERE command_id=%s",
                                            (command_id,)).fetchone()
        return (row[0], PresentationChangeReceipt.model_validate(row[1])) if row else None

    def presentation_exists(self, presentation_id):
        return bool(self.database.execute_native("SELECT 1 FROM study_recombination_presentations WHERE presentation_id=%s",
                                                 (presentation_id,)).fetchone())

    def save_change(self, change, digest, receipt):
        self._writer()
        self.database.execute_native("""INSERT INTO study_recombination_changes
            (command_id,digest,change_json,receipt_json) VALUES(%s,%s,%s::jsonb,%s::jsonb)""",
            (change.command_id, digest, _json(asdict(change)), receipt.model_dump_json()))
        for result in change.results:
            self.database.execute_native("""INSERT INTO study_recombination_presentations
                (presentation_id,command_id,result_json) VALUES(%s,%s,%s::jsonb)""",
                (result.presentation.presentation_id, change.command_id, _json(asdict(result))))
            for component in result.presentation.components:
                self.database.execute_native("""INSERT INTO study_recombination_current
                    (route_id,decision_id,presentation_id) VALUES(%s,%s,%s)
                    ON CONFLICT(route_id,decision_id) DO UPDATE SET presentation_id=EXCLUDED.presentation_id""",
                    (change.route_id, component.decision_id, result.presentation.presentation_id))

    def current_presentation(self, route_id, decision_id):
        """Read projection only; original #487 identity/deadline remains the owner row."""
        row = self.database.execute_native("""SELECT presentation_id FROM study_recombination_current
            WHERE route_id=%s AND decision_id=%s""", (route_id, decision_id)).fetchone()
        return row[0] if row else None


def installed(database):
    return isinstance(getattr(database, "raw", None), psycopg.Connection) and database.execute_native(
        "SELECT to_regclass('study_recombination_current') IS NOT NULL").fetchone()[0]


@dataclass(frozen=True)
class _Route:
    route_id: str
    repertoire_id: str
    learner_color: str
    policy_id: str
    source_revision: str
    starting_fen: str
    moves: tuple[str, ...]


def _instant(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value


class CurrentStudyRecombination:
    """Actual retained Study rows, bounded current source and immutable receipts.

    No producer capture authorizes this adapter. Current source, integrity run,
    publication, exact executable/route uses and current attempts are reread and
    held in this writer. Generated presentations never edit/rebuild that source.
    The retained queue cannot represent ambiguous shared occurrences: those
    members are unavailable for regrouping and retain their normal Study uses.
    """
    def __init__(self, database, *, clock=None):
        self.database = database
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        name = database.execute_native("SELECT timezone FROM settings WHERE id=1").fetchone()[0]
        self.zone = datetime.now().astimezone().tzinfo if name == "local" else ZoneInfo(name)
        self.current = None
        self.reason = "current_authority_unavailable"

    def next_day(self, now):
        local = now.astimezone(self.zone)
        return (local.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)).astimezone(timezone.utc)

    def _hold(self, reason):
        self.reason = reason
        return None

    def try_current(self, route_id):
        try:
            return self._read_current(route_id)
        except (ValueError, KeyError, TypeError):
            return self._hold("current_route_unsupported")

    def _read_current(self, route_id):
        database = self.database
        PostgresRecombinationStore(database)._writer()
        route_row = database.execute_native("SELECT repertoire_id FROM repertoire_lines WHERE id=%s", (route_id,)).fetchone()
        if not route_row:
            return self._hold("route_missing")
        repertoire_id = route_row[0]
        if not database.execute_native("SELECT pg_try_advisory_xact_lock_shared(hashtextextended(%s,0))",
                ("tempo:opening-graph:" + repertoire_id,)).fetchone()[0]:
            return self._hold("current_graph_busy")
        header = database.execute_native("""SELECT r.scope_source_revision,r.canonical_prefix_revision,
            p.generation graph_generation,p.state graph_state,i.status integrity_status,i.scan_status,
            i.scan_generation,i.checked_at,i.scan_completed_sources,i.scan_total_sources,i.scan_error,
            g.generation graph_task_generation,g.state graph_task_state,
            s.id scan_task_id,s.generation scan_task_generation,s.state scan_task_state,s.payload_json
            FROM repertoires r JOIN opening_graph_publications p ON p.repertoire_id=r.id
            JOIN repertoire_integrity_state i ON i.repertoire_id=r.id
            JOIN background_tasks g ON g.kind='opening_graph_rebuild' AND g.deduplication_key=r.id
            JOIN background_tasks s ON s.kind='integrity_scan' AND s.deduplication_key=r.id
            WHERE r.id=%s FOR SHARE OF r,p,i,g,s SKIP LOCKED""", (repertoire_id,)).fetchone()
        if not header or header["graph_state"] != "ready":
            return self._hold("current_publication_unavailable")
        try:
            certificate = LegacyIntegrityCertificate.model_validate({k:header[k] for k in LegacyIntegrityCertificate.model_fields})
        except ValueError:
            return self._hold("current_integrity_unavailable")
        payload = json.loads(header["payload_json"])
        if (certificate.graph_task_generation != header["graph_generation"] or payload.get("graph_generation") != header["graph_generation"]
                or payload.get("repertoire_id")!=repertoire_id):
            return self._hold("current_integrity_generation_changed")
        lines = database.execute_native("""SELECT id,repertoire_id,trained_color,start_fen,moves_json
            FROM repertoire_lines WHERE repertoire_id=%s ORDER BY id LIMIT 65 FOR SHARE SKIP LOCKED""", (repertoire_id,)).fetchall()
        graphs = database.execute_native("""SELECT line_id,card_id,starting_fen,moves_json,trained_color
            FROM opening_graph_steps WHERE repertoire_id=%s AND generation=%s
            ORDER BY line_id,decision_index LIMIT 257 FOR SHARE SKIP LOCKED""", (repertoire_id,header["graph_generation"])).fetchall()
        if not lines or len(lines)>64 or not graphs or len(graphs)>256:
            return self._hold("current_route_scope_unavailable_or_exceeds_bound")
        # Holding the repertoire row fences authored inserts/deletes through the
        # existing source-revision triggers; the graph advisory lock fences its
        # publication. Full bounded row inventories also reject skipped locks.
        expected = database.execute_native("SELECT count(*) FROM repertoire_lines WHERE repertoire_id=%s", (repertoire_id,)).fetchone()[0]
        graph_count = database.execute_native("SELECT count(*) FROM opening_graph_steps WHERE repertoire_id=%s AND generation=%s",
            (repertoire_id,header["graph_generation"])).fetchone()[0]
        if len(lines)!=expected or len(graphs)!=graph_count:
            return self._hold("current_source_busy")
        source_revision = fingerprint([header["scope_source_revision"],header["canonical_prefix_revision"],header["graph_generation"],
            [dict(row) for row in lines],[dict(row) for row in graphs]])
        routes = tuple(_Route(row["id"], repertoire_id, row["trained_color"], repertoire_id,
            source_revision,row["start_fen"],tuple(json.loads(row["moves_json"]))) for row in lines)
        matching = tuple(r for r in routes if r.route_id==route_id)
        if len(matching)!=1:
            return self._hold("current_route_missing")
        route = matching[0]
        if route.learner_color not in {"white","black"} or not 0<len(route.moves)<=1000:
            return self._hold("current_route_unsupported")
        board, frames = chess.Board(route.starting_fen), [route.starting_fen]
        try:
            for move in route.moves:
                board.push_uci(move)
                frames.append(board.fen(en_passant="fen"))
        except ValueError:
            return self._hold("current_route_illegal")
        generated = database.execute_native("""SELECT p.result_json,c.change_json->>'source_revision' source_revision
            FROM study_recombination_presentations p JOIN study_recombination_changes c USING(command_id)
            WHERE p.result_json->'presentation'->>'route_id'=%s ORDER BY p.presentation_id LIMIT 129""", (route_id,)).fetchall()
        if len(generated)>128:
            return self._hold("presentation_scope_exceeds_bound")
        current_map = dict(tuple(row) for row in database.execute_native(
            "SELECT decision_id,presentation_id FROM study_recombination_current WHERE route_id=%s", (route_id,)).fetchall())
        metadata = {row["result_json"]["presentation"]["presentation_id"]:row for row in generated}
        graph_ids = {row["card_id"] for row in graphs if row["line_id"]==route_id}
        active_generated = {identifier for identifier,row in metadata.items()
            if all(current_map.get(c["decision_id"])==identifier for c in row["result_json"]["presentation"]["components"])}
        identifiers = sorted(graph_ids | active_generated)
        if len(identifiers)>128:
            return self._hold("member_scope_exceeds_bound")
        cards = {}
        for identifier in identifiers:
            if not database.execute_native("SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0))",
                    ("tempo:card-edit:" + identifier,)).fetchone()[0]:
                return self._hold("current_card_busy")
            card = database.execute_native("SELECT * FROM cards WHERE id=%s FOR UPDATE SKIP LOCKED", (identifier,)).fetchone()
            if not card:
                return self._hold("current_card_busy_or_missing")
            cards[identifier] = card
        members, dues = [], []
        for identifier in identifiers:
            card = cards[identifier]
            moves = tuple(json.loads(card["moves_json"]))
            roots = [i for i,fen in enumerate(frames) if opening_position_key(fen)==opening_position_key(card["start_fen"])
                and route.moves[i:i+len(moves)]==moves]
            if len(roots)!=1 or not moves:
                return self._hold("ambiguous_member_route")
            start, stop = roots[0], roots[0]+len(moves)
            if identifier in metadata:
                meta = metadata[identifier]
                value = meta["result_json"]
                if meta["source_revision"]!=source_revision or tuple(value["moves"])!=moves or card["revision"]!=1:
                    return self._hold("generated_content_or_source_changed")
                presentation = Presentation.model_validate_json(json.dumps(value["presentation"]))
                epoch, use = value["evidence_epoch"], RouteUse(**value["route_use"])
            else:
                graph_matches = [row for row in graphs if row["card_id"]==identifier and row["line_id"]==route_id
                    and row["starting_fen"]==card["start_fen"] and tuple(json.loads(row["moves_json"]))==moves]
                if len(graph_matches)!=1:
                    return self._hold("current_graph_executable_unproved")
                trace = chess.Board(card["start_fen"])
                components, cues = [], []
                for offset,uci in enumerate(moves):
                    position, own = opening_position_key(trace.fen()), trace.turn==(route.learner_color=="white")
                    trace.push_uci(uci)
                    result_key = opening_position_key(trace.fen())
                    if own:
                        components.append(DecisionComponent(decision_id=decision_identity(identifier,offset,moves[:offset]),
                            position_key=position,response_uci=uci,resulting_position_key=result_key))
                    elif components and offset+1<len(moves):
                        cues.append(OpponentCue(position_key=position,move_uci=uci,resulting_position_key=result_key))
                if not components:
                    return self._hold("no_learner_decision")
                if all(c.decision_id in current_map for c in components):
                    continue
                if any(c.decision_id in current_map for c in components):
                    return self._hold("partial_original_membership_unavailable")
                ancestry = database.execute_native("""SELECT source_card_id,shortened_card_id,continuation_card_id
                    FROM prefix_splits WHERE shortened_card_id=%s OR continuation_card_id=%s LIMIT 3""", (identifier,identifier)).fetchall()
                parents = {row["shortened_card_id"] for row in ancestry if row["continuation_card_id"]==identifier}
                if len(parents)>1:
                    return self._hold("ambiguous_recorded_split_parent")
                presentation = Presentation(presentation_id=identifier,revision=int(card["revision"]),
                    repertoire_id=repertoire_id,repertoire_revision=int(header["graph_generation"]),variant="standard",
                    learner_color=route.learner_color,route_id=route_id,components=tuple(components),opponent_cues=tuple(cues),
                    clean_streak=0,introduced=bool(card["introduced_at"]),lineage=tuple(dict.fromkeys(row["source_card_id"] for row in ancestry)),
                    recorded_parent_id=next(iter(parents),None))
                epoch, use = f"physical:{identifier}:{card['revision']}", RouteUse(route_id,f"physical:{identifier}:{start}",start,stop)
            shared = database.execute_native("""SELECT count(*) FROM repertoire_cards WHERE card_id=%s""", (identifier,)).fetchone()[0]>1
            shared = shared or len([g for g in graphs if g["card_id"]==identifier])>1
            unavailable = database.execute_native("""SELECT 1 FROM deleted_cards WHERE card_id=%s UNION ALL
                SELECT 1 FROM repertoire_integrity_card_blocks WHERE card_id=%s AND repertoire_id=%s LIMIT 1""",
                (identifier,identifier,repertoire_id)).fetchone()
            active = database.execute_native("""SELECT 1 FROM daily_queue q JOIN queue_attempt_origins o
                ON o.queue_entry_id=q.id AND o.card_id=q.card_id AND o.revision=%s
                WHERE q.card_id=%s AND q.status='queued' AND o.issued_as_head=1 LIMIT 1""", (card["revision"],identifier)).fetchone()
            pending = database.execute_native("""SELECT 1 FROM opening_evidence_attempts e
                WHERE e.card_id=%s AND e.card_revision=%s AND e.state<>'complete'
                  AND NOT EXISTS(SELECT 1 FROM review_attempt_receipts r WHERE r.attempt_id=e.attempt_id) LIMIT 1""",
                (identifier,card["revision"])).fetchone()
            available = not (shared or unavailable or card["archived"] or card["pending_validation"] or card["superseded_by"]
                or card["content_type"]!="opening" or card["trained_color"]!=route.learner_color or card["revision"]!=1)
            if shared:
                self.reason = "shared_occurrence_queue_unavailable"
            members.append(JoinMember(presentation,CleanStreak(identifier,int(card["revision"]),epoch),int(card["revision"]),
                (use,),card["start_fen"],frames[stop],moves,available,bool(active),bool(pending)))
            dues.append((identifier,datetime.fromisoformat(card["due_date"][:10]).replace(tzinfo=self.zone)))
        decision_ids = [c.decision_id for m in members for c in m.presentation.components]
        owed = database.execute_native("""SELECT effect_key,decision_id,available_by FROM study_game_feedback
            WHERE decision_id=ANY(%s) AND status IN ('pending','applied') ORDER BY effect_key LIMIT 129""", (decision_ids,)).fetchall()
        if len(owed)>128:
            return self._hold("obligation_scope_exceeds_bound")
        obligations = tuple(Obligation(row[0],row[1],row[2]) for row in owed if row[2] is not None)
        version = database.execute_native("SELECT count(*)+1 FROM study_recombination_changes WHERE change_json->>'route_id'=%s", (route_id,)).fetchone()[0]
        self.current = CurrentRoute(RouteContext(route,int(header["graph_generation"]),"standard",True,True,int(version),"pending","pending",
            authored_routes=routes),tuple(members),tuple(dues),obligations)
        return self.current

    def epoch(self, identifier, revision):
        row = self.database.execute_native("SELECT result_json->>'evidence_epoch' FROM study_recombination_presentations WHERE presentation_id=%s", (identifier,)).fetchone()
        return row[0] if row else f"physical:{identifier}:{revision}"

    def event_for_review(self, attempt_id):
        row = self.database.execute_native("""SELECT r.id,r.card_id,r.rating,r.guided,r.reviewed_at,o.revision,o.attempt_failed,
            e.state evidence_state,e.card_revision evidence_revision,e.review_id evidence_review_id,e.manifest_json
            FROM review_attempt_receipts receipt JOIN reviews r ON r.id=receipt.review_id AND r.card_id=receipt.card_id
            JOIN queue_attempt_origins o ON o.queue_entry_id=receipt.queue_entry_id AND o.card_id=r.card_id
            LEFT JOIN opening_evidence_attempts e ON e.attempt_id=receipt.attempt_id AND e.card_id=r.card_id
            WHERE receipt.attempt_id=%s AND r.invalidated_at IS NULL AND r.source_kind='study'
              AND o.issued_as_head=1 AND (e.card_revision IS NULL OR e.card_revision=o.revision)
            ORDER BY o.revision LIMIT 2""", (attempt_id,)).fetchall()
        if len(row)!=1:
            return None
        value = row[0]
        observations = self.database.execute_native("SELECT observation_json FROM opening_evidence_observations WHERE attempt_id=%s ORDER BY decision_index LIMIT 21", (attempt_id,)).fetchall()
        signals = self.database.execute_native("SELECT event_json FROM opening_evidence_events WHERE attempt_id=%s ORDER BY sequence LIMIT 256", (attempt_id,)).fetchall()
        assisted = any(json.loads(row[0])["kind"] in {"assistance","reveal","manual_failure","correction"} for row in signals)
        expected_count = len(json.loads(value["manifest_json"])["decisions"]) if value["manifest_json"] else 0
        known = value["evidence_state"]=="complete" and value["evidence_review_id"]==value["id"] and 0<expected_count==len(observations)<=20
        clean = known and not assisted and all((o:=json.loads(row[0])).get("first_response_correct") is True
            and not o.get("assistance_before_response") and not o.get("manual_failure") for row in observations)
        guided = bool(value["guided"] or value["attempt_failed"])
        return ReviewEvent("saved-review:"+str(value["id"]),value["card_id"],int(value["revision"]),
            self.epoch(value["card_id"],value["revision"]),_instant(value["reviewed_at"]),"study",
            "again" if guided or value["rating"]=="again" else "correct",attempt_id,str(value["id"]),True,
            "assisted" if guided or assisted else "unassisted" if clean else "unknown")

    def saved_review(self, event):
        return event.source=="study" and self.event_for_review(event.attempt_id)==event

    def identity_available(self, presentation_id):
        return not self.database.execute_native("SELECT 1 FROM cards WHERE id=%s UNION ALL SELECT 1 FROM deleted_cards WHERE card_id=%s LIMIT 1",
            (presentation_id,presentation_id)).fetchone()

    def apply(self, change):
        from app.services.review_service import ensure_card_queued_after
        from tempo_legacy_database.adapters.queue_position_lock import lock_queue_date_for_position
        now = self.clock()
        day = now.astimezone(self.zone).date().isoformat()
        lock_queue_date_for_position(self.database,day)
        for result in change.results:
            value = result.presentation
            self.database.execute_native("""INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,
                introduced_at,trained_color,content_type,canonical_route_source,source_ref,revision)
                VALUES(%s,%s,%s,%s,%s,'learning',%s,%s,%s,'opening',0,%s,1)""",
                (value.presentation_id,value.repertoire_id,"prefix" if len(value.components)>1 else "response",result.starting_fen,
                 json.dumps(result.moves),result.due_at.astimezone(self.zone).date().isoformat(),now.isoformat(),value.learner_color,"recombination:"+change.command_id))
            self.database.execute_native("INSERT INTO repertoire_cards(repertoire_id,card_id,canonical_route_source) VALUES(%s,%s,0)",
                (value.repertoire_id,value.presentation_id))
            if result.due_at.astimezone(self.zone).date().isoformat()<=day:
                ensure_card_queued_after(self.database,value.presentation_id,0,attempt_state="clean",
                    priority_reason="product_game_lapse" if change.obligations else None,queue_date=day,
                    protect_presented=True,preserve_order=False)
        for identifier,_ in change.retired_uses:
            self.database.execute_native("""UPDATE daily_queue q SET status='blocked'
                WHERE q.card_id=%s AND q.status='queued' AND NOT EXISTS(SELECT 1 FROM queue_attempt_origins o
                WHERE o.queue_entry_id=q.id AND o.card_id=q.card_id AND o.issued_as_head=1)""", (identifier,))


def routes_for_card(database, card_id):
    rows = database.execute_native("""SELECT DISTINCT step.line_id route_id FROM opening_graph_steps step
        JOIN opening_graph_publications p ON p.repertoire_id=step.repertoire_id AND p.generation=step.generation AND p.state='ready'
        WHERE step.card_id=%s UNION SELECT result_json->'presentation'->>'route_id'
        FROM study_recombination_presentations WHERE presentation_id=%s LIMIT 2""", (card_id,card_id)).fetchall()
    return tuple(row[0] for row in rows)


def after_saved_review(database, attempt_id, *, clock=None):
    """Called AFTER ordinary completion/evidence and prepared issuance, same writer."""
    if not attempt_id or not installed(database):
        return None
    from app.study.recombination.application import Recombination
    boundary = CurrentStudyRecombination(database,clock=clock)
    event = boundary.event_for_review(attempt_id)
    if event is None:
        return None
    routes = routes_for_card(database,event.presentation_id)
    if len(routes)!=1:
        return {"status":"held","reason":"ambiguous_route"}
    store = PostgresRecombinationStore(database,validate_reviews=True)
    application = Recombination(store,boundary,clock=boundary.clock,next_local_study_day=boundary.next_day)
    try:
        state = application.record_review(routes[0],event)
    except RuntimeError as error:
        if str(error) in {"route_busy","presentation_busy"}:
            return {"status":"held","reason":str(error)}
        raise
    try:
        # A bounded optional regrouping failure cannot erase the just-saved
        # ordinary review. The join itself remains all-or-nothing inside this
        # same outer writer; its source/streak fact above is already retained.
        with database.raw.transaction():
            proposed = application.propose(routes[0])
            if proposed is None or proposed.plan is None:
                return {"status":"held","reason":boundary.reason,"clean_streak":state.count}
            if len(proposed.plan.evidence.moves)>40 or len(proposed.plan.result.components)>20:
                return {"status":"held","reason":"presentation_exceeds_current_evidence_bound","clean_streak":state.count}
            receipt = application.apply_join(proposed.plan)
            return {**receipt.model_dump(),"clean_streak":0 if receipt.status=="applied" else state.count}
    except (psycopg.Error,ValueError,RuntimeError) as error:
        return {"status":"held","reason":"join_rolled_back:"+type(error).__name__,"clean_streak":state.count}


def current_presentation_available(database, presentation_id):
    if not installed(database):
        return True
    row = database.execute_native("SELECT result_json->'presentation'->>'route_id' FROM study_recombination_presentations WHERE presentation_id=%s", (presentation_id,)).fetchone()
    if not row:
        return True
    current = CurrentStudyRecombination(database).try_current(row[0])
    return current is not None and any(m.presentation.presentation_id==presentation_id and m.available for m in current.members)


def project_current_presentations(database, day, *, limit=16):
    """Queue recovery only: no new join, scheduler transition or producer call."""
    if not installed(database):
        return
    from app.services.review_service import ensure_card_queued_after
    routes = database.execute_native("SELECT DISTINCT route_id FROM study_recombination_current ORDER BY route_id LIMIT %s", (limit,)).fetchall()
    for row in routes:
        route_id = row[0]
        store = PostgresRecombinationStore(database)
        if not store.try_lock_route(route_id):
            continue
        boundary = CurrentStudyRecombination(database)
        current = boundary.try_current(route_id)
        if current is None:
            # Never change an issued attempt for an unavailable/busy capture.
            continue
        active_ids = [m.presentation.presentation_id for m in current.members]
        retired = database.execute_native("""SELECT DISTINCT retired->>0 id FROM study_recombination_changes c,
            jsonb_array_elements(c.change_json->'retired_uses') retired
            WHERE c.change_json->>'route_id'=%s UNION SELECT p.presentation_id FROM study_recombination_presentations p
            WHERE p.result_json->'presentation'->>'route_id'=%s AND NOT(p.presentation_id=ANY(%s))""", (route_id,route_id,active_ids)).fetchall()
        for identifier in retired:
            if identifier[0] in active_ids:
                continue
            database.execute_native("""UPDATE daily_queue q SET status='blocked' WHERE q.queue_date=%s AND q.card_id=%s
                AND q.status='queued' AND NOT EXISTS(SELECT 1 FROM queue_attempt_origins o
                    WHERE o.queue_entry_id=q.id AND o.card_id=q.card_id AND o.issued_as_head=1)""", (day,identifier[0]))
        for member in current.members:
            identifier = member.presentation.presentation_id
            if member.available and identifier in {r[0] for r in database.execute_native(
                "SELECT presentation_id FROM study_recombination_presentations WHERE presentation_id=%s", (identifier,)).fetchall()}:
                due = dict(current.due)[identifier].date().isoformat()
                if due<=day and not member.active_attempt and not member.pending_attempt:
                    ensure_card_queued_after(database,identifier,0,attempt_state="clean",queue_date=day,
                        protect_presented=True,preserve_order=False)


def accept_current_split(database, presentation_id, expected_revision):
    """Return None for legacy cards; generated identities never enter donor rewrites."""
    if not installed(database) or not PostgresRecombinationStore(database).presentation_exists(presentation_id):
        return None
    from app.study.recombination.application import Recombination
    row = database.execute_native("""SELECT p.result_json->'presentation'->>'route_id',
        (SELECT COALESCE(max(id),0) FROM reviews WHERE card_id=p.presentation_id AND rating='again' AND invalidated_at IS NULL)
        FROM study_recombination_presentations p WHERE p.presentation_id=%s""", (presentation_id,)).fetchone()
    route_id, last_failure = tuple(row)
    command_id = f"split:{presentation_id}:{expected_revision}:{last_failure}"
    store, boundary = PostgresRecombinationStore(database), CurrentStudyRecombination(database)
    prior = store.receipt(command_id)
    receipt = Recombination(store,boundary,clock=boundary.clock,next_local_study_day=boundary.next_day).accept_split(
        route_id,presentation_id,expected_revision,command_id,confirmed=True)
    if receipt.status!="applied":
        raise ValueError(receipt.reason or receipt.status)
    saved = database.execute_native("SELECT change_json FROM study_recombination_changes WHERE command_id=%s", (command_id,)).fetchone()[0]
    first, second = saved["results"]
    def fragment(value):
        return {"card_id":value["presentation"]["presentation_id"],"starting_fen":value["starting_fen"],
            "moves":value["moves"],"tested_player_moves":len(value["presentation"]["components"])}
    return dict(source_card_id=presentation_id,source_revision=expected_revision,parent=fragment(first),continuation=fragment(second),
        applied=True,idempotent=prior is not None)


def effect_target(database, effect):
    """Resolve only explicit decision lineage, retaining the original effect row."""
    if not installed(database):
        return effect.card_id
    repertoire_id = effect.lapse.finding.repertoire_id if effect.lapse else None
    rows = database.execute_native("""SELECT DISTINCT current.presentation_id FROM study_recombination_current current
        JOIN study_recombination_presentations p ON p.presentation_id=current.presentation_id
        WHERE current.decision_id=%s AND p.result_json->'presentation'->>'repertoire_id'=%s LIMIT 2""",
        (effect.key.decision_id,repertoire_id)).fetchall()
    return rows[0][0] if len(rows)==1 else effect.card_id


def record_admitted_failure(database, effect):
    if not installed(database) or effect.applied_at is None:
        return
    target = effect_target(database,effect)
    row = database.execute_native("SELECT revision FROM cards WHERE id=%s", (target,)).fetchone()
    if not row:
        return
    boundary, store = CurrentStudyRecombination(database), PostgresRecombinationStore(database)
    event = ReviewEvent("admitted-lapse:"+effect.key.logical_id(),target,int(row[0]),boundary.epoch(target,row[0]),
        effect.applied_at,"admitted_lapse","failure")
    if store.event(event.event_id) is not None:
        return
    identity = CleanStreak(target,int(row[0]),event.evidence_epoch)
    if not store.try_lock_evidence((identity,)):
        # A lapse effect must not commit without invalidating join eligibility.
        raise RuntimeError("recombination_evidence_busy")
    from app.study.recombination.policy import reduce_clean_streak
    store.save_streak(reduce_clean_streak(store.streak(identity),event))
    store.save_event(event)
