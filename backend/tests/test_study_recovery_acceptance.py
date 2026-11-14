"""Owning acceptance-harness checks; no database or Docker allocation."""

import chess
import pytest
from fixtures.study_recovery_content import Shape, publication
from native_study_fixture import (
    postgres_fixture_profile,
    study_recovery_assembly_allocations,
)


def test_recovery_profile_keeps_default_native_allocation_and_is_explicit():
    assert postgres_fixture_profile("default") == dict(memory_mib=384, storage="tmpfs")
    assert postgres_fixture_profile("study-recovery") == dict(
        memory_mib=1024, storage="owned-volume"
    )
    assert postgres_fixture_profile("study-recovery-assembly") == dict(
        memory_mib=448, storage="owned-volume"
    )
    for unsupported in (None, "", "live", "1024", "unlimited"):
        with pytest.raises(ValueError, match="unknown_owned_postgres_fixture_profile"):
            postgres_fixture_profile(unsupported)


def test_recovery_publication_is_legal_complete_short_card_scope_with_real_standard_target():
    shape = Shape("white", 12, 216, 33, 48, 84, 216, 7)
    generated = publication(shape, "rep", "fixture")
    assert generated["generated"]["routes"] == shape.routes
    assert generated["generated"]["authored_plies"] == shape.authored_plies
    assert generated["generated"]["graphs"] == shape.graphs
    assert generated["target"]["starting_fen"] == chess.STARTING_FEN
    assert generated["target"]["occurrence_ply"] == 9
    own = {}
    import json

    for row in generated["lines"]:
        board = chess.Board(row["start_fen"])
        for uci in json.loads(row["moves_json"]):
            move = chess.Move.from_uci(uci)
            assert move in board.legal_moves
            if board.turn:
                key = " ".join(board.fen(en_passant="legal").split()[:4])
                assert own.setdefault(key, uci) == uci
            board.push(move)
    cards = {row["id"]: row for row in generated["cards"]}
    for graph in generated["graphs"]:
        assert graph["moves_json"] == cards[graph["card_id"]]["moves_json"]
        board = chess.Board(graph["starting_fen"])
        for uci in json.loads(graph["moves_json"]):
            board.push_uci(uci)


def test_recovery_standard_target_is_actionable_across_complete_distinct_repertoire_scope():
    """A real target survives complete scope; competing physical owners stay distinct."""
    import json

    from app.games.feedback.first_boundary import select_first_boundary
    from tempo_game_review_contracts import CanonicalGame
    from test_product_snapshot import repertoire_facts

    from app.contracts.product_v02.repertoire import IntroductionGameScope
    from app.repertoire import PreparedPublishedRepertoire

    snapshots, ids, physical, target = [], [], {}, None
    colors = (
        "white",
        "white",
        "black",
        "white",
        "white",
        "white",
        "white",
        "black",
        "white",
        "black",
    )
    for ordinal, color in enumerate(colors):
        repertoire = f"rep-{ordinal}"
        shape = Shape(color, 12, 216, 33 if color == "white" else 32, 48, 84, 216, 7)
        content = publication(shape, repertoire, "fixture", ordinal=ordinal)
        template = repertoire_facts()
        facts = dict(
            repertoire=template["repertoire"] | dict(id=repertoire, graph_generation=1),
            lines=content["lines"],
            cards=[
                template["cards"][0] | row | dict(revision=1)
                for row in content["cards"]
            ],
            memberships=[
                dict(
                    repertoire_id=repertoire,
                    card_id=row["id"],
                    canonical_route_source=1,
                )
                for row in content["cards"]
            ],
            graphs=[row | dict(generation=1) for row in content["graphs"]],
        )
        for row in content["cards"]:
            geometry = (row["start_fen"], row["moves_json"], row["trained_color"])
            assert physical.setdefault(row["id"], geometry) == geometry

        snapshots.append(PreparedPublishedRepertoire(facts).snapshot)
        ids.append(repertoire)
        target = target or content["target"]
        assert (
            len(facts["lines"]) == shape.routes and len(facts["graphs"]) == shape.graphs
        )
        assert (
            sum(len(json.loads(row["moves_json"])) for row in facts["lines"])
            == shape.authored_plies
        )
    game = CanonicalGame(
        workspace_id="fixture",
        game_id="standard-root",
        game_revision=1,
        source_id="lichess",
        source_revision=1,
        source_digest="d" * 64,
        starting_fen=target["starting_fen"],
        moves=[*target["prefix"], target["wrong"]],
        learner_color="white",
        played_at="2026-10-29T00:00:00Z",
    )
    source = IntroductionGameScope(
        provider="lichess",
        game_id=game.game_id,
        source_revision=1,
        source_digest=game.source_digest,
    )
    selected = select_first_boundary(
        game, snapshots=snapshots, source=source, selected_repertoire_ids=ids
    )
    assert selected.status == "own_actionable"
    assert selected.ply == 9 and selected.expected_uci == (target["expected"],)
    assert selected.decision.presentation_id == target["card_id"]
    assert selected.decision.repertoire_id == "rep-0"
    assert {item.repertoire_id for item in selected.selected_scope} == {
        f"rep-{index}" for index, color in enumerate(colors) if color == "white"
    }


