"""Task-only legacy conflict seed/readback, supplied on stdin by the C browser test.

Normal imports create the repertoire/cards first. This supplies one synthetic
legacy conflicting line with an explicit stored card/graph binding. It records
no review and is not an import bypass exposed by the product.
"""
import hashlib
import json
import os
import sys
from datetime import date, timedelta

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
        graph = database.execute_native("SELECT generation FROM opening_graph_publications "
            "WHERE repertoire_id=%s AND state='ready'", (repertoire_id,)).fetchone()
        assert graph, "Legacy fixture requires an existing ready graph"
        identity = hashlib.sha256(("task501:legacy-conflict:"+repertoire_id).encode()).hexdigest()
        database.execute_native("INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,start_fen,moves_json,created_at) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(id) DO NOTHING",
            (identity,repertoire_id,first[0],first[1],first[2],json.dumps(["d2d4","d7d5"]),first[3]))
        card = hashlib.sha256(("task501:legacy-conflict-card:"+repertoire_id).encode()).hexdigest()
        database.execute_native("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,trained_color,"
            "state,due_date,introduced_at,content_type) VALUES(%s,%s,'prefix',%s,%s,'white','learning',%s,%s,'opening') "
            "ON CONFLICT(id) DO NOTHING", (card,repertoire_id,first[2],json.dumps(["d2d4"]),
                                          (date.today()+timedelta(days=2)).isoformat(),first[3]))
        database.execute_native("INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES(%s,%s) ON CONFLICT DO NOTHING",
                                (repertoire_id,card))
        database.execute_native("INSERT INTO opening_graph_steps(repertoire_id,generation,line_id,decision_index,"
            "card_id,decision_fen_key,starting_fen,moves_json,trained_color,segment_kind,first_decision_index,"
            "last_decision_index,decision_fen_keys_json) VALUES(%s,%s,%s,0,%s,%s,%s,%s,'white','prefix',0,0,'[]') "
            "ON CONFLICT DO NOTHING", (repertoire_id,graph[0],identity,card,' '.join(first[2].split()[:4]),
                                       first[2],json.dumps(["d2d4"])))
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
