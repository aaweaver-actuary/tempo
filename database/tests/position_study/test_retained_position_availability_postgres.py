"""Read-only retained-head omission at actual PostgreSQL query/issuance boundary.

Each named test needs a separately EMPTY owned N2 database. All fixture changes
below construct synthetic initial states only; production omission never writes.
"""
from datetime import datetime, timedelta, timezone
import json

import chess
from fsrs import Card
import psycopg
from psycopg.conninfo import make_conninfo

from database.tests.position_study.acceptance import _dsn
from database.tests.position_study.italian_acceptance import LINES, _moves, seed_disposable_source
from tempo_legacy_database.adapters.postgres_store import PostgresConnection, tempo_row_factory
from tempo_legacy_database.adapters.http_composition import queue_projection, retained_position_availability


def _fixture(raw, dsn):
    from app.services.position_queue_admission import prepare_due, admit_due
    db, source, _, day = seed_disposable_source(raw, dsn,
        (_moves(LINES['knight']), _moves(LINES['giuoco'])), ('knight', 'giuoco'))
    definition = source.routes[0].decisions[-1].definition
    # One interior learner move at the final Knight Attack decision. This row
    # emulates a retained physical review whose initial logical donor was held.
    board = chess.Board()
    for move in _moves(LINES['knight'])[:5]: board.push_uci(move)
    carrier_start = board.fen()
    opponent = _moves(LINES['knight'])[5]
    board.push_uci(opponent)
    assert definition.key.position == ' '.join(board.fen(en_passant='legal').split()[:4])
    carrier_moves = json.dumps([opponent, definition.expected_uci])
    now = datetime.now(timezone.utc)
    raw.execute("""INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,
        fsrs_card_json,introduced_at,revision,content_type,trained_color,canonical_route_source)
        VALUES('retained','italian','prefix',%s,%s,'learning',%s,%s,%s,1,'opening','white',0)""",
        (carrier_start, carrier_moves, day,
         Card(card_id=999,due=now-timedelta(days=1)).to_json(),(now-timedelta(days=3)).isoformat()))
    raw.execute("INSERT INTO repertoire_cards(repertoire_id,card_id,canonical_route_source) VALUES('italian','retained',0)")
    raw.execute("""INSERT INTO opening_graph_steps(repertoire_id,generation,line_id,decision_index,
        card_id,decision_fen_key,starting_fen,moves_json,trained_color)
        VALUES('italian',3,'route-0',99,'retained',%s,%s,%s,'white')""",
        (definition.key.position,carrier_start,carrier_moves))
    raw.execute("DELETE FROM study_position_states WHERE repertoire_id='italian' AND position=%s", (definition.key.position,))
    raw.execute("""UPDATE study_position_seed_outcomes SET disposition='hold',
        hold_reason='no_valid_current_introduced_donor',donor_card_id=NULL,donor_card_revision=NULL,
        donor_fingerprint=NULL,donor_selection_policy=NULL,donor_fsrs_internal_card_id=NULL,
        allocated_fsrs_internal_card_id=NULL,clone_fingerprint=NULL,introduced_at=NULL
        WHERE repertoire_id='italian' AND position=%s""", (definition.key.position,))
    raw.execute("UPDATE study_position_seed_runs SET seeded_count=seeded_count-1,held_count=held_count+1 WHERE repertoire_id='italian'")
    entry = raw.execute("""INSERT INTO daily_queue(queue_date,card_id,cycle,position,status,
        admission_kind,admission_repertoire_id) VALUES(%s,'retained',0,0,'queued','review','italian') RETURNING id""", (day,)).fetchone()[0]
    raw.commit()
    due = prepare_due(db, day, {})
    assert isinstance(due, dict) and 'candidate' in due, due
    assert admit_due(db, day, due) is None
    raw.commit()
    successor = raw.execute("SELECT id,card_id FROM daily_queue WHERE card_id<>'retained' ORDER BY position,id LIMIT 1").fetchone()
    assert successor
    return db, day, entry, dict(successor), definition


def _visible(db, day):
    from app.queue_commands import queue_storage
    head = queue_storage.prepare_queue_head(db, 'real_game_miss')(day)
    rows = queue_projection.queue_rows(db, day, 3, miss_reason='real_game_miss')
    assert head is not None and rows
    assert head['id'] == rows[0]['queue_entry_id']
    return head['id']


