"""Pure plan/supervision checks; never start Docker, build or native testing."""
import importlib.util
import json
import hashlib
import io
import zipfile
from pathlib import Path
import signal
import subprocess
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("legacy_bridge_qualification", ROOT / "scripts/qualify_legacy_bridge.py")
qualification = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qualification)


def test_finite_plan_pins_six_artifacts_exact_test_counts_caps_and_five_minute_cleanup():
    plan = qualification.describe()
    assert plan["artifact_test_total"] == 284 and plan["native_test_total"] == 5
    assert plan["cleanup_reserve_seconds"] == 300
    assert plan["work_seconds"] == {"artifacts": 900, "native": 600}
    assert plan["native_total"] == dict(containers=2, cpus=2, memory_mib=1024, pids=384, owned_internal_networks=1)
    assert len(plan["own_packages"]) == 6
    assert len(set(plan["native_tests"])) == 5


@pytest.mark.parametrize("image", ["python:3.12-slim", "latest", "sha256:wrong"])
def test_helper_rejects_mutable_or_unknown_images_before_any_resource_call(image):
    with pytest.raises(ValueError): qualification.helper_arguments(image, "owned", Path("/tmp/owned"), "artifacts", source=Path("/tmp/owned/source"), source_sha256="a" * 64)


def test_helper_caps_cover_all_descendants_and_mount_only_source_and_owned_evidence():
    args = qualification.helper_arguments("sha256:" + "a" * 64, "owned-helper", Path("/tmp/owned-evidence"), "native",
        source=Path("/private/tmp/owned/source"), source_sha256="b" * 64,
        network="owned-internal-network", env_file=Path("/private/tmp/owned/output/private-native.env"))
    assert args[args.index("--cpus") + 1] == "1"
    assert args[args.index("--memory") + 1] == args[args.index("--memory-swap") + 1] == "768m"
    assert args[args.index("--pids-limit") + 1] == "128"
    assert args[args.index("--network") + 1] == "owned-internal-network"
    assert not any("docker.sock" in value for value in args)
    assert len([value for value in args if value.startswith("type=bind")]) == 2
    assert "type=bind,source=/private/tmp/owned/source,target=/source,readonly" in args
    assert "TEMPO_QUALIFICATION_SOURCE_SHA256=" + "b" * 64 in args
    assert "PYTHONPYCACHEPREFIX=/tmp/legacy-qualified-bytecode" in args


def test_deadline_terminates_the_host_process_group_inside_the_work_budget(monkeypatch):
    calls = []
    class Child:
        pid = 41234
        def wait(self, timeout):
            calls.append(("wait", timeout))
            if len([item for item in calls if item[0] == "wait"]) == 1:
                raise subprocess.TimeoutExpired("synthetic", timeout)
            return -15
    def popen(command, **kwargs):
        assert kwargs["start_new_session"] is True
        return Child()
    monkeypatch.setattr(qualification.subprocess, "Popen", popen)
    monkeypatch.setattr(qualification.time, "monotonic", lambda: 0.0)
    monkeypatch.setattr(qualification.os, "killpg", lambda pid, signum: calls.append((pid, signum)))
    with pytest.raises(subprocess.TimeoutExpired): qualification.Deadline(40, {}).run(["synthetic"])
    assert calls == [("wait", 30.0), (41234, signal.SIGTERM), ("wait", 5)]


def test_failed_wrapper_reaps_its_orphan_process_group_before_cleanup(monkeypatch):
    calls = []
    child = SimpleNamespace(pid=41234, wait=lambda timeout: 1)
    monkeypatch.setattr(qualification.subprocess, "Popen", lambda *a, **k: child)
    monkeypatch.setattr(qualification.os, "killpg", lambda pid, sig: calls.append((pid, sig)))
    report = {}
    with pytest.raises(RuntimeError, match="Qualification command failed"):
        qualification.Deadline(40, report).run(["synthetic-wrapper"])
    assert calls == [(41234, signal.SIGKILL)]
    assert report['commands'][0]['process_group'] == 41234


