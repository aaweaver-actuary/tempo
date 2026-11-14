"""Deferred native proof against the current owned Study PostgreSQL fixture."""

from datetime import UTC, datetime

import pytest


def test_native_next_exercise_borrowed_reads_rollback_and_original_issuance(
    case, monkeypatch
):
    from tempo_storage import connections
    from tempo_storage.http_composition import queue_projection
    from tempo_storage.study import next_exercise_reads as reads

    from app import command_dispatch, queue_commands
    from app.product_composition.study import issue_returned_queue_head
    from app.services.queue_next_card import prepare_queue_head

    day = datetime.now(UTC).date().isoformat()
    with case.transaction() as db:
        entry = db.execute_native(
            "INSERT INTO daily_queue(queue_date,card_id,cycle,position) VALUES(%s,%s,0,0) RETURNING id",
            (day, case.card_id),
        ).fetchone()[0]
        identity = dict(entry_id=entry, card_id=case.card_id, revision=1)
        assert reads.original_attempt_ids(db, **identity) == ()
        origin = reads.admission_origin(db, **identity)
        assert origin and not origin.issued_as_head
        assert not reads.applicable_owner_exists(db, card_id=case.card_id)
        assert reads.position_owner(db, repertoire_id="missing-owner") is None
        assert (
            reads.prior_attempt_ids(
                db, repertoire_id="missing-owner", owner_epoch="missing", study_day=day
            )
            == ()
        )

    monkeypatch.setattr(connections, "read_connection", case.transaction)
    monkeypatch.setattr(queue_commands, "current_study_day", lambda db: day)
    card = dict(queue_entry_id=entry, id=case.card_id, revision=1)
    payload = dict(
        queue_date=day, entry_id=entry, card_id=case.card_id, expected_revision=1
    )
    prepared = prepare_queue_head(payload)
    assert prepared.mode == "legacy"
    with pytest.raises(RuntimeError, match="rollback issuance"):
        with case.transaction() as db:
            assert queue_commands.issue_queue_head(db, prepared)["issued"]
            raise RuntimeError("rollback issuance")
    with case.transaction() as db:
        assert not reads.admission_origin(db, **identity).issued_as_head

    calls = []

    def dispatch(name, request, *, idempotency_key):
        assert name == "queue.issue_head" and idempotency_key is None
        calls.append(request)
        with case.transaction() as db:
            return queue_commands.issue_queue_head(db, prepare_queue_head(request))

    monkeypatch.setattr(command_dispatch, "dispatch_command", dispatch)
    first = issue_returned_queue_head(
        day,
        card,
        read_connection=case.transaction,
        queue_projection_storage=queue_projection,
    )
    second = issue_returned_queue_head(
        day,
        card,
        read_connection=case.transaction,
        queue_projection_storage=queue_projection,
    )
    assert first == second == {"issued": True}
    assert calls == [payload]
    with case.transaction() as db:
        assert reads.admission_origin(db, **identity).issued_as_head
        assert reads.original_attempt_ids(db, **identity) == ()


