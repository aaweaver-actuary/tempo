"""Interactive queue read profile on the existing owned PostgreSQL fixture."""
import uuid

import chess
import pytest

from app.services.real_game_feedback import MISS_REASON
from tempo_legacy_database.adapters import frozen_order, postgres_store
from tempo_legacy_database.adapters.http_composition import queue_projection
from test_product_game_lapse_postgres import owned_postgres


@pytest.mark.parametrize('read_name', ['queue_rows', 'available_count'])
def test_interactive_queue_read_preserves_protection_and_scopes_jit_to_transaction(
        owned_postgres, monkeypatch, read_name):
    """A compiled plan must not spend interactive time on unchanged eligibility."""
    monkeypatch.setattr(postgres_store, 'configured', lambda: True)
    day = '2026-10-30'
    repertoire = 'queue-read-' + uuid.uuid4().hex
    names = ('new', 'held-legacy', 'issued-legacy', 'explicit-legacy',
             'stale-unstarted', 'stale-issued', 'reviewed-legacy', 'guided-legacy')
    cards = [repertoire + '-' + name for name in names]
    with owned_postgres() as database:
        original_jit = database.execute_native("SHOW jit").fetchone()[0]
        database.execute_native("INSERT INTO repertoires(id,name,source_name,created_at) VALUES(%s,'Queue proof','synthetic',%s)",
                                (repertoire, day))
        for position, card_id in enumerate(cards):
            database.execute_native("""INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,
                state,due_date,content_type,trained_color) VALUES(%s,%s,'prefix',%s,'["e2e4"]',
                'learning',%s,'opening','white')""", (card_id, repertoire, chess.STARTING_FEN, day))
            database.execute_native('INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES(%s,%s)',
                                    (repertoire, card_id))
            database.execute_native("""INSERT INTO daily_queue(queue_date,card_id,position,admission_kind)
                VALUES(%s,%s,%s,%s)""", (day, card_id, position,
                    'explicit' if names[position] == 'explicit-legacy' else 'new'))
        ranks = [dict(row, rank=position, fingerprint='synthetic', memberships=[repertoire])
                 for position, row in enumerate(database.execute_native(
                     'SELECT * FROM cards WHERE repertoire_id=%s ORDER BY id', (repertoire,)), 1)]
        plan = dict(run_id=repertoire, checksum=repertoire, ranks=ranks)
        frozen_order.stage_run(database, plan, day, reader_version='queue-read-proof')
        frozen_order.stage_ranks(database, plan, ranks)
        frozen_order.activate_run(database, plan)
        database.execute_native('UPDATE queue_attempt_origins SET legacy=1 WHERE card_id=ANY(%s)',
                                ([cards[index] for index in (1, 2, 3, 6, 7)],))
        database.execute_native('UPDATE queue_attempt_origins SET issued_as_head=1 WHERE card_id=ANY(%s)',
                                ([cards[index] for index in (2, 5)],))
        database.execute_native('UPDATE cards SET revision=revision+1 WHERE id=ANY(%s)',
                                ([cards[index] for index in (4, 5)],))
        database.execute_native("""INSERT INTO reviews(card_id,rating,reviewed_at,
            previous_interval,next_interval,source_kind) VALUES(%s,'correct',%s,0,1,'study')""",
                                (cards[6], day + 'T00:00:00Z'))
        database.execute_native("UPDATE daily_queue SET attempt_state='guided' WHERE card_id=%s", (cards[7],))
    try:
        with owned_postgres() as database:
            # Force the compiler admission boundary independently of this tiny
            # fixture's planner estimates. The separate representative paired
            # proof records natural-threshold compilation and timing.
            database.execute_native("SELECT set_config('jit', 'on', true), set_config('jit_above_cost', '0', true)")
            assert database.execute_native('SHOW jit').fetchone()[0] == 'on'
            txid = database.execute_native('SELECT txid_current()').fetchone()[0]
            if read_name == 'queue_rows':
                result = queue_projection.queue_rows(database, day, 20, miss_reason=MISS_REASON)
                assert [row['id'] for row in result] == [cards[index] for index in (0, 2, 3, 5, 6, 7)]
            else:
                assert queue_projection.available_count(database, day, miss_reason=MISS_REASON) == 6
            assert database.execute_native('SHOW jit').fetchone()[0] == 'off'
            assert database.execute_native('SELECT txid_current()').fetchone()[0] == txid
        with owned_postgres() as database:
            assert database.execute_native('SHOW jit').fetchone()[0] == original_jit
            assert database.execute_native('SELECT count(*) FROM reviews WHERE card_id=ANY(%s)',
                                           (cards,)).fetchone()[0] == 1
    finally:
        with owned_postgres() as database:
            database.execute_native('UPDATE frozen_opening_activation SET run_id=NULL WHERE run_id=%s', (repertoire,))
            database.execute_native('DELETE FROM frozen_opening_ranks WHERE run_id=%s', (repertoire,))
            database.execute_native('DELETE FROM frozen_opening_runs WHERE run_id=%s', (repertoire,))
            database.execute_native('DELETE FROM cards WHERE id=ANY(%s)', (cards,))
            database.execute_native('DELETE FROM repertoires WHERE id=%s', (repertoire,))
