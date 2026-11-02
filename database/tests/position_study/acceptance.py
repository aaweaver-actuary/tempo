"""Disposable full-schema source-to-queue probe, opt-in and never live data.

Requires an EMPTY task-owned local tempo_position_test_n2* database.  Explicitly
invoked: pytest database/tests/position_study/acceptance.py.  This probe does
not claim browser, review or installed-image qualification.
"""
import hashlib
import json
import os
from time import perf_counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import chess
import psycopg
import pytest
from fsrs import Card
from psycopg.conninfo import conninfo_to_dict

from tempo_evidence_math.position_learning import Hold, SourceCertificate
from tempo_legacy_database.adapters.postgres_store import PostgresConnection, tempo_row_factory
from tempo_legacy_database.adapters.authored_repertoires.position_study_source import prepare_position_source, fence_position_source
from tempo_legacy_database.adapters.study.position_seed_inventory import DonorFact, InventoryRequest, SourceFacts, read_card_page
from tempo_legacy_database.adapters.study.position_seed import seed_shadow_page
from app.repertoire.interfaces.position_schedule_occurrences import inventory_from_snapshot

ROOT = Path(__file__).resolve().parents[3]


def _dsn():
    dsn = os.environ.get('TEMPO_POSITION_N2_DSN')
    if not dsn:
        pytest.skip('requires explicit empty task-owned TEMPO_POSITION_N2_DSN')
    params = conninfo_to_dict(dsn)
    if params.get('host') not in ('127.0.0.1', 'localhost') or not params.get('dbname', '').startswith('tempo_position_test_n2'):
        raise RuntimeError('refusing non-local or non-N2 database')
    return dsn


def _migrate(raw):
    from tooling import apply_postgres_migrations as migration
    assert raw.execute("SELECT count(*) FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind IN ('r','p','v','S')").fetchone()[0] == 0, 'N2 database must be empty'
    paths = sorted(migration.MIGRATIONS.glob('[0-9][0-9][0-9]_*.sql'))
    migration.validate_migration_history(paths, [], expected_version=migration.POSTGRES_SCHEMA_VERSION)
    assert len(paths) == 57
    for path in paths:
        registration = migration.MODULE_MIGRATIONS.get(path.name)
        if registration:
            source = (ROOT / 'database' / registration[2]).read_bytes()
            assert hashlib.sha256(source).hexdigest() == registration[3]
            assert '-- sha256: ' + registration[3] in path.read_text()
            raw.execute(source.decode(), prepare=False)
        raw.execute(path.read_text(), prepare=False)
        assert raw.execute('SELECT MAX(version) FROM tempo_schema_migrations').fetchone()[0] == int(path.name[:3])


