"""Proposal and retained Intelligence surfaces preserve qualified public values."""
import ast
from importlib import import_module
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
SOURCES = ("backend", "packages/game-review-contracts/src", "contracts/game-evidence/src",
           "packages/chess-core/src", "packages/evidence-math/src", "contracts/analysis-jobs/src",
           "services/intelligence/src")
for source in SOURCES:
    sys.path.insert(0, str(ROOT / source))

from app.analysis.proposals import interfaces as public

ORIGINS = {
    "acceptability": ("QualityDecision", "QualityPolicy", "assess"),
    "blunders": ("BlunderPolicy", "BlunderRequest", "BlunderResult", "prepare_blunder"),
    "coordinator": ("ProposalCoordinator", "approval_requires_revalidation"),
    "ports": ("CandidateFacts", "CapabilityUnavailable", "ComparisonFacts", "ExistingCardCandidate", "ProposalBatch",
              "ProposalLimits", "ProposalResult", "ProposalTarget", "RetrievalFacts", "RetrievalRequestFacts",
              "SimilarityPort", "SourceFacts", "SupportFacts"),
}


def assert_owner_exports(surface, prefix):
    assert len(set(surface.__all__)) == len(surface.__all__)
    assert all(getattr(surface, name).__module__.startswith(prefix) for name in surface.__all__)


def test_proposal_exports_retain_identity_and_closed_owner_scope():
    assert set(public.__all__) == {name for names in ORIGINS.values() for name in names}
    for module, names in ORIGINS.items():
        original = import_module("app.analysis.proposals." + module)
        for name in names:
            assert getattr(public, name) is getattr(original, name)
    assert_owner_exports(public, "app.analysis.proposals.")
    from app.contracts.product_v02.analysis import Proposal
    with pytest.raises(AssertionError):
        assert_owner_exports(SimpleNamespace(__all__=("Proposal",), Proposal=Proposal), "app.analysis.proposals.")
    tree = ast.parse(Path(public.__file__).read_text())
    assert not any(alias.name == "*" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) for alias in node.names)


def test_proposal_public_quality_uses_existing_exact_engine_fixture():
    from app.contracts.product_v02.analysis import EngineEvidence
    payload = json.loads((ROOT / "contracts/product-v02/fixtures/engine-explicit-candidate.json").read_text())["payload"]
    evidence = EngineEvidence.model_validate_json(json.dumps(payload))
    result = public.assess(evidence.context, evidence, "a2a3")
    assert result.status == "acceptable" and result.loss_cp == 24
    assert result.policy.max_loss_cp == 30


def test_intelligence_values_preserve_identity_and_json_serialization():
    from tempo_intelligence import interfaces as legacy
    from tempo_intelligence import analysis_v1
    expected = {"Command", "Finding", "Manifest", "Page", "ReportPage", "RunRequest", "make_stockfish_job"}
    assert set(legacy.__all__) == expected
    assert_owner_exports(legacy, "tempo_intelligence.analysis.")
    for name in expected:
        assert getattr(legacy, name) is getattr(analysis_v1, name)
    command = legacy.Command.model_validate_json('{"command_id":"synthetic","action":"dismiss","expected_revision":1}')
    assert command.model_dump_json() == analysis_v1.Command.model_validate_json(command.model_dump_json()).model_dump_json()
    assert not {"Store", "Worker", "create_app", "run_analysis_work"} & set(legacy.__all__)


@pytest.mark.parametrize("module", ("app.analysis.proposals.interfaces", "tempo_intelligence.interfaces"))
def test_proposal_surfaces_cold_import_start_no_optional_resource(module):
    script = '''
import builtins, socket, subprocess, threading
original = builtins.__import__
denied = ("app.main", "app.tasks", "app.product_composition", "app.repertoire", "app.study", "app.games", "psycopg", "psycopg_pool", "tempo_intelligence_database", "tempo_intelligence.provider", "tempo_intelligence.worker", "tempo_intelligence.analysis.worker", "uvicorn")
def checked(name, *args, **kwargs):
    if any(name == root or name.startswith(root + ".") for root in denied): raise AssertionError(name)
    return original(name, *args, **kwargs)
def stopped(*args, **kwargs): raise AssertionError("optional resource started")
socket.socket.connect = socket.socket.connect_ex = socket.socket.bind = stopped
subprocess.Popen = threading.Thread.start = stopped
builtins.__import__ = checked
__import__(MODULE)
'''.replace("MODULE", repr(module))
    completed = subprocess.run([sys.executable, "-c", script], cwd=ROOT,
        env={"PYTHONPATH": ":".join(str(ROOT / source) for source in SOURCES)}, capture_output=True, text=True, timeout=15)
    assert completed.returncode == 0, completed.stderr
