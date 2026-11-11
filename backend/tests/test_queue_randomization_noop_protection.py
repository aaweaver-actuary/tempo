"""Queue reorder preserves matched membership and does not rewrite unchanged origins.

Native cases use the task-owned schema-58 fixture and roll back every case.
They do not establish packaged or live service behavior.
"""

from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "database" / "tests"))
from queue_refresh_scale_fixture import protected_hash, seed_queue_refresh_case
from native_study_fixture import owned_postgres

from tempo_storage.queue_refresh.presentation import apply_plan


class NoSQL:
    def execute_native(self, *_args, **_kwargs):
        pytest.fail("invalid plan must be rejected before SQL")


@pytest.mark.parametrize(
    "ids,positions,buckets,kinds",
    [
        ([1, 1], [1, 2], ["opening", "opening"], ["due", "due"]),
        ([1, 2], [1], ["opening", "opening"], ["due", "due"]),
        ([1], [1], [], ["due"]),
        ([1], [1], ["opening"], []),
    ],
    ids=["duplicate_ids", "short_positions", "short_buckets", "short_kinds"],
)
def test_invalid_plan_duplicate_or_unequal_arrays_rejected_before_sql(
    ids, positions, buckets, kinds
):
    with pytest.raises(ValueError):
        apply_plan(NoSQL(), ids, positions, buckets, kinds, "2026-01-01")


@pytest.fixture
def case(owned_postgres, monkeypatch):
    monkeypatch.setenv("TEMPO_TEST_INSTANCE", "disposable")
    with owned_postgres() as db:
        facts = seed_queue_refresh_case(
            db.raw, queued_count=32, issued_count=2, changed_kind_count=3
        )
        # Observe the real UPDATE OF trigger condition, including same-value SET.
        db.execute_native("CREATE TEMP TABLE kind_updates (id bigint) ON COMMIT DROP")
        db.execute_native("""CREATE FUNCTION pg_temp.observe_kind_update() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN INSERT INTO kind_updates VALUES (NEW.id); RETURN NEW; END $$""")
        db.execute_native("""CREATE TRIGGER test_kind_updates AFTER UPDATE OF admission_kind ON daily_queue
            FOR EACH ROW EXECUTE FUNCTION pg_temp.observe_kind_update()""")
        yield db, facts
        db.raw.rollback()


def _plan(db, day, ids, *, positions=None, buckets=None, kinds=None):
    rows = db.execute_native(
        "SELECT id,position,card_bucket,admission_kind FROM daily_queue WHERE id=ANY(%s) ORDER BY id",
        (list(ids),),
    ).fetchall()
    return apply_plan(
        db,
        list(ids),
        positions if positions is not None else [r["position"] for r in rows],
        buckets if buckets is not None else [r["card_bucket"] for r in rows],
        kinds if kinds is not None else [r["admission_kind"] for r in rows],
        day,
    )


def test_native_unchanged_plan_matches_every_row_without_origin_trigger(case):
    db, facts = case
    ids = facts.queued_ids[:3]
    before = protected_hash(db.raw, ids)
    assert _plan(db, facts.queue_date, ids) == len(ids)
    assert db.execute_native("SELECT count(*) FROM kind_updates").fetchone()[0] == 0
    assert protected_hash(db.raw, ids) == before


def test_native_position_only_does_not_touch_kind_or_issued_origin(case):
    db, facts = case
    ids = facts.queued_ids[2:4]
    protected = protected_hash(db.raw, facts.issued_ids)
    assert _plan(db, facts.queue_date, ids, positions=[100, 101]) == len(ids)
    assert [
        row[0]
        for row in db.execute_native(
            "SELECT position FROM daily_queue WHERE id=ANY(%s) ORDER BY id",
            (list(ids),),
        ).fetchall()
    ] == [100, 101]
    assert db.execute_native("SELECT count(*) FROM kind_updates").fetchone()[0] == 0
    assert protected_hash(db.raw, facts.issued_ids) == protected


