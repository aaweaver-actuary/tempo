"""Task-only legacy conflict seed/readback, supplied on stdin by the C browser test.

Normal imports create the repertoire/cards first. This simulates one pre-existing
legacy conflicting line; it is not an import bypass exposed by the product.
"""
import hashlib
import json
import os
import sys

from tempo_legacy_database.adapters import postgres_store

action, repertoire_id = sys.argv[1:]
assert action in {"seed", "readback"}
assert os.environ.get("TEMPO_TEST_INSTANCE") == "disposable"
assert postgres_store.configured()

with postgres_store.connection(read_only=action=="readback") as database:
    marker = database.execute_native("SELECT shobj_description((SELECT oid FROM pg_database "
        "WHERE datname=current_database()), 'pg_database')").fetchone()[0]
    assert marker == "tempo-disposable-postgres-test", "Refusing non-disposable database"
    repertoire = database.execute_native("SELECT source_name FROM repertoires WHERE id=%s",
                                         (repertoire_id,)).fetchone()
    assert repertoire and repertoire[0] == "product-repertoire-review.pgn"
    lines = database.execute_native("SELECT id,moves_json FROM repertoire_lines "
        "WHERE repertoire_id=%s ORDER BY id", (repertoire_id,)).fetchall()
    assert 1 <= len(lines) <= 8
    if action == "seed":
        first = database.execute_native("SELECT name,trained_color,start_fen,created_at FROM repertoire_lines "
            "WHERE repertoire_id=%s ORDER BY id LIMIT 1", (repertoire_id,)).fetchone()
        assert first[1] == "white"
        identity = hashlib.sha256(("task501:legacy-conflict:"+repertoire_id).encode()).hexdigest()
        database.execute_native("INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,start_fen,moves_json,created_at) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(id) DO NOTHING",
            (identity,repertoire_id,first[0],first[1],first[2],json.dumps(["d2d4","d7d5"]),first[3]))
    else:
        cards = database.execute_native("SELECT card.id,card.revision,card.start_fen,card.moves_json FROM cards card "
            "WHERE card.repertoire_id=%s OR EXISTS(SELECT 1 FROM repertoire_cards membership "
            "WHERE membership.repertoire_id=%s AND membership.card_id=card.id) ORDER BY card.id",
            (repertoire_id,repertoire_id)).fetchall()
        reviews = database.execute_native("SELECT review.id,review.card_id,review.rating,review.reviewed_at,review.source_kind "
            "FROM reviews review WHERE review.card_id IN (SELECT card.id FROM cards card WHERE card.repertoire_id=%s "
            "OR EXISTS(SELECT 1 FROM repertoire_cards membership WHERE membership.repertoire_id=%s "
            "AND membership.card_id=card.id)) ORDER BY review.id", (repertoire_id,repertoire_id)).fetchall()
        print(json.dumps(dict(lines=[list(row) for row in lines],cards=[list(row) for row in cards],
                              reviews=[list(row) for row in reviews])))