def test_real_schema_source_seed_and_due_queue(monkeypatch):
    from app.services.position_queue_admission import prepare_due, admit_due
    dsn = _dsn()
    now = datetime.now(timezone.utc)
    day = now.date().isoformat()
    moves = json.dumps(['e2e4', 'e7e5', 'g1f3'])
    fsrs = Card(card_id=42, due=now - timedelta(days=1)).to_json()
    with psycopg.connect(dsn, row_factory=tempo_row_factory) as raw:
        _migrate(raw)
        raw.execute('INSERT INTO settings(id) VALUES(1)')
        raw.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES('n2','N2 synthetic','fixture',%s)", (now.isoformat(),))
        raw.execute("INSERT INTO opening_graph_publications(repertoire_id,generation,state,published_at) VALUES('n2',3,'ready',%s)", (now.isoformat(),))
        raw.execute("INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,start_fen,moves_json,created_at) VALUES('route','n2','Legal route','white',%s,%s,%s)", (chess.STARTING_FEN,moves,now.isoformat()))
        raw.execute("""INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,fsrs_card_json,introduced_at,revision,content_type,trained_color)
            VALUES('card','n2','prefix',%s,%s,'learning',%s,%s,%s,1,'opening','white')""", (chess.STARTING_FEN,moves,day,fsrs,(now-timedelta(days=2)).isoformat()))
        raw.execute("INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES('n2','card')")
        raw.execute("""INSERT INTO opening_graph_steps(repertoire_id,generation,line_id,decision_index,card_id,decision_fen_key,starting_fen,moves_json,trained_color)
            VALUES('n2',3,'route',0,'card',%s,%s,%s,'white')""", (chess.STARTING_FEN,chess.STARTING_FEN,moves))
        raw.execute("""INSERT INTO repertoire_integrity_state(repertoire_id,status,scan_status,scan_generation,checked_at,scan_completed_sources,scan_total_sources)
            VALUES('n2','clean','idle','scan:2',%s,1,1)""", (now.isoformat(),))
        for ident, kind, generation, payload in [('graph','opening_graph_rebuild',3,{}), ('scan','integrity_scan',2,{'repertoire_id':'n2','graph_generation':3})]:
            raw.execute("""INSERT INTO background_tasks(id,kind,deduplication_key,generation,state,payload_json,next_attempt_at,created_at,updated_at)
                VALUES(%s,%s,'n2',%s,'complete',%s,%s,%s,%s)""", (ident,kind,generation,json.dumps(payload),now.isoformat(),now.isoformat(),now.isoformat()))
        raw.commit()
        port = PostgresConnection(raw)
        prepared = prepare_position_source(port, 'n2')
        assert not isinstance(prepared, Hold), prepared
        assert fence_position_source(port, prepared) is None
        membership = prepared.snapshot.membership
        certificate = SourceCertificate(membership.snapshot_id, membership.source_revision, membership.digest, True, True)
        raw.commit()
        # Grants affect this task-owned database only; never alter shared role credentials.
        raw.execute('GRANT USAGE ON SCHEMA public TO tempo_reader, tempo_writer')
        raw.execute('GRANT SELECT ON ALL TABLES IN SCHEMA public TO tempo_reader')
        raw.execute('GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA public TO tempo_writer')
        raw.execute('GRANT USAGE,SELECT,UPDATE ON ALL SEQUENCES IN SCHEMA public TO tempo_writer')
        raw.commit()
        with psycopg.connect(dsn, user='tempo_reader', row_factory=tempo_row_factory) as reader:
            reader.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            read_port = PostgresConnection(reader, read_only=True)
            rows = read_card_page(read_port, candidate_card_ids=('card',))
            donors = tuple(DonorFact(**(dict(row) | {
                'owner_repertoire_ids': tuple(row['owner_repertoire_ids']),
                'archived': bool(row['archived']),
                'pending_validation': bool(row['pending_validation']),
                'integrity_blocked': bool(row['integrity_blocked']),
                'deleted': bool(row['deleted']),
            })) for row in rows)
        inventory = inventory_from_snapshot(prepared.snapshot, source_certificate=certificate)
        assert not isinstance(inventory, Hold), inventory
        request = InventoryRequest((SourceFacts(prepared.snapshot, inventory),), donors)
        for _ in range(10):
            raw.execute('BEGIN')
            result = seed_shadow_page(port, prepared, request, epoch='n2-epoch', now=now, page_size=100)
            raw.commit()
            if result['stage'] == 'complete': break
        assert result['stage'] == 'complete' and result['held'] == 0
        assert raw.execute("SELECT fsrs_card_json FROM cards WHERE id='card'").fetchone()[0] == fsrs
        assert raw.execute('SELECT count(*) FROM study_position_states').fetchone()[0] > 0
        # Active mode is a synthetic fixture insertion only, not the operator path.
        raw.execute("UPDATE study_position_owners SET mode='active' WHERE repertoire_id='n2'")
        raw.commit()
        queue_started = perf_counter()
        proposal = prepare_due(port, day, {})
        assert isinstance(proposal, dict) and 'candidate' in proposal, proposal
        assert admit_due(port, day, proposal) is None
        raw.commit()
        print(f'N2_SOURCE_QUEUE_ADMISSION_MS={(perf_counter()-queue_started)*1000:.3f}')
        assert tuple(raw.execute("SELECT card_id,admission_repertoire_id FROM daily_queue WHERE queue_date=%s", (day,)).fetchone()) == ('card','n2')
        raw.execute("INSERT INTO daily_queue_days(queue_date,seed,membership_hash,generated_at) VALUES(%s,1,'n2',%s)", (day,now.isoformat()))
        raw.commit()
        entry = raw.execute('SELECT id FROM daily_queue').fetchone()[0]
        raw.commit()
        # Connection routing points the real preparation at this fixture; the
        # source, head, lease, stamp and issuer implementations are untouched.
        from contextlib import contextmanager
        from tempo_legacy_database import connections
        from app.services.queue_next_card import prepare_queue_head
        from app.queue_commands import issue_queue_head
        @contextmanager
        def fixture_reader():
            with psycopg.connect(dsn, user='tempo_reader', row_factory=tempo_row_factory) as reader:
                yield PostgresConnection(reader, read_only=True)
        monkeypatch.setattr(connections, 'read_connection', fixture_reader)
        envelope = {'queue_date': day, 'entry_id': entry, 'card_id': 'card', 'expected_revision': 1}
        proposal = prepare_queue_head(envelope)
        assert proposal.mode == 'position', proposal
        issued = issue_queue_head(port, proposal)
        assert issued['issued'] and issued['position_study_hold'] is None, issued
        raw.commit()
        replay = prepare_queue_head(envelope)
        assert replay.mode == 'replay' and replay.proposal.to_wire() == issued['position_presentation']
        assert raw.execute('SELECT issued_as_head FROM queue_attempt_origins WHERE queue_entry_id=%s', (entry,)).fetchone()[0] == 1
        assert raw.execute('SELECT count(*) FROM study_position_issued_plans').fetchone()[0] == 1
        raw.commit()
        _real_review_journey(raw, port, dsn, monkeypatch, replay.proposal, entry, fsrs)



