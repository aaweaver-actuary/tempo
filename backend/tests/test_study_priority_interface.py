"""Cheap production-boundary guards; native isolation lives in #487's owner suite."""
import ast
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
CHECKER = ROOT / "tools/component-build/check-interface-boundaries.mjs"
INTAKE = "backend/app/study/encounter_feedback.py"
FORBIDDEN = "eligible*,*eligibility*,prepare*,promote*,*promotion*,*queue*,lock_target,refresh_encounter,maintain,_advance,admit_encounter,accept_lapse"


def intake_check(root=ROOT):
    return subprocess.run(["node", str(CHECKER), "--intake", INTAKE,
        "EncounterFeedback.accept_encounter", FORBIDDEN, str(root)],
        env=os.environ | {"TEMPO_COMPONENT_PYTHON": sys.executable},
        capture_output=True, text=True, timeout=30)


def test_actual_encounter_intake_has_no_inline_or_local_helper_queue_work():
    result = intake_check()
    assert result.returncode == 0, result.stderr


def test_actual_intake_negative_control_rejects_promotion_then_passes_after_removal(tmp_path):
    source = ROOT / INTAKE
    target = tmp_path / INTAKE
    shutil.copytree(source.parent, target.parent)
    original = source.read_text()
    signature = "    def accept_encounter(self, command: GameEncounter) -> EncounterReceipt:\n"
    assert original.count(signature) == 1
    target.write_text(original.replace(signature, signature + '        self.store.promote_encounter("synthetic-negative-control", "fixture")\n'))
    prohibited = intake_check(tmp_path)
    assert prohibited.returncode == 1
    assert "Intake references prohibited queue work" in prohibited.stderr
    target.write_text(original)
    repaired = intake_check(tmp_path)
    assert repaired.returncode == 0, repaired.stderr


def test_games_domain_has_no_database_execution_or_dynamic_loader():
    files = sorted(str(file.relative_to(ROOT)) for file in (ROOT / "backend/app/games").rglob("*.py"))
    result = subprocess.run([sys.executable, str(ROOT / "tools/component-build/check-python.py"),
        "boundary-facts", str(ROOT)], input=json.dumps(files), capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    for facts in json.loads(result.stdout):
        assert not facts["loaders"], facts["path"]
        assert not [call for call in facts["calls"] if "sql" in call], facts["path"]
        for imported in facts["imports"]:
            module = imported["module"].split(".")[0]
            assert not module.endswith("_database"), facts["path"]
            assert not module.startswith(("psycopg", "sqlite3", "asyncpg", "sqlalchemy")), facts["path"]


def test_public_priority_surface_is_pure_and_reuses_exact_frozen_contract_types():
    script = '''
import importlib.abc, sys
import pydantic
class NoPrivate(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, *args):
        if fullname.startswith(('app.games', 'app.services', 'app.product_composition', 'tempo_legacy_database', 'psycopg', 'sqlite3', 'httpx', 'requests', 'fastapi')) or (fullname.startswith('app.study.') and not fullname.startswith('app.study.interfaces')):
            raise AssertionError('public interface imported implementation: ' + fullname)
sys.meta_path.insert(0, NoPrivate())
from app.study.interfaces import game_priority as api
from app.contracts.product_v02 import learning
expected = {'EncounterFeedbackPort', 'EncounterReceipt', 'GameEffectControl', 'GameEffectKey', 'GameEncounter', 'GameLapse', 'LearningFeedbackPort', 'LearningReceipt'}
expected |= {'FirstDepartureWitness', 'EncounterTarget', 'IssuedAttemptEvidence', 'RemediationFence', 'EncounterSettlement', 'EncounterProgress', 'QualifiedGameFeedbackPort'}
assert set(api.__all__) == expected
for name in expected:
    assert getattr(api, name) is getattr(learning, name)
for name in expected - {'EncounterFeedbackPort', 'LearningFeedbackPort', 'QualifiedGameFeedbackPort'}:
    assert getattr(api, name).model_config['frozen']
'''
    paths = ["backend", "database", "packages/game-review-contracts/src", "contracts/game-evidence/src", "packages/chess-core/src"]
    result = subprocess.run([sys.executable, "-B", "-c", script], cwd=ROOT,
        env=os.environ | {"PYTHONPATH": os.pathsep.join(str(ROOT / path) for path in paths)},
        capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr


def test_required_priority_command_registers_real_failure_replay_and_independent_consumption():
    runner = ast.parse((ROOT / "backend/app/study/qualify_priority.py").read_text())
    required = next(ast.literal_eval(node.value) for node in runner.body if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "REQUIRED" for target in node.targets))
    assert len(required) == len(set(required))
    critical = {
        "test_intake_never_calls_queue_and_commits_once_when_refresh_fails",
        "test_study_refresh_without_games_consumes_accepted_priority",
        "test_real_shape_history_intake_commits_pending_then_study_refresh_without_games",
        "test_pending_encounter_resumes_on_existing_durable_pause_and_task_fence_without_games",
    }
    assert critical <= {selector.split("::")[-1] for selector in required}
    # Missing/renamed required cases fail both this cheap check and pytest
    # collection; a new uncollected file cannot stand in for owner evidence.
    for selector in required:
        path, *case = selector.split("::")
        tree = ast.parse((ROOT / path).read_text())
        if case:
            assert case[0] in {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}, selector
