"""Pure preparation and replay checks; native transaction races are separate."""
from dataclasses import replace
from datetime import datetime
from types import SimpleNamespace

from app.study.position_learning.issued_plan_service import prepare_position_issuance
from tempo_evidence_math.position_learning import Hold
from tempo_legacy_database.adapters.study.position_issued_plans import replay_exact_attempt, issue_in_borrowed_transaction
from tests.test_position_issued_plan_contracts import option, request_for


class Token:
    def __init__(self, route, repertoire):
        self.authored_digest = "authored-digest"
        self.value = {"original_snapshot_id": route.certificate.snapshot_id,
            "original_source_revision": route.certificate.source_revision,
            "original_source_digest": route.certificate.source_digest,
            "repertoire_id": repertoire, "authored_digest": self.authored_digest,
            "header": {"id": repertoire}, "integrity": {"status": "clean"}}
    def as_dict(self): return self.value


class Result:
    def __init__(self, row): self.row = row
    def fetchone(self): return self.row


class Database:
    read_only = False
    raw = SimpleNamespace(info=SimpleNamespace(transaction_status=SimpleNamespace(name="INTRANS")))
    def __init__(self, row=None): self.row, self.calls = row, []
    def execute_native(self, sql, params):
        self.calls.append((sql, params))
        return Result(self.row)


def prepared():
    route = option("italian-knight-attack")
    request = request_for(route)
    source = SimpleNamespace(routes=(route,), authored_token=Token(route, request.target.key.repertoire_id))
    return prepare_position_issuance(source, request)


def stored(plan):
    record = plan.to_record()
    return {"plan_json": record, "attempt_id": plan.attempt_id,
        "queue_entry_id": plan.queue_entry_id, "card_id": plan.original_card_id,
        "card_revision": plan.original_card_revision, "repertoire_id": plan.target.key.repertoire_id,
        "variant": plan.target.key.variant, "owner_epoch": plan.owner_epoch,
        "source_digest": record["authored_source_token"]["authored_digest"],
        "issue_study_day": plan.local_day, "plan_digest": plan.plan_id, "issued_at": plan.issued_at}


def test_replay_keeps_exact_record_without_current_source_or_schedule():
    work = prepared()
    db = Database(stored(work.plan))
    assert replay_exact_attempt(db, work.plan.attempt_id).to_record() == work.plan.to_record()
    assert len(db.calls) == 1
    assert issue_in_borrowed_transaction(db, work).to_record() == work.plan.to_record()
    assert len(db.calls) == 2


def test_same_attempt_changed_payload_conflicts_and_corrupt_row_holds():
    work = prepared()
    other = prepare_position_issuance(work.source, replace(work.request, dues=tuple(
        replace(w, due=not w.due) for w in work.request.dues)))
    db = Database(stored(work.plan))
    assert issue_in_borrowed_transaction(db, other).reason == "position_attempt_payload_conflict"
    db.row = {**db.row, "source_digest": "snapshot-instead-of-authored"}
    assert replay_exact_attempt(db, work.plan.attempt_id).reason == "position_issued_row_mismatch"


def test_preparation_rejects_unowned_routes_and_unproven_prefixes():
    work = prepared()
    assert isinstance(work.plan.issued_at, datetime)
    assert prepare_position_issuance(work.source, replace(work.request, routes=())).reason == "position_routes_not_owner_prepared"
    assert issue_in_borrowed_transaction(Database(), object()).reason == "position_issuance_preparation_required"


