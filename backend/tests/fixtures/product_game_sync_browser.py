"""Synthetic T10 browser fixture, executable only in its verified disposable stack.

The Node caller verifies Compose/container/DB identity before supplying this
helper over stdin. Production code resolves from the package, without overlays.
"""
import argparse
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import os
import re
from urllib.parse import urlparse


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("seed", "run", "inspect", "refresh"))
    parser.add_argument("--project", required=True)
    parser.add_argument("--fixture-json")
    options = parser.parse_args()
    dsn = urlparse(os.environ.get("TEMPO_DATABASE_WRITE_URL", ""))
    if (os.environ.get("TEMPO_TEST_INSTANCE") != "disposable"
            or not re.fullmatch(r"tempo-pg-regressions-\d+-[a-f0-9]+", options.project)
            or dsn.hostname != "postgres" or dsn.path != "/tempo"):
        raise RuntimeError("verified disposable PostgreSQL browser stack required")
    # Production modules resolve exclusively from the packaged worker. Only
    # this synthetic helper is supplied over stdin; there is no source overlay.
    from tempo_legacy_database.adapters.games.product_review import DedicatedGamesConnections

    connections = DedicatedGamesConnections(os.environ["TEMPO_DATABASE_WRITE_URL"])
    transaction = connections.writer

    if options.action == "seed":
        manifest = seed_fixture(transaction, options.project)
        for provider, rows in provider_fixtures(manifest).items():
            for offset in range(0, len(rows), 20):
                with transaction() as database:
                    for item in rows[offset:offset+20]:
                        insert_fixture_game(database, provider, item, manifest)
        print(json.dumps(dict(manifest, http_fixtures=provider_fixtures(manifest))))
        return

    manifest = json.loads(options.fixture_json or "null")
    if not isinstance(manifest, dict) or manifest.get("project") != options.project:
        raise RuntimeError("this fixture's exact manifest is required")
    if options.action == "inspect":
        with transaction() as database:
            row = database.execute_native("""SELECT queue.id,queue.card_id,queue.status,queue.position,queue.cycle,
                queue.attempt_failed,origin.issued_as_head FROM daily_queue queue JOIN queue_attempt_origins origin
                ON origin.queue_entry_id=queue.id WHERE queue.id=%s AND queue.card_id=%s""",
                (manifest["unrelated_entry"], manifest["unrelated_card"])).fetchone()
            print(json.dumps(dict(active=dict(row) if row else None,
                settled=database.execute_native("""SELECT COUNT(*) FROM study_game_feedback WHERE card_id=%s
                    AND status='settled' AND payload_json::jsonb->'command'->>'observation'='missed'""",
                    (manifest["card_id"],)).fetchone()[0])))
        return

    if options.action == "refresh":
        # Dispatch the registered, independently owned task through real Celery.
        # Do not manufacture a queue or run Study refresh inside Games intake.
        from app.celery_app import celery_app
        celery_app.send_task("app.tasks.maintain_game_feedback", queue="foreground")
        with transaction() as database:
            available = database.execute_native("""SELECT COUNT(*) FROM study_game_feedback WHERE card_id=%s
                AND status='applied' AND payload_json::jsonb->'command'->>'observation'='missed'""",
                (manifest["card_id"],)).fetchone()[0]
        print(json.dumps(dict(available=available == 3, available_misses=available)))
        return

    # Readback only: the optional packaged CLI is the sole provider executor.
    with transaction() as database:
        sources = database.execute_native("SELECT COUNT(*) FROM product_feedback_sources WHERE payload->'normalized'->>'workspace_id'='legacy'").fetchone()[0]
        effects = database.execute_native("SELECT COUNT(*) FROM study_game_feedback WHERE card_id=%s", (manifest["card_id"],)).fetchone()[0]
        pending = database.execute_native("SELECT COUNT(*) FROM product_feedback_deliveries WHERE state='pending'").fetchone()[0]
        jobs = database.execute_native("SELECT status,payload::text FROM product_feedback_jobs").fetchall()
        replayed = database.execute_native("SELECT COUNT(*) FROM product_feedback_deliveries WHERE (payload->>'attempts')::int>1").fetchone()[0]
        complete = len(jobs) == 2 and all(row[0] == 'complete' and json.loads(row[1])['window_complete'] for row in jobs)
        print(json.dumps(dict(sources=sources, effects=effects, complete=complete and pending == 0, replayed=replayed)))


