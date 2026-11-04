"""Synthetic cutover classification; native migration proof is separately required."""

import copy

import pytest

from database.tooling.verify_postgres_cli_state import (
    propose_three_journey_card_deletes,
    three_journey_expected_rows,
    verify_three_journey_rows,
)


def mixed_fixture():
    return {
        'cards': [
            {'id': 'opening', 'content_type': 'opening'},
            {'id': 'puzzle-rush-defense-theme', 'content_type': 'tactic'},
            {'id': 'defense', 'content_type': 'defense'},
            {'id': 'shared', 'content_type': 'defense'},
            {'id': 'unknown', 'content_type': 'future-type'},
        ],
        'daily_queue': [
            {'id': 1, 'card_id': 'opening', 'status': 'issued'},
            {'id': 2, 'card_id': 'defense', 'status': 'issued'},
            {'id': 3, 'card_id': 'shared', 'status': 'pending'},
        ],
        'queue_attempt_origins': [
            {'queue_entry_id': 1, 'card_id': 'opening', 'content_type': 'opening'},
            {'queue_entry_id': 2, 'card_id': 'defense', 'content_type': 'defense'},
            {'queue_entry_id': 3, 'card_id': 'shared', 'content_type': 'tactic'},
        ],
        'tactic_captures': [{'id': 'capture', 'card_id': 'puzzle-rush-defense-theme',
                             'source_kind': 'puzzle_rush', 'request_json': '{"theme":"defense"}'}],
        'tactic_discovery_attempts': [{'id': 'pack-receipt', 'result_json': '{"clean":true}'}],
        'reviews': [{'id': 1, 'card_id': 'opening', 'rating': 'again'}],
        'operation_receipts': [
            {'operation_id': 'phone', 'command_name': 'review.submit', 'state': 'pending'},
            {'operation_id': 'retired', 'command_name': 'defense.attempt.submit', 'state': 'pending'},
            {'operation_id': 'mixed', 'command_name': 'unknown', 'response_json': '{"card_id":"defense"}'},
        ],
        'study_game_feedback': [{'effect_id': 'effect', 'state': 'held', 'card_id': 'opening'}],
        'imported_games': [{'id': 'game', 'pgn': '[FEN "synthetic"]'}],
    }


def test_three_journey_mixed_ownership_preserves_uncertainty():
    data = mixed_fixture()
    result = propose_three_journey_card_deletes(
        data['cards'], {'defense', 'shared'}, {'shared', 'puzzle-rush-defense-theme'},
        data['queue_attempt_origins'])
    assert result['proposed_card_ids'] == ['defense']
    assert 'shared' in result['ambiguous']
    assert 'unknown' not in result['proposed_card_ids']
    assert 'puzzle-rush-defense-theme' not in result['proposed_card_ids']


def test_three_journey_exact_witness_rejects_survivor_tamper_and_resurrection():
    source = mixed_fixture()
    keys = {name: ('id',) for name in source}
    keys.update(queue_attempt_origins=('queue_entry_id',),
                operation_receipts=('operation_id',), study_game_feedback=('effect_id',))
    deletes = {'cards': [('defense',)], 'daily_queue': [(2,)],
               'queue_attempt_origins': [(2,)]}
    witness = three_journey_expected_rows(source, keys, deletes)
    target = copy.deepcopy(source)
    for table, proposed in deletes.items():
        target[table] = [row for row in target[table]
                         if tuple(row[column] for column in keys[table]) not in proposed]
    verify_three_journey_rows(witness, target)
    target['study_game_feedback'][0]['state'] = 'applied'
    with pytest.raises(ValueError, match='study_game_feedback'):
        verify_three_journey_rows(witness, target)
    target = copy.deepcopy(source)
    with pytest.raises(ValueError):
        verify_three_journey_rows(witness, target)


def test_three_journey_unknown_delete_key_is_rejected():
    with pytest.raises(ValueError, match='unknown proposed key'):
        three_journey_expected_rows({'cards': [{'id': 'kept'}]}, {'cards': ('id',)},
                                   {'cards': [('missing',)]})


def test_digest_witness_hides_rows_and_detects_tamper():
    import json
    from database.tooling.verify_postgres_cli_state import (
        three_journey_digest_witness, verify_three_journey_digest,
    )
    source = {'cards': [{'id': 'private-opening', 'state': 'learning'},
                        {'id': 'retired-defense', 'state': 'new'}]}
    keys = {'cards': ('id',)}
    secret = b'x' * 32
    witness = three_journey_digest_witness(source, keys, {'cards': [('retired-defense',)]}, secret)
    assert 'private-opening' not in json.dumps(witness)
    assert 'learning' not in json.dumps(witness)
    verify_three_journey_digest(witness, {'cards': source['cards'][:1]}, keys, secret)
    with pytest.raises(ValueError):
        verify_three_journey_digest(witness, {'cards': [{'id': 'private-opening', 'state': 'new'}]}, keys, secret)
    with pytest.raises(ValueError):
        verify_three_journey_digest(witness, source, keys, secret)