def test_fresh_issuance_checks_admission_and_all_witnesses_before_insert(monkeypatch):
    from tempo_legacy_database.adapters.study import position_issued_plans as issuer
    work = prepared()
    plan = work.plan
    owner = SimpleNamespace(disposition="position", epoch=plan.owner_epoch, reason="active")
    monkeypatch.setattr(issuer, "owner_mode", lambda *args: owner)
    monkeypatch.setattr(issuer, "fence_position_owner_in_writer", lambda *args, **kwargs: owner)
    monkeypatch.setattr(work.source.authored_token, "repertoire_id", plan.target.key.repertoire_id, raising=False)
    card = dict(id=plan.original_card_id, revision=plan.original_card_revision, archived=0,
                content_type="opening", start_fen="fen", moves_json="[]", trained_color="white")
    queue = dict(card_id=plan.original_card_id, status="queued", queue_date=plan.local_day.isoformat(), cycle=0)
    origin = dict(card_id=plan.original_card_id, revision=plan.original_card_revision,
                  queue_date=queue["queue_date"], cycle=0, admission_repertoire_id=plan.target.key.repertoire_id,
                  **{k: card[k] for k in ("start_fen", "moves_json", "trained_color", "content_type")})

    class Cursor(Result):
        def fetchall(self): return self.row
    class Writer(Database):
        current_good = False
        def execute_native(self, sql, params):
            self.calls.append((sql, params))
            if 'SELECT * FROM study_position_issued_plans WHERE attempt_id' in sql: return Cursor(None)
            if 'pg_try_advisory' in sql or 'FROM pg_index' in sql: return Cursor((True,))
            if 'FROM cards WHERE' in sql: return Cursor(card)
            if 'FROM daily_queue_days' in sql: return Cursor({"queue_date": queue["queue_date"]})
            if 'FROM daily_queue WHERE' in sql: return Cursor(queue)
            if 'FROM study_position_issued_plans' in sql: return Cursor([])
            if 'count(*) FROM queue_attempt_origins' in sql: return Cursor((1,))
            if 'FROM queue_attempt_origins' in sql: return Cursor([origin])
            if 'count(*) FROM opening_evidence_attempts' in sql: return Cursor((0,))
            if 'FROM opening_evidence_attempts' in sql or 'FROM review_attempt_receipts' in sql: return Cursor(None)
            if 'FROM study_position_grades' in sql:
                assert 'owner_epoch=%s AND version_after=%s' in sql
                assert params[-2:] == (plan.owner_epoch, next(w.position_revision for w in plan.due_positions if w.definition == plan.target))
                return Cursor((1,) if self.current_good else None)
            if 'INSERT INTO study_position_issued_plans' in sql: return Cursor((plan.attempt_id,))
            raise AssertionError(sql)
    class Reader:
        witnesses = plan.due_positions
        def __init__(self, *args, **kwargs): self.states = self
        def read_many(self, keys, *, lock):
            assert lock and keys == tuple(w.definition.key for w in plan.due_positions)
            return tuple(object() for _ in keys)
        def read_context(self, keys): return self.witnesses
    monkeypatch.setattr(issuer, "PositionDueCandidateReader", Reader)
    db = Writer()
    assert issuer.issue_in_borrowed_transaction(db, work) == plan
    insert = next(params for sql, params in db.calls if 'INSERT INTO study_position_issued_plans' in sql)
    assert insert[7] == work.source.authored_token.authored_digest
    db = Writer()
    db.current_good = True
    assert issuer.issue_in_borrowed_transaction(db, work).reason == "position_already_cleanly_tested_today"
    assert not any('INSERT INTO study_position_issued_plans' in sql for sql, _ in db.calls)
    Reader.witnesses = tuple(replace(w, position_revision=w.position_revision + 1) for w in plan.due_positions)
    db = Writer()
    assert issuer.issue_in_borrowed_transaction(db, work).reason == "position_due_witness_changed"
    assert not any('INSERT INTO study_position_issued_plans' in sql for sql, _ in db.calls)

    # A forged or stale cutoff is rejected even when all shown witnesses match.
    route = work.source.routes[0]
    request = request_for(route, target_ply=2)
    shown = {d.definition.key for d in route.decisions if d.ply < 5}
    short = prepare_position_issuance(work.source, replace(request, end_ply=5,
        dues=tuple(w for w in request.dues if w.definition.key in shown)))
    plan = short.plan
    Reader.witnesses = plan.due_positions
    next_key = next(d.definition.key for d in route.decisions if d.ply >= 5)
    def context(self, keys):
        if keys == (next_key,):
            return (self.omitted,)
        return self.witnesses
    Reader.read_context = context
    Reader.omitted = Hold("position_state_missing")
    assert issuer.issue_in_borrowed_transaction(Writer(), short) == plan
    Reader.omitted = request.dues[-1]
    db = Writer()
    assert issuer.issue_in_borrowed_transaction(db, short).reason == "position_presentation_end_unproven"
    assert not any('INSERT INTO study_position_issued_plans' in sql for sql, _ in db.calls)
    assert issuer.issue_in_borrowed_transaction(Database(stored(plan)), short).to_record() == plan.to_record()