def seed_fixture(transaction, project):
    """Synthetic rows only; actual public capture/factory validates them later.

    Keep this helper independent of pytest, which is absent from the API image.
    No authority token, certificate object or Study save response is fabricated.
    """
    import uuid
    import chess
    from fsrs import Card
    token = uuid.uuid4().hex
    now = datetime(2026, 10, 26, 12, tzinfo=timezone.utc)
    identities = {}
    for name, removed in (("primary", chess.D8), ("unrelated", chess.A8)):
        card_id, repertoire = f"__t10-{name}-card-{token}", f"__t10-{name}-repertoire-{token}"
        route, scan = repertoire + "-route", repertoire + "-scan"
        board = chess.Board(); board.remove_piece_at(removed)
        start = board.fen(en_passant="legal")
        position = " ".join(start.split()[:4])
        due = datetime.now(timezone.utc) if name == "unrelated" else now + timedelta(days=20)
        state = Card(card_id=int(uuid.uuid4().hex[:12], 16), due=due,
                     last_review=now-timedelta(days=1), stability=20, difficulty=5).to_json()
        title = "Game sync fixture repertoire" if name == "primary" else "Preserved Study fixture"
        with transaction() as database:
            database.execute_native("INSERT INTO repertoires(id,name,source_name,created_at) VALUES(%s,%s,'T10 synthetic fixture',%s)",
                                    (repertoire,title,now.isoformat()))
            database.execute_native("""INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,trained_color,
                state,due_date,introduced_at,revision,interval_days,fsrs_card_json,first_correct_at,stability,content_type)
                VALUES(%s,%s,'prefix',%s,'["e2e4"]','white','mature',%s,%s,1,20,%s,%s,20,'opening')""",
                (card_id,repertoire,start,date.today().isoformat() if name == "unrelated" else due.date().isoformat(),
                 (now-timedelta(days=6)).date().isoformat(),state,(now-timedelta(days=25)).isoformat()))
            database.execute_native("""INSERT INTO reviews(card_id,rating,reviewed_at,previous_interval,next_interval,
                internal_rating,guided,source_kind,source_ref) VALUES(%s,'correct',%s,10,20,'good',0,'study',%s)""",
                (card_id,(now-timedelta(days=3)).isoformat(),"fixture-history:"+card_id))
            database.execute_native("INSERT INTO repertoire_cards(repertoire_id,card_id,canonical_route_source) VALUES(%s,%s,1)", (repertoire,card_id))
            database.execute_native("""INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,start_fen,moves_json,created_at)
                VALUES(%s,%s,'Route','white',%s,'["e2e4"]',%s)""", (route,repertoire,start,now.isoformat()))
            database.execute_native("INSERT INTO opening_graph_publications(repertoire_id,generation,state,published_at) VALUES(%s,1,'ready',%s)", (repertoire,now.isoformat()))
            database.execute_native("""INSERT INTO opening_graph_steps(repertoire_id,generation,line_id,decision_index,
                card_id,decision_fen_key,starting_fen,moves_json,trained_color,segment_kind,first_decision_index,
                last_decision_index,decision_fen_keys_json) VALUES(%s,1,%s,0,%s,%s,%s,'["e2e4"]','white','prefix',0,0,%s)""",
                (repertoire,route,card_id,position,start,json.dumps([position])))
            for kind, identity in (("opening_graph_rebuild",repertoire+"-graph"),("integrity_scan",scan)):
                database.execute_native("""INSERT INTO background_tasks(id,kind,deduplication_key,generation,state,
                    payload_json,next_attempt_at,created_at,updated_at) VALUES(%s,%s,%s,1,'complete',%s,%s,%s,%s)""",
                    (identity,kind,repertoire,json.dumps(dict(repertoire_id=repertoire,graph_generation=1)),
                     now.isoformat(),now.isoformat(),now.isoformat()))
            database.execute_native("""INSERT INTO repertoire_integrity_state(repertoire_id,status,checked_at,
                scan_status,scan_generation,scan_completed_sources,scan_total_sources)
                VALUES(%s,'clean',%s,'idle',%s,1,1)""", (repertoire,now.isoformat(),scan+":1"))
            entry = None
            if name == "unrelated":
                entry = database.execute_native("INSERT INTO daily_queue(queue_date,card_id,position,admission_kind) VALUES(%s,%s,0,'review') RETURNING id",
                                                (date.today().isoformat(),card_id)).fetchone()[0]
        identities[name] = dict(card_id=card_id,repertoire_id=repertoire,start_fen=start,entry=entry)
    primary, unrelated = identities["primary"], identities["unrelated"]
    source_id = f"source-{token}"
    manifest = dict(project=project,repertoire_id=primary["repertoire_id"],card_id=primary["card_id"],
        source_id=source_id,game_id="lichess:"+source_id,start_fen=primary["start_fen"],
        played_at=(now-timedelta(days=1)).isoformat(),token=token,
        unrelated_card=unrelated["card_id"],unrelated_entry=unrelated["entry"],unrelated_fen=unrelated["start_fen"])
    # The fixture starts with actual canonical library records; provider capture
    # is the new atomic raw/checkpoint/delivery path, never an invented game ID.
    with transaction() as database:
        primary_source = provider_fixtures(manifest)["lichess"][0]
        insert_fixture_game(database,"lichess",primary_source,manifest,primary=True)
    return manifest


