"""A real completed line revisiting one position must not grade Good twice.

Run only against an empty task-owned local N2 database. This is a source/native
consumer proof, not a browser or operator-lifecycle qualification.
"""
from contextlib import contextmanager
from datetime import datetime, timezone

import chess
import psycopg

from database.tests.position_study.acceptance import _dsn
from database.tests.position_study.italian_acceptance import seed_disposable_source
from tempo_legacy_database.adapters.postgres_store import PostgresConnection, tempo_row_factory


def test_real_repeated_position_completion_has_one_good_and_observe(monkeypatch):
    _run_repeated_position(monkeypatch, wrong=False)


def test_real_repeated_wrong_occurrences_advance_successive_versions(monkeypatch):
    _run_repeated_position(monkeypatch, wrong=True)


def _run_repeated_position(monkeypatch, *, wrong):
    from app.contracts.opening_evidence import OpeningEvidenceCheckpoint
    from app.queue_commands import issue_queue_head
    from app.services import postgres_opening_evidence as evidence, learning_review_adapter
    from app.services.opening_decision_evidence import decision_manifest
    from app.services.position_queue_admission import prepare_due, admit_due
    from app.services.queue_next_card import prepare_queue_head
    from tempo_legacy_database import connections
    from tempo_legacy_database.adapters.opening_evidence import storage as evidence_storage
    from tempo_legacy_database.adapters.study.recombination import prepare_recombination_review

    dsn = _dsn()
    repeated = ('g1f3','g8f6','f3g1','f6g8','g1f3')
    # Different authored route IDs, identical response for the revisited root.
    routes = (repeated, repeated + ('g8f6',))
    board = chess.Board()
    keys = []
    from tempo_evidence_math import opening_position_key
    for i, move in enumerate(repeated):
        if i % 2 == 0: keys.append(opening_position_key(board.fen()))
        board.push_uci(move)
    assert keys[0] == keys[2] and keys[0] != keys[1]

    @contextmanager
    def reader(*args, **kwargs):
        with psycopg.connect(dsn,user='tempo_reader',row_factory=tempo_row_factory) as raw:
            yield PostgresConnection(raw,read_only=True)

    monkeypatch.setenv('TEMPO_DATABASE_READ_URL',dsn)
    monkeypatch.setenv('TEMPO_DATABASE_WRITE_URL',dsn)
    monkeypatch.setattr(connections,'read_connection',reader)
    monkeypatch.setattr(connections,'background_read_connection',reader)
    monkeypatch.setattr(evidence_storage,'read_connection',reader)
    monkeypatch.setattr(learning_review_adapter,'read_connection',reader)
    with psycopg.connect(dsn,row_factory=tempo_row_factory) as raw:
        db, _, physical, day = seed_disposable_source(raw,dsn,routes,('repeated','alias'),repertoire_id='repeat')
        proposal = prepare_due(db,day,{})
        assert isinstance(proposal,dict) and 'candidate' in proposal, proposal
        assert admit_due(db,day,proposal) is None
        raw.commit()
        row = raw.execute("SELECT id,card_id FROM daily_queue WHERE status='queued' ORDER BY position,id LIMIT 1").fetchone()
        entry,card = row['id'],row['card_id']
        raw.commit()
        envelope = {'queue_date':day,'entry_id':entry,'card_id':card,'expected_revision':1}
        prepared = prepare_queue_head(envelope)
        assert prepared.mode == 'position', prepared
        issued = issue_queue_head(db,prepared)
        assert issued['issued'], issued
        raw.commit()
        replay = prepare_queue_head(envelope)
        assert replay.mode == 'replay' and replay.proposal.to_wire() == issued['position_presentation']
        plan = replay.proposal
        assert tuple(slot.original_ply for slot in plan.decision_slots) == (0,2,4)
        shared = plan.decision_slots[0].definition.key
        assert plan.decision_slots[2].definition.key == shared
        assert len([w for w in plan.due_positions if w.definition.key == shared]) == 1
        root_before = tuple(raw.execute('''SELECT version,fsrs_json FROM study_position_states
            WHERE repertoire_id=%s AND variant=%s AND position=%s''',
            (shared.repertoire_id,shared.variant,shared.position)).fetchone())
        assert root_before[0] == 1
        scope = raw.execute('''SELECT s.*,q.effective_trained_color FROM opening_evidence_presentations s
            JOIN opening_evidence_queue_contexts q ON q.presentation_snapshot_id=s.id
            WHERE q.queue_entry_id=%s AND q.repertoire_id='repeat' ''',(entry,)).fetchone()
        assert scope is not None
        manifest = evidence.issued_position_manifest(plan,decision_manifest(evidence._manifest_snapshot(dict(scope)),'repeat'))
        assert len(manifest['decisions']) == 3
        stamp = datetime.now(timezone.utc).isoformat()
        if wrong:
            events = []
            for index, decision in enumerate(manifest['decisions']):
                expected = decision['expected_uci']
                board_at_decision = chess.Board(decision['fen'])
                response = (next(m.uci() for m in board_at_decision.legal_moves if m.uci() != expected)
                            if index in (0,2) else expected)
                events.append(dict(sequence=len(events)+1,decision_index=index,decision_id=decision['decision_id'],
                    expected_uci=expected,kind='first_response',observed_at=stamp,response_uci=response,
                    disposition='wrong' if index in (0,2) else 'expected'))
                if index == 0:
                    events.append(dict(sequence=len(events)+1,decision_index=index,decision_id=decision['decision_id'],
                        expected_uci=expected,kind='correction',observed_at=stamp,response_uci=expected))
            partial = dict(attempt_id=plan.attempt_id,manifest=manifest,origin_queue_entry_id=entry,
                queue_entry_id=entry,started_at=stamp,study_timezone='UTC',source='live',events=events)
            raw.commit()
            prepared_checkpoint = evidence.prepare_standalone_checkpoint({'checkpoint':partial})
            raw.execute('BEGIN')
            assert evidence.commit_standalone_checkpoint(db,prepared_checkpoint)['persisted']
            raw.commit()
            rows = [dict(r) for r in raw.execute('''SELECT event_id,decision_index,grade,version_before,
                version_after,fsrs_before_json,fsrs_after_json FROM study_position_grades
                WHERE attempt_id=%s ORDER BY decision_index''',(plan.attempt_id,)).fetchall()]
            assert len(rows) == 2 and [r['grade'] for r in rows] == ['again','again']
            assert [r['decision_index'] for r in rows] == [0,2] and rows[0]['event_id'] != rows[1]['event_id']
            assert [(r['version_before'],r['version_after']) for r in rows] == [(1,2),(2,3)]
            assert rows[0]['fsrs_before_json'] == root_before[1]
            assert rows[1]['fsrs_before_json'] == rows[0]['fsrs_after_json']
            current = tuple(raw.execute('''SELECT version,fsrs_json FROM study_position_states
                WHERE repertoire_id=%s AND variant=%s AND position=%s''',
                (shared.repertoire_id,shared.variant,shared.position)).fetchone())
            assert current == (3,rows[1]['fsrs_after_json'])
            assert raw.execute('SELECT count(*) FROM reviews').fetchone()[0] == 0
            assert {r['id']:r['fsrs_card_json'] for r in raw.execute('SELECT id,fsrs_card_json FROM cards').fetchall()} == physical
            raw.commit()
            prepared_checkpoint = evidence.prepare_standalone_checkpoint({'checkpoint':partial})
            raw.execute('BEGIN')
            assert evidence.commit_standalone_checkpoint(db,prepared_checkpoint)['persisted']
            raw.commit()
            assert raw.execute('SELECT count(*) FROM study_position_grades WHERE attempt_id=%s',(plan.attempt_id,)).fetchone()[0] == 2
            assert tuple(raw.execute('''SELECT version,fsrs_json FROM study_position_states
                WHERE repertoire_id=%s AND variant=%s AND position=%s''',
                (shared.repertoire_id,shared.variant,shared.position)).fetchone()) == current
            raw.commit()
            return
        events = [dict(sequence=i+1,decision_index=i,decision_id=d['decision_id'],expected_uci=d['expected_uci'],
            kind='first_response',observed_at=stamp,response_uci=d['expected_uci'],disposition='expected')
            for i,d in enumerate(manifest['decisions'])]
        completion = dict(attempt_id=plan.attempt_id,manifest=manifest,origin_queue_entry_id=entry,
            queue_entry_id=entry,started_at=stamp,study_timezone='UTC',source='live',events=events,
            terminal={'state':'complete','final_sequence':len(events),'ended_at':stamp})
        raw.commit()
        request = OpeningEvidenceCheckpoint.model_validate(completion)
        prepared_manifest = evidence.prepare_checkpoint(request)
        with reader() as read_db:
            position_checkpoint = evidence.prepare_position_checkpoint(read_db,request)
        payload = {'card_id':card,'review':{'outcome':'correct','guided':False,'queue_entry_id':entry,
            'expected_revision':1,'attempt_id':plan.attempt_id,'opening_evidence_completion':completion}}
        payload = prepare_recombination_review(payload,read_connection=reader)
        payload.update(prepared_manifest=prepared_manifest,prepared_position_checkpoint=position_checkpoint)
        raw.execute('BEGIN')
        saved = learning_review_adapter.submit_legacy_review(db,payload)
        raw.commit()
        assert saved['persisted'] and saved['queue_entry_id'] == entry
        rows = [dict(r) for r in raw.execute('''SELECT event_id,decision_index,position,grade,
            version_before,version_after,fsrs_before_json,fsrs_after_json FROM study_position_grades
            WHERE attempt_id=%s ORDER BY decision_index''',(plan.attempt_id,)).fetchall()]
        assert len(rows) == 3 and [r['grade'] for r in rows] == ['good','good','observe']
        assert len({r['event_id'] for r in rows}) == 3
        assert rows[0]['position'] == rows[2]['position'] == shared.position
        root_after = tuple(raw.execute('''SELECT version,fsrs_json FROM study_position_states
            WHERE repertoire_id=%s AND variant=%s AND position=%s''',
            (shared.repertoire_id,shared.variant,shared.position)).fetchone())
        assert root_after[0] == root_before[0] + 1 and root_after[1] != root_before[1]
        assert (rows[0]['version_before'],rows[0]['version_after']) == (1,2)
        assert rows[0]['fsrs_before_json'] == root_before[1] and rows[0]['fsrs_after_json'] == root_after[1]
        assert (rows[2]['version_before'],rows[2]['version_after']) == (2,2)
        assert rows[2]['fsrs_before_json'] == rows[2]['fsrs_after_json'] == root_after[1]
        assert {r['id']:r['fsrs_card_json'] for r in raw.execute('SELECT id,fsrs_card_json FROM cards').fetchall()} == physical
        receipt = raw.execute('''SELECT r.card_id,r.queue_entry_id,r.review_id,v.source_kind,v.source_ref
            FROM review_attempt_receipts r JOIN reviews v ON v.id=r.review_id WHERE r.attempt_id=%s''',(plan.attempt_id,)).fetchone()
        assert tuple(receipt) == (card,entry,saved['review_id'],'position_study','attempt:'+plan.attempt_id)
        raw.commit()
        with reader() as read_db:
            payload['prepared_position_checkpoint'] = evidence.prepare_position_checkpoint(read_db,request)
        raw.execute('BEGIN')
        result = learning_review_adapter.submit_legacy_review(db,payload)
        raw.commit()
        assert result['review_id'] == saved['review_id']
        assert raw.execute('SELECT count(*) FROM study_position_grades WHERE attempt_id=%s',(plan.attempt_id,)).fetchone()[0] == 3
        assert raw.execute('SELECT count(*) FROM reviews WHERE card_id=%s',(card,)).fetchone()[0] == 1
        assert tuple(raw.execute('''SELECT version,fsrs_json FROM study_position_states
            WHERE repertoire_id=%s AND variant=%s AND position=%s''',
            (shared.repertoire_id,shared.variant,shared.position)).fetchone()) == root_after
        raw.commit()