def test_clean_proof_requires_saved_review_and_all_first_response_receipts(monkeypatch):
    from app.services import postgres_opening_evidence as evidence
    from tempo_legacy_database.adapters.study.position_issued_plans import read_clean_presentation_proof
    work = prepared()
    plan = work.plan
    monkeypatch.setattr(evidence, 'decode_position_first_response', lambda payload, digest, saved: payload, raising=False)
    done = dict(card_id=plan.original_card_id, card_revision=plan.original_card_revision,
        queue_entry_id=plan.queue_entry_id, repertoire_id=plan.target.key.repertoire_id,
        state='complete', contiguous_sequence=7, terminal_json={'state': 'complete', 'final_sequence': 7},
        review_id=51, retired_operation_id=None, receipt_card=plan.original_card_id,
        receipt_queue=plan.queue_entry_id, receipt_review=51, outcome='correct', guided=0,
        review_card=plan.original_card_id, source_kind='position_study', source_ref=f'attempt:{plan.attempt_id}',
        invalidated_at=None)
    receipts = []
    for index, slot in enumerate(plan.decision_slots):
        payload = dict(event_id='event-' + str(index), decision_index=index, disposition='expected',
            manual_failure=False, assistance_before=False, actual_uci=slot.definition.expected_uci)
        receipts.append(dict(payload_json=payload, payload_digest='digest', decision_index=index,
            event_id=payload['event_id'], attempt_id=plan.attempt_id, repertoire_id=slot.definition.key.repertoire_id,
            variant=slot.definition.key.variant, position=slot.definition.key.position, owner_epoch=plan.owner_epoch))
    class Cursor(Result):
        def fetchall(self): return self.row
    class Reader(Database):
        def execute_native(self, sql, params):
            if 'study_position_issued_plans' in sql: return Cursor(stored(plan))
            if 'FROM opening_evidence_attempts a' in sql: return Cursor(done)
            if 'FROM study_position_first_responses' in sql: return Cursor(receipts)
            raise AssertionError(sql)
    db = Reader()
    proof = read_clean_presentation_proof(db, plan.attempt_id)
    assert proof.attempt_id == plan.attempt_id and len(proof.first_responses) == len(plan.decision_slots)
    done['source_ref'] = 'different-attempt'
    assert read_clean_presentation_proof(db, plan.attempt_id).reason == 'position_prior_completion_unavailable'
    done['source_ref'] = f'attempt:{plan.attempt_id}'
    done['invalidated_at'] = 'later'
    assert read_clean_presentation_proof(db, plan.attempt_id).reason == 'position_prior_completion_unavailable'
    done['invalidated_at'] = None
    receipts[0]['payload_json']['assistance_before'] = True
    assert read_clean_presentation_proof(db, plan.attempt_id).reason == 'position_prior_responses_not_clean'


