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
from app.contracts.product_v02.repertoire import DecisionPresentation
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
    def __init__(self, database, *, validate_reviews=False, prepared=None):
        self.database = database
        self.validate_reviews = validate_reviews
        self.prepared = prepared

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
        document = asdict(change)
        capture = json.loads(self.prepared.source_json) if self.prepared and self.prepared.source_json else None
        if capture is None:
            row = self.database.execute_native("""SELECT change_json->'source_capture' FROM study_recombination_changes
                WHERE change_json->>'route_id'=%s AND change_json->>'source_revision'=%s
                  AND change_json ? 'source_capture' LIMIT 1""", (change.route_id,change.source_revision)).fetchone()
            capture = row[0] if row else None
        if capture and capture['source_revision']==change.source_revision and capture['route_id']==change.route_id:
            document['source_capture'] = capture
        self.database.execute_native("""INSERT INTO study_recombination_changes
            (command_id,digest,change_json,receipt_json) VALUES(%s,%s,%s::jsonb,%s::jsonb)""",
            (change.command_id, digest, _json(document), receipt.model_dump_json()))
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
    return (isinstance(getattr(database, "raw", None), psycopg.Connection)
            or isinstance(database, _ClosedRecombinationReads)) and database.execute_native(
        "SELECT to_regclass('study_recombination_current') IS NOT NULL").fetchone()[0]


def regrouped_card_sql(database, qualified_card_id):
    """Explicit already-introduced material for the admission owner's reads.

    Source prefixes, FEN and a new card's creation date are not identity proof.
    The caller owns its query and allowance policy; this owner supplies only
    membership in its persisted generated-presentation history. FALSE preserves
    existing readers when this optional schema is absent, including SQLite.
    """
    if qualified_card_id not in {'c.id','card.id','cards.id','daily_queue.card_id'}:
        raise ValueError('unsupported_regrouping_card_reference')
    if not installed(database):
        return 'FALSE'
    return ('EXISTS(SELECT 1 FROM study_recombination_presentations regrouped '
            'WHERE regrouped.presentation_id='+qualified_card_id+')')


def regrouped_presentation_ids(database, identifiers, *, read=None):
    """Bounded identity reads through the serving owner's supplied capability.

    Queue preparation may deliberately pass database=None and a bounded reader.
    Keep that boundary: callback mode never dereferences a retained connection.
    Missing optional schema and empty input preserve the existing classification.
    """
    identifiers = sorted(set(identifiers))
    if not identifiers:
        return frozenset()
    if read is not None:
        available = read("SELECT to_regclass('study_recombination_presentations') IS NOT NULL", ())
        if not available or available[0][0] is not True:
            return frozenset()
    elif not installed(database):
        return frozenset()
    result = set()
    for offset in range(0,len(identifiers),128):
        page = identifiers[offset:offset+128]
        if read is not None:
            rows = read('SELECT presentation_id FROM study_recombination_presentations '
                        'WHERE presentation_id IN (' + ','.join('?' for _ in page) + ')', tuple(page))
        else:
            rows = database.execute_native(
                'SELECT presentation_id FROM study_recombination_presentations WHERE presentation_id=ANY(%s)',
                (page,)).fetchall()
        result.update(row[0] for row in rows)
    return frozenset(result)


@dataclass(frozen=True)
class _Route:
    route_id: str
    repertoire_id: str
    learner_color: str
    policy_id: str
    source_revision: str
    starting_fen: str
    moves: tuple[str, ...]


@dataclass(frozen=True)
class PreparedRecombination:
    """Owner-derived discovery, prepared before the command writer opens."""
    route_id: str | None = None
    source_json: str | None = None
    reason: str | None = None


class _ReadRows:
    def __init__(self, rows):
        self.rows = rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class _ClosedRecombinationReads:
    """Existing owner reads, closed before the reused executable policy runs."""
    def __init__(self, read_connection):
        self.read_connection = read_connection

    def execute_native(self, statement, parameters=()):
        with self.read_connection() as database:
            rows = database.execute_native(statement, parameters).fetchall()
        return _ReadRows(rows)


def _source_header(database, repertoire_id, *, fence=False):
    return database.execute_native("""SELECT r.scope_source_revision,r.canonical_prefix_revision,
        p.generation graph_generation,p.state graph_state,i.status integrity_status,i.scan_status,
        i.scan_generation,i.checked_at,i.scan_completed_sources,i.scan_total_sources,i.scan_error,
        g.generation graph_task_generation,g.state graph_task_state,
        s.id scan_task_id,s.generation scan_task_generation,s.state scan_task_state,s.payload_json
        FROM repertoires r JOIN opening_graph_publications p ON p.repertoire_id=r.id
        JOIN repertoire_integrity_state i ON i.repertoire_id=r.id
        JOIN background_tasks g ON g.kind='opening_graph_rebuild' AND g.deduplication_key=r.id
        JOIN background_tasks s ON s.kind='integrity_scan' AND s.deduplication_key=r.id
        WHERE r.id=%s""" + (' FOR SHARE OF r,p,i,g,s SKIP LOCKED' if fence else ''), (repertoire_id,)).fetchone()


def _source_binding(header):
    return fingerprint([header[key] for key in
        ('scope_source_revision','canonical_prefix_revision','graph_generation','graph_state')])


def _current_certificate(header, repertoire_id):
    if not header or header['graph_state']!='ready':
        raise ValueError('current_publication_unavailable')
    certificate = LegacyIntegrityCertificate.model_validate({k:header[k] for k in LegacyIntegrityCertificate.model_fields})
    payload = json.loads(header['payload_json'])
    if (certificate.graph_task_generation!=header['graph_generation']
            or payload.get('graph_generation')!=header['graph_generation'] or payload.get('repertoire_id')!=repertoire_id):
        raise ValueError('current_integrity_generation_changed')


def _source_route_page(database, repertoire_id, after):
    # Source traversal is preparation, not an authored-route prefix declared
    # complete by the saved-review writer. Oversized text never leaves storage.
    return database.execute_native("""WITH page AS MATERIALIZED (
        SELECT id,repertoire_id,trained_color,start_fen,moves_json,
            octet_length(concat_ws('',id,repertoire_id,trained_color,start_fen,moves_json))>65536 oversized
        FROM repertoire_lines WHERE repertoire_id=%s AND id>%s ORDER BY id LIMIT 16
        ) SELECT CASE WHEN octet_length(id)<=200 THEN id END id,repertoire_id,trained_color,
            CASE WHEN NOT oversized THEN start_fen END start_fen,
            CASE WHEN NOT oversized THEN moves_json END moves_json,oversized FROM page ORDER BY id""",
        (repertoire_id,after)).fetchall()


