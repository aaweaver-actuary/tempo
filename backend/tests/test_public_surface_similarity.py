"""Similarity's explicit public surface; no approximation-to-identity change."""
import ast
from importlib import import_module
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import chess
import pytest

ROOT = Path(__file__).resolve().parents[2]
SOURCES = ("backend", "packages/game-review-contracts/src", "contracts/game-evidence/src",
           "packages/chess-core/src", "packages/evidence-math/src", "contracts/analysis-jobs/src")
for source in SOURCES:
    sys.path.insert(0, str(ROOT / source))

from app.analysis.similarity import interfaces as public

ORIGINS = {
    "features": ("Comparison", "PositionFeatures", "compare", "features"),
    "retrieval": ("FamiliarCandidate", "IndexFacts", "KeyFacts", "NodeFacts", "NodeInventory", "OccurrenceFacts",
                  "OutlierCandidate", "OutlierScanResult", "RetrievalRequest", "RetrievalResult", "SourceOccurrence",
                  "SupportingPosition", "retrieve", "scan_outliers"),
}


def assert_owner_exports(surface):
    assert len(set(surface.__all__)) == len(surface.__all__)
    assert all(getattr(surface, name).__module__.startswith("app.analysis.similarity.") for name in surface.__all__)


def test_similarity_exports_retain_identity_and_closed_owner_scope():
    assert set(public.__all__) == {name for names in ORIGINS.values() for name in names}
    for module, names in ORIGINS.items():
        original = import_module("app.analysis.similarity." + module)
        for name in names:
            assert getattr(public, name) is getattr(original, name)
    assert_owner_exports(public)
    from app.contracts.product_v02.analysis import Proposal
    with pytest.raises(AssertionError):
        assert_owner_exports(SimpleNamespace(__all__=("Proposal",), Proposal=Proposal))
    tree = ast.parse(Path(public.__file__).read_text())
    assert not any(alias.name == "*" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) for alias in node.names)


def test_similarity_public_features_keep_score_and_tactical_difference():
    target = chess.Board()
    source = target.copy()
    source.castling_rights = 0
    result = public.compare(target, source, "white")
    assert result.score == 1000
    assert any("castling rights" in difference for difference in result.differences)
    assert public.features(target).castling == "KQkq"
    assert target.castling_xfen() == "KQkq"


def test_similarity_surface_cold_import_starts_no_optional_resource():
    script = '''
import builtins, socket, subprocess, threading
original = builtins.__import__
denied = ("app.main", "app.tasks", "app.product_composition", "app.repertoire", "app.study", "app.games", "httpx", "requests", "psycopg", "sqlite3")
def checked(name, *args, **kwargs):
    if any(name == root or name.startswith(root + ".") for root in denied): raise AssertionError(name)
    return original(name, *args, **kwargs)
def stopped(*args, **kwargs): raise AssertionError("optional resource started")
socket.socket.connect = socket.socket.connect_ex = socket.socket.bind = stopped
subprocess.Popen = threading.Thread.start = stopped
builtins.__import__ = checked
from app.analysis.similarity.interfaces import retrieve, scan_outliers, IndexFacts
'''
    completed = subprocess.run([sys.executable, "-c", script], cwd=ROOT,
        env={"PYTHONPATH": ":".join(str(ROOT / source) for source in SOURCES)}, capture_output=True, text=True, timeout=15)
    assert completed.returncode == 0, completed.stderr
