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
    parser.add_argument("action", choices=("seed", "run", "inspect", "refresh", "prepare-intake-probe", "intake-probe"))
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

    if options.action in {"seed", "prepare-intake-probe"}:
        manifest = seed_fixture(transaction, options.project)
        if options.action == "prepare-intake-probe":
            from app.games.feedback.review import SyncConfiguration
            from app.product_composition.game_review import create_game_review, SystemClock
            clock = SystemClock()
            review = create_game_review(workspace="legacy", connections=connections, learning=None, clock=clock)
            configuration = SyncConfiguration.model_validate_json(json.dumps(dict(workspace_id="legacy",provider="lichess",
                account="Learner",window=dict(start_at="2026-09-01T00:00:00Z",end_at="2026-11-01T00:00:00Z",precision="second"),
                repertoire_ids=[manifest["repertoire_id"]],speeds=["rapid"],rated=True,interval_seconds=None,revision=1)))
            review.configure(configuration,0)
            review.sync(provider="lichess",operation_id="intake-probe-"+manifest["token"])
            data = provider_fixtures(manifest)
            print(json.dumps(dict(manifest,http_fixtures=dict(lichess=data["lichess"][:1],chesscom=[],intake_probe=True))))
            return
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
    if options.action == "intake-probe":
        if dsn.username != "tempo_writer" or os.environ.get("TEMPO_PRODUCT_GAME_SYNC_ENABLED") != "1":
            raise RuntimeError("probe requires the real opted-in disposable writer profile")
        from app.games.feedback.ports import Delivery
        from tempo_learning_remediation import RemediationError
        from app.product_composition.game_review import SystemClock
        from app.product_composition.game_sync_current import current_provider_worker, current_sync_admission
        with transaction() as database:
            rows = database.execute_native("SELECT payload::text FROM product_feedback_deliveries WHERE state='pending'").fetchall()
        if len(rows) != 1:
            print(json.dumps(dict(stage="published_outbox",pending_commands=len(rows),
                error_type="ExactCliPublishedCommandRequired")))
            raise SystemExit(2)
        command = Delivery.model_validate_json(rows[0][0]).command
        if (command.key.game_id != manifest["game_id"] or command.decision.repertoire_id != manifest["repertoire_id"]):
            raise RuntimeError("pending command differs from this exact synthetic source")

        def protected():
            with transaction() as database:
                cards = database.execute_native("SELECT id,state,introduced_at,due_date,fsrs_card_json FROM cards WHERE id=ANY(%s) ORDER BY id",
                    ([manifest["card_id"],manifest["unrelated_card"]],)).fetchall()
                reviews = database.execute_native("SELECT id,card_id,rating,reviewed_at FROM reviews ORDER BY id").fetchall()
                queue = database.execute_native("SELECT id,card_id,status,position,cycle FROM daily_queue ORDER BY id").fetchall()
                return tuple(tuple(row) for row in cards),tuple(tuple(row) for row in reviews),tuple(tuple(row) for row in queue)

        before, stage = protected(), "controls"
        runtime, _ = current_provider_worker(workspace="legacy",connections=connections,clock=SystemClock())
        intake = runtime.runner.coordinator.learning
        try:
            with current_sync_admission():
                stage = "capture"
                captured = intake.capture(command.decision.repertoire_id,command.key.game_id,(command.key.occurrence_ply-1,))
                stage = "projection"
                from app.repertoire import project_capture
                from app.product_composition.retained_game import retained_canonical_game
                project_capture(captured,comparison_game=retained_canonical_game(captured))
                stage = "intake_or_commit"
                receipt = intake.accept_encounter(command)
            assert protected() == before, "intake changed ordinary cards/history/queue"
            print(json.dumps(dict(stage="committed",receipt=receipt.model_dump(mode="json"),ordinary_state_unchanged=True)))
        except Exception as error:
            # This action accepts only the manifest's synthetic command. Never
            # print traceback/SQL parameters/PGN, even in diagnostic evidence.
            safe_codes = {"one_explicit_existing_game_required","legacy_game_already_accepted_use_retained_preview",
                "legacy_game_changed_or_accepted_during_export","explicit_decision_scope_exceeds_bound_or_is_invalid",
                "canonical_imported_source_changed","capture_time_requires_timezone",
                "retained_owner_capture_required","retained_owner_capture_changed","retained_owner_projection_mismatch",
                "retained_owner_scope_mismatch","retained_owner_scope_exceeds_bound","borrowed_owner_writer_transaction_required",
                "snapshot_comparison_game_mismatch","invalid_source_position","illegal_source_move",
                "unsupported_chronology_provider","snapshot_capture_exceeds_bound","invalid_owner_capture",
                "snapshot_graph_unavailable","snapshot_revision_changed","snapshot_game_scope_mismatch",
                "snapshot_scope_revision_mismatch","snapshot_scope_completeness_unknown","snapshot_source_digest_mismatch",
                "duplicate_source_route","snapshot_source_revision_mismatch","canonical_prefix_snapshot_unsupported"}
            reason = getattr(error,"reason",None)
            if not isinstance(reason,str) or reason not in safe_codes:
                reason = str(error) if type(error) is ValueError and str(error) in safe_codes else None
            if isinstance(error,RemediationError) and isinstance(error.reason,str) and re.fullmatch(r"legacy_[a-z_]{1,90}",error.reason):
                reason = error.reason  # Existing owner's fixed capture reason, never arbitrary exception text.
            sqlstate = getattr(error,"sqlstate",None)
            try:
                unchanged = protected() == before
            except Exception:
                unchanged = None
            print(json.dumps(dict(stage=stage,error_type=type(error).__name__,reason=reason,
                sqlstate=sqlstate if isinstance(sqlstate,str) and re.fullmatch(r"[0-9A-Z]{5}",sqlstate) else None,
                ordinary_state_unchanged=unchanged)))
            raise SystemExit(2)
        finally:
            runtime.close()
        return
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

    if os.environ.get("TEMPO_PACKAGED_PROVIDER_FIXTURE") != "1":
        return run_component_fixture(transaction, manifest, connections)

    # Readback only: the optional packaged CLI is the sole provider executor.
    with transaction() as database:
        sources = database.execute_native("SELECT COUNT(*) FROM product_feedback_sources WHERE payload->'normalized'->>'workspace_id'='legacy'").fetchone()[0]
        effects = database.execute_native("SELECT COUNT(*) FROM study_game_feedback WHERE card_id=%s", (manifest["card_id"],)).fetchone()[0]
        pending = database.execute_native("SELECT COUNT(*) FROM product_feedback_deliveries WHERE state='pending'").fetchone()[0]
        jobs = database.execute_native("SELECT status,payload::text FROM product_feedback_jobs").fetchall()
        replayed = database.execute_native("SELECT COUNT(*) FROM product_feedback_deliveries WHERE (payload->>'attempts')::int>1").fetchone()[0]
        complete = len(jobs) == 2 and all(row[0] == 'complete' and json.loads(row[1])['window_complete'] for row in jobs)
        print(json.dumps(dict(sources=sources, effects=effects, complete=complete and pending == 0, replayed=replayed)))