def _route_positions(row):
    moves = json.loads(row['moves_json'])
    if row['trained_color'] not in {'white','black'} or not isinstance(moves,list) or not 0<len(moves)<=1000:
        raise ValueError('source_route_unsupported')
    board = chess.Board(row['start_fen'])
    if not board.is_valid():
        raise ValueError('source_route_illegal')
    decisions = set()
    for uci in moves:
        if board.turn==(row['trained_color']=='white'):
            decisions.add(opening_position_key(board.fen()))
        move = chess.Move.from_uci(uci)
        if move not in board.legal_moves:
            raise ValueError('source_route_illegal')
        board.push(move)
    terminal = opening_position_key(board.fen()) if board.turn==(row['trained_color']=='white') else None
    return moves, decisions, terminal


def _target_graphs(database, repertoire_id, generation, route_id, *, fence=False):
    return database.execute_native("""SELECT line_id,card_id,
        CASE WHEN octet_length(concat_ws('',starting_fen,moves_json))<=65536 THEN starting_fen END starting_fen,
        CASE WHEN octet_length(concat_ws('',starting_fen,moves_json))<=65536 THEN moves_json END moves_json,trained_color
        FROM opening_graph_steps WHERE repertoire_id=%s AND generation=%s AND line_id=%s
        ORDER BY decision_index LIMIT 257""" + (' FOR SHARE SKIP LOCKED' if fence else ''),
        (repertoire_id,generation,route_id)).fetchall()


def _prepare_recombination_source(database, card_id):
    if not installed(database):
        return PreparedRecombination()
    routes = routes_for_card(database,card_id)
    if len(routes)!=1:
        return PreparedRecombination(reason='route_missing' if not routes else 'ambiguous_route')
    route_id = routes[0]
    target = database.execute_native("""SELECT id,repertoire_id,trained_color,
        CASE WHEN octet_length(concat_ws('',id,repertoire_id,trained_color,start_fen,moves_json))<=65536 THEN start_fen END start_fen,
        CASE WHEN octet_length(concat_ws('',id,repertoire_id,trained_color,start_fen,moves_json))<=65536 THEN moves_json END moves_json
        FROM repertoire_lines WHERE id=%s""", (route_id,)).fetchone()
    if not target:
        return PreparedRecombination(route_id,reason='route_missing')
    if target['start_fen'] is None or target['moves_json'] is None:
        return PreparedRecombination(route_id,reason='target_source_oversized')
    repertoire_id = target['repertoire_id']
    header = _source_header(database,repertoire_id)
    _current_certificate(header,repertoire_id)
    _, wanted, _ = _route_positions(target)
    expected, highwater = tuple(database.execute_native(
        "SELECT count(*),max(id) FROM repertoire_lines WHERE repertoire_id=%s", (repertoire_id,)).fetchone())
    if not 0<expected<=10000:
        return PreparedRecombination(route_id,reason='source_discovery_bound')
    after, visited, plies, size, relevant_plies = '', 0, 0, 0, 0
    relevant, small_inventory = [], []
    while True:
        page = _source_route_page(database,repertoire_id,after)
        if page is None:
            return PreparedRecombination(route_id,reason='source_discovery_interrupted')
        if not page:
            break
        for row in page:
            if row['oversized'] or not row['id']:
                return PreparedRecombination(route_id,reason='source_row_oversized')
            if row['id']<=after or visited>=expected:
                return PreparedRecombination(route_id,reason='source_discovery_changed')
            after, visited = row['id'], visited+1
            moves, positions, terminal = _route_positions(row)
            plies += len(moves)
            size += len(_json(dict(row)).encode())
            if plies>100000 or size>32*1024*1024:
                return PreparedRecombination(route_id,reason='source_discovery_bound')
            source = {key:row[key] for key in ('id','repertoire_id','trained_color','start_fen','moves_json')}
            if expected<=64:
                small_inventory.append(source)
            if row['trained_color']==target['trained_color'] and (positions & wanted or terminal in wanted):
                relevant.append(source)
                relevant_plies += len(moves)
                if len(relevant)>64 or relevant_plies>4096 or len(_json(relevant).encode())>262144:
                    return PreparedRecombination(route_id,reason='relevant_source_bound')
    if visited!=expected or after!=highwater:
        return PreparedRecombination(route_id,reason='source_discovery_incomplete')
    graphs = [dict(row) for row in _target_graphs(database,repertoire_id,header['graph_generation'],route_id)]
    if not graphs or len(graphs)>256:
        return PreparedRecombination(route_id,reason='target_graph_missing_or_exceeds_bound')
    if any(row['starting_fen'] is None or row['moves_json'] is None for row in graphs):
        return PreparedRecombination(route_id,reason='target_graph_row_oversized')
    # Retain the original fingerprint for previously supported small sources.
    # Large source identity uses only the bounded relevant definition plus the
    # same monotone source/publication binding, never an unrelated graph census.
    graph_count = database.execute_native("SELECT count(*) FROM opening_graph_steps WHERE repertoire_id=%s AND generation=%s",
        (repertoire_id,header['graph_generation'])).fetchone()[0]
    if expected<=64 and graph_count<=256:
        legacy_graphs = database.execute_native("""SELECT line_id,card_id,starting_fen,moves_json,trained_color
            FROM opening_graph_steps WHERE repertoire_id=%s AND generation=%s
              AND octet_length(concat_ws('',starting_fen,moves_json))<=65536 ORDER BY line_id,decision_index""",
            (repertoire_id,header['graph_generation'])).fetchall()
        if len(legacy_graphs)!=graph_count:
            return PreparedRecombination(route_id,reason='source_graph_row_oversized')
        revision = fingerprint([header['scope_source_revision'],header['canonical_prefix_revision'],header['graph_generation'],
            small_inventory,[dict(row) for row in legacy_graphs]])
    else:
        revision = fingerprint(['recombination-relevant-v1',_source_binding(header),route_id,relevant,graphs])
    current_header = _source_header(database,repertoire_id)
    _current_certificate(current_header,repertoire_id)
    if _source_binding(current_header)!=_source_binding(header):
        return PreparedRecombination(route_id,reason='source_discovery_changed')
    document = dict(version=1,route_id=route_id,repertoire_id=repertoire_id,binding=_source_binding(header),
        source_revision=revision,lines=relevant,graphs=graphs,expected=expected,visited=visited,
        highwater=highwater,last_key=after,exhausted=True)
    return PreparedRecombination(route_id,_json(document))