def test_native_replay_after_restart_uses_stored_authored_digest():
    """Draft native proof; requires an explicitly task-owned disposable DSN."""
    import os
    from pathlib import Path
    from uuid import uuid4
    import pytest
    dsn = os.environ.get('TEMPO_POSITION_TEST_DSN')
    if not dsn:
        pytest.skip('requires task-owned disposable TEMPO_POSITION_TEST_DSN')
    import json
    import psycopg
    from psycopg import sql
    from tempo_legacy_database.adapters.postgres_store import tempo_row_factory
    work = prepared()
    plan = work.plan
    record = plan.to_record()
    with psycopg.connect(dsn, autocommit=True, row_factory=tempo_row_factory) as conn:
        assert conn.execute('SELECT current_database()').fetchone()[0].startswith('tempo_position_test_')
        schema = 'position_g2_' + uuid4().hex
        conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
        try:
            conn.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(schema)))
            conn.execute('CREATE TABLE tempo_schema_migrations(version integer PRIMARY KEY)')
            ddl = Path(__file__).resolve().parents[3] / 'database/tempo_legacy_database/ddl/migrations/057_position_study.sql'
            conn.execute(ddl.read_text())
            conn.execute('INSERT INTO study_position_epochs VALUES (%s,%s,%s,%s)',
                (plan.target.key.repertoire_id, plan.owner_epoch, work.source.authored_token.authored_digest, plan.issued_at))
            conn.execute('''INSERT INTO study_position_issued_plans VALUES
                (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s)''',
                (plan.attempt_id,plan.queue_entry_id,plan.original_card_id,plan.original_card_revision,
                 plan.target.key.repertoire_id,plan.target.key.variant,plan.owner_epoch,
                 work.source.authored_token.authored_digest,plan.local_day,plan.plan_id,
                 json.dumps(record),plan.issued_at))
            db = SimpleNamespace(execute_native=conn.execute)
            assert replay_exact_attempt(db, plan.attempt_id).to_record() == record
        finally:
            conn.execute('SET search_path TO public')
            conn.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))


def test_later_again_reopens_target_without_discarding_other_clean_prefix(monkeypatch):
    from app.study.position_learning.prefix_eligibility import CleanFirstResponse, CleanPresentationProof
    from app.study.position_learning.route_planner import choose_plan
    from tempo_legacy_database.adapters.study import position_issued_plans as issuer
    route = option('italian-knight-attack')
    work = prepared()
    plan = work.plan
    decisions = tuple(route.decisions)
    proof = CleanPresentationProof('prior', route.route_id, route.start_fen, route.moves_uci,
        decisions, tuple(CleanFirstResponse(d.ply, d.definition, d.definition.expected_uci, False,
            'event-' + str(d.ply)) for d in decisions), route.certificate.snapshot_id,
        route.certificate.source_revision, route.certificate.source_digest, plan.local_day,
        plan.owner_epoch, True, 'prior', '51')
    monkeypatch.setattr(issuer, 'read_clean_presentation_proof', lambda *a, **k: proof)
    monkeypatch.setattr(issuer, 'replay_exact_attempt', lambda *a: plan)
    class History(Database):
        has_index = True
        has_later_again = False
        def execute_native(self, sql, params):
            self.calls.append((sql, params))
            if 'to_regclass' in sql: return Result(('study_position_recent_again' if self.has_index else None,))
            if 'FROM study_position_grades again' in sql: return Result((1,) if self.has_later_again else None)
            raise AssertionError(sql)
    db = History()
    args = dict(target=plan.target, owner_epoch=plan.owner_epoch, study_day=plan.local_day)
    unchanged, = issuer.read_applicable_clean_proofs(db, ('prior',), **args)
    assert unchanged == proof
    assert choose_plan(plan.target.key, (route,), clean_proofs=(unchanged,),
        study_day=plan.local_day, owner_epoch=plan.owner_epoch).reason == 'position_already_cleanly_tested_today'
    db.has_later_again = True
    projected, = issuer.read_applicable_clean_proofs(db, ('prior',), **args)
    assert len(projected.first_responses) > 0
    assert all(d.definition.key != plan.target.key for d in projected.attempt_decisions)
    selected = choose_plan(plan.target.key, (route,), clean_proofs=(projected,),
        study_day=plan.local_day, owner_epoch=plan.owner_epoch)
    assert not isinstance(selected, Hold) and selected.start_ply > 0
    sql, params = db.calls[-1]
    assert 'again.version_after <= state.version' in sql
    assert 'again.accepted_at > prior_review.completed_at::timestamptz' in sql
    assert params[-1] == next(w.position_revision for w in plan.due_positions if w.definition == plan.target)
    db.has_index = False
    assert issuer.read_applicable_clean_proofs(db, ('prior',), **args).reason == 'position_again_lookup_index_required'