@pytest.mark.parametrize("diagnostic,identity,expected", [
    ("error: no such object: owned-helper", "owned-helper", True),
    ("Error response from daemon: No such container: owned-helper", "owned-helper", True),
    ("No such network: owned-network", "owned-network", True),
    ("No such container: another-helper", "owned-helper", False),
    ("No such container: another-owned-helper", "owned-helper", False),
    ("connection refused for owned-helper", "owned-helper", False),
    ("Error response from daemon: no such volume: owned-volume", "owned-volume", True),
    ("no such volume: other-volume", "owned-volume", False),
    ("volume inspect unavailable for owned-volume", "owned-volume", False),
])
def test_absence_requires_the_exact_identity_but_not_diagnostic_case(diagnostic, identity, expected):
    assert qualification.verified_absence(SimpleNamespace(returncode=1, stderr=diagnostic), identity) is expected


def test_native_controller_requires_explicit_window_and_managed_budget_before_any_docker(monkeypatch):
    monkeypatch.delenv("TEMPO_DEVELOPMENT_BUDGET_TOKEN", raising=False)
    monkeypatch.setattr(qualification.subprocess, "Popen", lambda *args, **kwargs: pytest.fail("subprocess started"))
    with pytest.raises(RuntimeError, match="approved finite window"):
        qualification.controller(SimpleNamespace(approved_window=False))


def test_helper_refuses_missing_or_wrong_cgroup_caps(monkeypatch):
    values = {"cpu.max": "100000 100000", "memory.max": str(768 * 1024 * 1024), "memory.swap.max": "0", "pids.max": "128"}
    monkeypatch.setattr(qualification, "Path", lambda value: SimpleNamespace(read_text=lambda: values[value.rsplit("/", 1)[-1]]))
    qualification.verify_helper_caps()
    values["pids.max"] = "max"
    with pytest.raises(ValueError): qualification.verify_helper_caps()


def test_pytest_first_failure_stops_the_window_with_no_complete_inventory(tmp_path):
    commands = []
    class Timer:
        def run(self, command, **_options):
            commands.append(command)
            assert "--maxfail=1" in command
            if "--collect-only" in command:
                Path(command[3]).write_text(json.dumps({"collected": ["first", "not-executed"]}))
                return
            Path(command[3]).write_text(json.dumps({"collected": ["first", "not-executed"],
                                                  "executed": ["first"], "exit_code": 1}))
            raise RuntimeError("first test failed")
    with pytest.raises(RuntimeError, match="first test failed"):
        qualification.run_suite(Timer(), "python", tmp_path, "failed", ROOT / "services/learning/remediation",
                                ("tests",), 2, True)
    assert len(commands) == 2
    assert json.loads((tmp_path / "failed-execution.json").read_text())["exit_code"] == 1


def test_native_pinned_tool_requires_retained_artifact_without_network(tmp_path, monkeypatch):
    monkeypatch.setattr(qualification.platform, "machine", lambda: "aarch64")
    monkeypatch.setattr(qualification.urllib.request, "urlopen", lambda *a, **k: pytest.fail("network fallback"))
    with pytest.raises(RuntimeError, match="qualified pinned tool"):
        qualification.pinned_uv(tmp_path, "native", None, tmp_path)


def test_pinned_tool_rejects_changed_wheel_before_extraction_or_execution(tmp_path, monkeypatch):
    monkeypatch.setattr(qualification.platform, "machine", lambda: "aarch64")
    directory = tmp_path / "tools/uv-0.9.3"
    directory.mkdir(parents=True)
    (directory / "vendor.whl").write_bytes(b"changed")
    with pytest.raises(RuntimeError, match="wheel changed"):
        qualification.pinned_uv(tmp_path, "artifacts", None, tmp_path)