def prepare_recombination_review(payload, *, read_connection=None):
    """Existing command-gateway preparation; no writer or request-identity change.

    Failures defer optional regrouping, not the actual Study save. The returned
    frozen value is ephemeral server preparation; the original envelope remains
    the command receipt's identity.
    """
    if read_connection is None:
        from tempo_legacy_database.connections import read_connection
    try:
        with read_connection() as database:
            prepared = _prepare_recombination_source(database,payload['card_id'])
    except (psycopg.Error,ValueError,KeyError,TypeError,RuntimeError) as error:
        prepared = PreparedRecombination(reason='source_discovery_unavailable:'+type(error).__name__)
    return {**payload,'prepared_recombination':prepared}


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
    def __init__(self, database, *, clock=None, prepared=None, _preparing=False):
        self.database = database
        self.prepared = prepared
        self._preparing = _preparing
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        name = database.execute_native("SELECT timezone FROM settings WHERE id=1").fetchone()[0]
        self.zone = datetime.now().astimezone().tzinfo if name == "local" else ZoneInfo(name)
        self.current = None
        self.reason = "current_authority_unavailable"
        self._retired_empty_split_attempts = ()

    def next_day(self, now):
        local = now.astimezone(self.zone)
        return (local.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)).astimezone(timezone.utc)

    def _hold(self, reason):
        self.reason = reason
        return None

    def retire_confirmed_split_head(self, presentation_id, revision, entry_id):
        """Explicit manual confirmation may retire only its untouched issued head.

        Original evidence/origins are retained. The caller's savepoint rolls
        this retirement back if any later source, policy or split write fails.
        Empty checkpoint metadata is not a response or a fabricated completion.
        """
        from tempo_legacy_database.adapters.commands import queue as queue_storage
        from app.services.queue_application import MISS_REASON
        database = self.database
        PostgresRecombinationStore(database)._writer()
        day = self.clock().astimezone(self.zone).date().isoformat()
        head = queue_storage.prepare_queue_head(database,MISS_REASON)(day)
        if not head or head['id'] != entry_id:
            raise ValueError('confirmed_split_attempt_changed')
        row = database.execute_native("""SELECT queue.id FROM daily_queue queue
            JOIN cards card ON card.id=queue.card_id
            JOIN queue_attempt_origins origin ON origin.queue_entry_id=queue.id
              AND origin.card_id=card.id AND origin.revision=card.revision
            WHERE queue.id=%s AND queue.card_id=%s AND card.revision=%s AND queue.queue_date=%s
              AND queue.status='queued' AND queue.attempt_failed=0 AND queue.review_result_json IS NULL
              AND origin.issued_as_head=1 AND origin.attempt_failed=0 AND origin.review_result_json IS NULL
              AND origin.start_fen=card.start_fen AND origin.moves_json=card.moves_json
              AND origin.trained_color=card.trained_color AND origin.content_type=card.content_type
            FOR UPDATE OF queue,origin SKIP LOCKED""",(entry_id,presentation_id,revision,day)).fetchone()
        if not row:
            raise ValueError('confirmed_split_attempt_changed')
        predicate = """card_id=%s AND card_revision=%s AND state<>'complete'
            AND NOT EXISTS(SELECT 1 FROM review_attempt_receipts receipt WHERE receipt.attempt_id=opening_evidence_attempts.attempt_id)"""
        expected = database.execute_native('SELECT count(*) FROM opening_evidence_attempts WHERE '+predicate,
            (presentation_id,revision)).fetchone()[0]
        attempts = database.execute_native('SELECT attempt_id,queue_entry_id,contiguous_sequence,terminal_json FROM opening_evidence_attempts WHERE '
            +predicate+' ORDER BY attempt_id LIMIT 17 FOR UPDATE SKIP LOCKED',(presentation_id,revision)).fetchall()
        if expected>16 or len(attempts)!=expected:
            raise ValueError('pending_attempt')
        for attempt in attempts:
            if (attempt['queue_entry_id']!=entry_id or attempt['contiguous_sequence']!=0 or attempt['terminal_json'] is not None
                    or database.execute_native("""SELECT 1 FROM opening_evidence_events WHERE attempt_id=%s UNION ALL
                        SELECT 1 FROM opening_evidence_observations WHERE attempt_id=%s LIMIT 1""",
                        (attempt['attempt_id'],attempt['attempt_id'])).fetchone()):
                raise ValueError('pending_attempt')
        database.execute_native("UPDATE daily_queue SET status='buried' WHERE id=%s AND status='queued'",(entry_id,))
        self._retired_empty_split_attempts = tuple(row['attempt_id'] for row in attempts)

    def try_current(self, route_id, *, only_presentation_id=None):
        try:
            return self._read_current(route_id,only_presentation_id=only_presentation_id)
        except (ValueError, KeyError, TypeError):
            return self._hold("current_route_unsupported")

    def _captured_source(self, route_id, repertoire_id, header):
        if self.prepared is not None:
            if not isinstance(self.prepared,PreparedRecombination):
                raise ValueError('source_preparation_invalid')
            if self.prepared.reason:
                raise ValueError(self.prepared.reason)
            if self.prepared.route_id not in (None,route_id):
                raise ValueError('source_discovery_changed')
            document = json.loads(self.prepared.source_json) if self.prepared.source_json else None
        else:
            row = self.database.execute_native("""SELECT change_json->'source_capture' FROM study_recombination_changes
                WHERE change_json->>'route_id'=%s AND change_json->'source_capture'->>'binding'=%s LIMIT 1""",
                (route_id,_source_binding(header))).fetchone()
            document = row[0] if row else None
        if document is None:
            return None
        if (document['version']!=1 or document['route_id']!=route_id or document['repertoire_id']!=repertoire_id
                or document['binding']!=_source_binding(header) or document['exhausted'] is not True
                or document['expected']!=document['visited'] or document['highwater']!=document['last_key']
                or not 0<document['visited']<=10000 or not 0<len(document['lines'])<=64):
            raise ValueError('source_discovery_changed_or_incomplete')
        lines = self.database.execute_native("""SELECT id,repertoire_id,trained_color,
            CASE WHEN octet_length(concat_ws('',start_fen,moves_json))<=65536 THEN start_fen END start_fen,
            CASE WHEN octet_length(concat_ws('',start_fen,moves_json))<=65536 THEN moves_json END moves_json
            FROM repertoire_lines WHERE repertoire_id=%s AND id=ANY(%s) ORDER BY id"""
            + ('' if self._preparing else ' FOR SHARE SKIP LOCKED'),
            (repertoire_id,[row['id'] for row in document['lines']])).fetchall()
        graphs = _target_graphs(self.database,repertoire_id,header['graph_generation'],route_id,fence=not self._preparing)
        if len(lines)!=len(document['lines']) or len(graphs)!=len(document['graphs']):
            counts = self.database.execute_native("""SELECT
                (SELECT count(*) FROM repertoire_lines WHERE repertoire_id=%s AND id=ANY(%s)),
                (SELECT count(*) FROM opening_graph_steps WHERE repertoire_id=%s AND generation=%s AND line_id=%s)""",
                (repertoire_id,[row['id'] for row in document['lines']],repertoire_id,header['graph_generation'],route_id)).fetchone()
            if counts[0]!=len(document['lines']) or counts[1]!=len(document['graphs']):
                raise ValueError('relevant_source_missing')
            raise ValueError('relevant_source_busy')
        if [dict(row) for row in lines]!=document['lines'] or [dict(row) for row in graphs]!=document['graphs']:
            raise ValueError('relevant_source_changed')
        return lines, graphs, document['source_revision']

    def _read_current(self, route_id, *, only_presentation_id=None):
        database = self.database
        if not self._preparing:
            PostgresRecombinationStore(database)._writer()
        route_row = database.execute_native("SELECT repertoire_id FROM repertoire_lines WHERE id=%s", (route_id,)).fetchone()
        if not route_row:
            return self._hold("route_missing")
        repertoire_id = route_row[0]
        if not self._preparing and not database.execute_native("SELECT pg_try_advisory_xact_lock_shared(hashtextextended(%s,0))",
                ("tempo:opening-graph:" + repertoire_id,)).fetchone()[0]:
            return self._hold("current_graph_busy")
        header = _source_header(database,repertoire_id,fence=not self._preparing)
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
        try:
            captured = self._captured_source(route_id,repertoire_id,header)
        except (ValueError,KeyError,TypeError) as error:
            return self._hold(str(error))
        if captured is not None:
            lines, graphs, source_revision = captured
        else:
            # Compatibility for existing small-source captures and direct
            # legacy callers. Large scopes require complete preparation.
            lines = database.execute_native("""SELECT id,repertoire_id,trained_color,
                CASE WHEN octet_length(concat_ws('',start_fen,moves_json))<=65536 THEN start_fen END start_fen,
                CASE WHEN octet_length(concat_ws('',start_fen,moves_json))<=65536 THEN moves_json END moves_json
                FROM repertoire_lines WHERE repertoire_id=%s ORDER BY id LIMIT 65"""
                + ('' if self._preparing else ' FOR SHARE SKIP LOCKED'), (repertoire_id,)).fetchall()
            graphs = database.execute_native("""SELECT line_id,card_id,
                CASE WHEN octet_length(concat_ws('',starting_fen,moves_json))<=65536 THEN starting_fen END starting_fen,
                CASE WHEN octet_length(concat_ws('',starting_fen,moves_json))<=65536 THEN moves_json END moves_json,trained_color
                FROM opening_graph_steps WHERE repertoire_id=%s AND generation=%s
                ORDER BY line_id,decision_index LIMIT 257"""
                + ('' if self._preparing else ' FOR SHARE SKIP LOCKED'), (repertoire_id,header["graph_generation"])).fetchall()
            if not lines or len(lines)>64 or not graphs or len(graphs)>256:
                return self._hold("current_route_scope_unavailable_or_exceeds_bound")
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
        if only_presentation_id is not None:
            if only_presentation_id not in identifiers:
                return self._hold("current_presentation_replaced")
            # Serving an existing attempt needs its own definition plus the
            # held source fence, not locks on an unrelated join neighbour.
            identifiers = [only_presentation_id]
        if len(identifiers)>128:
            return self._hold("member_scope_exceeds_bound")
        cards = {}
        for identifier in identifiers:
            if not self._preparing and not database.execute_native("SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0))",
                    ("tempo:card-edit:" + identifier,)).fetchone()[0]:
                return self._hold("current_card_busy")
            card = database.execute_native("SELECT * FROM cards WHERE id=%s"
                + ('' if self._preparing else ' FOR UPDATE SKIP LOCKED'), (identifier,)).fetchone()
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
            shared = shared or len(database.execute_native("""SELECT 1 FROM opening_graph_steps
                WHERE repertoire_id=%s AND generation=%s AND card_id=%s LIMIT 2""",
                (repertoire_id,header['graph_generation'],identifier)).fetchall())>1
            unavailable = database.execute_native("""SELECT 1 FROM deleted_cards WHERE card_id=%s UNION ALL
                SELECT 1 FROM repertoire_integrity_card_blocks WHERE card_id=%s AND repertoire_id=%s LIMIT 1""",
                (identifier,identifier,repertoire_id)).fetchone()
            active = database.execute_native("""SELECT 1 FROM daily_queue q JOIN queue_attempt_origins o
                ON o.queue_entry_id=q.id AND o.card_id=q.card_id AND o.revision=%s
                WHERE q.card_id=%s AND q.status='queued' AND o.issued_as_head=1 LIMIT 1""", (card["revision"],identifier)).fetchone()
            pending = database.execute_native("""SELECT 1 FROM opening_evidence_attempts e
                WHERE e.card_id=%s AND e.card_revision=%s AND e.state<>'complete'
                  AND NOT EXISTS(SELECT 1 FROM review_attempt_receipts r WHERE r.attempt_id=e.attempt_id)
                  AND NOT(e.attempt_id=ANY(%s)) LIMIT 1""",
                (identifier,card["revision"],list(self._retired_empty_split_attempts))).fetchone()
            available = not (shared or unavailable or card["archived"] or card["pending_validation"] or card["superseded_by"]
                or card["content_type"]!="opening" or card["trained_color"]!=route.learner_color or card["revision"]!=1)
            if shared:
                self.reason = "shared_occurrence_queue_unavailable"
            members.append(JoinMember(presentation,CleanStreak(identifier,int(card["revision"]),epoch),int(card["revision"]),
                (use,),card["start_fen"],frames[stop],moves,available,bool(active),bool(pending)))
            dues.append((identifier,datetime.fromisoformat(card["due_date"][:10]).replace(tzinfo=self.zone)))
        decision_ids = [c.decision_id for m in members for c in m.presentation.components]
        owed = database.execute_native("""SELECT effect_key,decision_id,
            COALESCE(available_by,
                (payload_json::jsonb->'owner'->'progress'->>'determined_at')::timestamptz,
                (payload_json::jsonb->'owner'->'qualification'->>'determined_at')::timestamptz,
                received_at) owed_at FROM study_game_feedback
            WHERE decision_id=ANY(%s) AND status IN ('pending','applied') ORDER BY effect_key LIMIT 129""", (decision_ids,)).fetchall()
        if len(owed)>128:
            return self._hold("obligation_scope_exceeds_bound")
        # The compatibility field carries owed ordering for ordinal-policy
        # effects, not a fresh wall-clock promise or an expiration time.
        obligations = tuple(Obligation(row[0],row[1],row[2]) for row in owed)
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
                 json.dumps(result.moves),result.due_at.astimezone(self.zone).date().isoformat(),day,value.learner_color,"recombination:"+change.command_id))
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