def provider_fixtures(manifest):
    moment = datetime.fromisoformat(manifest["played_at"])
    def item(provider, source_id, at, moves):
        san = "d4" if moves == ["d2d4"] else "e4"
        pgn = ('[Event "Synthetic T10 fixture"]\n[White "Learner"]\n[Black "Opponent"]\n'
            '[Result "1/2-1/2"]\n[UTCDate "' + at.strftime('%Y.%m.%d') + '"]\n[UTCTime "' + at.strftime('%H:%M:%S') + '"]\n[SetUp "1"]\n[FEN "' + manifest["start_fen"] + '"]\n\n1. ' + san + ' 1/2-1/2\n')
        legacy = "chess.com" if provider == "chesscom" else provider
        return dict(source_id=source_id, game_id=manifest["game_id"] if source_id == manifest["source_id"] else f"{legacy}:{source_id}",
                    played_at=at.isoformat(), pgn=pgn, moves=moves)
    lichess = [item("lichess", manifest["source_id"], moment, ["d2d4"])] + [
        item("lichess", f"tail-{manifest['token']}-{index}", moment-timedelta(seconds=index), ["e2e4"]) for index in range(1,101)]
    chesscom = [item("chesscom", f"september-{manifest['token']}", datetime(2026,9,25,12,tzinfo=timezone.utc), ["d2d4"]),
                item("chesscom", f"october-{manifest['token']}", moment, ["d2d4"])]
    return dict(lichess=lichess, chesscom=chesscom)


def insert_fixture_game(database, provider, item, manifest, primary=False):
    if item["source_id"] == manifest["source_id"] and not primary:
        return
    database.execute_native("""INSERT INTO imported_games(id,provider,provider_game_id,username,played_at,
        speed,rated,color,result,start_fen,moves_json,content_hash)
        VALUES(%s,%s,%s,'Learner',%s,'rapid',1,'white','draw',%s,%s,%s)""",
        (item["game_id"], "chess.com" if provider == "chesscom" else provider, item["source_id"],
         item["played_at"], manifest["start_fen"], json.dumps(item["moves"]), hashlib.sha256(item["pgn"].encode()).hexdigest()))


if __name__ == "__main__":
    main()