def test_pinned_tool_extracts_only_verified_binary_and_native_reuses_it_offline(tmp_path, monkeypatch):
    content = io.BytesIO()
    with zipfile.ZipFile(content, "w") as archive:
        archive.writestr("uv.data/scripts/uv", b"synthetic binary, never executed")
        archive.writestr("uv.data/scripts/uvx", b"not extracted")
    data = content.getvalue()
    url = "https://files.pythonhosted.org/synthetic-reviewed.whl"
    monkeypatch.setattr(qualification.platform, "machine", lambda: "aarch64")
    monkeypatch.setitem(qualification.UV_WHEELS, "aarch64", (url, hashlib.sha256(data).hexdigest()))
    class Download:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def geturl(self): return url
        def read(self, maximum):
            assert maximum == 24 * 1024 * 1024 + 1
            return data
    monkeypatch.setattr(qualification.urllib.request, "urlopen", lambda source, timeout:
        Download() if source == url and timeout == 20 else pytest.fail("unreviewed download"))
    timer = SimpleNamespace(capture=lambda argv, maximum: SimpleNamespace(stdout="uv 0.9.3\n"))
    binary = qualification.pinned_uv(tmp_path, "artifacts", timer, tmp_path)
    assert Path(binary).read_bytes() == b"synthetic binary, never executed"
    assert not (Path(binary).parent / "uvx").exists()
    monkeypatch.setattr(qualification.urllib.request, "urlopen", lambda *a, **k: pytest.fail("native network"))
    assert qualification.pinned_uv(tmp_path, "native", timer, tmp_path) == binary


# Tmp staging is part of the same regular runner suite, not a native qualification.
def staged_fixture(tmp_path):
    module = qualification.staging
    source = tmp_path / 'repository'; source.mkdir()
    names = [*module.SOURCE_FILES, 'backend/app/owned.py',
             'services/game-evidence/src/owned/__init__.py', 'unrelated/secret.txt',
             'backend/app/private/pgpass', 'backend/app/secret.env']
    for name in names:
        path = source / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text('tracked fixture ' + name)
    stage = module.OwnedStaging('owned-test', 'test-head', base=tmp_path)
    stage.stage_source(source, names, qualification.PACKAGES)
    return module, stage, source


def test_qualification_stages_only_tracked_allowlisted_source_and_verifies_manifest(tmp_path):
    module, stage, source = staged_fixture(tmp_path)
    assert stage.root.stat().st_mode & 0o777 == 0o700
    manifest = module.verify_source(stage.source, stage.record['source_manifest_sha256'])
    assert manifest['head'] == 'test-head'
    assert 'backend/app/owned.py' in manifest['files']
    assert not any('secret' in name or 'unrelated' in name or 'pgpass' in name for name in manifest['files'])
    (source / 'backend/app/owned.py').write_text('later checkout edit')
    module.verify_source(stage.source, stage.record['source_manifest_sha256'])


@pytest.mark.parametrize('fault', ['source', 'manifest', 'extra', 'symlink'])
def test_changed_staged_source_is_refused_before_helper_work(tmp_path, fault):
    module, stage, _ = staged_fixture(tmp_path)
    if fault == 'source': (stage.source / 'backend/app/owned.py').write_text('changed')
    if fault == 'manifest': (stage.source / '.qualification-source.json').write_text('{}')
    if fault == 'extra': (stage.source / 'extra.py').write_text('unreviewed')
    if fault == 'symlink':
        path = stage.source / 'backend/app/owned.py'; path.unlink(); path.symlink_to('/etc/hosts')
    with pytest.raises(ValueError): module.verify_source(stage.source, stage.record['source_manifest_sha256'])


def test_qualified_installed_environment_dependency_and_tool_bytes_are_bound(tmp_path):
    module = qualification.staging
    root = tmp_path / 'qualified'; root.mkdir()
    for name in ['wheels/owned.whl', 'wheels/owned.tar.gz', 'tools/uv-0.9.3/uv',
                 'work/environment/lib/python3.12/site-packages/dependency/__init__.py',
                 'work/environment/lib/python3.12/site-packages/pytest/__init__.py']:
        path = root / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text('qualified')
    expected = module.inventory(root, retained=True)
    manifest = root / 'qualified-inputs.json'; manifest.write_text(json.dumps(expected))
    host = dict(qualified_manifest_sha256=module.digest(manifest))
    assert 'work/environment/lib/python3.12/site-packages/pytest/__init__.py' in expected
    assert module.qualified_inputs(root, host) == expected
    stage = module.OwnedStaging('owned-test', 'head', base=tmp_path)
    stage.stage_qualified(root, expected)
    (root / 'work/environment/lib/python3.12/site-packages/dependency/__init__.py').write_text('changed')
    with pytest.raises(ValueError, match='inventory changed'): module.qualified_inputs(root, host)
    # The staged input remains exactly the accepted immutable copy.
    module.verify_inventory(stage.qualified, expected, retained=True)