def after_saved_review(database, attempt_id, *, clock=None, prepared=None):
    """Called AFTER ordinary completion/evidence and prepared issuance, same writer."""
    if not attempt_id or not installed(database):
        return None
    from app.study.recombination.application import Recombination
    boundary = CurrentStudyRecombination(database,clock=clock,prepared=prepared)
    event = boundary.event_for_review(attempt_id)
    if event is None:
        return None
    routes = routes_for_card(database,event.presentation_id)
    if len(routes)!=1:
        return {"status":"held","reason":"route_missing" if not routes else "ambiguous_route"}
    store = PostgresRecombinationStore(database,validate_reviews=True,prepared=prepared)
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
            if proposed is None:
                return {"status":"held","reason":boundary.reason,"clean_streak":state.count}
            if proposed.plan is None:
                return {"status":"held","reason":';'.join(sorted({hold.code for hold in proposed.holds})) or 'no_valid_join',
                    "clean_streak":state.count}
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
    current = CurrentStudyRecombination(database).try_current(row[0],only_presentation_id=presentation_id)
    return current is not None and any(m.presentation.presentation_id==presentation_id and m.available for m in current.members)


@dataclass(frozen=True)
class PreparedFeedbackTarget:
    """Ephemeral Study authority, never a replacement Games command or key."""
    original: DecisionPresentation
    current: DecisionPresentation
    source: PreparedRecombination
    binding_json: str


