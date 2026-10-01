"""Regressions for durable PostgreSQL priority preparation and publication."""

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace

import chess

from app.services import introduction_priorities, postgres_priority, priority_retention


def test_priority_miss_source_triggers_target_tables_and_publication():
    migration = (Path(__file__).parents[1] / "migrations"
                 / "021_priority_preparations.sql").read_text()
    assert "ON repertoire_decision_events\n" not in migration
    assert "ON repertoire_decision_events_legacy\n" in migration
    assert "ON repertoire_decision_events_staged\n" in migration
    assert "ON game_derivation_jobs\n" in migration


def test_priority_frozen_shared_transposition_evidence_is_order_independent():
    starting_position = chess.STARTING_FEN
    first_route = ["d2d4", "d7d5", "g1f3", "g8f6", "c2c4"]
    second_route = ["d2d4", "g8f6", "g1f3", "d7d5", "c2c4"]
    board = chess.Board(starting_position)
    for move_uci in first_route[:4]:
        board.push_uci(move_uci)
    transposed_position = board.fen()
    lines = tuple(introduction_priorities._maximal_intended_lines([
        {"id": "first", "start_fen": starting_position, "trained_color": "white",
         "moves_json": json.dumps(first_route)},
        {"id": "second", "start_fen": starting_position, "trained_color": "white",
         "moves_json": json.dumps(second_route)},
    ]))
    cards = tuple({
        "id": card_id, "start_fen": start_fen, "moves_json": json.dumps(moves),
        "state": "new", "introduced_at": None,
    } for card_id, start_fen, moves in (
        ("shared", starting_position, ["d2d4"]),
        ("first-route", starting_position, first_route[:3]),
        ("second-route", starting_position, second_route[:3]),
        ("transposed", transposed_position, ["c2c4"]),
    ))
    frozen_time = datetime(2026, 10, 1, tzinfo=timezone.utc)
    personal_evidence = introduction_priorities._personal_evidence_from_rows([
        {"fen_key": introduction_priorities._board_fen_key(chess.Board(starting_position)),
         "move_uci": "d7d5", "played_at": "2026-09-01T00:00:00+00:00"},
    ], calculated_at=frozen_time)

    def calculate(ordered_cards):
        calculation_input = introduction_priorities.PriorityCalculationInput(
            "rep", lines, ordered_cards, {}, personal_evidence, 15, 0.0005,
        )
        return {record.card_id: record
                for record in introduction_priorities.calculate_priority_records(
                    calculation_input)}

    first = calculate(cards)
    reordered = calculate(tuple(reversed(cards)))
    assert first == reordered
    assert json.loads(first["shared"].completed_line_ids_json) == ["first", "second"]
    assert len(json.loads(first["transposed"].frontier_decisions_json)) > 0
    assert first["transposed"].priority_score > 0


class Result:
    def __init__(self, row=None, rows=()):
        self.row = row
        self.rows = rows

    def fetchone(self):
        return self.row

    def __iter__(self):
        return iter(self.rows)