def run_component_fixture(transaction, manifest, connections):
    """Existing component fixture; never claimed as packaged CLI evidence."""
    from contextlib import contextmanager
    import httpx
    from app.product_composition.game_sync_current import current_provider_worker
    from app.product_composition.game_review import SystemClock
    from tempo_legacy_database.adapters.games.product_review import PostgresReviewStore

    @contextmanager
    def raw_connections():
        with transaction() as database:
            yield database.raw
    store = PostgresReviewStore(raw_connections)
    configurations = [store.configuration("legacy", provider) for provider in ("lichess", "chesscom")]
    if any(value is None or value.account.casefold() != "learner" or value.speeds != ("rapid",)
           or value.rated is not True or value.repertoire_ids != (manifest["repertoire_id"],) for value in configurations):
        raise RuntimeError("browser must explicitly configure both synthetic accounts and filters first")
    fixtures = provider_fixtures(manifest)
    def handle(request):
        if request.url.host == "lichess.org":
            params = request.url.params
            rows = [{"id": item["source_id"], "createdAt": int(datetime.fromisoformat(item["played_at"]).timestamp() * 1000),
                     "pgn": item["pgn"], "speed": "rapid", "rated": True} for item in fixtures["lichess"]]
            rows = sorted((row for row in rows if int(params["since"]) <= row["createdAt"] < int(params["until"])), key=lambda row: row["createdAt"])
            return httpx.Response(200, content="\n".join(json.dumps(row) for row in rows[:int(params["max"])]), headers={"Content-Type": "application/x-ndjson"})
        archive = "https://api.chess.com/pub/player/learner/games/"
        if request.url.path.endswith("archives"):
            return httpx.Response(200, json={"archives": [archive + "2026/09", archive + "2026/10"]})
        month = request.url.path[-2:]
        return httpx.Response(200, json={"games": [{"uuid": item["source_id"], "pgn": item["pgn"],
            "end_time": int(datetime.fromisoformat(item["played_at"]).timestamp()), "rules": "chess", "time_class": "rapid", "rated": True,
            "url": "https://www.chess.com/game/live/123456"} for item in fixtures["chesscom"] if item["played_at"][5:7] == month]})
    os.environ["TEMPO_PRODUCT_GAME_SYNC_ENABLED"] = "1"  # This verified synthetic helper only.
    runtime, _ = current_provider_worker(workspace="legacy", connections=connections,
        clock=SystemClock(), transport_factory=lambda: httpx.MockTransport(handle))
    try:
        for _ in range(50):
            if runtime.run_once() in {"idle", "deferred"}:
                break
        complete = True
        for provider in ("lichess", "chesscom"):
            job = store.last_job("legacy", provider)
            complete = complete and job is not None and job.status == "complete" and job.window_complete
        with transaction() as database:
            sources = database.execute_native("SELECT COUNT(*) FROM product_feedback_sources WHERE payload->'normalized'->>'workspace_id'='legacy'").fetchone()[0]
            effects = database.execute_native("SELECT COUNT(*) FROM study_game_feedback WHERE card_id=%s", (manifest["card_id"],)).fetchone()[0]
            pending = database.execute_native("SELECT COUNT(*) FROM product_feedback_deliveries WHERE state='pending'").fetchone()[0]
        print(json.dumps(dict(sources=sources, effects=effects, complete=complete and pending == 0)))
    finally:
        runtime.close()


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
        fresh = name == "primary"
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
            # Keep ordinary seeding from consuming the target before the
            # real encounter exception is exercised. Only this synthetic
            # repertoire has zero allowance; the unrelated review is unchanged.
            database.execute_native("INSERT INTO repertoires(id,name,source_name,created_at,new_cards_per_day) VALUES(%s,%s,'T10 synthetic fixture',%s,%s)",
                                    (repertoire,title,now.isoformat(),0 if fresh else 2))
            database.execute_native("""INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,trained_color,
                state,due_date,introduced_at,revision,interval_days,fsrs_card_json,first_correct_at,stability,content_type)
                VALUES(%s,%s,'prefix',%s,'["e2e4"]','white',%s,%s,%s,1,%s,%s,%s,%s,'opening')""",
                (card_id,repertoire,start,"new" if fresh else "mature",date.today().isoformat(),
                 None if fresh else (now-timedelta(days=6)).date().isoformat(),0 if fresh else 20,
                 None if fresh else state,None if fresh else (now-timedelta(days=25)).isoformat(),0 if fresh else 20))
            if not fresh:
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
        VALUES(%s,%s,%s,'Learner',%s,'rapid',1,'white','1/2-1/2',%s,%s,%s)""",
        (item["game_id"], "chess.com" if provider == "chesscom" else provider, item["source_id"],
         item["played_at"], manifest["start_fen"], json.dumps(item["moves"]), hashlib.sha256(item["pgn"].encode()).hexdigest()))


if __name__ == "__main__":
    main()