_FEEDBACK_CARD_DEFINITION = ("id", "revision", "start_fen", "moves_json", "trained_color",
    "content_type", "repertoire_id", "archived", "pending_validation", "superseded_by")


def _feedback_card(database, identifier, *, fence=False):
    return database.execute_native('SELECT '+','.join(_FEEDBACK_CARD_DEFINITION)+',introduced_at FROM cards WHERE id=%s'
        + (' FOR UPDATE SKIP LOCKED' if fence else ''), (identifier,)).fetchone()


def _feedback_record(database, identifier, *, fence=False):
    return database.execute_native("""SELECT p.result_json,c.change_json,c.receipt_json
        FROM study_recombination_presentations p JOIN study_recombination_changes c USING(command_id)
        WHERE p.presentation_id=%s""" + (' FOR SHARE OF p,c SKIP LOCKED' if fence else ''),
        (identifier,)).fetchone()


def _feedback_mapping(database, route_id, components, *, fence=False):
    return database.execute_native("""SELECT decision_id,presentation_id FROM study_recombination_current
        WHERE route_id=%s AND decision_id=ANY(%s) ORDER BY decision_id"""
        + (' FOR SHARE SKIP LOCKED' if fence else ''),
        (route_id,[component.decision_id for component in components])).fetchall()


def _feedback_definition(card):
    return {key:card[key] for key in _FEEDBACK_CARD_DEFINITION}


def _feedback_blocked(database,original,target):
    identifiers=sorted({original.presentation_id,target.presentation_id})
    return database.execute_native('''SELECT 1 FROM deleted_cards WHERE card_id=ANY(%s) UNION ALL
        SELECT 1 FROM repertoire_integrity_card_blocks WHERE card_id=ANY(%s) AND repertoire_id=%s LIMIT 1''',
        (identifiers,identifiers,original.repertoire_id)).fetchone() is not None


def _feedback_receipt(record, source):
    receipt = PresentationChangeReceipt.model_validate_json(_json(record['receipt_json']))
    change = record['change_json']
    document = json.loads(source.source_json)
    if (receipt.status!='applied' or receipt.plan_id!=change['command_id']
            or receipt.owner_receipt_id!='recombination:'+change['command_id']
            or change['source_revision']!=document['source_revision']
            or sum(result==record['result_json'] for result in change['results'])!=1):
        raise ValueError('feedback_target_applied_receipt_unproved')


def _feedback_original_component(database,original,card,source):
    """Original physical occurrence must also belong to this owner's proof."""
    record = _feedback_record(database,original.presentation_id)
    if record is not None:
        _feedback_receipt(record,source)
        result = record['result_json']
        presentation = Presentation.model_validate_json(_json(result['presentation']))
        matches = [component for component in presentation.components if component.decision_id==original.decision_id]
        if (presentation.route_id!=original.route_id or presentation.repertoire_id!=original.repertoire_id
                or presentation.learner_color!=card['trained_color']
                or presentation.revision!=card['revision'] or result['starting_fen']!=card['start_fen']
                or tuple(result['moves'])!=tuple(json.loads(card['moves_json'])) or len(matches)!=1
                or (matches[0].position_key,matches[0].response_uci)!=(original.position_key,original.selected_response_uci)):
            raise ValueError('feedback_target_original_definition_unproved')
        return
    graphs = json.loads(source.source_json)['graphs']
    moves = tuple(json.loads(card['moves_json']))
    matches = [row for row in graphs if row['card_id']==card['id'] and row['starting_fen']==card['start_fen']
        and tuple(json.loads(row['moves_json']))==moves and row['trained_color']==card['trained_color']]
    if len(matches)!=1:
        raise ValueError('feedback_target_original_definition_unproved')
    board = chess.Board(card['start_fen'])
    for offset,move in enumerate(moves):
        if (decision_identity(card['id'],offset,moves[:offset])==original.decision_id
                and board.turn==(card['trained_color']=='white')
                and opening_position_key(board.fen())==original.position_key and move==original.selected_response_uci):
            return
        board.push_uci(move)
    raise ValueError('feedback_target_original_definition_unproved')