def _real_review_journey(raw, port, dsn, monkeypatch, plan, entry, original_fsrs):
    """Use real checkpoint/review owners; connection routing is fixture-only."""
    from contextlib import contextmanager
    from app.contracts.opening_evidence import OpeningEvidenceCheckpoint
    from app.services import postgres_opening_evidence as evidence, learning_review_adapter
    from app.services.opening_decision_evidence import decision_manifest
    from tempo_legacy_database import connections
    from tempo_legacy_database.adapters.opening_evidence import storage as evidence_storage
    from tempo_legacy_database.adapters.study.recombination import prepare_recombination_review

    @contextmanager
    def fixture_reader(*args, **kwargs):
        with psycopg.connect(dsn, user='tempo_reader', row_factory=tempo_row_factory) as reader:
            yield PostgresConnection(reader, read_only=True)

    monkeypatch.setenv('TEMPO_DATABASE_READ_URL', dsn)
    monkeypatch.setenv('TEMPO_DATABASE_WRITE_URL', dsn)
    monkeypatch.setattr(connections, 'read_connection', fixture_reader)
    monkeypatch.setattr(connections, 'background_read_connection', fixture_reader)
    monkeypatch.setattr(evidence_storage, 'read_connection', fixture_reader)
    monkeypatch.setattr(learning_review_adapter, 'read_connection', fixture_reader)
    scope_row = raw.execute('''SELECT snapshot.*, context.effective_trained_color
        FROM opening_evidence_presentations snapshot
        JOIN opening_evidence_queue_contexts context ON context.presentation_snapshot_id=snapshot.id
        WHERE context.queue_entry_id=%s AND context.repertoire_id='n2' ''', (entry,)).fetchone()
    assert scope_row is not None
    original = decision_manifest(evidence._manifest_snapshot(dict(scope_row)), 'n2')
    manifest = evidence.issued_position_manifest(plan, original)
    assert len(manifest['decisions']) >= 2, 'fixture must include a clean decision before the later wrong'
    now = datetime.now(timezone.utc).isoformat()
    events = []
    for index, decision in enumerate(manifest['decisions']):
        board = chess.Board(decision['fen'])
        move = decision['expected_uci']
        if index == 1:
            move = next(m.uci() for m in board.legal_moves if m.uci() != move)
        events.append(dict(sequence=len(events)+1, decision_index=index,
            decision_id=decision['decision_id'], expected_uci=decision['expected_uci'],
            kind='first_response', observed_at=now, response_uci=move,
            disposition='wrong' if index == 1 else 'expected'))
        if index == 1:
            events.append(dict(sequence=len(events)+1, decision_index=index,
                decision_id=decision['decision_id'], expected_uci=decision['expected_uci'],
                kind='correction', observed_at=now, response_uci=decision['expected_uci']))
    checkpoint = dict(attempt_id=plan.attempt_id, manifest=manifest, origin_queue_entry_id=entry,
        queue_entry_id=entry, started_at=now, study_timezone='UTC', source='live', events=events[:2])
    raw.commit()
    # Two first responses: the second wrong is durable before aggregate review.
    prepared = evidence.prepare_standalone_checkpoint({'checkpoint': checkpoint})
    raw.execute('BEGIN')
    first = evidence.commit_standalone_checkpoint(port, prepared)
    raw.commit()
    assert first['persisted'] and first['contiguous_sequence'] == 2
    grade_rows = [tuple(row) for row in raw.execute('SELECT event_id,grade FROM study_position_grades ORDER BY event_id').fetchall()]
    assert len(grade_rows) == 1 and grade_rows[0][1] == 'again'
    assert raw.execute('SELECT count(*) FROM reviews').fetchone()[0] == 0
    assert raw.execute("SELECT fsrs_card_json FROM cards WHERE id='card'").fetchone()[0] == original_fsrs
    raw.commit()
    # Exact delivery replay cannot duplicate the negative effect.
    prepared = evidence.prepare_standalone_checkpoint({'checkpoint': checkpoint})
    raw.execute('BEGIN')
    retry = evidence.commit_standalone_checkpoint(port, prepared)
    raw.commit()
    assert retry['persisted']
    assert [tuple(row) for row in raw.execute('SELECT event_id,grade FROM study_position_grades ORDER BY event_id').fetchall()] == grade_rows
    raw.commit()
    completion = {**checkpoint, 'events': events,
        'terminal': {'state': 'complete', 'final_sequence': len(events), 'ended_at': now}}
    request = OpeningEvidenceCheckpoint.model_validate(completion)
    prepared_manifest = evidence.prepare_checkpoint(request)
    with fixture_reader() as reader:
        prepared_position = evidence.prepare_position_checkpoint(reader, request)
    review_payload = {'card_id': 'card', 'review': {'outcome': 'again', 'guided': True,
        'queue_entry_id': entry, 'expected_revision': 1, 'attempt_id': plan.attempt_id,
        'opening_evidence_completion': completion}}
    review_payload = prepare_recombination_review(review_payload, read_connection=fixture_reader)
    review_payload.update(prepared_manifest=prepared_manifest, prepared_position_checkpoint=prepared_position)
    raw.execute('BEGIN')
    save_started = perf_counter()
    saved = learning_review_adapter.submit_legacy_review(port, review_payload)
    raw.commit()
    print(f'N2_DIRECT_REVIEW_COMMIT_MS={(perf_counter()-save_started)*1000:.3f}')
    assert saved['persisted'] and saved['queue_entry_id'] == entry
    assert saved['requeue_entry_id'] is None
    rows = [tuple(row) for row in raw.execute('SELECT grade FROM study_position_grades ORDER BY decision_index').fetchall()]
    assert rows[0] == ('good',) and rows[1] == ('again',), rows
    receipt = raw.execute('''SELECT r.card_id,r.queue_entry_id,r.review_id,v.source_kind,v.source_ref
        FROM review_attempt_receipts r JOIN reviews v ON v.id=r.review_id WHERE r.attempt_id=%s''', (plan.attempt_id,)).fetchone()
    assert tuple(receipt) == ('card',entry,saved['review_id'],'position_study','attempt:'+plan.attempt_id)
    assert raw.execute("SELECT fsrs_card_json FROM cards WHERE id='card'").fetchone()[0] == original_fsrs
    raw.commit()
    # Same aggregate delivery must return the original receipt, not regrade.
    with fixture_reader() as reader:
        review_payload['prepared_position_checkpoint'] = evidence.prepare_position_checkpoint(reader, request)
    raw.execute('BEGIN')
    replay = learning_review_adapter.submit_legacy_review(port, review_payload)
    raw.commit()
    assert replay['review_id'] == saved['review_id']
    assert raw.execute('SELECT count(*) FROM reviews').fetchone()[0] == 1
    assert [tuple(row) for row in raw.execute('SELECT grade FROM study_position_grades ORDER BY decision_index').fetchall()] == rows
    assert raw.execute("SELECT fsrs_card_json FROM cards WHERE id='card'").fetchone()[0] == original_fsrs
    raw.commit()