def test_native_position_writer_rolls_back_and_replays_original_plan(case, monkeypatch):
    """The current owner writes a real legal plan; rollback cannot leave an origin or plan."""
    import json
    import uuid
    from datetime import timedelta

    import chess
    from fsrs import Card
    from tempo_evidence_math.position_learning import Hold
    from tempo_storage import connections
    from tempo_storage.http_composition import queue_projection
    from tempo_storage.study import next_exercise_reads as reads

    from app import command_dispatch, queue_commands
    from app.product_composition.study import issue_returned_queue_head
    from app.repertoire.position_study_source import prepare_position_source
    from app.services.position_queue_admission import admit_due, prepare_due
    from app.services.queue_next_card import prepare_queue_head

    rep = "native-position-" + uuid.uuid4().hex
    now = datetime.now(UTC)
    # This module-scoped owned fixture may already contain today's legacy head.
    # Use a separate Study day; the production head fence still runs unchanged.
    day = (now.date() + timedelta(days=1)).isoformat()
    routes = (
        ["e2e4", "e7e5", "g1f3", "b8c6", "f1c4", "g8f6", "f3g5", "d7d5", "e4d5"],
        ["e2e4", "e7e5", "g1f3", "b8c6", "f1c4", "f8c5", "c2c3"],
    )
    with case.transaction() as db:
        raw = db.raw
        raw.execute(
            "INSERT INTO repertoires(id,name,source_name,created_at) VALUES(%s,%s,'fixture',%s)",
            (rep, rep, now.isoformat()),
        )
        raw.execute(
            "INSERT INTO opening_graph_publications(repertoire_id,generation,state,published_at) VALUES(%s,3,'ready',%s)",
            (rep, now.isoformat()),
        )
        for index, moves in enumerate(routes):
            route, card = f"{rep}-route-{index}", f"{rep}-card-{index}"
            encoded = json.dumps(moves)
            raw.execute(
                "INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,start_fen,moves_json,created_at) VALUES(%s,%s,%s,'white',%s,%s,%s)",
                (route, rep, route, chess.STARTING_FEN, encoded, now.isoformat()),
            )
            raw.execute(
                """INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,fsrs_card_json,introduced_at,revision,content_type,trained_color)
                VALUES(%s,%s,'prefix',%s,%s,'learning',%s,%s,%s,1,'opening','white')""",
                (
                    card,
                    rep,
                    chess.STARTING_FEN,
                    encoded,
                    day,
                    Card(card_id=20000 + index, due=now - timedelta(days=2)).to_json(),
                    (now - timedelta(days=2)).date().isoformat(),
                ),
            )
            raw.execute(
                "INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES(%s,%s)",
                (rep, card),
            )
            raw.execute(
                """INSERT INTO opening_graph_steps(repertoire_id,generation,line_id,decision_index,card_id,decision_fen_key,starting_fen,moves_json,trained_color)
                VALUES(%s,3,%s,0,%s,%s,%s,%s,'white')""",
                (rep, route, card, chess.STARTING_FEN, chess.STARTING_FEN, encoded),
            )
        raw.execute(
            """INSERT INTO repertoire_integrity_state(repertoire_id,status,scan_status,scan_generation,checked_at,scan_completed_sources,scan_total_sources)
            VALUES(%s,'clean','idle',%s,%s,2,2)""",
            (rep, rep + "-scan:2", now.isoformat()),
        )
        for suffix, kind, generation, payload in [
            ("graph", "opening_graph_rebuild", 3, {}),
            (
                "scan",
                "integrity_scan",
                2,
                {"repertoire_id": rep, "graph_generation": 3},
            ),
        ]:
            raw.execute(
                """INSERT INTO background_tasks(id,kind,deduplication_key,generation,state,payload_json,next_attempt_at,created_at,updated_at)
                VALUES(%s,%s,%s,%s,'complete',%s,%s,%s,%s)""",
                (
                    rep + "-" + suffix,
                    kind,
                    rep,
                    generation,
                    json.dumps(payload),
                    now.isoformat(),
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
    with case.transaction() as db:
        raw = db.raw
        source = prepare_position_source(db, rep)
        assert not isinstance(source, Hold), source
        digest = source.authored_token.authored_digest
        raw.execute(
            "INSERT INTO study_position_epochs VALUES(%s,%s,%s,%s)",
            (rep, "native-epoch", digest, now),
        )
        raw.execute(
            "INSERT INTO study_position_owners VALUES(%s,'native-epoch','active',%s)",
            (rep, now),
        )
        definitions = {
            occurrence.definition.key.position: occurrence.definition
            for route in source.routes
            for occurrence in route.decisions
        }
        assert len(definitions) == 6
        for definition in definitions.values():
            ident = raw.execute(
                "SELECT nextval('study_position_fsrs_id_seq')"
            ).fetchone()[0]
            due = now - timedelta(days=2)
            raw.execute(
                """INSERT INTO study_position_states(repertoire_id,variant,position,expected_uci,policy_version,owner_epoch,source_digest,fsrs_internal_card_id,fsrs_json,due_at,introduced_at)
                VALUES(%s,%s,%s,%s,%s,'native-epoch',%s,%s,%s,%s,%s)""",
                (
                    rep,
                    definition.key.variant,
                    definition.key.position,
                    definition.expected_uci,
                    definition.policy_version,
                    digest,
                    ident,
                    Card(card_id=ident, due=due).to_json(),
                    due,
                    now - timedelta(days=2),
                ),
            )
    with case.transaction() as db:
        item = prepare_due(db, day, {})
        assert isinstance(item, dict) and "candidate" in item, item
        assert admit_due(db, day, item) is None
        from tempo_storage.queue_refresh.application.publication import (
            begin_projection,
            publish_projection,
        )

        from app.services.queue_application import randomize_daily_queue

        randomize_daily_queue(db, day)
        begin_projection(db, day)
        publish_projection(db, day, now.isoformat(), 0)
        row = db.execute_native(
            "SELECT id,card_id FROM daily_queue WHERE admission_repertoire_id=%s ORDER BY position,id LIMIT 1",
            (rep,),
        ).fetchone()
        assert row
        entry, card_id = row[0], row[1]
    monkeypatch.setattr(connections, "read_connection", case.transaction)
    monkeypatch.setattr(queue_commands, "current_study_day", lambda db: day)
    payload = dict(queue_date=day, entry_id=entry, card_id=card_id, expected_revision=1)
    prepared = prepare_queue_head(payload)
    assert prepared.mode == "position", prepared
    identity = dict(entry_id=entry, card_id=card_id, revision=1)
    with pytest.raises(RuntimeError, match="rollback position issuance"):
        with case.transaction() as db:
            assert queue_commands.issue_queue_head(db, prepared)["issued"]
            raise RuntimeError("rollback position issuance")
    with case.transaction() as db:
        assert reads.original_attempt_ids(db, **identity) == ()
        assert not reads.admission_origin(db, **identity).issued_as_head
    calls = []

    def dispatch(name, request, *, idempotency_key):
        assert name == "queue.issue_head" and idempotency_key is None
        calls.append(request)
        with case.transaction() as db:
            return queue_commands.issue_queue_head(db, prepare_queue_head(request))

    monkeypatch.setattr(command_dispatch, "dispatch_command", dispatch)
    card = dict(queue_entry_id=entry, id=card_id, revision=1)
    first = issue_returned_queue_head(
        day,
        card,
        read_connection=case.transaction,
        queue_projection_storage=queue_projection,
    )
    second = issue_returned_queue_head(
        day,
        card,
        read_connection=case.transaction,
        queue_projection_storage=queue_projection,
    )
    assert first == second and first["issued"] and first["position_presentation"]
    assert calls == [payload]
    with case.transaction() as db:
        attempts = reads.original_attempt_ids(db, **identity)
        assert (
            len(attempts) == 1
            and attempts[0] == first["position_presentation"]["attempt_id"]
        )
