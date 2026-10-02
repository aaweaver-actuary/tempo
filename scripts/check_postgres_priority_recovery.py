"""Rehearse priority upgrade and retry boundaries on one disposable PostgreSQL database."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import uuid

import chess
import psycopg
from psycopg import sql

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from scripts.apply_postgres_migrations import MIGRATIONS, apply_migrations


ADMIN_DSN = "postgresql://postgres@postgres:5432/postgres"
PAST = "2026-09-29T00:00:00+00:00"
START_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


def seed_repertoire(database, repertoire_id: str, card_count: int, *, legacy_state: str) -> None:
    database.execute(
        "INSERT INTO repertoires(id,name,source_name,created_at) VALUES(%s,%s,%s,%s)",
        (repertoire_id, repertoire_id, "priority-recovery.pgn", PAST),
    )
    for ordinal in range(card_count):
        card_id = f"{repertoire_id}-card-{ordinal:04d}"
        database.execute(
            "INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,due_date) "
            "VALUES(%s,%s,'prefix',%s,'[]','2026-09-29')",
            (card_id, repertoire_id, START_FEN),
        )
        database.execute(
            "INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES(%s,%s)",
            (repertoire_id, card_id),
        )
    first_card_id = f"{repertoire_id}-card-0000"
    database.execute(
        "INSERT INTO repertoire_card_priority_generations("
        "repertoire_id,generation,card_id,scoring_version,updated_at) "
        "VALUES(%s,1,%s,2,%s)", (repertoire_id, first_card_id, PAST),
    )
    database.execute(
        "INSERT INTO repertoire_priority_publications(repertoire_id,generation,updated_at) "
        "VALUES(%s,1,%s)", (repertoire_id, PAST),
    )
    database.execute(
        "INSERT INTO repertoire_priority_jobs(repertoire_id,generation,status,next_attempt_at,updated_at) "
        "VALUES(%s,2,'running',%s,%s)", (repertoire_id, PAST, PAST),
    )
    legacy_payload = {"repertoire_id": repertoire_id, "generation": 2, "cursor": 1,
                      "source_signature": "schema20"}
    database.execute(
        "INSERT INTO background_tasks(id,kind,deduplication_key,generation,priority,state,"
        "phase,payload_json,next_attempt_at,lease_token,lease_expires_at,created_at,updated_at) "
        "VALUES(%s,'repertoire_priority',%s,1,131,%s,'staging_priorities',%s,%s,%s,%s,%s,%s)",
        (f"task-{repertoire_id}", repertoire_id, legacy_state,
         json.dumps(legacy_payload), PAST,
         "expired-token" if legacy_state == "leased" else None,
         PAST if legacy_state == "leased" else None, PAST, PAST),
    )
    database.execute(
        "INSERT INTO repertoire_card_priority_generations("
        "repertoire_id,generation,card_id,scoring_version,updated_at) "
        "VALUES(%s,2,%s,2,%s)", (repertoire_id, first_card_id, PAST),
    )


def state(database, repertoire_id: str) -> dict:
    return {
        "job": database.execute(
            "SELECT generation,status FROM repertoire_priority_jobs WHERE repertoire_id=%s",
            (repertoire_id,),
        ).fetchone(),
        "publication": database.execute(
            "SELECT generation FROM repertoire_priority_publications WHERE repertoire_id=%s",
            (repertoire_id,),
        ).fetchone(),
        "task": database.execute(
            "SELECT generation,state,phase,payload_json FROM background_tasks "
            "WHERE kind='repertoire_priority' AND deduplication_key=%s",
            (repertoire_id,),
        ).fetchone(),
    }


def claim_ready_task(durable_tasks, database, expected_repertoire_id: str):
    database.execute(
        "UPDATE background_tasks SET next_attempt_at='9999-12-31T00:00:00+00:00' "
        "WHERE kind='repertoire_priority' AND deduplication_key<>%s",
        (expected_repertoire_id,),
    )
    database.execute(
        "UPDATE background_tasks SET next_attempt_at=%s WHERE kind='repertoire_priority' "
        "AND deduplication_key=%s", (PAST, expected_repertoire_id),
    )
    database.commit()

    claimed = durable_tasks.claim_task(kind="repertoire_priority")
    assert claimed is not None and claimed["payload"]["repertoire_id"] == expected_repertoire_id, (
        f"Expected priority task for {expected_repertoire_id}; claimed={claimed}; "
        f"state={state(database, expected_repertoire_id)}"
    )
    return claimed


def publish_with_bound(durable_tasks, priority, database, repertoire_id: str,
                       expected_generation: int, maximum_claims: int) -> None:
    for _ in range(maximum_claims):
        claimed = claim_ready_task(durable_tasks, database, repertoire_id)
        assert priority.execute_repertoire_priority_slice(claimed)
        database.commit()
        publication = state(database, repertoire_id)["publication"]
        if publication and publication[0] == expected_generation:
            return
    raise AssertionError(
        f"Priority publication exceeded {maximum_claims} claims: "
        f"{state(database, repertoire_id)}"
    )


def priority_position_publication_invalidates_prepared_generation(
    durable_tasks, priority, priority_inputs, game_derivation, database,
) -> None:
    """Exercise the production publisher, not a synthetic epoch update."""

    repertoire_id = "position-publication"
    game_id = "position-publication-game"
    seed_repertoire(database, repertoire_id, 1, legacy_state="queued")
    database.execute(
        "UPDATE cards SET content_type='opening',moves_json=%s "
        "WHERE id='position-publication-card-0000'",
        (json.dumps(["d2d4", "d7d5"]),),
    )
    database.execute(
        "INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,start_fen,"
        "moves_json,created_at) VALUES('position-publication-line',%s,'line','white',%s,%s,%s)",
        (repertoire_id, START_FEN, json.dumps(["d2d4", "d7d5"]), PAST),
    )
    database.execute(
        "UPDATE background_tasks SET payload_json=%s,phase='queued' "
        "WHERE deduplication_key=%s AND kind='repertoire_priority'",
        (json.dumps({"repertoire_id": repertoire_id, "generation": 2}), repertoire_id),
    )
    database.execute(
        "DELETE FROM repertoire_card_priority_generations "
        "WHERE repertoire_id=%s AND generation=2", (repertoire_id,),
    )
    board_after_first_move = chess.Board()
    board_after_first_move.push_uci("d2d4")
    personal_fen_key = " ".join(board_after_first_move.fen().split()[:4])
    database.execute(
        "INSERT INTO imported_games(id,provider,username,played_at,speed,rated,color,result,"
        "start_fen,moves_json,analysis_version) "
        "VALUES(%s,'lichess','recovery',%s,'rapid',1,'white','1-0',%s,%s,1)",
        (game_id, PAST, START_FEN, json.dumps(["d2d4", "d7d5"])),
    )
    database.execute(
        "INSERT INTO game_position_occurrences_legacy(game_id,ply,fen_key,move_uci) "
        "VALUES(%s,1,%s,'g8f6')", (game_id, personal_fen_key),
    )
    database.execute(
        "INSERT INTO game_derivation_jobs(game_id,status,updated_at,derivation_version,"
        "completed_phases,published_position_version) "
        "VALUES(%s,'queued',%s,2,0,0)", (game_id, PAST),
    )
    database.execute(
        "INSERT INTO imported_games(id,provider,username,played_at,speed,rated,color,result,"
        "start_fen,moves_json,analysis_version) "
        "VALUES('position-publication-later','lichess','recovery',%s,'rapid',1,"
        "'white','1-0',%s,%s,1)",
        (PAST, START_FEN, json.dumps(["d2d4", "d7d5"])),
    )
    database.execute(
        "INSERT INTO game_position_occurrences_legacy(game_id,ply,fen_key,move_uci) "
        "VALUES('position-publication-later',1,%s,'d7d5')", (personal_fen_key,),
    )
    database.execute(
        "INSERT INTO background_tasks(id,kind,deduplication_key,generation,priority,state,"
        "phase,payload_json,next_attempt_at,created_at,updated_at) "
        "VALUES('position-publication-task','game_derivation_positions',%s,1,125,"
        "'queued','queued',%s,%s,%s,%s)",
        (game_id, json.dumps({"game_id": game_id, "derivation_version": 2, "cursor": 0}),
         PAST, PAST, PAST),
    )
    database.commit()

    epoch_before_unpublished_stage = database.execute(
        "SELECT version FROM priority_source_epoch WHERE id=1"
    ).fetchone()[0]
    database.execute(
        "INSERT INTO game_position_occurrences_staged("
        "game_id,derivation_version,ply,fen_key,move_uci) "
        "VALUES(%s,2,0,%s,'d2d4')",
        (game_id, " ".join(chess.Board().fen().split()[:4])),
    )
    database.commit()
    assert database.execute(
        "SELECT version FROM priority_source_epoch WHERE id=1"
    ).fetchone()[0] == epoch_before_unpublished_stage

    preparation_claim = claim_ready_task(durable_tasks, database, repertoire_id)
    assert priority.execute_repertoire_priority_slice(preparation_claim)
    database.commit()
    manifest = database.execute(
        "SELECT status,source_version FROM repertoire_priority_preparations "
        "WHERE repertoire_id=%s AND generation=2", (repertoire_id,),
    ).fetchone()
    assert manifest and manifest[0] == "ready", manifest
    prepared_score = database.execute(
        "SELECT priority_score FROM repertoire_priority_prepared_rows "
        "WHERE repertoire_id=%s AND generation=2", (repertoire_id,),
    ).fetchone()[0]
    epoch_before_publication = database.execute(
        "SELECT version FROM priority_source_epoch WHERE id=1"
    ).fetchone()[0]
    assert state(database, repertoire_id)["publication"] == (1,)

    def publish_position_index() -> None:
        epoch_before_index_slices = database.execute(
            "SELECT version FROM priority_source_epoch WHERE id=1"
        ).fetchone()[0]
        database.commit()
        for _ in range(4):
            position_claim = durable_tasks.claim_task(kind="game_derivation_positions")
            assert position_claim is not None, "position-index claim missing before publication"
            assert game_derivation.execute_game_position_index_slice(position_claim)
            database.commit()
            published_version = database.execute(
                "SELECT published_position_version FROM game_derivation_jobs WHERE game_id=%s",
                (game_id,),
            ).fetchone()[0]
            if published_version == 2:
                return
            assert database.execute(
                "SELECT version FROM priority_source_epoch WHERE id=1"
            ).fetchone()[0] == epoch_before_index_slices, (
                "unpublished position slice advanced the priority epoch",
                position_claim,
            )
            database.commit()
        raise AssertionError("position index did not publish within four claims")

    # Force two one-row pages and publish between them through the real index
    # handler. The preparation's post-load token check must reject this read.
    interleaved_repertoire_id = "position-interleaved"
    seed_repertoire(database, interleaved_repertoire_id, 1, legacy_state="queued")
    database.execute(
        "UPDATE cards SET content_type='opening',moves_json=%s "
        "WHERE id='position-interleaved-card-0000'",
        (json.dumps(["d2d4", "d7d5"]),),
    )
    database.execute(
        "INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,start_fen,"
        "moves_json,created_at) VALUES('position-interleaved-line',%s,'line','white',%s,%s,%s)",
        (interleaved_repertoire_id, START_FEN, json.dumps(["d2d4", "d7d5"]), PAST),
    )
    database.execute(
        "UPDATE background_tasks SET payload_json=%s,phase='queued' "
        "WHERE deduplication_key=%s AND kind='repertoire_priority'",
        (json.dumps({"repertoire_id": interleaved_repertoire_id, "generation": 2}),
         interleaved_repertoire_id),
    )
    database.execute(
        "DELETE FROM repertoire_card_priority_generations "
        "WHERE repertoire_id=%s AND generation=2", (interleaved_repertoire_id,),
    )
    database.commit()
    original_personal_read = priority_inputs._read_personal_evidence_rows
    original_page_size = priority_inputs.PERSONAL_EVIDENCE_ROWS_PER_READ
    personal_pages = 0

    def read_while_publishing(read_section, fen_keys, trained_color):
        @contextmanager
        def interleaved_read_section():
            nonlocal personal_pages
            with read_section() as read_database:
                yield read_database
            personal_pages += 1
            if personal_pages == 1:
                publish_position_index()

        return original_personal_read(interleaved_read_section, fen_keys, trained_color)

    priority_inputs.PERSONAL_EVIDENCE_ROWS_PER_READ = 1
    priority_inputs._read_personal_evidence_rows = read_while_publishing
    try:
        interleaved_claim = claim_ready_task(durable_tasks, database,
                                             interleaved_repertoire_id)
        assert priority.execute_repertoire_priority_slice(interleaved_claim)
        database.commit()
    finally:
        priority_inputs._read_personal_evidence_rows = original_personal_read
        priority_inputs.PERSONAL_EVIDENCE_ROWS_PER_READ = original_page_size
    assert personal_pages >= 2, personal_pages
    assert state(database, interleaved_repertoire_id)["job"] == (3, "queued")
    assert state(database, interleaved_repertoire_id)["publication"] == (1,)
    assert database.execute(
        "SELECT COUNT(*) FROM repertoire_priority_prepared_rows "
        "WHERE repertoire_id=%s AND generation=2", (interleaved_repertoire_id,),
    ).fetchone()[0] == 0

    visible_move = database.execute(
        "SELECT move_uci FROM game_position_occurrences WHERE game_id=%s AND ply=1",
        (game_id,),
    ).fetchone()[0]
    epoch_after_publication = database.execute(
        "SELECT version FROM priority_source_epoch WHERE id=1"
    ).fetchone()[0]
    assert visible_move == "d7d5", visible_move
    assert epoch_after_publication > epoch_before_publication, (
        "position publication changed visible evidence without fencing priorities; "
        f"before={epoch_before_publication}, after={epoch_after_publication}, "
        f"manifest={manifest}, priority={state(database, repertoire_id)}"
    )

    stale_claim = claim_ready_task(durable_tasks, database, repertoire_id)
    assert priority.execute_repertoire_priority_slice(stale_claim)
    database.commit()
    assert state(database, repertoire_id)["job"] == (3, "queued")
    assert state(database, repertoire_id)["publication"] == (1,)
    assert not priority.execute_repertoire_priority_slice(stale_claim)
    database.commit()
    assert state(database, repertoire_id)["job"] == (3, "queued")
    publish_with_bound(durable_tasks, priority, database, repertoire_id, 3, 4)
    published_score = database.execute(
        "SELECT priority_score FROM repertoire_card_priority_generations "
        "WHERE repertoire_id=%s AND generation=3", (repertoire_id,),
    ).fetchone()[0]
    assert published_score > prepared_score, (prepared_score, published_score)

    # A no-op publication and an aborted selector change do not commit a token.
    settled_epoch = database.execute(
        "SELECT version FROM priority_source_epoch WHERE id=1"
    ).fetchone()[0]
    database.execute(
        "UPDATE game_derivation_jobs SET published_position_version=2 WHERE game_id=%s",
        (game_id,),
    )
    database.commit()
    assert database.execute(
        "SELECT version FROM priority_source_epoch WHERE id=1"
    ).fetchone()[0] == settled_epoch
    database.execute(
        "UPDATE game_derivation_jobs SET published_position_version=0 WHERE game_id=%s",
        (game_id,),
    )
    assert database.execute(
        "SELECT version FROM priority_source_epoch WHERE id=1"
    ).fetchone()[0] == settled_epoch + 1
    database.rollback()
    assert database.execute(
        "SELECT published_position_version FROM game_derivation_jobs WHERE game_id=%s",
        (game_id,),
    ).fetchone()[0] == 2
    assert database.execute(
        "SELECT version FROM priority_source_epoch WHERE id=1"
    ).fetchone()[0] == settled_epoch

    database.execute(
        "UPDATE game_position_occurrences_staged SET move_uci='g8f6' "
        "WHERE game_id=%s AND derivation_version=2 AND ply=1", (game_id,),
    )
    database.commit()
    staged_visible_epoch = database.execute(
        "SELECT version FROM priority_source_epoch WHERE id=1"
    ).fetchone()[0]
    assert staged_visible_epoch == settled_epoch + 1
    database.execute(
        "UPDATE game_derivation_jobs SET published_position_version=0 WHERE game_id=%s",
        (game_id,),
    )
    database.commit()
    legacy_visible_epoch = database.execute(
        "SELECT version FROM priority_source_epoch WHERE id=1"
    ).fetchone()[0]
    assert legacy_visible_epoch == staged_visible_epoch + 1
    database.execute(
        "UPDATE game_position_occurrences_legacy SET move_uci='d7d5' "
        "WHERE game_id=%s AND ply=1", (game_id,),
    )
    database.commit()
    assert database.execute(
        "SELECT version FROM priority_source_epoch WHERE id=1"
    ).fetchone()[0] == legacy_visible_epoch + 1
    database.execute(
        "UPDATE game_derivation_jobs SET published_repertoire_version=1 WHERE game_id=%s",
        (game_id,),
    )
    database.commit()
    assert database.execute(
        "SELECT version FROM priority_source_epoch WHERE id=1"
    ).fetchone()[0] == legacy_visible_epoch + 2


def main() -> None:
    if os.getenv("TEMPO_TEST_INSTANCE") != "disposable":
        raise RuntimeError("Priority recovery rehearsal requires a disposable PostgreSQL instance")
    database_name = f"tempo_priority_recovery_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(ADMIN_DSN, autocommit=True) as administrator:
        administrator.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name)))
    rehearsal_dsn = f"postgresql://postgres@postgres:5432/{database_name}"
    try:
        with psycopg.connect(rehearsal_dsn) as database:
            for migration in sorted(MIGRATIONS.glob("[0-9][0-9][0-9]_*.sql")):
                if int(migration.name[:3]) > 20:
                    break
                database.execute(migration.read_text(), prepare=False)
                database.commit()
            database.execute("INSERT INTO settings(id) VALUES(1)")
            for repertoire_id, legacy_state in (
                ("legacy-queued", "queued"),
                ("legacy-retrying", "retrying"),
                ("legacy-expired", "leased"),
            ):
                seed_repertoire(database, repertoire_id, 1, legacy_state=legacy_state)
            seed_repertoire(database, "shuffled-retry", 17, legacy_state="queued")
            database.execute(
                "UPDATE background_tasks SET payload_json=%s WHERE deduplication_key='shuffled-retry'",
                (json.dumps({"repertoire_id": "shuffled-retry", "generation": 2}),),
            )
            for changed_repertoire_id in ("changed-source", "changed-scoring"):
                seed_repertoire(database, changed_repertoire_id, 1, legacy_state="queued")
                database.execute(
                    "UPDATE background_tasks SET payload_json=%s WHERE deduplication_key=%s",
                    (json.dumps({"repertoire_id": changed_repertoire_id, "generation": 2}),
                     changed_repertoire_id),
                )
            database.commit()
            migration21 = next(MIGRATIONS.glob("021_*.sql"))
            database.execute(migration21.read_text(), prepare=False)
            database.commit()
            seed_repertoire(database, "pre-fix-order", 1, legacy_state="queued")
            database.execute(
                "UPDATE background_tasks SET payload_json=%s WHERE deduplication_key='pre-fix-order'",
                (json.dumps({"repertoire_id": "pre-fix-order", "generation": 2, "cursor": 0}),),
            )
            database.execute(
                "INSERT INTO repertoire_priority_preparations("
                "repertoire_id,generation,source_version,scoring_version,calculated_at,status) "
                "VALUES('pre-fix-order',2,'0:0',2,%s,'preparing')", (PAST,),
            )
            database.execute(
                "INSERT INTO repertoire_priority_prepared_rows("
                "repertoire_id,generation,ordinal,card_id,completed_line_ids_json,"
                "completion_mass,frontier_decisions_json,frontier_reach,priority_score,evidence_json) "
                "VALUES('pre-fix-order',2,0,'pre-fix-order-card-0000','[]',0,'[]',0,0,'{}')"
            )
            database.commit()
            migration22 = next(MIGRATIONS.glob("022_*.sql"))
            database.execute(migration22.read_text(), prepare=False)
            database.commit()
            seed_repertoire(database, "pre-position-fence", 1, legacy_state="queued")
            database.execute(
                "UPDATE background_tasks SET payload_json=%s "
                "WHERE deduplication_key='pre-position-fence'",
                (json.dumps({"repertoire_id": "pre-position-fence", "generation": 2,
                             "cursor": 0}),),
            )
            shared_epoch_before_upgrade = database.execute(
                "SELECT version FROM priority_source_epoch WHERE id=1"
            ).fetchone()[0]
            repertoire_epoch_before_upgrade = database.execute(
                "SELECT version FROM priority_repertoire_source_epochs "
                "WHERE repertoire_id='pre-position-fence'"
            ).fetchone()
            old_source_version = (
                f"{shared_epoch_before_upgrade}:"
                f"{repertoire_epoch_before_upgrade[0] if repertoire_epoch_before_upgrade else 0}"
            )
            database.execute(
                "INSERT INTO repertoire_priority_preparations("
                "repertoire_id,generation,source_version,scoring_version,calculated_at,"
                "ordering_version,expected_count,status) "
                "VALUES('pre-position-fence',2,%s,2,%s,1,1,'ready')",
                (old_source_version, PAST),
            )
            database.execute(
                "INSERT INTO repertoire_priority_prepared_rows("
                "repertoire_id,generation,ordinal,card_id,completed_line_ids_json,"
                "completion_mass,frontier_decisions_json,frontier_reach,priority_score,evidence_json) "
                "VALUES('pre-position-fence',2,0,'pre-position-fence-card-0000','[]',0,'[]',0,0,'{}')"
            )
            database.commit()
        apply_migrations(rehearsal_dsn)
        apply_migrations(rehearsal_dsn)
        os.environ["TEMPO_DATABASE_WRITE_URL"] = rehearsal_dsn
        os.environ["TEMPO_DATABASE_READ_URL"] = rehearsal_dsn
        from app.services import (
            durable_tasks, introduction_priorities, postgres_game_derivation,
            postgres_priority,
        )
        from app import postgres_store
        with psycopg.connect(rehearsal_dsn) as observer:
            epoch_after_upgrade = observer.execute(
                "SELECT version FROM priority_source_epoch WHERE id=1"
            ).fetchone()[0]
            assert epoch_after_upgrade == shared_epoch_before_upgrade + 1, (
                shared_epoch_before_upgrade, epoch_after_upgrade,
            )
            pre_repair_claim = claim_ready_task(durable_tasks, observer, "pre-position-fence")
            assert postgres_priority.execute_repertoire_priority_slice(pre_repair_claim)
            observer.commit()
            assert state(observer, "pre-position-fence")["job"] == (3, "queued")
            assert state(observer, "pre-position-fence")["publication"] == (1,)
            assert not postgres_priority.execute_repertoire_priority_slice(pre_repair_claim)
            observer.commit()
            publish_with_bound(durable_tasks, postgres_priority, observer,
                               "pre-position-fence", 3, maximum_claims=4)
            for repertoire_id in (
                "legacy-queued", "legacy-retrying", "legacy-expired", "pre-fix-order",
            ):
                previous_publication = state(observer, repertoire_id)["publication"]
                assert previous_publication == (1,)
                old_delivery = claim_ready_task(durable_tasks, observer, repertoire_id)
                assert postgres_priority.execute_repertoire_priority_slice(old_delivery)
                observer.commit()
                assert state(observer, repertoire_id)["publication"] == (1,)
                assert state(observer, repertoire_id)["job"] == (3, "queued")
                assert not postgres_priority.execute_repertoire_priority_slice(old_delivery)
                observer.commit()
                assert state(observer, repertoire_id)["job"] == (3, "queued")
                publish_with_bound(durable_tasks, postgres_priority, observer,
                                   repertoire_id, 3, maximum_claims=4)
                published_cards = observer.execute(
                    "SELECT card_id FROM repertoire_card_priority_generations "
                    "WHERE repertoire_id=%s AND generation=3", (repertoire_id,),
                ).fetchall()
                assert len(published_cards) == 1
                assert state(observer, repertoire_id)["publication"] == (3,)
                assert observer.execute(
                    "SELECT COUNT(*) FROM repertoire_card_priority_generations "
                    "WHERE repertoire_id=%s AND generation=1", (repertoire_id,),
                ).fetchone()[0] == 1
                observer.commit()
            original_write_batch = postgres_store.PostgresConnection.executemany
            original_calculator = postgres_priority.calculate_priority_records
            preparation_batches = 0
            calculated_snapshots = []

            def fail_second_preparation_batch(database, statement, parameter_rows):
                nonlocal preparation_batches
                if "INSERT INTO repertoire_priority_prepared_rows" in statement:
                    preparation_batches += 1
                    if preparation_batches == 2:
                        raise RuntimeError("injected second-batch crash")
                return original_write_batch(database, statement, parameter_rows)

            def reorder_second_calculation(calculation_input):
                records = original_calculator(calculation_input)
                calculated_snapshots.append({record.card_id: record for record in records})
                return list(reversed(records)) if len(calculated_snapshots) == 2 else records

            postgres_store.PostgresConnection.executemany = fail_second_preparation_batch
            postgres_priority.calculate_priority_records = reorder_second_calculation
            try:
                first_claim = claim_ready_task(durable_tasks, observer, "shuffled-retry")
                try:
                    postgres_priority.execute_repertoire_priority_slice(first_claim)
                except RuntimeError as error:
                    assert str(error) == "injected second-batch crash"
                else:
                    raise AssertionError("Second preparation batch did not fail")
                assert observer.execute(
                    "SELECT COUNT(*) FROM repertoire_priority_prepared_rows "
                    "WHERE repertoire_id='shuffled-retry' AND generation=2"
                ).fetchone()[0] == 16
                assert state(observer, "shuffled-retry")["publication"] == (1,)
                observer.commit()
                failure = RuntimeError("injected second-batch crash")
                assert durable_tasks.fail_task(
                    first_claim["id"], first_claim["generation"],
                    first_claim["lease_token"], failure,
                )["state"] == "retrying"
                assert not postgres_priority.execute_repertoire_priority_slice(first_claim)
                observer.commit()
            finally:
                postgres_store.PostgresConnection.executemany = original_write_batch
            restarted_claim = claim_ready_task(durable_tasks, observer, "shuffled-retry")
            assert restarted_claim["lease_token"] != first_claim["lease_token"]
            assert postgres_priority.execute_repertoire_priority_slice(restarted_claim)
            observer.commit()
            postgres_priority.calculate_priority_records = original_calculator
            assert len(calculated_snapshots) == 2
            prepared = observer.execute(
                "SELECT ordinal,card_id,priority_score,evidence_json,completed_line_ids_json,"
                "frontier_decisions_json FROM repertoire_priority_prepared_rows "
                "WHERE repertoire_id='shuffled-retry' AND generation=2 ORDER BY ordinal"
            ).fetchall()
            assert len(prepared) == 17
            assert [row[0] for row in prepared] == list(range(17))
            assert [row[1] for row in prepared] == sorted(calculated_snapshots[0])
            for _, card_id, priority_score, evidence, completed_lines, frontiers in prepared:
                expected = calculated_snapshots[0][card_id]
                assert (priority_score, evidence, completed_lines, frontiers) == (
                    expected.priority_score, expected.evidence_json,
                    expected.completed_line_ids_json, expected.frontier_decisions_json,
                )
            assert state(observer, "shuffled-retry")["publication"] == (1,)
            observer.commit()
            publish_with_bound(durable_tasks, postgres_priority, observer,
                               "shuffled-retry", 2, maximum_claims=4)
            assert observer.execute(
                "SELECT COUNT(*) FROM repertoire_card_priority_generations "
                "WHERE repertoire_id='shuffled-retry' AND generation=2"
            ).fetchone()[0] == 17
            observer.commit()
            for changed_repertoire_id in ("changed-source", "changed-scoring"):
                preparation_claim = claim_ready_task(durable_tasks, observer,
                                                      changed_repertoire_id)
                assert postgres_priority.execute_repertoire_priority_slice(preparation_claim)
                observer.commit()
                assert state(observer, changed_repertoire_id)["publication"] == (1,)
                if changed_repertoire_id == "changed-source":
                    observer.execute("UPDATE priority_source_epoch SET version=version+1 WHERE id=1")
                else:
                    observer.execute(
                        "UPDATE repertoire_priority_preparations SET scoring_version=1 "
                        "WHERE repertoire_id=%s AND generation=2",
                        (changed_repertoire_id,),
                    )
                observer.commit()
                stale_claim = claim_ready_task(durable_tasks, observer,
                                               changed_repertoire_id)
                assert postgres_priority.execute_repertoire_priority_slice(stale_claim)
                observer.commit()
                assert state(observer, changed_repertoire_id)["publication"] == (1,)
                assert state(observer, changed_repertoire_id)["job"] == (3, "queued")
                assert not postgres_priority.execute_repertoire_priority_slice(stale_claim)
                observer.commit()
                publish_with_bound(durable_tasks, postgres_priority, observer,
                                   changed_repertoire_id, 3, maximum_claims=4)
            priority_position_publication_invalidates_prepared_generation(
                durable_tasks, postgres_priority, introduction_priorities,
                postgres_game_derivation, observer,
            )
            from scripts.rehearse_integrity_repair import guided_repair_postgres_contention_restart_replay_and_publication
            guided_repair_postgres_contention_restart_replay_and_publication(observer)
        print("PASS schema 20/21 priority recovery across queued, retrying, expired, "
              "and old-ordering work; repeated migration and stale delivery are inert")
        print("PASS PostgreSQL second-batch crash, reconstructed task claim, shuffled retry, "
              "unique ordinals, and atomic publication")
        print("PASS PostgreSQL source/scoring invalidation retains old publication "
              "and publishes a current follow-up generation")
        print("PASS priority_position_publication_invalidates_prepared_generation")
    finally:
        with psycopg.connect(ADMIN_DSN, autocommit=True) as administrator:
            administrator.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(
                sql.Identifier(database_name)
            ))


if __name__ == "__main__":
    main()