@pytest.mark.parametrize('fault', ['wheel', 'tool', 'manifest', 'extra_dependency'])
def test_native_rejects_changed_qualified_inputs_without_reinstalling(tmp_path, fault):
    module = qualification.staging
    root = tmp_path / 'qualified'; root.mkdir()
    for name in ['wheels/owned.whl', 'tools/uv-0.9.3/uv', 'work/environment/pyvenv.cfg']:
        path = root / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text('accepted')
    manifest = root / 'qualified-inputs.json'; manifest.write_text(json.dumps(module.inventory(root, retained=True)))
    host = dict(qualified_manifest_sha256=module.digest(manifest))
    name = {'wheel': 'wheels/owned.whl', 'tool': 'tools/uv-0.9.3/uv',
            'manifest': 'qualified-inputs.json', 'extra_dependency': 'work/environment/unexpected.py'}[fault]
    (root / name).write_text('different')
    with pytest.raises(ValueError): module.qualified_inputs(root, host)


def test_durable_copyback_excludes_fixture_credentials_and_requires_verified_absence(tmp_path, monkeypatch):
    module, stage, _ = staged_fixture(tmp_path)
    (stage.output / 'inside.json').write_text('{"complete": true}')
    private = stage.output / 'private'; private.mkdir(); (private / 'pgpass').write_text('fixture credential')
    (stage.output / 'private-native.env').write_text('fixture credential')
    scratch = stage.output / 'pytest'; scratch.mkdir(); (scratch / 'scratch').write_text('not a receipt')
    destination = tmp_path / 'durable'; destination.mkdir()
    calls = []; real_fsync = module.os.fsync
    def fsync(fd): calls.append(fd); real_fsync(fd)
    monkeypatch.setattr(module.os, 'fsync', fsync)
    stage.copy_back(stage.output, destination)
    assert calls and (destination / 'inside.json').read_bytes() == (stage.output / 'inside.json').read_bytes()
    assert sorted(path.name for path in destination.iterdir()) == ['inside.json']
    with pytest.raises(ValueError): stage.remove(resources_absent=False)
    assert stage.root.exists()
    stage.remove(resources_absent=True)
    assert stage.record['removed'] and not stage.root.exists()


def test_copyback_failure_preserves_stage_and_durable_previous_receipt(tmp_path, monkeypatch):
    module, stage, _ = staged_fixture(tmp_path)
    (stage.output / 'inside.json').write_text('new receipt')
    destination = tmp_path / 'durable'; destination.mkdir(); (destination / 'inside.json').write_text('old receipt')
    monkeypatch.setattr(module.os, 'fsync', lambda _fd: (_ for _ in ()).throw(OSError('copy interrupted')))
    with pytest.raises(OSError): stage.copy_back(stage.output, destination)
    assert (destination / 'inside.json').read_text() == 'old receipt'
    with pytest.raises(ValueError): stage.remove(resources_absent=True)
    assert stage.root.exists() and not stage.record['receipts_copied']


def test_qualification_refuses_changed_stage_ownership_and_output_symlinks(tmp_path):
    module, stage, _ = staged_fixture(tmp_path)
    (stage.output / 'leak.json').symlink_to('/etc/hosts')
    destination = tmp_path / 'durable'; destination.mkdir()
    with pytest.raises(ValueError): stage.copy_back(stage.output, destination)
    (stage.root / 'owner.json').write_text('{}')
    with pytest.raises(ValueError): stage.remove(resources_absent=True)
    assert stage.root.exists()


def test_qualified_environment_links_remain_inside_tree_and_copy_back_idempotently(tmp_path):
    module = qualification.staging
    source = tmp_path / 'qualified'; directory = source / 'work/environment/bin'; directory.mkdir(parents=True)
    (directory / 'python').symlink_to('/usr/local/bin/python3.12')
    (directory / 'python3').symlink_to('python')
    expected = module.inventory(source, retained=True)
    destination = tmp_path / 'copied'; destination.mkdir()
    module.copy_entries(source, destination, expected, durable=True)
    module.copy_entries(source, destination, expected, durable=True)
    assert module.inventory(destination, retained=True) == expected
    (directory / 'escape').symlink_to('../../../../outside')
    with pytest.raises(ValueError): module.inventory(source, retained=True)



