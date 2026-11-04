"""F01/F04/F08: disposable native effect prototype; no serving adoption proof."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path

import chess
import psycopg
import pytest
from tempo_learning_database.adapters.position_effect_store import (
    PositionHold, StoredPositionReceipt, digest_bytes, owner_day,
)
from tempo_learning_runtime.position_effect_owner import accept_position_effect
from tempo_scheduling import schedule_position_review
from tempo_learning_database.ddl.migrate import expected_migrations

ROOT = Path(__file__).resolve().parents[4]
AT = datetime(2026,11,4,15,tzinfo=timezone.utc)
POSITION = chess.STARTING_BOARD_FEN + ' w KQkq -'
FSRS = json.dumps(dict(card_id=4503599627370496,state=2,step=None,stability=3.5,
    difficulty=5.0,due=(AT-timedelta(days=1)).isoformat(),last_review=(AT-timedelta(days=4)).isoformat()))


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'))


@pytest.fixture
def store(case):
    with psycopg.connect(case.admin) as db:
        db.execute('SET search_path TO learning,public')
        migration_id = 'learning.position_effect_owner/0001'
        expected = expected_migrations()
        actual = dict(db.execute('SELECT id,checksum FROM learning.migration_history').fetchall())
        assert actual == expected
        ddl = (ROOT/'database/tempo_learning_database/ddl/migrations/0007_position_effect_owner.sql').read_text()
        if migration_id in expected:
            # Integrated proof uses the real registered chain and exact receipt.
            assert actual[migration_id] == digest_bytes(ddl)
            assert db.execute("SELECT to_regclass('learning.position_effect_authorities')").fetchone()[0]
        else:
            # Leaf prototype cannot edit the coordinator/T02-owned manifest.
            assert db.execute("SELECT to_regclass('learning.position_effect_authorities')").fetchone()[0] is None
            db.execute(ddl)
        db.execute("INSERT INTO position_effect_authorities VALUES ('r','e1','source','America/New_York','disposable-prototype')")
        db.execute("INSERT INTO position_effect_states VALUES ('r','standard',%s,'e2e4','p1','e1','source',3,4503599627370496,%s,%s)",
                   (POSITION,FSRS,AT-timedelta(days=1)))
    return case.admin


def connect(store):
    db = psycopg.connect(store)
    db.execute('SET search_path TO learning,public')
    db.execute("SET LOCAL lock_timeout='5s'")
    return db


def issue(db, name='a', *, actual='e2e4', complete=True, epoch='e1', issued_day=date(2026,11,4), version=3):
    plan = dict(attempt_id=name,queue_entry_id=9,card_id='original-card',card_revision=2,
        repertoire_id='r',variant='standard',position=POSITION,expected_uci='e2e4',policy_version='p1',
        owner_epoch=epoch,source_digest='source',due=True,issued_version=version,
        issued_day=issued_day.isoformat(),issued_at=(AT-timedelta(minutes=3)).isoformat())
    raw = canonical(plan)
    db.execute('''INSERT INTO position_effect_attempts
        (attempt_id,queue_entry_id,card_id,card_revision,repertoire_id,variant,position,expected_uci,policy_version,
         owner_epoch,source_digest,plan_digest,plan_json,issued_day,issued_at,due,issued_version,completion_receipt_id,completion_saved_at)
        VALUES (%s,9,'original-card',2,'r','standard',%s,'e2e4','p1',%s,'source',%s,%s,%s,%s,true,%s,%s,%s)''',
        (name,POSITION,epoch,digest_bytes(raw),raw,issued_day,AT-timedelta(minutes=3),version,
         'original-review:'+name if complete else None,AT-timedelta(seconds=1) if complete else None))
    payload = canonical(dict(event_id='event:'+name,attempt_id=name,decision_index=0,actual_uci=actual,
        assistance_before=False,manual_failure=False,observed_at=(AT-timedelta(minutes=1)).isoformat(),
        disposition='expected' if actual == 'e2e4' else 'wrong'))
    db.execute('INSERT INTO position_effect_responses VALUES (%s,%s,0,%s,%s,%s)',
        ('event:'+name,name,payload,digest_bytes(payload),AT-timedelta(seconds=30)))
    return dict(attempt_id=name,event_id='event:'+name,operation_id='op:'+name,
        request_digest=digest_bytes(digest_bytes(raw)+':'+digest_bytes(payload)),accepted_at=AT,
        expected_owner_epoch=epoch,expected_source_digest='source')


def counts(db):
    return tuple(db.execute('SELECT (SELECT count(*) FROM position_effect_receipts),version,fsrs_json,(SELECT sum(checkpoint) FROM position_effect_attempts) FROM position_effect_states').fetchone())


def test_original_receipt_replay_next_review_and_rollback(store):
    with connect(store) as db:
        request = issue(db)
    expected = schedule_position_review('good',fsrs_card_json=FSRS,accepted_at=AT,review_day=date(2026,11,4))
    with connect(store) as db:
        before = counts(db)
        with pytest.raises(RuntimeError,match='crash'):
            with db.transaction():
                receipt = accept_position_effect(db,**request)
                assert isinstance(receipt,StoredPositionReceipt)
                raise RuntimeError('crash after schedule/receipt/checkpoint')
        assert counts(db) == before
        assert db.execute('SELECT count(*) FROM position_effect_responses').fetchone()[0] == 1
        receipt = accept_position_effect(db,**request)
        assert receipt.fsrs_before_json == FSRS
        assert receipt.fsrs_after_json == expected.fsrs_card_json
        assert (receipt.grade,receipt.version_after,receipt.checkpoint) == ('good',4,1)
        assert db.execute('SELECT due_at FROM position_effect_states').fetchone()[0] == expected.due_at
    with connect(store) as db:
        db.execute("UPDATE position_effect_authorities SET owner_epoch='e2',source_digest='new-source'")
        replay = accept_position_effect(db,**(request | dict(operation_id='new-transport',accepted_at=AT+timedelta(days=1))))
        assert replay == StoredPositionReceipt(**(receipt.__dict__ | dict(replayed=True)))
        assert replay.operation_id == 'op:a'
        assert counts(db)[0:2] == (1,4)
        assert accept_position_effect(db,**(request | dict(request_digest='changed'))).classification == 'immutable_conflict'
        assert accept_position_effect(db,**(request | dict(source_kind='game_lapse'))).reason == 'separate_game_lapse_authority_required'


def test_first_response_pending_recovers_same_attempt_and_day(store):
    with connect(store) as db:
        request = issue(db,complete=False)
        before = counts(db)
        hold = accept_position_effect(db,**request)
        assert hold == PositionHold('awaiting_saved_completion','pending_operation')
        assert counts(db) == before
    with connect(store) as db:
        db.execute('UPDATE position_effect_attempts SET completion_receipt_id=%s,completion_saved_at=%s WHERE attempt_id=%s',
                   ('original-aggregate',AT,'a'))
        receipt = accept_position_effect(db,**request)
        assert (receipt.attempt_id,receipt.event_id,receipt.grade) == ('a','event:a','good')


@pytest.mark.parametrize('mutation,reason',[
    ("UPDATE position_effect_authorities SET owner_epoch='stale'",'owner_or_source_changed'),
    ("UPDATE position_effect_authorities SET source_digest='stale'",'owner_or_source_changed'),
    ("UPDATE position_effect_states SET fsrs_internal_card_id=77",'position_fsrs_identity_mismatch'),
    ("UPDATE position_effect_responses SET payload_json='{}'",'first_response_digest_mismatch'),
    ("UPDATE position_effect_attempts SET plan_json='{}'",'issued_plan_digest_mismatch'),
])
def test_stale_and_corrupt_evidence_holds_without_schedule(store,mutation,reason):
    with connect(store) as db:
        request = issue(db)
        db.execute(mutation)
        before = counts(db)
        assert accept_position_effect(db,**request).reason == reason
        assert counts(db) == before


def test_racing_good_and_cross_epoch_daily_credit(store):
    with connect(store) as db:
        first, second = issue(db,'a'),issue(db,'b')
    def accept(request):
        with connect(store) as db:
            return accept_position_effect(db,**request)
    with ThreadPoolExecutor(max_workers=2) as pool:
        receipts = list(pool.map(accept,(first,second)))
    assert sorted(r.grade for r in receipts) == ['good','observe']
    with connect(store) as db:
        assert counts(db)[0:2] == (2,4)
        db.execute("UPDATE position_effect_authorities SET owner_epoch='e2'")
        db.execute("UPDATE position_effect_states SET owner_epoch='e2'")
        third = issue(db,'c',epoch='e2',version=4)
        assert accept_position_effect(db,**third).grade == 'observe'
        fourth = issue(db,'d',epoch='e2',version=4,actual='d2d4',complete=False)
        negative = accept_position_effect(db,**fourth)
        assert negative.grade == 'again'
        assert negative.version_after == 5
        assert db.execute("SELECT count(*) FROM position_effect_receipts WHERE grade='good'").fetchone()[0] == 1


def test_day_rollover_and_operation_collision(store):
    with connect(store) as db:
        request = issue(db,issued_day=date(2026,11,3))
        before = counts(db)
        assert accept_position_effect(db,**(request | dict(accepted_study_day=date(2026,11,3)))).reason == 'accepted_study_day_mismatch'
        assert counts(db) == before
        assert accept_position_effect(db,**request).grade == 'observe'
        other = issue(db,'b')
        assert accept_position_effect(db,**(other | dict(operation_id=request['operation_id']))).classification == 'immutable_conflict'
        with pytest.raises(psycopg.errors.UniqueViolation):
            with db.transaction():
                db.execute("INSERT INTO position_effect_responses SELECT 'other-event',attempt_id,decision_index,payload_json,payload_digest,accepted_at FROM position_effect_responses WHERE event_id=%s",(request['event_id'],))


@pytest.mark.parametrize('instant,day',[
    ('2026-11-04T04:59:59+00:00','2026-11-03'),('2026-11-04T05:00:00+00:00','2026-11-04'),
    ('2026-11-01T05:30:00+00:00','2026-11-01'),('2026-11-01T06:30:00+00:00','2026-11-01'),
])
def test_iana_study_day_midnight_and_dst(instant,day):
    assert owner_day(datetime.fromisoformat(instant),'America/New_York') == date.fromisoformat(day)
