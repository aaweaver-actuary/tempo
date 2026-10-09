"""Sparse eligibility must bound queue preparation by useful unlocks."""
import sqlite3


def test_daily_queue_sparse_unlock_does_not_scan_locked_backlog():
    from app.main import _unlock_eligible_opening_cards

    with sqlite3.connect(":memory:") as sparse_database:
        sparse_database.executescript("""
            CREATE TABLE reviews(card_id TEXT,source_kind TEXT,invalidated_at TEXT);
            CREATE INDEX reviews_by_card ON reviews(card_id);
            CREATE TABLE cards(id TEXT PRIMARY KEY,content_type TEXT,state TEXT,archived INTEGER);
            CREATE TABLE opening_graph_steps(card_id TEXT,parent_card_id TEXT,repertoire_id TEXT,generation INTEGER);
            CREATE TABLE opening_graph_publications(repertoire_id TEXT,generation INTEGER);
            CREATE INDEX graph_by_card ON opening_graph_steps(card_id,repertoire_id,generation,parent_card_id);
            INSERT INTO opening_graph_publications VALUES('published',2);
            INSERT INTO cards VALUES('practiced-parent','opening','learning',0);
            INSERT INTO reviews VALUES('practiced-parent','study',NULL);
            INSERT INTO cards VALUES('learning-parent','opening','learning',0);
        """)
        sparse_database.executemany("INSERT INTO cards VALUES(?,'opening','locked',0)",
            [(f"locked-{number:05}",) for number in range(15_000)])
        eligible_ids = [f"locked-{number:05}" for number in range(14_979, 15_000)]
        sparse_database.executemany("INSERT INTO opening_graph_steps VALUES(?,NULL,'published',2)",
            [(identifier,) for identifier in eligible_ids])
        sparse_database.execute("INSERT INTO opening_graph_steps VALUES('locked-00000',NULL,'published',1)")
        sparse_database.execute("INSERT INTO opening_graph_steps VALUES('locked-00001','learning-parent','published',2)")
        sparse_database.execute("INSERT INTO opening_graph_steps VALUES('locked-14999','learning-parent','published',2)")
        # A transposed incoming path with a practiced parent is sufficient.
        sparse_database.execute("DELETE FROM opening_graph_steps WHERE card_id='locked-14998'")
        sparse_database.execute("INSERT INTO opening_graph_steps VALUES('locked-14998','practiced-parent','published',2)")
        cursor = ""
        slice_count = 0
        while cursor is not None:
            cursor = _unlock_eligible_opening_cards(sparse_database, "2026-10-07", after_card_id=cursor, batch_size=8)
            slice_count += 1
        assert slice_count == 3
        assert [row[0] for row in sparse_database.execute("SELECT id FROM cards WHERE state='new' ORDER BY id")] == eligible_ids