def test_controller_mounts_only_owned_tmp_then_copies_receipts_before_removal(tmp_path, monkeypatch):
    module, unused_stage, source = staged_fixture(tmp_path)
    monkeypatch.setattr(qualification, 'ROOT', source)
    names = [str(path.relative_to(source)) for path in source.rglob('*') if path.is_file()]
    def git(command, **_options):
        if command[1] == 'status': return ''
        if command[1] == 'rev-parse': return 'test-head\n'
        if command[1] == 'ls-files': return '\0'.join(names) + '\0'
        pytest.fail('unexpected command')
    monkeypatch.setattr(qualification.subprocess, 'check_output', git)
    monkeypatch.setenv('TEMPO_DEVELOPMENT_BUDGET_TOKEN', 'synthetic-test-owner')
    real_staging = module.OwnedStaging
    monkeypatch.setattr(module, 'OwnedStaging', lambda identity, head: real_staging(identity, head, base=tmp_path))
    mounts = {}
    class Timer:
        def __init__(self, *_args, **_options): pass
        def run(self, command, **_options):
            if command[:2] == ['docker', 'run']:
                for value in command:
                    if value.startswith('type=bind,'):
                        parts = dict(item.split('=', 1) for item in value.split(',') if '=' in item)
                        mounts[parts['target']] = Path(parts['source'])
                assert mounts['/source'] != source
                assert mounts['/source'].parent == mounts['/evidence'].parent
                module.verify_source(mounts['/source'], module.digest(mounts['/source'] / '.qualification-source.json'))
                (mounts['/evidence'] / 'inside.json').write_text(json.dumps(dict(complete=True, qualified_manifest_sha256='synthetic')))
        def capture(self, command, **_options):
            if command[1] == 'inspect':
                return subprocess.CompletedProcess(command, 1, '', 'no such object: ' + command[2])
            return subprocess.CompletedProcess(command, 0, '', '')
    monkeypatch.setattr(qualification, 'Deadline', Timer)
    evidence = tmp_path / 'durable'
    args = SimpleNamespace(approved_window=True, helper_image_id='sha256:' + 'a' * 64,
                           mode='artifacts', evidence=evidence)
    assert qualification.controller(args) == 0
    receipts = list(evidence.glob('*/host.json')); assert len(receipts) == 1
    host = json.loads(receipts[0].read_text())
    assert host['complete'] and host['cleanup_verified']
    assert host['staging']['receipts_copied'] and host['staging']['removed']
    assert (receipts[0].parent / 'inside.json').is_file()
    assert not mounts['/source'].parent.exists()


def test_native_role_provisioning_dependency_is_required_and_hash_verified(tmp_path):
    module = qualification.staging
    provision = 'scripts/provision_postgres_roles.py'
    assert provision in module.SOURCE_FILES
    stage = module.OwnedStaging('required-native-source', 'head', base=tmp_path)
    with pytest.raises(ValueError, match='Required runner source inputs missing'):
        stage.stage_source(ROOT, module.SOURCE_FILES - {provision}, qualification.PACKAGES)
    stage.stage_source(ROOT, module.SOURCE_FILES, qualification.PACKAGES)
    assert (stage.source / provision).read_bytes() == (ROOT / provision).read_bytes()
    manifest = module.verify_source(stage.source, stage.record['source_manifest_sha256'])
    assert manifest['files'][provision]['sha256'] == module.digest(ROOT / provision)
    (stage.source / provision).write_text('changed provision implementation')
    with pytest.raises(ValueError, match='Staged source bytes changed'):
        module.verify_source(stage.source, stage.record['source_manifest_sha256'])


def test_remediation_migrations_survive_both_source_allowlists(tmp_path):
    module = qualification.staging
    package = 'services/learning/remediation'
    names = [*module.SOURCE_FILES, *[path.relative_to(ROOT).as_posix()
        for path in (ROOT / package / 'migrations').rglob('*') if path.is_file()]]
    assert any(name.endswith('.sql') for name in names)
    stage = module.OwnedStaging('required-migrations', 'head', base=tmp_path)
    stage.stage_source(ROOT, names, qualification.PACKAGES)
    restricted = tmp_path / 'artifact-source'
    qualification.copy_artifact_source(stage.source, restricted)
    for name in names:
        if name.startswith(package + '/migrations/'):
            assert (restricted / name).read_bytes() == (ROOT / name).read_bytes()
    assert not module.source_allowed(package + '/unreviewed/config.json', qualification.PACKAGES)
    assert not module.source_allowed(package + '/migrations/private/password', qualification.PACKAGES)
    changed = next((stage.source / package / 'migrations').glob('*.sql'))
    changed.write_text('changed migration')
    with pytest.raises(ValueError, match='Staged source bytes changed'):
        module.verify_source(stage.source, stage.record['source_manifest_sha256'])


