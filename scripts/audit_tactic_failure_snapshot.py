"""Read-only, synthetic-identifier-scoped evidence for issue 208."""
import json
import re
import sys
import psycopg
from check_postgres_daily_study_dispatch import configure, DSN

identifier = sys.argv[1]
if not re.fullmatch(r"training-transition-[a-f0-9-]+", identifier):
    raise ValueError("Invocation-owned synthetic identifier required")
configure()
with psycopg.connect(DSN) as database:
    pattern = identifier + "-prepared-%"
    snapshot = {
        "cards": database.execute("SELECT id,revision,state,due_date,interval_days,repetitions,lapses,guided_review FROM cards WHERE id LIKE %s ORDER BY id", (pattern,)).fetchall(),
        "queue": database.execute("SELECT card_id,id,status,attempt_failed,cycle FROM daily_queue WHERE card_id LIKE %s ORDER BY card_id,id", (pattern,)).fetchall(),
        "origins": database.execute("SELECT card_id,queue_entry_id,revision,attempt_failed,status FROM queue_attempt_origins WHERE card_id LIKE %s ORDER BY card_id,queue_entry_id,revision", (pattern,)).fetchall(),
        "reviews": database.execute("SELECT card_id,id,rating,guided,previous_interval,next_interval FROM reviews WHERE card_id LIKE %s ORDER BY card_id,id", (pattern,)).fetchall(),
        "receipts": database.execute("SELECT card_id,attempt_id,queue_entry_id,review_id,outcome,guided,scheduling_status FROM review_attempt_receipts WHERE card_id LIKE %s ORDER BY card_id,attempt_id", (pattern,)).fetchall(),
    }
print(json.dumps(snapshot, default=str))
