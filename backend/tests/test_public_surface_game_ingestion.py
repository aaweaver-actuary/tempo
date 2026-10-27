"""Allocated Games surfaces: identity, useful operations and cold no-I/O import."""
import ast
import hashlib
import importlib
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
EXTRA_SOURCE_ROOTS = (ROOT / "services/game-evidence/src", ROOT / "contracts/content-publication/src")
for directory in EXTRA_SOURCE_ROOTS:
    sys.path.insert(0, str(directory))

SURFACES = {
    "tempo_game_ingestion.interfaces": {
        "tempo_game_ingestion": ("BytesInput", "FileInput", "PgnGameIngester"),
        "tempo_game_ingestion.pgn": ("MAX_SOURCE_BYTES", "normalize_pgn"),
    },
    "app.games.ingestion.interfaces": {
        "app.games.records": ("GameRecord",),
        "app.games.ingestion.normalizer": ("RejectedGame", "normalize_provider_pgn"),
        "app.games.ingestion.serialization": ("serialize_job",),
    },
    "app.games.feedback.interfaces": {
        "app.games.feedback.continuous": ("ContinuousSync", "ForwardWindow"),
        "app.games.feedback.coordinator": ("Coordinator", "delivery_for", "project_encounters", "stable_id"),
        "app.games.feedback.jobs": ("FeedbackRunner",),
        "app.games.feedback.ports": ("Acquisition", "AcquisitionFailure", "Capture", "Clock", "Comparer",
            "ConcurrentUpdate", "Delivery", "GamesStore", "JobSpec", "JobState", "Normalizer",
            "PageCommit", "SourceIdentity", "SourceState"),
        "app.games.feedback.review": ("GameReview", "ReviewStore", "ReviewUnavailable", "SyncConfiguration"),
    },
    "tempo_game_evidence.interfaces": {
        "tempo_game_evidence.snapshot": ("ComparisonSnapshot", "ComparisonSnapshotV2", "parse_snapshot"),
        "tempo_game_evidence.legacy_authority": ("LegacyComparisonSnapshot", "PhysicalCard"),
        "tempo_game_evidence.legacy_scoped_authority": ("LegacyScopedComparisonSnapshot",),
        "tempo_game_evidence.legacy_payloads": ("preview_legacy_observation",),
    },
}


def cold_import(module, *, blocked=()):
    code = '''
import importlib, importlib.abc, socket, sqlite3, subprocess, sys, threading
import httpx, psycopg
def forbidden(*args, **kwargs):
    raise AssertionError("public import started I/O or a lifecycle")
socket.create_connection = socket.socket.connect = socket.socket.connect_ex = forbidden
sqlite3.connect = psycopg.connect = forbidden
httpx.Client.__init__ = httpx.AsyncClient.__init__ = forbidden
subprocess.Popen = threading.Thread.start = forbidden
blocked = BLOCKED
class NoConcreteAssembly(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, *args):
        if any(fullname == prefix or fullname.startswith(prefix + ".") for prefix in blocked):
            raise AssertionError("public import loaded forbidden owner/root: " + fullname)
sys.meta_path.insert(0, NoConcreteAssembly())
importlib.import_module(MODULE)
print("ok")
'''.replace("BLOCKED", repr(tuple(blocked))).replace("MODULE", repr(module))
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(
        [str(directory) for directory in EXTRA_SOURCE_ROOTS] + [environment.get("PYTHONPATH", "")])
    result = subprocess.run([sys.executable, "-B", "-c", code], cwd=ROOT,
        env=environment, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


@pytest.mark.parametrize("module", SURFACES)
def test_ingestion_public_exports_keep_exact_existing_identity(module):
    public = importlib.import_module(module)
    expected = {name for names in SURFACES[module].values() for name in names}
    assert set(public.__all__) == expected
    assert len(public.__all__) == len(expected)
    assert {name for name in vars(public) if not name.startswith("_")} == expected
    for origin, names in SURFACES[module].items():
        implementation = importlib.import_module(origin)
        for name in names:
            assert getattr(public, name) is getattr(implementation, name)
    tree = ast.parse(Path(public.__file__).read_text())
    assert not any(isinstance(node, ast.ImportFrom) and any(a.name == "*" for a in node.names)
                   for node in ast.walk(tree))
    assert not hasattr(public, "__getattr__")


@pytest.mark.parametrize("module", SURFACES)
def test_ingestion_public_cold_import_has_no_effect_or_study_root_dependency(module):
    cold_import(module, blocked=("app.main", "app.tasks", "app.study", "app.product_composition",
        "tempo_learning_review", "tempo_learning_remediation", "tempo_learning_runtime"))


def test_public_ingester_preserves_exact_source_and_explicit_identity():
    from tempo_game_ingestion.interfaces import BytesInput, PgnGameIngester
    raw = b'[White "Learner"]\n[Black "Opponent"]\n[Result "1-0"]\n\n1. d4 d5 1-0\n'
    game = PgnGameIngester().ingest(BytesInput(raw), workspace_id="workspace", game_id="exact-game",
        game_revision=3, source_id="exact-source", source_revision=7, player="Learner")
    assert game.game_id == "exact-game" and game.source_id == "exact-source"
    assert game.game_revision == 3 and game.source_revision == 7
    assert game.source_digest == hashlib.sha256(raw).hexdigest()
    assert game.moves == ["d2d4", "d7d5"] and game.played_at is None


def test_retained_ingestion_public_operations_are_not_stubs_or_effects():
    from app.games.ingestion.interfaces import GameRecord, RejectedGame, normalize_provider_pgn, serialize_job
    raw = '[White "Learner"]\n[Black "Opponent"]\n[Result "1-0"]\n\n1. d4 1-0\n'
    game = normalize_provider_pgn("lichess", "Learner", raw, provider_game_id="exact",
        played_at="2026-10-27T12:00:00Z", speed="rapid", rated=False)
    assert isinstance(game, GameRecord) and game.provider_game_id == "exact"
    assert game.uci_moves == ["d2d4"] and not game.rated
    with pytest.raises(RejectedGame):
        normalize_provider_pgn("lichess", "AnotherPlayer", raw, provider_game_id="exact")
    row = dict(id="job", status="queued", created_at="t", started_at=None,
        completed_at=None, updated_at="t", error=None, result_json="not ready JSON")
    assert serialize_job(row)["result"] is None
    row.update(status="complete", result_json='{"imported":1}')
    assert serialize_job(row)["result"] == {"imported": 1}


def test_game_evidence_surface_does_not_hide_foreign_storage_or_ingestion_exports():
    from tempo_game_evidence import interfaces
    assert not hasattr(interfaces, "EvidenceStore")
    assert not hasattr(interfaces, "normalize_pgn")
    assert not hasattr(interfaces, "LegacyAuthorityToken")
