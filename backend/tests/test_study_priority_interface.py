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
        "test_applied_game_priority_allows_bounded_daily_queue_preparation_without_losing_attempts",
        "test_intake_never_calls_queue_and_commits_once_when_refresh_fails",
        "test_study_refresh_without_games_consumes_accepted_priority",
        "test_real_shape_history_intake_commits_pending_then_study_refresh_without_games",
        "test_pending_encounter_resumes_on_existing_durable_pause_and_task_fence_without_games",
    }
    assert critical <= {selector.split("::")[-1].split("[", 1)[0] for selector in required}
    # Missing/renamed required cases fail both this cheap check and pytest
    # collection; a new uncollected file cannot stand in for owner evidence.
    parameterized = []
    for selector in required:
        path, *case = selector.split("::")
        tree = ast.parse((ROOT / path).read_text())
        if case:
            function, bracket, _ = case[0].partition("[")
            assert function in {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}, selector
            if bracket:
                parameterized.append(selector)
    # AST names do not include pytest's parameter IDs. Validate the exact
    # selected variants through collection, rather than silently dropping them.
    if parameterized:
        collected = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q",
            "--rootdir=.", "-o", "addopts=", *parameterized], cwd=ROOT,
            env=os.environ | {"PYTEST_ADDOPTS": ""},
            capture_output=True, text=True, timeout=30)
        assert collected.returncode == 0, collected.stdout + collected.stderr
        nodeids = {line.strip() for line in collected.stdout.splitlines()
                   if line.startswith("backend/tests/")}
        assert nodeids == set(parameterized), collected.stdout


def test_queue_presentation_owner_proof_requires_exact_passing_junit_case(tmp_path):
    import pytest
    from app.study.qualify_priority import QUEUE_PROOF, queue_proof_passed, junit_path
    report = tmp_path / 'cases.xml'
    name = QUEUE_PROOF.split('::')[1]
    def write(case):
        report.write_text(f'<testsuite>{case}</testsuite>')
    good = f'<testcase classname="backend.tests.test_study_encounter_intake_boundary" name="{name}" />'
    write(good)
    assert queue_proof_passed(report) == QUEUE_PROOF
    for case in ('', good.replace('/>', '><skipped /></testcase>'),
                 good.replace('/>', '><failure /></testcase>'), good + good,
                 good + '<testcase classname="unrelated" name="skipped"><skipped /></testcase>'):
        write(case)
        with pytest.raises(ValueError, match='missing, skipped or failed'):
            queue_proof_passed(report)
    assert junit_path([], {'PYTEST_ADDOPTS': f'--junitxml={report}'}) == report
    assert junit_path(['--junit-xml', str(report)], {}) == report
    assert junit_path([], {}) is None


def test_queue_presentation_owner_binding_uses_declared_inputs_and_candidate(tmp_path):
    import hashlib
    from app.study.qualify_priority import source_binding
    def git(*args):
        return subprocess.run(['git', '-C', str(tmp_path), *args], check=True, capture_output=True, text=True).stdout.strip()
    git('init', '-q')
    git('config', 'user.email', 'fixture@example.invalid')
    git('config', 'user.name', 'Fixture')
    (tmp_path / 'source').mkdir()
    (tmp_path / 'source' / 'queue.py').write_text('baseline')
    (tmp_path / 'components.json').write_text(json.dumps({'suites': [{'id':'study-game-priority', 'inputs':['source']}]}))
    git('add', 'components.json', 'source/queue.py'); git('commit', '-qm', 'base')
    base = git('rev-parse', 'HEAD')
    (tmp_path / 'source' / 'queue.py').write_text('candidate')
    git('add', 'source/queue.py'); git('commit', '-qm', 'candidate')
    candidate = git('rev-parse', 'HEAD')
    binding = source_binding(tmp_path, base)
    assert (binding['resolvedBase'], binding['resolvedHead'], binding['mergeBase']) == (base, candidate, base)
    assert binding['sourceInputs']['source/queue.py'] == hashlib.sha256(b'candidate').hexdigest()
    assert binding['worktreeStatus'] == []
    (tmp_path / 'source' / 'queue.py').write_text('dirty')
    changed = source_binding(tmp_path, base)
    assert changed['sourceInputs']['source/queue.py'] == hashlib.sha256(b'dirty').hexdigest()
    assert changed['worktreeStatus'] and changed != binding


def test_priority_qualifier_requires_exact_native_identity_before_reporting(monkeypatch, tmp_path, capsys):
    from types import SimpleNamespace
    from app.study import qualify_priority as owner
    monkeypatch.setenv('TEMPO_VALIDATION_LOCK_PATH', str(tmp_path / 'absent-lock'))
    monkeypatch.delenv('PYTEST_ADDOPTS', raising=False)
    binding = {'requestedBase':'base', 'resolvedBase':'base-sha', 'resolvedHead':'head-sha',
               'mergeBase':'base-sha', 'sourceInputs':{'queue.py':'sha256'}, 'worktreeStatus':[]}
    monkeypatch.setattr(owner, 'source_binding', lambda **kwargs: binding)
    monkeypatch.setattr(owner.platform, 'platform', lambda: 'fixture-platform')
    mode = ['passed']
    def run(command, **kwargs):
        assert owner.QUEUE_PROOF in command
        report = Path(next(argument.split('=', 1)[1] for argument in command if argument.startswith('--junitxml=')))
        case = owner.QUEUE_PROOF.split('::')[1]
        content = f'<testcase classname="backend.tests.test_study_encounter_intake_boundary" name="{case}">'
        report.write_text('<testsuite>' + (content + ('<skipped />' if mode[0] == 'skipped' else '') + '</testcase>' if mode[0] != 'missing' else '') + '</testsuite>')
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(owner.subprocess, 'run', run)
    assert owner.main() == 0
    output = json.loads(capsys.readouterr().out)
    assert output['executedIdentities'] == [owner.QUEUE_PROOF]
    assert output['comparison'] == binding and output['passed'] == 1
    for value in ('missing', 'skipped'):
        mode[0] = value
        assert owner.main() == 1
        assert 'Priority proof evidence invalid' in capsys.readouterr().err