def test_recovery_graph_uses_real_owner_geometry_and_contiguous_frontier_metadata():
    import json

    from app.services.opening_frontier import (
        OpeningFrontierCard,
        OpeningFrontierPublication,
        OpeningFrontierReview,
        OpeningFrontierRoute,
        OpeningFrontierSnapshot,
        project_opening_frontier,
    )

    from app.contracts.opening_graph import GraphStep

    for color in ("white", "black"):
        shape = Shape(color, 12, 216, 33 if color == "white" else 32, 48, 84, 216, 7)
        content = publication(shape, "rep", "fixture")
        owner = OpeningFrontierPublication("rep", 1, "fixture")
        routes = []
        for line in content["lines"]:
            rows = [row for row in content["graphs"] if row["line_id"] == line["id"]]
            steps = tuple(
                GraphStep(
                    repertoire_id="rep",
                    line_id=line["id"],
                    decision_index=row["decision_index"],
                    segment_kind=row["segment_kind"],
                    first_decision_index=row["first_decision_index"],
                    last_decision_index=row["last_decision_index"],
                    decision_fen_keys=tuple(json.loads(row["decision_fen_keys_json"])),
                    card_id=row["card_id"],
                    parent_card_id=row["parent_card_id"],
                    decision_fen_key=row["decision_fen_key"],
                    starting_fen=row["starting_fen"],
                    moves=tuple(json.loads(row["moves_json"])),
                    trained_color=color,
                )
                for row in rows
            )
            routes.append(OpeningFrontierRoute(owner, line["id"], steps))
        snapshot = OpeningFrontierSnapshot(
            "fixture",
            (owner,),
            tuple(routes),
            tuple(
                OpeningFrontierCard(row["id"], ("rep",), introduced_at="2026-08-01")
                for row in content["cards"]
            ),
            tuple(
                OpeningFrontierReview("review-" + row["id"], row["id"])
                for row in content["cards"]
            ),
        )
        frontier = project_opening_frontier(snapshot)
        assert len(frontier.cards) == len(content["cards"])
        assert all(
            not route.blocked_reasons and not route.missing_prerequisite_card_ids
            for card in frontier.cards
            for route in card.routes
        )


