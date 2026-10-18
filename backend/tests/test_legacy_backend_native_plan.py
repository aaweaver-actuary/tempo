"""Source-only plan/refusal checks: no Docker, sockets or backend fixture run."""
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('legacy_backend_native_plan', ROOT / 'scripts/qualify_legacy_backend.py')
plan = importlib.util.module_from_spec(spec); spec.loader.exec_module(plan)


def test_prepared_native_plan_has_three_exact_cases_under_existing_budget():
    value = plan.describe()
    assert len(set(value['cases'])) == 3
    assert value['work_seconds'] == 900 and value['cleanup_seconds'] == 300
    assert value['total'] == dict(containers=3, cpus=1.85, memory_mib=1216, pids=288,
        extra_swap_mib=0, owned_internal_networks=1, published_ports=0)
    budget = json.loads((ROOT / 'config/resource-budgets.json').read_text())['development']
    assert value['total']['cpus'] <= budget['cpus'] and value['total']['memory_mib'] <= budget['memoryMiB']
    assert value['candidate_images']['redis'] is None


@pytest.mark.parametrize('value', ['redis:7-alpine', 'latest', 'sha256:wrong'])
def test_native_plan_refuses_mutable_or_unknown_image_identity(value):
    with pytest.raises(ValueError, match='immutable image ID'): plan.image_id(value)


def test_native_plan_requires_new_grant_before_git_docker_or_any_resource(monkeypatch):
    monkeypatch.setattr(plan.subprocess, 'check_output', lambda *a, **k: pytest.fail('Inspected resources without a grant'))
    with pytest.raises(ValueError, match='Fresh parent-approved'):
        plan.host(SimpleNamespace(approved_window=False))


def test_native_caps_have_no_extra_swap_host_ports_or_live_mounts():
    for kind in plan.CAPS:
        arguments = plan.docker_arguments(kind, 'owned-' + kind, 'sha256:' + 'a' * 64, 'owned-internal')
        assert arguments[arguments.index('--memory') + 1] == arguments[arguments.index('--memory-swap') + 1]
        assert '--publish' not in arguments and '--env-file' not in arguments and '--mount' not in arguments
        assert arguments[arguments.index('--network') + 1] == 'owned-internal'


@pytest.mark.parametrize('fault', ['unqualified', 'head', 'image', 'inventory', 'bytes'])
def test_native_gate_refuses_incomplete_foreign_or_changed_artifacts(tmp_path, fault):
    (tmp_path / 'wheels').mkdir()
    artifacts = {}
    for index in range(12):
        name = str(index) + '.fixture'
        (tmp_path / 'wheels' / name).write_bytes(b'owned synthetic fixture')
        artifacts[name] = hashlib.sha256(b'owned synthetic fixture').hexdigest()
    host = dict(complete=True, cleanup_verified=True, source_head='head', image='image', plan=dict(artifact_test_total=284))
    inside = dict(complete=True, artifacts=artifacts)
    if fault == 'unqualified': host['cleanup_verified'] = False
    if fault == 'head': host['source_head'] = 'other'
    if fault == 'image': host['image'] = 'other'
    if fault == 'inventory': host['plan']['artifact_test_total'] = 255
    if fault == 'bytes': (tmp_path / 'wheels/0.fixture').write_bytes(b'changed')
    for name, document in [('host.json', host), ('inside.json', inside)]:
        (tmp_path / name).write_text(json.dumps(document))
    with pytest.raises(ValueError): plan.qualified_artifacts(tmp_path, 'head', 'image')
