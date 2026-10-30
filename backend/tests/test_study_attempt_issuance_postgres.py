"""#622 actual committed issuance/fence proof on the existing owned PG fixture."""
from datetime import timedelta

import pytest

from test_product_game_lapse_postgres import owned_postgres, case, NOW
from tempo_legacy_database.adapters.study.issuance import (
    attempt_issuance, first_target_issuance, issuance_frontier, stamp_issued_head,
)


def entry(database, card_id, day, *, position=0):
    return database.execute_native("""INSERT INTO daily_queue(queue_date,card_id,cycle,position)
        VALUES(%s,%s,(SELECT COALESCE(MAX(cycle),-1)+1 FROM daily_queue WHERE card_id=%s),%s)
        RETURNING id""", (day, card_id, card_id, position)).fetchone()[0]


def test_native_committed_issuance_ordinal_survives_rollback_reload_and_rollover(case):
    with case.transaction() as database:
        active = entry(database, case.card_id, NOW.date().isoformat())
        stamp_issued_head(database, active, case.card_id, 1, now=NOW-timedelta(seconds=1))
        old = attempt_issuance(database, active, case.card_id, 1)
    # Original actionable determination precedes acceptance/preparation. Count
    # every actual subsequent new attempt, including an ordinary earlier head.
    determined = NOW.isoformat()
    with case.transaction() as database:
        ordinary = entry(database, case.card_id, NOW.date().isoformat(), position=1)
        stamp_issued_head(database, ordinary, case.card_id, 1, now=NOW+timedelta(seconds=1))
        fence = issuance_frontier(database, lock=True)
    with pytest.raises(RuntimeError, match='rollback issuance'):
        with case.transaction() as database:
            rolled_back = entry(database, case.card_id, NOW.date().isoformat(), position=2)
            stamp_issued_head(database, rolled_back, case.card_id, 1, now=NOW+timedelta(seconds=2))
            raise RuntimeError('rollback issuance')
    with case.transaction() as database:
        target = entry(database, case.card_id, (NOW+timedelta(days=1)).date().isoformat(), position=0)
        stamp_issued_head(database, target, case.card_id, 1, now=NOW+timedelta(days=1))
        first, ordinal = first_target_issuance(database, case.card_id, 1, determined,
                                               after_issuance_sequence=fence)
        assert first.queue_entry_id == target and ordinal == 2
        assert first.issuance_sequence > fence+1  # Sequence gap is not a third card.
        assert first.issuance_sequence > old.issuance_sequence
    with case.transaction() as database:
        before = issuance_frontier(database)
        stamp_issued_head(database, target, case.card_id, 1, now=NOW+timedelta(days=2))
        assert attempt_issuance(database, target, case.card_id, 1) == first
        assert issuance_frontier(database) == before
        assert first_target_issuance(database, case.card_id, 1, determined,
                                     after_issuance_sequence=fence) == (first, 2)


def test_native_legacy_issuance_is_not_backfilled_by_reload(case):
    with case.transaction() as database:
        historical = entry(database, case.card_id, NOW.date().isoformat())
        database.execute_native('UPDATE queue_attempt_origins SET issued_as_head=1 WHERE queue_entry_id=%s',
                                (historical,))
        before = issuance_frontier(database)
        stamp_issued_head(database, historical, case.card_id, 1)
        assert attempt_issuance(database, historical, case.card_id, 1) is None
        assert issuance_frontier(database) == before


def test_native_prepared_priority_moves_unissued_repeat_but_preserves_issued_origin(case):
    from tempo_legacy_database.adapters.study.review.storage import apply_priority_positions, PriorityPositionConflict
    with case.transaction() as database:
        repeated = entry(database, case.card_id, NOW.date().isoformat(), position=20)
        database.execute_native("UPDATE daily_queue SET attempt_state='guided',attempt_failed=1 WHERE id=%s",
                                (repeated,))
        apply_priority_positions(database, NOW.date().isoformat(), ((repeated,20,21),))
        assert tuple(database.execute_native('SELECT position,attempt_failed,attempt_state FROM daily_queue WHERE id=%s',
                                        (repeated,)).fetchone()) == (21,1,'guided')
        stamp_issued_head(database, repeated, case.card_id, 1)
        with pytest.raises(PriorityPositionConflict):
            apply_priority_positions(database, NOW.date().isoformat(), ((repeated,21,22),))
