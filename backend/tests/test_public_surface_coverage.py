"""Coverage's new public boundary over the unchanged qualified policy."""
import ast
from importlib import import_module
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
SOURCES = ("backend", "packages/game-review-contracts/src", "contracts/game-evidence/src",
           "packages/chess-core/src", "packages/evidence-math/src", "contracts/analysis-jobs/src")
for source in SOURCES:
    sys.path.insert(0, str(ROOT / source))

from app.analysis.coverage import interfaces as public

ORIGINS = {
    "personal_evidence": ("PersonalEncounter", "PersonalGame", "deduplicate_games", "encounter_sources"),
    "ports": ("AnalysisLimits", "IndexedOccurrence", "IndexedPosition", "RepertoireIndex"),
    "ranking": ("POLICY_VERSION", "CandidateDetail", "CoverageRanking", "Diagnostic", "RouteSupport", "rank_candidates", "rank_coverage"),
}


def assert_owner_exports(surface):
    assert len(set(surface.__all__)) == len(surface.__all__)
    for name in surface.__all__:
        value = getattr(surface, name)
        if not isinstance(value, str):
            assert value.__module__.startswith("app.analysis.coverage.")


def test_coverage_exports_retain_identity_and_closed_owner_scope():
    assert set(public.__all__) == {name for names in ORIGINS.values() for name in names}
    for module, names in ORIGINS.items():
        original = import_module("app.analysis.coverage." + module)
        for name in names:
            assert getattr(public, name) is getattr(original, name)
    assert_owner_exports(public)
    from app.contracts.product_v02.analysis import RankingInput
    with pytest.raises(AssertionError):
        assert_owner_exports(SimpleNamespace(__all__=("RankingInput",), RankingInput=RankingInput))
    tree = ast.parse(Path(public.__file__).read_text())
    assert not any(alias.name == "*" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) for alias in node.names)


def test_coverage_public_rank_preserves_twenty_before_two_and_inputs():
    from app.contracts.product_v02.analysis import RankingCandidate, RankingInput
    candidates = tuple(RankingCandidate(decision_id=name, repertoire_id="synthetic", reach_probability=value,
        evidence_support=None, uncertainty=None, personal_encounters=0) for name, value in (("two", .02), ("twenty", .2)))
    supplied = RankingInput(repertoire_revisions=(("synthetic", 1),), candidates=candidates, evidence_ids=())
    result = public.rank_candidates(supplied)
    assert [item.decision_id for item in result.ordered] == ["twenty", "two"]
    assert result.based_on is supplied


def test_coverage_surface_cold_import_starts_no_optional_resource():
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
from app.analysis.coverage.interfaces import rank_coverage, RepertoireIndex
'''
    completed = subprocess.run([sys.executable, "-c", script], cwd=ROOT,
        env={"PYTHONPATH": ":".join(str(ROOT / source) for source in SOURCES)}, capture_output=True, text=True, timeout=15)
    assert completed.returncode == 0, completed.stderr