class PriorityDatabase:
    def __init__(self):
        self.version = 0
        self.repertoire_version = 0
        self.generation = 3
        self.status = "queued"
        self.manifest = None
        self.prepared = {}
        self.staged = {}
        self.publication = None
        self.fail_prepared_batch = False
        self.fail_prepared_batch_number = None
        self.prepared_batch_number = 0
        self.record_order = None
        self.max_write_batch = 0
        self.connection_open = False

    def execute(self, statement, parameters=()):
        if "FROM background_tasks" in statement:
            return Result({"generation": 1, "lease_token": "lease", "state": "leased"})
        if "SELECT version FROM priority_source_epoch" in statement:
            return Result((self.version,))
        if "INSERT INTO priority_repertoire_source_epochs" in statement:
            return Result()
        if "SELECT version FROM priority_repertoire_source_epochs" in statement:
            return Result((self.repertoire_version,))
        if "SELECT generation,status FROM repertoire_priority_jobs" in statement:
            return Result({"generation": self.generation, "status": self.status})
        if "SELECT * FROM repertoire_priority_preparations" in statement:
            return Result(self.manifest)
        if "INSERT INTO repertoire_priority_preparations" in statement:
            self.manifest = {
                "source_version": parameters[2], "scoring_version": parameters[3],
                "calculated_at": parameters[4], "status": "preparing",
                "expected_count": None, "ordering_version": 1,
            }
            return Result()
        if "UPDATE repertoire_priority_preparations" in statement:
            self.manifest.update(expected_count=parameters[0], status="ready")
            return Result()
        if "SELECT * FROM repertoire_priority_prepared_rows" in statement:
            cursor, limit = parameters[2:]
            return Result(rows=[
                self.prepared[ordinal] for ordinal in sorted(self.prepared)
                if ordinal >= cursor
            ][:limit])
        if "COUNT(*) FROM repertoire_priority_prepared_rows" in statement:
            return Result((len(self.prepared),))
        if "COUNT(*) FROM repertoire_card_priority_generations" in statement:
            return Result((len(self.staged),))
        if "UPDATE repertoire_priority_jobs SET status='running'" in statement:
            self.status = "running"
            return Result()
        if "UPDATE repertoire_priority_jobs SET status='complete'" in statement:
            self.status = "complete"
            return Result()
        if "INSERT INTO repertoire_priority_publications" in statement:
            self.publication = parameters[1]
            return Result()
        raise AssertionError(statement)

    def executemany(self, statement, parameter_rows):
        self.max_write_batch = max(self.max_write_batch, len(parameter_rows))
        if "INSERT INTO repertoire_priority_prepared_rows" in statement:
            self.prepared_batch_number += 1
            if self.prepared_batch_number == self.fail_prepared_batch_number:
                self.fail_prepared_batch_number = None
                raise RuntimeError("simulated crash after a committed batch")
            if self.fail_prepared_batch:
                self.fail_prepared_batch = False
                raise RuntimeError("simulated crash before checkpoint")
            for row in parameter_rows:
                if any(existing["card_id"] == row[3] and ordinal != row[2]
                       for ordinal, existing in self.prepared.items()):
                    raise RuntimeError("duplicate prepared card at another ordinal")
                self.prepared[row[2]] = dict(zip((
                    "repertoire_id", "generation", "ordinal", "card_id",
                    "completed_line_ids_json", "completion_mass",
                    "frontier_decisions_json", "frontier_reach",
                    "priority_score", "evidence_json",
                ), row))
        elif "INSERT INTO repertoire_card_priority_generations" in statement:
            for row in parameter_rows:
                self.staged[row[2]] = row
        else:
            raise AssertionError(statement)


def _harness(monkeypatch, record_count):
    database = PriorityDatabase()
    advanced = []
    calculations = []
    input_reads = []
    queued = []
    lease = {"current": True}

    @contextmanager
    def connection(*, background):
        assert background
        assert not database.connection_open
        database.connection_open = True
        try:
            yield database
        finally:
            database.connection_open = False

    def calculate(_calculation_input):
        assert not database.connection_open
        calculations.append(True)
        return [
            SimpleNamespace(
                card_id=f"card-{ordinal:04d}", priority_score=float(ordinal),
                evidence_json=f'{{"card":{ordinal}}}',
                completed_line_ids_json=f'["line-{ordinal}"]',
                frontier_decisions_json=f'["frontier-{ordinal}"]',
                completion_mass=float(ordinal) / 10,
                frontier_reach=float(ordinal) / 100,
            )
            for ordinal in (database.record_order if database.record_order is not None
                            else range(record_count))
        ]

    def advance(_database, _task, *, next_phase, next_payload):
        advanced.append((next_phase, next_payload))
        return True

    monkeypatch.setattr(postgres_priority, "connection", connection)
    monkeypatch.setattr(postgres_priority, "lock_current_slice",
                        lambda *_args: lease["current"])
    monkeypatch.setattr(postgres_priority, "_load_priority_calculation_input",
                        lambda *_args, **_kwargs: input_reads.append(True) or object())
    monkeypatch.setattr(postgres_priority, "calculate_priority_records", calculate)
    monkeypatch.setattr(postgres_priority, "advance_task_slice_in_transaction", advance)
    monkeypatch.setattr(postgres_priority, "complete_task_slice_in_transaction",
                        lambda *_args: lease["current"])
    monkeypatch.setattr(postgres_priority, "enqueue_priority_refresh_in_transaction",
                        lambda *_args: queued.append(True))
    monkeypatch.setattr(postgres_priority, "enqueue_compact_postgres_task_in_transaction",
                        lambda *_args, **_kwargs: None)
    task = {
        "id": "task", "generation": 1, "lease_token": "lease",
        "payload": {"repertoire_id": "rep", "generation": 3},
    }
    return database, task, advanced, calculations, input_reads, queued, lease