def _retained(raw, entry):
    tables = {
        'queue': ('SELECT * FROM daily_queue WHERE id=%s', (entry,)),
        'origin': ('SELECT * FROM queue_attempt_origins WHERE queue_entry_id=%s ORDER BY revision', (entry,)),
        'card': ("SELECT * FROM cards WHERE id='retained'", ()),
        'seed': ("SELECT * FROM study_position_seed_outcomes WHERE repertoire_id='italian' ORDER BY position", ()),
        'states': ('SELECT * FROM study_position_states ORDER BY repertoire_id,variant,position', ()),
        'reviews': ('SELECT * FROM reviews ORDER BY id', ()),
    }
    return {name: json.dumps([dict(row) for row in raw.execute(sql, params).fetchall()], sort_keys=True, default=str)
            for name, (sql, params) in tables.items()}


def test_retained_unissuable_physical_head_yields_to_fenced_successor_without_writes(monkeypatch):
    from app import queue_commands
    from app.services.queue_next_card import prepare_queue_head
    from tempo_legacy_database.adapters import postgres_store
    from app.product_composition.database_infrastructure import bind_database_infrastructure
    bind_database_infrastructure()
    dsn = _dsn()
    monkeypatch.setenv('TEMPO_DATABASE_READ_URL', make_conninfo(dsn,user='tempo_reader'))
    monkeypatch.setenv('TEMPO_DATABASE_WRITE_URL', make_conninfo(dsn,user='tempo_writer'))
    postgres_store.close_pools()
    try:
        with psycopg.connect(dsn,row_factory=tempo_row_factory) as raw:
            db, day, entry, successor, definition = _fixture(raw, dsn)
            before = _retained(raw, entry)
            assert _visible(db, day) == successor['id']
            diagnostics = queue_projection.queue_diagnostics(db, day)
            assert any(row['card_id']=='retained' and row['message']==retained_position_availability.RETAINED_HOLD for row in diagnostics)
            assert _retained(raw, entry) == before
            raw.commit()
            envelope = {'queue_date':day,'entry_id':successor['id'], 'card_id':successor['card_id'],'expected_revision':1}
            prepared = prepare_queue_head(envelope)
            assert prepared.mode == 'position', prepared
            assert _visible(db, day) == successor['id']
            result = queue_commands.issue_queue_head(db, prepared)
            assert result['issued'] and result['position_presentation'], result
            raw.commit()
            assert _retained(raw, entry) == before
            assert raw.execute('SELECT count(*) FROM study_position_issued_plans').fetchone()[0] == 1
            assert raw.execute('SELECT count(*) FROM study_position_grades').fetchone()[0] == 0
            assert not raw.execute('SELECT 1 FROM study_position_states WHERE repertoire_id=%s AND position=%s',
                ('italian',definition.key.position)).fetchone()
    finally:
        postgres_store.close_pools()