@pytest.mark.parametrize('complete', [True, False])
def test_native_case_receipts_survive_durable_copyback_and_stage_removal(tmp_path, complete):
    module, stage, _ = staged_fixture(tmp_path)
    (stage.output / 'inside.json').write_text(json.dumps({'complete': complete}))
    case = stage.output / 'pytest/test_native_generated_accept_r0'; case.mkdir(parents=True)
    names = ['process-cleanup.json', 'capture.json', 'capture.log', 'observation.json',
             'preview.json', 'api-2.log', 'background-1.log', 'foreground-0.log', 'background-3.log']
    if complete: names.append('native-trace.json')
    for name in names: (case / name).write_text('receipt: ' + name)
    (case / 'unknown.json').write_text('not a receipt')
    (case / 'api-private.log').write_text('not a process log')
    (case / 'secret.env').write_text('credential')
    private = case / 'private'; private.mkdir(); (private / 'tempo_reader.password').write_text('credential')
    (case.parent / 'test_native_generated_accept_rcurrent').symlink_to(case, target_is_directory=True)
    destination = tmp_path / 'durable'; destination.mkdir()
    stage.copy_back(stage.output, destination)
    expected = {'inside.json', *(str(Path('pytest') / case.name / name) for name in names)}
    assert set(stage.record['copied_files']) == expected
    assert set(module.inventory(destination)) == expected
    for name in names: assert (destination / 'pytest' / case.name / name).read_text() == 'receipt: ' + name
    stage.remove(resources_absent=True)
    assert stage.record['removed'] and not stage.root.exists()
    assert set(module.inventory(destination)) == expected


@pytest.mark.parametrize('fault', ['symlink', 'changed_during_copy'])
def test_native_receipt_tampering_prevents_stage_removal(tmp_path, monkeypatch, fault):
    module, stage, _ = staged_fixture(tmp_path)
    receipt = stage.output / 'pytest/test_native_failure0/process-cleanup.json'
    receipt.parent.mkdir(parents=True); receipt.write_text('original receipt')
    if fault == 'symlink':
        receipt.unlink(); receipt.symlink_to('/etc/hosts')
    else:
        original_copy = module.copy_entries
        def changed_copy(source, destination, expected, **options):
            receipt.write_text('tampered receipt')
            original_copy(source, destination, expected, **options)
        monkeypatch.setattr(module, 'copy_entries', changed_copy)
    destination = tmp_path / 'durable'; destination.mkdir()
    with pytest.raises(ValueError): stage.copy_back(stage.output, destination)
    with pytest.raises(ValueError): stage.remove(resources_absent=True)
    assert stage.root.exists() and not stage.record['receipts_copied']
    assert not list(destination.rglob('*'))


def test_pytest_shaped_unknown_source_cannot_escape_manifest_verification(tmp_path):
    module, stage, _ = staged_fixture(tmp_path)
    unknown = stage.source / 'pytest/test_native_failure0/unknown.py'
    unknown.parent.mkdir(parents=True); unknown.write_text('unreviewed source')
    with pytest.raises(ValueError, match='Staged source bytes changed'):
        module.verify_source(stage.source, stage.record['source_manifest_sha256'])


def test_pinned_x86_uv_url_matches_published_093_wheel_identity():
    url, checksum = qualification.UV_WHEELS['x86_64']
    assert url == ('https://files.pythonhosted.org/packages/82/45/'
        '488417c6c0127c00bcdfac3556ae2ea0597df8245fe5f9bcfda35ebdbe85/'
        'uv-0.9.3-py3-none-manylinux_2_17_x86_64.manylinux2014_x86_64.whl')
    assert checksum == '73ae4bbc7d555ba1738da08c64b55f21ab0ea0ff85636708cebaf460d98a440d'