def test_recovery_observed_shapes_keep_exact_route_graph_counts_and_explicit_payload_deltas():
    from fixtures.study_recovery_content import (
        OBSERVED,
        _prefix_decisions,
        _route_lengths,
    )

    authored = graphs = graph_plies = 0
    for ordinal, shape in enumerate(OBSERVED):
        custom = ordinal == 0
        lengths = _route_lengths(shape, existing_custom=custom)
        prefixes = _prefix_decisions(shape, lengths, existing_custom=custom)
        own = sum((length + (shape.color == "white")) // 2 for length in lengths)
        assert len(lengths) + custom == shape.routes
        assert sum(lengths) + custom == shape.authored_plies
        assert max(lengths) == shape.max_route_plies
        assert own - sum(prefixes) + len(lengths) + custom == shape.graphs
        payload = (
            sum(
                2 * ((length + (shape.color == "white")) // 2)
                - (shape.color == "white")
                for length in lengths
            )
            + custom
        )
        assert payload - shape.graph_plies == (-1 if ordinal in (4, 5) else 0)
        oversize = [
            value
            for value in prefixes
            if 2 * value - (shape.color == "white") > shape.max_card_plies
        ]
        assert len(oversize) == (2 if custom else 0)
        assert max(
            2 * value - (shape.color == "white") for value in prefixes
        ) == shape.max_card_plies + (2 if custom else 0)
        authored += sum(lengths) + custom
        graphs += own - sum(prefixes) + len(lengths) + custom
        graph_plies += payload
    assert (authored, graphs, graph_plies) == (242235, 94929, 242138)


def test_recovery_assembly_allocation_fits_existing_budget_without_changing_global_roles():
    import json
    from copy import deepcopy

    from native_study_fixture import ROOT

    configuration = json.loads((ROOT / "config/resource-budgets.json").read_text())
    before = deepcopy(configuration)
    selected = study_recovery_assembly_allocations(configuration)
    assert {role: value["memoryMiB"] for role, value in selected.items()} == {
        "postgres": 448,
        "redis": 32,
        "api": 192,
        "foreground-worker": 384,
        "background-worker": 512,
        "game-feedback-worker": 448,
        "web": 32,
    }
    assert sum(value["memoryMiB"] for value in selected.values()) == 2048
    assert configuration == before
    smaller = deepcopy(configuration)
    smaller["development"]["memoryMiB"] = 2047
    with pytest.raises(
        AssertionError, match="study_recovery_assembly_exceeds_development_budget"
    ):
        study_recovery_assembly_allocations(smaller)


def test_packaged_recovery_helper_binds_database_before_integrity_connection():
    """Execute the mounted fixture entry in a fresh process without storage."""
    import subprocess
    import sys
    from pathlib import Path

    entry = Path(__file__).resolve().parent / "fixtures/product_collection_packaged.py"
    probe = r"""
import contextlib, http.server, pathlib, runpy, sys, threading
from tempo_storage import connections, infrastructure
from app.services.activity_gate import activity_gate
assert infrastructure._admission is None and infrastructure._measurement is None
class StopBeforeConnection(Exception): pass
@contextlib.contextmanager
def connection(*args, **kwargs):
    assert infrastructure.database_admission() is activity_gate
    assert infrastructure._measurement is not None
    raise StopBeforeConnection()
    yield
connections.connection = connection
class Server:
    server_port = 12345
    def __init__(self, *args): pass
    def serve_forever(self): pass
    def shutdown(self): pass
    def server_close(self): pass
http.server.ThreadingHTTPServer = Server
threading.Thread.start = lambda self: None
read_text = pathlib.Path.read_text
exists = pathlib.Path.exists
pathlib.Path.exists = lambda self: True if str(self) == '/tmp/tempo-qualification-provider.json' else exists(self)
pathlib.Path.read_text = lambda self, *args, **kwargs: '{}' if str(self) == '/tmp/tempo-qualification-provider.json' else read_text(self, *args, **kwargs)
sys.argv = [sys.argv[1], 'hold-integrity', 'fixture']
try:
    runpy.run_path(sys.argv[0], run_name='__main__')
except StopBeforeConnection:
    pass
else:
    raise AssertionError('fixture never attempted the expected connection')
"""
    result = subprocess.run(
        [sys.executable, "-c", probe, str(entry)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_held_retry_waits_for_original_deadline_without_rotating_identity(monkeypatch):
    import sys
    from pathlib import Path

    sys.path.insert(
        0, str(Path(__file__).resolve().parents[2] / "database/tests/games_collection")
    )
    from study_recovery_acceptance import _wait_for_original_retry

    held = {"operation_id": "original-operation", "next_attempt_at": 102.0}
    original = dict(held)
    clock = {"wall": 100.0, "monotonic": 10.0}

    def sleep(seconds):
        clock["wall"] += seconds
        clock["monotonic"] += seconds

    current = _wait_for_original_retry(
        held,
        now=lambda: clock["wall"],
        monotonic=lambda: clock["monotonic"],
        sleep=sleep,
    )
    assert current >= original["next_attempt_at"]
    assert held == original
    assert clock["monotonic"] < 55.0

    with pytest.raises(
        AssertionError, match="held_original_retry_deadline_not_reached"
    ):
        _wait_for_original_retry(
            held,
            now=lambda: 100.0,
            monotonic=iter((0.0, 46.0)).__next__,
            sleep=lambda _: None,
        )


def test_packaged_fixture_clock_advances_with_wall_time():
    import ast
    from pathlib import Path
    from types import SimpleNamespace

    source = (
        Path(__file__).resolve().parent / "fixtures/product_collection_packaged.py"
    ).read_text()
    clock_class = next(
        node
        for node in ast.parse(source).body
        if isinstance(node, ast.ClassDef) and node.name == "Clock"
    )
    inputs = {"now": 100.0}
    wall = SimpleNamespace(time=lambda: 101.0)
    namespace = {"inputs": inputs, "time": wall}
    exec(
        compile(
            ast.Module(body=[clock_class], type_ignores=[]), "<fixture-clock>", "exec"
        ),
        namespace,
    )
    clock = namespace["Clock"]()
    assert clock.now() == 101.0
    inputs["now"] = 102.0
    assert clock.now() == 102.0


def test_recovery_review_command_checks_original_and_prepared_manifest(monkeypatch):
    import copy
    from pathlib import Path

    from test_opening_decision_evidence import manifest

    from app.contracts.transport.study import ReviewRequest
    from app.services.opening_decision_evidence import EvidenceConflict

    monkeypatch.syspath_prepend(
        str(Path(__file__).resolve().parents[2] / "database/tests/games_collection")
    )
    from study_recovery_acceptance import assert_original_review_command

    base = {"outcome": "correct", "attempt_id": "attempt", "queue_entry_id": 1}
    for checkpoint in (
        None,
        {
            "attempt_id": "attempt",
            "manifest": manifest(),
            "origin_queue_entry_id": 1,
            "started_at": "2026-09-30T12:00:00Z",
            "study_timezone": "UTC",
            "events": [],
        },
    ):
        body = {
            **base,
            **({"opening_evidence_completion": checkpoint} if checkpoint else {}),
        }
        request = {
            "card_id": "card",
            "review": ReviewRequest.model_validate(body).durable_payload(),
        }
        if checkpoint:
            request["prepared_manifest"] = copy.deepcopy(checkpoint["manifest"])
        assert_original_review_command(request, "card", body)
        tampered = copy.deepcopy(request)
        tampered["review"]["outcome"] = "again"
        with pytest.raises(AssertionError):
            assert_original_review_command(tampered, "card", body)
        if checkpoint:
            missing = copy.deepcopy(request)
            del missing["prepared_manifest"]
            with pytest.raises(AssertionError):
                assert_original_review_command(missing, "card", body)
            mismatched = copy.deepcopy(request)
            mismatched["prepared_manifest"]["card_revision"] += 1
            with pytest.raises(EvidenceConflict):
                assert_original_review_command(mismatched, "card", body)


def test_capture_observer_preserves_command_result_stdout_and_limits(monkeypatch):
    import subprocess

    from test_packaged_collection import _run_packaged_docker

    result = subprocess.CompletedProcess(
        ["docker"], 0, '{"bounded_units":3}\n', "ordinary stderr"
    )
    calls, retained = [], []

    def run(arguments, **options):
        calls.append((arguments, options))
        return result

    monkeypatch.setattr(subprocess, "run", run)
    observed = _run_packaged_docker(
        ("exec", "owned", "worker"), input="input", retain_stderr=retained.append
    )
    assert observed is result and observed.stdout == '{"bounded_units":3}\n'
    assert calls == [
        (
            ["docker", "exec", "owned", "worker"],
            dict(input="input", capture_output=True, text=True, timeout=45),
        )
    ]
    assert retained == ["ordinary stderr"]


def test_capture_observer_preserves_original_timeout_and_partial_markers(
    monkeypatch, tmp_path
):
    import subprocess
    from pathlib import Path

    from test_packaged_collection import _retain_capture_markers, _run_packaged_docker

    stderr = b'ordinary stderr\n{"capture_unit_diagnostic":1,"event":"unit_start","private":"secret"}\n'
    timeout = subprocess.TimeoutExpired(
        ["docker"], 45, output=b"original stdout", stderr=stderr
    )

    def run(*arguments, **options):
        raise timeout

    monkeypatch.setattr(subprocess, "run", run)
    report = {}
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        _run_packaged_docker(
            ("exec", "owned", "worker"),
            retain_stderr=lambda value: _retain_capture_markers(
                value,
                directory=tmp_path,
                owner="owned",
                report=report,
                private_values=("secret",),
            ),
        )
    assert (
        caught.value is timeout
        and timeout.output == b"original stdout"
        and timeout.stderr is stderr
    )
    retained = report["capture_unit_diagnostics"][0]
    assert (
        Path(retained["path"]).read_text()
        == '{"capture_unit_diagnostic":1,"event":"unit_start","private":"<redacted>"}\n'
    )
    assert retained["marker_count"] == 1 and not retained["truncated"]


@pytest.mark.parametrize(
    "stderr",
    [
        ('{"capture_unit_diagnostic":1}\n' * 257),
        ('{"capture_unit_diagnostic":1,"large":"' + "x" * (1024 * 1024) + '"}\n'),
    ],
)
def test_capture_marker_retention_is_bounded(tmp_path, stderr):
    from test_packaged_collection import _retain_capture_markers

    report = {}
    _retain_capture_markers(stderr, directory=tmp_path, owner="owned", report=report)
    retained = report["capture_unit_diagnostics"][0]
    assert (
        retained["marker_count"] <= 256
        and retained["bytes"] <= 512 * 1024
        and retained["truncated"]
    )


def test_capture_marker_write_failure_does_not_replace_timeout(monkeypatch, tmp_path):
    import subprocess
    from pathlib import Path

    from test_packaged_collection import _retain_capture_markers, _run_packaged_docker

    timeout = subprocess.TimeoutExpired(
        ["docker"], 45, stderr=b'{"capture_unit_diagnostic":1}\n'
    )

    def run(*arguments, **options):
        raise timeout

    def write(*arguments, **options):
        raise OSError("fixture disk full")

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(Path, "write_bytes", write)
    report = {}
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        _run_packaged_docker(
            ("exec", "owned", "worker"),
            retain_stderr=lambda value: _retain_capture_markers(
                value, directory=tmp_path, owner="owned", report=report
            ),
        )
    assert caught.value is timeout and report[
        "capture_unit_diagnostic_write_errors"
    ] == ["OSError"]


def test_capture_observer_retention_exception_does_not_replace_timeout(monkeypatch):
    import subprocess

    from test_packaged_collection import _run_packaged_docker

    timeout = subprocess.TimeoutExpired(["docker"], 45, stderr=b"partial")

    def run(*args, **kwargs):
        raise timeout

    def retain(_):
        raise RuntimeError("retention failure")

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        _run_packaged_docker(("exec", "owned", "worker"), retain_stderr=retain)
    assert caught.value is timeout


def test_capture_timed_wrapper_preserves_result_and_exception(capsys):
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parent / "fixtures/capture_unit_diagnostic.py"
    spec = importlib.util.spec_from_file_location("capture_unit_diagnostic_test", path)
    observer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(observer)
    result = object()
    error = RuntimeError("original")
    assert (
        observer._timed("snapshot_full", lambda *args, **kwargs: result)("private")
        is result
    )

    def fail(*args, **kwargs):
        raise error

    with pytest.raises(RuntimeError) as caught:
        observer._timed("snapshot_candidate", fail)("private")
    assert caught.value is error
    markers = capsys.readouterr()
    assert markers.out == "" and "private" not in markers.err
    assert "snapshot_full_start" in markers.err and "snapshot_full_end" in markers.err
    assert "snapshot_candidate_raise" in markers.err


def test_packaged_collection_foreground_owns_periodic_scheduler():
    from test_packaged_collection import _ordinary_worker_command

    foreground = _ordinary_worker_command("foreground-worker")
    background = _ordinary_worker_command("background-worker")
    assert "--queues=foreground" in foreground and "--concurrency=2" in foreground
    assert "--beat" in foreground
    assert "--schedule=/tmp/qualification-celerybeat-schedule" in foreground
    assert "--queues=background" in background and "--concurrency=1" in background
    assert "--beat" not in background
    assert not any(value.startswith("--schedule=") for value in background)
    with pytest.raises(ValueError, match="unknown packaged worker role"):
        _ordinary_worker_command("unexpected")