def test_native_kind_change_and_null_preserve_origin_trigger(case):
    db, facts = case
    ids = facts.queued_ids[2:5]
    assert _plan(db, facts.queue_date, ids, kinds=["new", None, "due"]) == len(ids)
    assert [
        r[0]
        for r in db.execute_native("SELECT id FROM kind_updates ORDER BY id").fetchall()
    ] == list(ids[:2])
    assert [
        r[0]
        for r in db.execute_native(
            "SELECT admission_kind FROM daily_queue WHERE id=ANY(%s) ORDER BY id",
            (list(ids),),
        ).fetchall()
    ] == ["new", None, "due"]
    assert (
        db.execute_native(
            "SELECT count(*) FROM queue_attempt_origins WHERE queue_entry_id=ANY(%s)",
            (list(ids),),
        ).fetchone()[0]
        == 3
    )


@pytest.mark.parametrize("bad", ["missing", "blocked", "wrong_day"])
def test_native_missing_or_nonqueued_member_forces_caller_rollback(case, bad):
    db, facts = case
    good = facts.queued_ids[2]
    bad_id = (
        999999
        if bad == "missing"
        else (facts.blocked_id if bad == "blocked" else facts.queued_ids[3])
    )
    if bad == "wrong_day":
        db.execute_native(
            "UPDATE daily_queue SET queue_date=%s WHERE id=%s", ("2000-01-01", bad_id)
        )
    before = db.execute_native(
        "SELECT position FROM daily_queue WHERE id=%s", (good,)
    ).fetchone()[0]
    with pytest.raises(RuntimeError, match="membership"):
        with db.raw.transaction():
            matched = apply_plan(
                db,
                [good, bad_id],
                [999, 1000],
                ["opening"] * 2,
                ["due"] * 2,
                facts.queue_date,
            )
            if matched != 2:
                raise RuntimeError("membership changed")
    assert (
        db.execute_native(
            "SELECT position FROM daily_queue WHERE id=%s", (good,)
        ).fetchone()[0]
        == before
    )


def test_native_abandoned_origin_immutable_when_kind_changes(case):
    db, facts = case
    entry = facts.queued_ids[2]
    origin = db.execute_native(
        "SELECT card_id,revision FROM queue_attempt_origins WHERE queue_entry_id=%s",
        (entry,),
    ).fetchone()
    db.execute_native(
        """INSERT INTO queue_attempt_abandonments(queue_entry_id,card_id,revision,operation_id,
        request_sha256,origin_sha256,actor,reason,abandoned_at)
        VALUES(%s,%s,%s,'w06-abandon',%s,%s,'test','test',now())""",
        (entry, origin[0], origin[1], "a" * 64, "b" * 64),
    )
    before = db.execute_native(
        "SELECT to_jsonb(o)::text FROM queue_attempt_origins o WHERE queue_entry_id=%s",
        (entry,),
    ).fetchone()[0]
    assert _plan(db, facts.queue_date, [entry], kinds=["new"]) == 1
    after = db.execute_native(
        "SELECT to_jsonb(o)::text FROM queue_attempt_origins o WHERE queue_entry_id=%s",
        (entry,),
    ).fetchone()[0]
    assert after == before
    assert (
        db.execute_native(
            "SELECT admission_kind FROM daily_queue WHERE id=%s", (entry,)
        ).fetchone()[0]
        == "new"
    )


def test_native_changed_kind_retains_failed_attempt_origin(case):
    db, facts = case
    entry = facts.queued_ids[2]
    db.execute_native("UPDATE daily_queue SET attempt_failed=1 WHERE id=%s", (entry,))
    assert _plan(db, facts.queue_date, [entry], kinds=["new"]) == 1
    assert (
        db.execute_native(
            "SELECT attempt_failed FROM queue_attempt_origins WHERE queue_entry_id=%s",
            (entry,),
        ).fetchone()[0]
        == 1
    )