def prepare_game_feedback_target(original: DecisionPresentation, *, read_connection=None):
    """Resolve explicit existing lineage with no connection during chess work.

    No lineage returns None. Existing but stale/ambiguous authority is a finite
    hold, never permission to fall back to the retired physical target. Reads
    use the supplied owner lifecycle; the prepared object is server-only.
    """
    if original.route_id is None:
        return None
    if read_connection is None:
        from tempo_legacy_database.connections import read_connection
    database = _ClosedRecombinationReads(read_connection)
    if not installed(database):
        return None
    identifier = PostgresRecombinationStore(database).current_presentation(original.route_id,original.decision_id)
    if identifier is None:
        return None
    source = _prepare_recombination_source(database,original.presentation_id)
    if source.reason or source.route_id!=original.route_id or not source.source_json:
        raise ValueError('feedback_target_source_unavailable')
    boundary = CurrentStudyRecombination(database,prepared=source,_preparing=True)
    current = boundary.try_current(original.route_id,only_presentation_id=identifier)
    if current is None:
        raise ValueError('feedback_target_'+boundary.reason)
    members = [member for member in current.members if member.presentation.presentation_id==identifier]
    if len(members)!=1 or not members[0].available:
        raise ValueError('feedback_target_current_unavailable')
    member = members[0]
    components = [component for component in member.presentation.components if component.decision_id==original.decision_id]
    if (len(components)!=1 or len(member.route_uses)!=1
            or (components[0].position_key,components[0].response_uci)!=(original.position_key,original.selected_response_uci)
            or (member.presentation.repertoire_id,member.presentation.repertoire_revision)!=(original.repertoire_id,original.repertoire_revision)):
        raise ValueError('feedback_target_original_decision_changed')
    use = member.route_uses[0]
    # The original zero-based authored occurrence is required; position alone
    # cannot pick one of several transposed/repeated route occurrences.
    if use.route_id!=original.route_id or not use.start_ply<=original.route_ply<use.stop_ply:
        raise ValueError('feedback_target_original_occurrence_changed')
    board = chess.Board(member.starting_fen)
    offset = original.route_ply-use.start_ply
    for move in member.moves[:offset]:
        board.push_uci(move)
    if (opening_position_key(board.fen())!=original.position_key
            or member.moves[offset]!=original.selected_response_uci
            or board.turn!=(member.presentation.learner_color=='white')):
        raise ValueError('feedback_target_original_occurrence_changed')
    card = _feedback_card(database,identifier)
    original_card = _feedback_card(database,original.presentation_id)
    record = _feedback_record(database,identifier)
    mapping = _feedback_mapping(database,original.route_id,member.presentation.components)
    if (not card or not original_card or not record
            or original_card['revision']!=original.presentation_revision
            or len(mapping)!=len(member.presentation.components)
            or any(row['presentation_id']!=identifier for row in mapping)):
        raise ValueError('feedback_target_mapping_changed')
    _feedback_receipt(record,source)
    _feedback_original_component(database,original,original_card,source)
    if (original_card['archived'] or original_card['pending_validation']
            or original_card['content_type']!='opening' or _feedback_blocked(database,original,original)):
        raise ValueError('feedback_target_original_unavailable')
    target = original.model_copy(update=dict(presentation_id=identifier,presentation_revision=int(card['revision']),
        available=True,introduction='introduced' if card['introduced_at'] else 'unintroduced',
        introduced_at=None,introduction_matches_current_content=bool(card['introduced_at']),
        introduced_before_game=None,introduction_game=None))
    binding = dict(record=dict(record),card=_feedback_definition(card),original=_feedback_definition(original_card),
        mapping=[dict(row) for row in mapping])
    return PreparedFeedbackTarget(original,target,source,_json(binding))


def fence_game_feedback_target(database, prepared: PreparedFeedbackTarget):
    """Bounded current owner checks only; never chess replay or projection."""
    if not isinstance(prepared,PreparedFeedbackTarget):
        raise ValueError('feedback_target_preparation_required')
    original, target = prepared.original,prepared.current
    store = PostgresRecombinationStore(database)
    if not store.try_lock_route(original.route_id):
        raise ValueError('feedback_target_route_busy')
    if not database.execute_native('SELECT pg_try_advisory_xact_lock_shared(hashtextextended(%s,0))',
            ('tempo:opening-graph:'+original.repertoire_id,)).fetchone()[0]:
        raise ValueError('feedback_target_source_busy')
    header = _source_header(database,original.repertoire_id,fence=True)
    _current_certificate(header,original.repertoire_id)
    # Reuse the existing complete captured-source fence, without constructing
    # CurrentStudyRecombination (which also reconstructs executable routes).
    boundary = object.__new__(CurrentStudyRecombination)
    boundary.database,boundary.prepared,boundary._preparing = database,prepared.source,False
    boundary._captured_source(original.route_id,original.repertoire_id,header)
    expected = json.loads(prepared.binding_json)
    cards = {}
    for identifier in sorted({original.presentation_id,target.presentation_id}):
        if not database.execute_native('SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0))',
                ('tempo:card-edit:'+identifier,)).fetchone()[0]:
            raise ValueError('feedback_target_card_busy')
        card = _feedback_card(database,identifier,fence=True)
        if not card:
            raise ValueError('feedback_target_card_busy_or_missing')
        cards[identifier] = card
    record = _feedback_record(database,target.presentation_id,fence=True)
    if not record or dict(record)!=expected['record']:
        raise ValueError('feedback_target_receipt_changed')
    _feedback_receipt(record,prepared.source)
    presentation = Presentation.model_validate_json(_json(record['result_json']['presentation']))
    mapping = _feedback_mapping(database,original.route_id,presentation.components,fence=True)
    if ([dict(row) for row in mapping]!=expected['mapping']
            or _feedback_definition(cards[target.presentation_id])!=expected['card']
            or _feedback_definition(cards[original.presentation_id])!=expected['original']):
        raise ValueError('feedback_target_definition_changed')
    memberships = database.execute_native('SELECT repertoire_id FROM repertoire_cards WHERE card_id=%s ORDER BY repertoire_id FOR SHARE SKIP LOCKED',
        (target.presentation_id,)).fetchall()
    blocked = _feedback_blocked(database,original,target)
    if len(memberships)!=1 or memberships[0][0]!=target.repertoire_id or blocked:
        raise ValueError('feedback_target_membership_or_integrity_changed')
    card = cards[target.presentation_id]
    return target.model_copy(update=dict(introduction='introduced' if card['introduced_at'] else 'unintroduced',
        introduction_matches_current_content=bool(card['introduced_at'])))