def test_priority_1000_records_calculate_once_across_resumable_slices(monkeypatch):
    database, task, advanced, calculations, input_reads, _, lease = _harness(
        monkeypatch, 1000,
    )
    assert postgres_priority.execute_repertoire_priority_slice(task)
    assert database.manifest["status"] == "ready"
    assert len(database.prepared) == 1000
    assert database.max_write_batch == 16
    assert len(calculations) == len(input_reads) == 1

    # A stale delivery after preparation cannot stage or recalculate.
    lease["current"] = False
    task["payload"] = advanced[-1][1]
    assert not postgres_priority.execute_repertoire_priority_slice(task)
    lease["current"] = True

    # Recreate the task object as a restarted worker would do.
    for _ in range(65):
        assert postgres_priority.execute_repertoire_priority_slice(task)
        if database.publication is not None:
            break
        task = {**task, "payload": advanced[-1][1]}
    assert database.publication == 3
    assert len(database.staged) == 1000
    assert len(calculations) == len(input_reads) == 1


def test_priority_stale_lease_skips_expensive_preparation(monkeypatch):
    database, task, _, calculations, input_reads, _, lease = _harness(monkeypatch, 1000)
    lease["current"] = False
    assert not postgres_priority.execute_repertoire_priority_slice(task)
    assert database.manifest is None
    assert calculations == input_reads == []


def test_priority_crash_before_checkpoint_retries_without_duplicate_rows(monkeypatch):
    database, task, advanced, calculations, _, _, _ = _harness(monkeypatch, 33)
    database.fail_prepared_batch = True
    try:
        postgres_priority.execute_repertoire_priority_slice(task)
    except RuntimeError as error:
        assert "simulated crash" in str(error)
    else:
        raise AssertionError("Expected preparation failure")
    assert database.manifest["status"] == "preparing"
    assert postgres_priority.execute_repertoire_priority_slice(task)
    assert len(database.prepared) == 33
    assert len(calculations) == 2
    task["payload"] = advanced[-1][1]
    while database.publication is None:
        assert postgres_priority.execute_repertoire_priority_slice(task)
        task["payload"] = advanced[-1][1] if database.publication is None else task["payload"]
    assert len(database.staged) == 33


def test_priority_legacy_nonzero_cursor_requests_one_fenced_replacement(monkeypatch):
    database, task, _, calculations, input_reads, queued, lease = _harness(monkeypatch, 2)
    task["payload"].update(cursor=1, source_signature="schema20")
    assert postgres_priority.execute_repertoire_priority_slice(task)
    assert queued == [True]
    assert calculations == input_reads == []
    assert database.publication is None
    lease["current"] = False
    assert not postgres_priority.execute_repertoire_priority_slice(task)
    assert queued == [True]


def test_priority_old_ordering_manifest_requests_replacement_before_writing(monkeypatch):
    database, task, _, calculations, input_reads, queued, _ = _harness(monkeypatch, 33)
    database.manifest = {
        "source_version": "0:0", "scoring_version": 2,
        "calculated_at": "2026-10-01T00:00:00+00:00", "status": "preparing",
        "expected_count": None, "ordering_version": None,
    }
    database.prepared[0] = {"card_id": "card-0032", "ordinal": 0}
    assert postgres_priority.execute_repertoire_priority_slice(task)
    assert queued == [True]
    assert database.prepared == {0: {"card_id": "card-0032", "ordinal": 0}}
    assert calculations == input_reads == []