def test_schema61_native_mixed_witness():
    """Only runs against the explicitly named disposable local W07 database."""
    import os
    from urllib.parse import urlparse
    import psycopg
    from psycopg.rows import dict_row
    from database.tooling.verify_postgres_cli_state import three_journey_digest_witness, verify_three_journey_digest

    dsn = os.environ.get('TEMPO_W07_DISPOSABLE_DSN')
    if not dsn:
        pytest.skip('no W07 disposable PostgreSQL allocated')
    parsed = urlparse(dsn)
    if parsed.hostname != '127.0.0.1' or parsed.path != '/w07_fixture':
        raise ValueError('refusing non-W07 database')
    secret = os.urandom(32)
    with psycopg.connect(dsn, row_factory=dict_row) as db:
        assert db.execute('SELECT max(version) AS v FROM tempo_schema_migrations').fetchone()['v'] == 61
        try:
            db.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES ('w07-r','synthetic','synthetic','2026-01-01')")
            for card, kind in [('w07-opening', 'opening'), ('w07-defense', 'defense'),
                               ('w07-tactic', 'tactic'), ('w07-shared', 'defense')]:
                db.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,due_date,content_type) VALUES (%s,'w07-r','prefix','synthetic','[]','2026-01-01',%s)", (card, kind))
            db.execute("INSERT INTO tactic_captures(id,card_id,source_kind,captured_at,request_json,result_json) VALUES ('w07-capture','w07-tactic','puzzle_rush','2026-01-01','{\"theme\":\"defense\"}','{}'), ('w07-cross','w07-shared','game','2026-01-01','{}','{}')")
            db.execute("INSERT INTO tactic_discovery_attempts(id,deck_id,puzzle_id,clean,result_json) VALUES ('w07-pack','w07-deck','w07-puzzle',1,'{}')")
            db.execute("INSERT INTO reviews(id,card_id,rating,reviewed_at,previous_interval,next_interval) VALUES (700001,'w07-opening','again','2026-01-01',1,0)")
            db.execute("INSERT INTO daily_queue(id,queue_date,card_id,position,status) VALUES (700001,'2026-01-01','w07-opening',1,'issued'),(700002,'2026-01-01','w07-defense',2,'issued')")
            db.execute("INSERT INTO tactic_progress(puzzle_id,deck_id,card_id,admitted_at) VALUES ('w07-puzzle','w07-deck','w07-tactic','2026-01-01')")
            db.execute("INSERT INTO queue_attempt_abandonments(queue_entry_id,card_id,revision,operation_id,request_sha256,origin_sha256,actor,reason,abandoned_at) VALUES (700001,'w07-opening',1,'w07-abandon-open',repeat('a',64),repeat('b',64),'synthetic','synthetic',now()), (700002,'w07-defense',1,'w07-abandon-defense',repeat('a',64),repeat('b',64),'synthetic','synthetic',now())")
            db.execute("INSERT INTO imported_games(id,provider,username,played_at,speed,rated,color,result,start_fen,moves_json) VALUES ('w07-game','synthetic','synthetic','2026-01-01','rapid',0,'white','draw','custom synthetic fen','[]')")
            db.execute("INSERT INTO operation_receipts(operation_id,command_name,request_hash,state,response_json) VALUES ('w07-phone','review.submit','synthetic','pending',NULL),('w07-retired','defense.attempt.submit','synthetic','pending',NULL),('w07-legacy','game_evidence.receive_legacy','synthetic','pending',NULL)")
            db.execute("INSERT INTO study_game_feedback(effect_key,identity_json,source_revision,decision_id,received_at,status,updated_at,card_id) VALUES ('w07-effect','{}','1','decision',now(),'held',now(),'w07-opening')")
            db.execute("INSERT INTO product_feedback_sources(source_key,version,payload) VALUES ('w07-source',1,'{\"game\":\"w07-game\"}')")
            db.execute("INSERT INTO product_feedback_deliveries(operation_id,source_key,effect_key,command_kind,state,next_attempt_at,payload) VALUES ('w07-delivery','w07-source','w07-effect','again','pending',0,'{\"card_id\":\"w07-opening\"}')")
            tables = {'cards': ('id',), 'daily_queue': ('id',),
                      'queue_attempt_origins': ('queue_entry_id','card_id','revision'),
                      'reviews': ('id',), 'tactic_captures': ('id',),
                      'tactic_progress': ('puzzle_id',), 'imported_games': ('id',),
                      'queue_attempt_abandonments': ('queue_entry_id','card_id','revision'),
                      'tactic_discovery_attempts': ('id',), 'operation_receipts': ('operation_id',),
                      'study_game_feedback': ('effect_key',), 'product_feedback_sources': ('source_key',),
                      'product_feedback_deliveries': ('operation_id',)}
            from psycopg import sql
            rows = {table: db.execute(sql.SQL('SELECT * FROM {}').format(sql.Identifier(table))).fetchall() for table in tables}
            proposal = propose_three_journey_card_deletes(rows['cards'], {'w07-defense','w07-shared'},
                {row['card_id'] for row in rows['tactic_captures']}, rows['queue_attempt_origins'])
            assert proposal['proposed_card_ids'] == ['w07-defense']
            assert 'w07-shared' in proposal['ambiguous']
            deletes = {'cards': [('w07-defense',)], 'daily_queue': [(700002,)],
                       'queue_attempt_origins': [(700002,'w07-defense',1)],
                       'queue_attempt_abandonments': [(700002,'w07-defense',1)]}
            witness = three_journey_digest_witness(rows, tables, deletes, secret)
            retained = {table: [row for row in values if tuple(row[k] for k in tables[table]) not in deletes.get(table, [])] for table, values in rows.items()}
            verify_three_journey_digest(witness, retained, tables, secret)
            retained['product_feedback_deliveries'][0]['payload']['card_id'] = 'tampered'
            with pytest.raises(ValueError, match='product_feedback_deliveries'):
                verify_three_journey_digest(witness, retained, tables, secret)
        finally:
            db.rollback()