def feedback_admission_target(prepared: PreparedFeedbackTarget):
    """Physical spelling for canonical admission after explicit lineage proof.

    The original logical identity remains in the command, witness and progress.
    This spelling is used only by the existing physical executable checks.
    """
    if not isinstance(prepared,PreparedFeedbackTarget):
        raise ValueError('feedback_target_preparation_required')
    result = json.loads(prepared.binding_json)['record']['result_json']
    offset = prepared.original.route_ply-result['route_use']['start_ply']
    moves = tuple(result['moves'])
    if not 0 <= offset < len(moves):
        raise ValueError('feedback_target_original_occurrence_changed')
    return prepared.current.model_copy(update={'decision_id':decision_identity(
        prepared.current.presentation_id,offset,moves[:offset])})


def fence_feedback_completion(database,effect,completion):
    """Bounded current binding for an already applied qualified obligation.

    Application retained only the owner-prepared header/receipt digest, never
    a new lineage decision or replayable repertoire. The ordinary review owns
    its card lock; this hook takes only nonblocking existing route/source fences.
    """
    authority = effect.target_authority
    if (not authority or effect.progress is None
            or authority.get('target') != effect.progress.target.model_dump(mode='json')):
        return False
    route_id,repertoire_id = authority['route_id'],authority['repertoire_id']
    if not PostgresRecombinationStore(database).try_lock_route(route_id):
        return False
    if not database.execute_native('SELECT pg_try_advisory_xact_lock_shared(hashtextextended(%s,0))',
            ('tempo:opening-graph:'+repertoire_id,)).fetchone()[0]:
        return False
    try:
        header = _source_header(database,repertoire_id,fence=True)
        _current_certificate(header,repertoire_id)
        if _source_binding(header) != authority['source_binding']:
            return False
        record = _feedback_record(database,completion.card_id,fence=True)
        if record is None or fingerprint(dict(record)) != authority['record_digest']:
            return False
        result = record['result_json']
        presentation = Presentation.model_validate_json(_json(result['presentation']))
        receipt = PresentationChangeReceipt.model_validate_json(_json(record['receipt_json']))
        change = record['change_json']
        card = _feedback_card(database,completion.card_id)
        components = [item for item in presentation.components if item.decision_id==effect.key.decision_id]
        if (receipt.status!='applied' or receipt.plan_id!=change['command_id']
                or receipt.owner_receipt_id!='recombination:'+change['command_id']
                or sum(item==result for item in change['results'])!=1
                or len(components)!=1 or not card or card['revision']!=completion.revision
                or presentation.revision!=completion.revision or presentation.route_id!=route_id
                or presentation.repertoire_id!=repertoire_id or card['content_type']!='opening'
                or any(card[field] for field in ('archived','pending_validation','superseded_by'))
                or card['start_fen']!=result['starting_fen']
                or tuple(json.loads(card['moves_json']))!=tuple(result['moves'])):
            return False
        mappings = _feedback_mapping(database,route_id,presentation.components,fence=True)
        if len(mappings)!=len(presentation.components) or any(
                row['presentation_id']!=completion.card_id for row in mappings):
            return False
        memberships = database.execute_native('SELECT repertoire_id FROM repertoire_cards WHERE card_id=%s '
            'ORDER BY repertoire_id LIMIT 2 FOR SHARE SKIP LOCKED',(completion.card_id,)).fetchall()
        blocked = database.execute_native('SELECT 1 FROM deleted_cards WHERE card_id=%s UNION ALL '
            'SELECT 1 FROM repertoire_integrity_card_blocks WHERE card_id=%s LIMIT 1',
            (completion.card_id,completion.card_id)).fetchone()
        return len(memberships)==1 and memberships[0][0]==repertoire_id and not blocked
    except (ValueError,KeyError,TypeError):
        return False


def is_current_occurrence(database,occurrence):
    """Explicit Study regrouping proof for #487's physical target validator.

    This is identity/scope proof, not conflict or restoration eligibility. The
    existing #487 source fence/current Repertoire capability still owns those.
    """
    if not installed(database):
        return False
    row=database.execute_native("""SELECT result_json,card.start_fen,card.moves_json,card.revision
        FROM study_recombination_presentations presentation JOIN cards card ON card.id=presentation.presentation_id
        WHERE presentation.presentation_id=%s""",(occurrence.presentation_id,)).fetchone()
    if not row:
        return False
    result=row['result_json']
    value=Presentation.model_validate_json(json.dumps(result['presentation']))
    use=RouteUse(**result['route_use'])
    if ((value.repertoire_id,value.route_id,value.revision)!=(occurrence.repertoire_id,occurrence.route_id,occurrence.presentation_revision)
            or row['revision']!=value.revision or row['start_fen']!=result['starting_fen']
            or tuple(json.loads(row['moves_json']))!=tuple(result['moves'])
            or not use.start_ply<=occurrence.ply<use.stop_ply):
        return False
    mapping=PostgresRecombinationStore(database)
    if not all(mapping.current_presentation(use.route_id,c.decision_id)==value.presentation_id for c in value.components):
        return False
    board=chess.Board(result['starting_fen'])
    for offset,move in enumerate(result['moves']):
        if use.start_ply+offset==occurrence.ply:
            return board.turn==(value.learner_color=='white')
        board.push_uci(move)
    return False