def test_retained_head_omission_preserves_uncertain_source_state_and_protected_work(monkeypatch):
    from tempo_legacy_database.adapters import postgres_store
    from app.product_composition.database_infrastructure import bind_database_infrastructure
    bind_database_infrastructure()
    dsn = _dsn()
    monkeypatch.setenv('TEMPO_DATABASE_READ_URL', make_conninfo(dsn,user='tempo_reader'))
    monkeypatch.setenv('TEMPO_DATABASE_WRITE_URL', make_conninfo(dsn,user='tempo_writer'))
    postgres_store.close_pools()
    try:
        with psycopg.connect(dsn,row_factory=tempo_row_factory) as raw:
            db, day, entry, successor, definition = _fixture(raw, dsn)
            assert _visible(db, day) == successor['id']
            cases = [
                ('explicit', "UPDATE daily_queue SET admission_kind='explicit' WHERE id=%s", (entry,)),
                ('retry', 'UPDATE daily_queue SET cycle=1 WHERE id=%s', (entry,)),
                ('failed', 'UPDATE daily_queue SET attempt_failed=1 WHERE id=%s', (entry,)),
                ('guided', "UPDATE daily_queue SET attempt_state='guided' WHERE id=%s", (entry,)),
                ('issued', 'UPDATE queue_attempt_origins SET issued_as_head=1 WHERE queue_entry_id=%s', (entry,)),
                ('owner paused', "UPDATE study_position_owners SET mode='paused' WHERE repertoire_id='italian'", ()),
                ('source header changed', "UPDATE repertoires SET scope_source_revision=scope_source_revision+1 WHERE id='italian'", ()),
                ('integrity changed', "UPDATE repertoire_integrity_state SET scan_error='fixture unavailable' WHERE repertoire_id='italian'", ()),
                ('incomplete manifest', "UPDATE study_position_seed_runs SET status='incomplete',completed_at=NULL WHERE repertoire_id='italian'", ()),
                ('unknown hold', "UPDATE study_position_seed_outcomes SET hold_reason='fixture_unknown' WHERE repertoire_id='italian' AND position=%s", (definition.key.position,)),
                ('multiple aliases', """INSERT INTO opening_graph_steps(repertoire_id,generation,line_id,decision_index,card_id,decision_fen_key,starting_fen,moves_json,trained_color)
                   SELECT repertoire_id,generation,line_id,100,card_id,decision_fen_key,starting_fen,moves_json,trained_color FROM opening_graph_steps WHERE card_id='retained'""", ()),
                ('accepted Games obligation', """INSERT INTO study_game_feedback(effect_key,identity_json,source_revision,card_id,decision_id,received_at,status,updated_at)
                   VALUES('fixture-owed','{}','fixture','retained','fixture',NOW(),'held',NOW())""", ()),
                ('remapped Games obligation', """INSERT INTO study_game_feedback(effect_key,identity_json,source_revision,card_id,decision_id,received_at,status,updated_at,payload_json,payload_hash)
                   VALUES('fixture-remapped','{}','fixture','different-original','fixture',NOW(),'held',NOW(),
                     '{"owner":{"progress":{"target":{"card_id":"retained"}}}}','fixture')""", ()),
                ('guided card', "UPDATE cards SET guided_review=1 WHERE id='retained'", ()),
                ('evidence receipt', """INSERT INTO review_attempt_receipts(attempt_id,card_id,queue_entry_id,outcome,guided,scheduling_status,result_json)
                   VALUES('fixture-receipt','retained',%s,'correct',0,'pending','{}')""", (entry,)),
            ]
            for label, sql, params in cases:
                raw.execute('SAVEPOINT negative_case')
                raw.execute(sql, params)
                assert _visible(db, day) == entry, label
                assert not retained_position_availability.retained_holds(db, day), label
                raw.execute('ROLLBACK TO SAVEPOINT negative_case')
                raw.execute('RELEASE SAVEPOINT negative_case')
            # A multi-learner physical carrier must never be judged solely from
            # its final graph key, even if its first expected move is unchanged.
            raw.execute('SAVEPOINT longer_carrier')
            longer = json.dumps(['g8f6','f3g5','d7d5','e4d5'])
            raw.execute("UPDATE cards SET moves_json=%s WHERE id='retained'", (longer,))
            raw.execute("UPDATE opening_graph_steps SET moves_json=%s WHERE card_id='retained'", (longer,))
            raw.execute('UPDATE queue_attempt_origins SET moves_json=%s WHERE queue_entry_id=%s', (longer,entry))
            assert _visible(db, day) == entry
            raw.execute('ROLLBACK TO SAVEPOINT longer_carrier')
            raw.execute('RELEASE SAVEPOINT longer_carrier')
            # A logical state in a different epoch is uncertainty, never proof
            # that no obligation exists. Its current writer still owns repair.
            raw.execute('SAVEPOINT old_state')
            raw.execute("INSERT INTO study_position_epochs VALUES('italian','old-epoch','old-digest',NOW())")
            raw.execute("""INSERT INTO study_position_states(repertoire_id,variant,position,expected_uci,policy_version,owner_epoch,
                source_digest,fsrs_internal_card_id,fsrs_json,due_at,introduced_at)
                SELECT repertoire_id,variant,%s,%s,policy_version,'old-epoch','old-digest',
                    nextval('study_position_fsrs_id_seq'),fsrs_json,due_at,introduced_at FROM study_position_states LIMIT 1""",
                (definition.key.position,definition.expected_uci))
            assert _visible(db, day) == entry
            raw.execute('ROLLBACK TO SAVEPOINT old_state')
            assert _visible(db, day) == successor['id']
    finally:
        postgres_store.close_pools()