def test_native_superseding_again_preserves_clean_prefix(monkeypatch):
    """Opt-in serial proof against the task-owned disposable PostgreSQL DSN."""
    import os
    from datetime import timedelta
    from uuid import uuid4
    import pytest
    dsn = os.environ.get('TEMPO_POSITION_TEST_DSN')
    if not dsn:
        pytest.skip('requires task-owned disposable TEMPO_POSITION_TEST_DSN')
    import psycopg
    from psycopg import sql
    from tempo_legacy_database.adapters.postgres_store import tempo_row_factory
    from app.study.position_learning.prefix_eligibility import CleanFirstResponse, CleanPresentationProof
    from tempo_legacy_database.adapters.study import position_issued_plans as issuer
    work = prepared()
    plan, route = work.plan, work.source.routes[0]
    proof = CleanPresentationProof('prior', route.route_id, route.start_fen, route.moves_uci,
        route.decisions, tuple(CleanFirstResponse(d.ply, d.definition, d.definition.expected_uci,
            False, 'event-' + str(d.ply)) for d in route.decisions), plan.source_snapshot_id,
        plan.source_revision, plan.source_digest, plan.local_day, plan.owner_epoch, True, 'prior', '51')
    monkeypatch.setattr(issuer, 'read_clean_presentation_proof', lambda *a, **k: proof)
    monkeypatch.setattr(issuer, 'replay_exact_attempt', lambda *a: plan)
    witness = next(w for w in plan.due_positions if w.definition == plan.target)
    target = plan.target
    args = dict(target=target, owner_epoch=plan.owner_epoch, study_day=plan.local_day)
    with psycopg.connect(dsn, autocommit=True, row_factory=tempo_row_factory) as conn:
        assert conn.execute('SELECT current_database()').fetchone()[0].startswith('tempo_position_test_')
        schema = 'position_g2_again_' + uuid4().hex
        conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
        try:
            conn.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(schema)))
            conn.execute('''CREATE TABLE study_position_states(repertoire_id text,variant text,position text,
                owner_epoch text,version bigint)''')
            conn.execute('''CREATE TABLE study_position_grades(repertoire_id text,variant text,position text,
                owner_epoch text,accepted_study_day date,grade text,version_before bigint,version_after bigint,
                expected_uci text,policy_version text,accepted_at timestamptz)''')
            conn.execute('''CREATE INDEX study_position_recent_again ON study_position_grades
                (repertoire_id,variant,position,owner_epoch,accepted_study_day,version_after) WHERE grade='again' ''')
            conn.execute('CREATE TABLE review_attempt_receipts(attempt_id text PRIMARY KEY, completed_at text)')
            conn.execute('INSERT INTO review_attempt_receipts VALUES (%s,%s)', ('prior', plan.issued_at.isoformat()))
            conn.execute('INSERT INTO study_position_states VALUES (%s,%s,%s,%s,%s)',
                (target.key.repertoire_id,target.key.variant,target.key.position,plan.owner_epoch,witness.position_revision))
            db = SimpleNamespace(execute_native=conn.execute)
            assert issuer.read_applicable_clean_proofs(db, ('prior',), **args) == (proof,)
            conn.execute('''INSERT INTO study_position_grades VALUES (%s,%s,%s,%s,%s,'again',%s,%s,%s,%s,%s)''',
                (target.key.repertoire_id,target.key.variant,target.key.position,plan.owner_epoch,plan.local_day,
                 witness.position_revision,witness.position_revision + 1,target.expected_uci,target.policy_version,
                 plan.issued_at + timedelta(seconds=1)))
            # An uncommitted or not-yet-reflected effect cannot supersede proof.
            assert issuer.read_applicable_clean_proofs(db, ('prior',), **args) == (proof,)
            conn.execute('UPDATE study_position_states SET version=version+1')
            projected, = issuer.read_applicable_clean_proofs(db, ('prior',), **args)
            assert 0 < len(projected.first_responses) < len(proof.first_responses)
            assert all(d.definition != target for d in projected.attempt_decisions)
            conn.execute("UPDATE study_position_grades SET accepted_at=%s", (plan.issued_at - timedelta(seconds=1),))
            assert issuer.read_applicable_clean_proofs(db, ('prior',), **args) == (proof,)
        finally:
            conn.execute('SET search_path TO public')
            conn.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))