def test_priority_cursor_zero_incomplete_preparation_recovers_with_or_without_legacy_signature(monkeypatch):
    for legacy_signature in (False, True):
        database, task, advanced, calculations, _, queued, _ = _harness(monkeypatch, 33)
        task["payload"]["cursor"] = 0
        database.fail_prepared_batch_number = 2
        try:
            postgres_priority.execute_repertoire_priority_slice(task)
        except RuntimeError as error:
            assert "simulated crash" in str(error)
        else:
            raise AssertionError("Expected crash after the first committed batch")
        assert len(database.prepared) == 16
        assert database.manifest["status"] == "preparing"
        if legacy_signature:
            task["payload"]["source_signature"] = "schema20"
        assert postgres_priority.execute_repertoire_priority_slice(task)
        if legacy_signature:
            assert queued == [True]
            assert len(calculations) == 1
        else:
            assert database.manifest["status"] == "ready"
            assert len(calculations) == 2
            assert advanced


def test_priority_shuffled_retry_keeps_unique_cards_and_contiguous_ordinals(monkeypatch):
    database, task, advanced, calculations, _, _, _ = _harness(monkeypatch, 33)
    database.fail_prepared_batch_number = 2
    try:
        postgres_priority.execute_repertoire_priority_slice(task)
    except RuntimeError as error:
        assert "simulated crash" in str(error)
    else:
        raise AssertionError("Expected crash after the first committed batch")
    assert len(database.prepared) == 16
    database.record_order = list(reversed(range(33)))
    assert postgres_priority.execute_repertoire_priority_slice(task)
    assert database.manifest["status"] == "ready"
    assert sorted(database.prepared) == list(range(33))
    assert [database.prepared[ordinal]["card_id"] for ordinal in range(33)] == [
        f"card-{ordinal:04d}" for ordinal in range(33)
    ]
    assert len({row["card_id"] for row in database.prepared.values()}) == 33
    for ordinal, row in database.prepared.items():
        assert row["priority_score"] == float(ordinal)
        assert row["evidence_json"] == f'{{"card":{ordinal}}}'
        assert row["completed_line_ids_json"] == f'["line-{ordinal}"]'
        assert row["frontier_decisions_json"] == f'["frontier-{ordinal}"]'
    task = {**task, "payload": advanced[-1][1]}
    for attempt in range(4):
        assert postgres_priority.execute_repertoire_priority_slice(task)
        if database.publication is not None:
            break
        task = {**task, "payload": advanced[-1][1]}
    else:
        raise AssertionError("Publication did not complete within four slices")
    assert database.publication == 3
    assert len(calculations) == 2


def test_priority_source_change_withholds_publication_and_requests_followup(monkeypatch):
    database, task, advanced, calculations, _, queued, _ = _harness(monkeypatch, 1)
    assert postgres_priority.execute_repertoire_priority_slice(task)
    task["payload"] = advanced[-1][1]
    assert postgres_priority.execute_repertoire_priority_slice(task)
    task["payload"] = advanced[-1][1]
    database.version += 1
    assert postgres_priority.execute_repertoire_priority_slice(task)
    assert database.publication is None
    assert queued == [True]
    assert len(calculations) == 1


def test_priority_repertoire_change_withholds_publication(monkeypatch):
    database, task, advanced, _, _, queued, _ = _harness(monkeypatch, 1)
    assert postgres_priority.execute_repertoire_priority_slice(task)
    task["payload"] = advanced[-1][1]
    database.repertoire_version += 1
    assert postgres_priority.execute_repertoire_priority_slice(task)
    assert queued == [True]
    assert database.publication is None