def _suppress_replaced_presentations(database, day):
    """Complete queue exclusion from accepted lineage, without route recapture.

    Recovery may skip busy routes or stop at its batch bound; retirement cannot.
    A retained physical identity with an independent current membership remains
    usable because the legacy queue cannot identify its route occurrence.
    """
    database.execute_native("""WITH replaced_uses AS MATERIALIZED (
        SELECT DISTINCT retired->>0 presentation_id, change.change_json->>'route_id' route_id,
            change.change_json->'results'->0->'presentation'->>'repertoire_id' repertoire_id
        FROM study_recombination_changes change,
            jsonb_array_elements(change.change_json->'retired_uses') retired
        WHERE retired->1->>'route_id'=change.change_json->>'route_id'
          AND EXISTS(SELECT 1 FROM study_recombination_current current
              WHERE current.route_id=change.change_json->>'route_id')
          AND NOT EXISTS(SELECT 1 FROM study_recombination_current current
              WHERE current.route_id=change.change_json->>'route_id' AND current.presentation_id=retired->>0)
          AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements(change.change_json->'results') result,
              jsonb_array_elements(result->'presentation'->'components') component
              WHERE NOT EXISTS(SELECT 1 FROM study_recombination_current current
                  JOIN study_recombination_presentations presentation ON presentation.presentation_id=current.presentation_id
                  WHERE current.route_id=change.change_json->>'route_id'
                    AND current.decision_id=component->>'decision_id'
                    AND presentation.result_json->'presentation'->>'route_id'=current.route_id
                    AND presentation.result_json->'presentation'->>'repertoire_id'=
                        result->'presentation'->>'repertoire_id'))
        ) UPDATE daily_queue queue SET status='blocked' FROM cards card
        WHERE queue.card_id=card.id AND queue.queue_date=%s AND queue.status='queued'
          AND EXISTS(SELECT 1 FROM replaced_uses replaced
              WHERE replaced.presentation_id=card.id AND replaced.repertoire_id=card.repertoire_id)
          AND NOT EXISTS(SELECT 1 FROM repertoire_cards membership WHERE membership.card_id=card.id
              AND NOT EXISTS(SELECT 1 FROM replaced_uses replaced
                  WHERE replaced.presentation_id=card.id AND replaced.repertoire_id=membership.repertoire_id))
          AND NOT EXISTS(SELECT 1 FROM opening_graph_steps step JOIN opening_graph_publications publication
              ON publication.repertoire_id=step.repertoire_id AND publication.generation=step.generation AND publication.state='ready'
              WHERE step.card_id=card.id AND NOT EXISTS(SELECT 1 FROM replaced_uses replaced
                  WHERE replaced.presentation_id=card.id AND replaced.route_id=step.line_id
                    AND replaced.repertoire_id=step.repertoire_id))
          AND NOT EXISTS(SELECT 1 FROM queue_attempt_origins origin
              WHERE origin.queue_entry_id=queue.id AND origin.card_id=queue.card_id AND origin.issued_as_head=1)
          AND NOT EXISTS(SELECT 1 FROM opening_evidence_attempts attempt
              WHERE attempt.card_id=card.id AND attempt.state<>'complete'
                AND NOT EXISTS(SELECT 1 FROM review_attempt_receipts receipt WHERE receipt.attempt_id=attempt.attempt_id))""", (day,))


def project_current_presentations(database, day, *, limit=16):
    """Complete retirement exclusion, then bounded optional queue recovery."""
    if not installed(database):
        return
    _suppress_replaced_presentations(database,day)
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
        for member in current.members:
            identifier = member.presentation.presentation_id
            if member.available and identifier in {r[0] for r in database.execute_native(
                "SELECT presentation_id FROM study_recombination_presentations WHERE presentation_id=%s", (identifier,)).fetchall()}:
                due = dict(current.due)[identifier].date().isoformat()
                if due<=day and not member.active_attempt and not member.pending_attempt:
                    ensure_card_queued_after(database,identifier,0,attempt_state="clean",queue_date=day,
                        protect_presented=True,preserve_order=False)


def accept_current_split(database, presentation_id, expected_revision, *, confirmed_queue_entry_id=None):
    """Return None for legacy cards; generated identities never enter donor rewrites."""
    if not installed(database) or not PostgresRecombinationStore(database).presentation_exists(presentation_id):
        return None
    from app.study.recombination.application import Recombination
    row = database.execute_native("""SELECT p.result_json->'presentation'->>'route_id',
        (SELECT COALESCE(max(id),0) FROM reviews WHERE card_id=p.presentation_id AND rating='again' AND invalidated_at IS NULL)
        FROM study_recombination_presentations p WHERE p.presentation_id=%s""", (presentation_id,)).fetchone()
    route_id, last_failure = tuple(row)
    command_id = f"split:{presentation_id}:{expected_revision}:{last_failure}"
    if confirmed_queue_entry_id is not None:
        command_id += f":attempt:{confirmed_queue_entry_id}"
    store, boundary = PostgresRecombinationStore(database), CurrentStudyRecombination(database)
    prior = store.receipt(command_id)
    with database.raw.transaction():
        if confirmed_queue_entry_id is not None and prior is None:
            boundary.retire_confirmed_split_head(presentation_id,expected_revision,confirmed_queue_entry_id)
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
    rows = feedback_effect_targets(database,effect)
    return rows[0] if len(rows)==1 else effect.card_id


def feedback_effect_targets(database,effect):
    repertoire_id = (effect.lapse.finding.repertoire_id if effect.lapse else
                     effect.encounter.decision.repertoire_id if getattr(effect, 'encounter', None) else None)
    rows = database.execute_native("""SELECT DISTINCT current.presentation_id FROM study_recombination_current current
        JOIN study_recombination_presentations p ON p.presentation_id=current.presentation_id
        WHERE current.decision_id=%s AND p.result_json->'presentation'->>'repertoire_id'=%s LIMIT 2""",
        (effect.key.decision_id,repertoire_id)).fetchall()
    return tuple(row[0] for row in rows)


def feedback_target_matches(database,effect,target):
    """Modern observations never treat ambiguous lineage as original authority."""
    rows = feedback_effect_targets(database,effect)
    if rows:
        return rows==(target,)
    return target==effect.card_id and _feedback_record(database,target) is None


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
