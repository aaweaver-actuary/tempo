"""Existing repeat-attempt facts and writes on the caller transaction."""

from tempo_storage.http_composition.availability import available_attempt_sql
from tempo_storage.postgres import PostgresConnection, TempoRow


def _required_integer(row: TempoRow | None) -> int:
    if row is None:
        raise TypeError("'NoneType' object is not subscriptable")
    value: object = row[0]
    if type(value) is not int:
        raise TypeError("Invalid queue requeue integer")
    return value


def next_cycle(db: PostgresConnection, day: str, cid: str) -> int:
    return _required_integer(db.execute(
        'SELECT COALESCE(MAX(cycle),-1)+1 FROM (SELECT cycle FROM daily_queue WHERE queue_date=%s AND card_id=%s UNION ALL SELECT cycle FROM queue_attempt_origins WHERE queue_date=%s AND card_id=%s) cycles',
        (day, cid, day, cid),
    ).fetchone())


def next_position(db: PostgresConnection, day: str) -> int:
    return _required_integer(db.execute(
                'SELECT COALESCE(MAX(position),-1)+1 FROM daily_queue WHERE queue_date=%s',
                (day,),
            ).fetchone())


def position_at_offset(db: PostgresConnection, day: str, miss_reason: str, offset: int) -> int | None:
    row = db.execute(
            available_attempt_sql(miss_reason).replace("SELECT q.id FROM", "SELECT q.position FROM", 1)
            + ' ORDER BY q.position,q.id LIMIT 1 OFFSET %s',
            (day, offset),
        ).fetchone()
    return _required_integer(row) if row is not None else None


def shift_positions(db: PostgresConnection, day: str, position: int) -> None:
    db.execute(
            "UPDATE daily_queue SET position=position+1 WHERE queue_date=%s AND status='queued' AND position>=%s",
            (day, position),
        )


def append_attempt(db: PostgresConnection, day: str, cid: str, cycle: int, position: int, attempt: str) -> int:
    return _required_integer(db.execute(
        'INSERT INTO daily_queue(queue_date,card_id,cycle,position,attempt_state) VALUES(%s,%s,%s,%s,%s) RETURNING id',
        (day, cid, cycle, position, attempt),
    ).fetchone())


def set_review_admission(db: PostgresConnection, inserted_entry_id: int) -> None:
    db.execute(
        "UPDATE daily_queue SET\n               card_bucket=(SELECT content_type FROM cards WHERE id=card_id),\n               admission_kind='review'\n           WHERE id=%s",
        (inserted_entry_id,),
    )