def test_priority_empty_repertoire_publishes_complete_generation(monkeypatch):
    database, task, advanced, calculations, _, _, _ = _harness(monkeypatch, 0)
    assert postgres_priority.execute_repertoire_priority_slice(task)
    task["payload"] = advanced[-1][1]
    assert postgres_priority.execute_repertoire_priority_slice(task)
    assert database.publication == 3
    assert len(calculations) == 1


def test_priority_missing_staged_row_withholds_publication(monkeypatch):
    database, task, advanced, _, _, _, _ = _harness(monkeypatch, 1)
    assert postgres_priority.execute_repertoire_priority_slice(task)
    task["payload"] = {**advanced[-1][1], "cursor": 1}
    try:
        postgres_priority.execute_repertoire_priority_slice(task)
    except RuntimeError as error:
        assert "incomplete" in str(error)
    else:
        raise AssertionError("Expected count mismatch")
    assert database.publication is None


def test_priority_scoring_version_change_requests_followup(monkeypatch):
    database, task, advanced, _, _, queued, _ = _harness(monkeypatch, 1)
    assert postgres_priority.execute_repertoire_priority_slice(task)
    database.manifest["scoring_version"] -= 1
    task["payload"] = advanced[-1][1]
    assert postgres_priority.execute_repertoire_priority_slice(task)
    assert queued == [True]
    assert database.publication is None


def test_priority_retention_reclaims_abandoned_preparations_but_keeps_active(monkeypatch):
    prepared = {(1, 0), (2, 0), (3, 0)}
    manifests = {1, 2, 3}

    class RetentionDatabase:
        def execute(self, statement, parameters=()):
            if "FROM repertoire_priority_publications" in statement:
                return Result({"generation": 2})
            if "FROM repertoire_priority_jobs" in statement:
                return Result({"generation": 3, "status": "running"})
            if "SELECT generation,ordinal FROM repertoire_priority_prepared_rows" in statement:
                return Result(rows=[
                    {"generation": generation, "ordinal": ordinal}
                    for generation, ordinal in sorted(prepared)
                    if generation != parameters[1]
                ][:parameters[2]])
            if "SELECT manifest.generation FROM repertoire_priority_preparations" in statement:
                return Result(rows=[
                    {"generation": generation} for generation in sorted(manifests)
                    if generation != parameters[1]
                    and not any(item[0] == generation for item in prepared)
                ][:parameters[2]])
            if "SELECT generation,card_id FROM repertoire_card_priority_generations" in statement:
                return Result(rows=[])
            if "SELECT 1 FROM repertoire_card_priority_generations" in statement:
                return Result()
            if "SELECT 1 FROM repertoire_priority_prepared_rows" in statement:
                return Result(next((
                    (1,) for generation, _ in prepared if generation != parameters[1]
                ), None))
            if "SELECT 1 FROM repertoire_priority_preparations" in statement:
                return Result(next((
                    (1,) for generation in manifests if generation != parameters[1]
                ), None))
            raise AssertionError(statement)

        def executemany(self, statement, parameter_rows):
            if "DELETE FROM repertoire_priority_prepared_rows" in statement:
                for _, generation, ordinal in parameter_rows:
                    prepared.remove((generation, ordinal))
            elif "DELETE FROM repertoire_priority_preparations" in statement:
                for _, generation in parameter_rows:
                    manifests.remove(generation)
            elif "DELETE FROM repertoire_card_priority_generations" not in statement:
                raise AssertionError(statement)

    @contextmanager
    def connection(*, background):
        assert background
        yield RetentionDatabase()

    monkeypatch.setattr(priority_retention, "connection", connection)
    monkeypatch.setattr(priority_retention.postgres_store, "configured", lambda: True)
    monkeypatch.setattr(priority_retention, "lock_current_slice", lambda *_args: True)
    monkeypatch.setattr(priority_retention.activity_gate, "wait_for_foreground",
                        lambda: None)
    assert not priority_retention.execute_priority_retention_slice({
        "id": "retention", "generation": 1, "lease_token": "lease",
        "payload": {"repertoire_id": "rep"},
    })
    assert prepared == {(3, 0)}
    assert manifests == {3}
