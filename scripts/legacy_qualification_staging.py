"""Private temporary inputs and durable receipts for legacy qualification only."""
import hashlib
import json
import os
from pathlib import Path
import posixpath
import re
import shutil
import stat
import tempfile
import uuid
import zipfile

PACKAGE_PARTS = {'src', 'schemas', 'fixtures', 'examples', 'tests', 'integration', 'migrations',
                 'run_suite.py', 'pyproject.toml', 'uv.lock', 'README.md', 'REGRESSIONS.md'}
SOURCE_FILES = {'scripts/qualify_legacy_bridge.py', 'scripts/qualify_legacy_backend.py',
                'scripts/legacy_qualification_staging.py', 'scripts/apply_postgres_migrations.py',
                'scripts/provision_postgres_roles.py',
                'contracts/requirements-test.lock', 'services/learning/application/qualification.py',
                'packages/scheduling/pyproject.toml', 'services/learning/application/pyproject.toml',
                'services/learning/runtime/pyproject.toml',
                'backend/pyproject.toml'}
SOURCE_TREES = ('contracts/database-ownership/', 'backend/app/', 'backend/migrations/',
                'backend/integration/legacy_bridge/', 'public/data/')
MAX_BYTES, MAX_FILES = 2 * 1024**3, 100000
BACKEND_WHEELS = {'tempo_scheduling': '0.2.0', 'tempo_learning_review': '0.1.0', 'tempo_learning_runtime': '0.1.0'}


def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''): value.update(chunk)
    return value.hexdigest()


def private_path(relative):
    return any(part in {'private', '__pycache__', '.pytest_cache', '.venv', '.git', 'pgpass'}
               or part.endswith(('.env', '.pgpass', '.pyc')) for part in relative.parts)


def source_allowed(name, packages):
    path = Path(name)
    if path.is_absolute() or '..' in path.parts or private_path(path): return False
    if name in SOURCE_FILES or name.startswith(SOURCE_TREES): return True
    return any(name.startswith(package + '/') and path.relative_to(package).parts[0] in PACKAGE_PARTS
               for package in packages)


def retained_allowed(path):
    if private_path(path): return False
    name = path.as_posix()
    return (len(path.parts) == 2 and path.parts[0] == 'wheels' and name.endswith(('.whl', '.tar.gz'))
            or name in {'tools/uv-0.9.3/vendor.whl', 'tools/uv-0.9.3/uv', 'work/dependencies.txt'}
            or name.startswith(('work/environment/', 'work/source/')))


def entry(root, path):
    relative = path.relative_to(root)
    if path.is_symlink():
        target = os.readlink(path)
        prefix = 'work/environment/'
        if not relative.as_posix().startswith(prefix): raise ValueError('Input/output symlink refused')
        if target.startswith('/'):
            if relative.parent.as_posix() != 'work/environment/bin' or target not in {
                    '/usr/local/bin/python', '/usr/local/bin/python3', '/usr/local/bin/python3.12',
                    '/usr/local/bin/python3.14'}: raise ValueError('Unapproved environment symlink')
        elif not posixpath.normpath(str(relative.parent / target)).startswith(prefix):
            raise ValueError('Environment symlink escapes its owned tree')
        return {'type': 'symlink', 'target': target}
    if not path.is_file(): raise ValueError('Only regular files and approved environment links are retained')
    return {'type': 'file', 'sha256': digest(path), 'bytes': path.stat().st_size,
            'executable': bool(path.stat().st_mode & 0o111)}


def native_receipt_allowed(relative):
    # pytest creates "current" directory symlinks and arbitrary scratch files.
    # Retain only the native fixture's explicit, non-secret case outputs.
    if len(relative.parts) != 3 or relative.parts[0] != 'pytest': return False
    if not re.fullmatch(r'test_[A-Za-z0-9_]+[0-9]+', relative.parts[1]): return False
    return (relative.name in {'native-trace.json', 'process-cleanup.json', 'capture.json',
                              'observation.json', 'preview.json', 'capture.log'}
            or re.fullmatch(r'(api|foreground|background)-[0-9]+\.log', relative.name) is not None)


def inventory(root, *, retained=False, receipts=False):
    result, total = {}, 0
    for path in sorted(root.rglob('*')):
        relative = path.relative_to(root)
        if private_path(relative): continue
        if receipts and relative.parts[0] == 'pytest' and not native_receipt_allowed(relative): continue
        if path.is_dir() and not path.is_symlink(): continue
        if retained and not retained_allowed(relative): continue
        result[relative.as_posix()] = value = entry(root, path)
        total += value.get('bytes', 0)
        if total > MAX_BYTES or len(result) > MAX_FILES: raise ValueError('Qualification copy bound exceeded')
    return result


def verify_inventory(root, expected, *, retained=False):
    if not expected or inventory(root, retained=retained) != expected:
        raise ValueError('Staged/retained file inventory changed')


def copy_entries(source, destination, expected, *, durable=False):
    directories = set()
    for name, value in expected.items():
        relative = Path(name)
        if relative.is_absolute() or '..' in relative.parts or private_path(relative):
            raise ValueError('Copy path escaped its allowlist')
        original, target = source / relative, destination / relative
        if entry(source, original) != value: raise ValueError('Input changed before copy')
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if any(p.is_symlink() for p in list(target.parents) if p != destination.parent):
            raise ValueError('Destination symlink refused')
        if target.is_symlink() and value['type'] != 'symlink': raise ValueError('Destination symlink refused')
        if not target.parent.resolve().is_relative_to(destination.resolve()): raise ValueError('Copy destination escaped')
        if value['type'] == 'symlink':
            if target.is_symlink():
                if os.readlink(target) != value['target']: raise ValueError('Destination symlink changed')
            else:
                target.symlink_to(value['target'])
        else:
            temporary = target.with_name(target.name + '.copying-' + uuid.uuid4().hex)
            with original.open('rb') as incoming, temporary.open('xb') as outgoing:
                shutil.copyfileobj(incoming, outgoing, 1024 * 1024)
                outgoing.flush()
                if durable: os.fsync(outgoing.fileno())
            temporary.chmod(0o700 if value['executable'] else 0o600)
            if digest(temporary) != value['sha256']: raise ValueError('Copied file hash differs')
            if target.exists() and digest(target) != value['sha256']:
                previous = target.with_name(target.name + '.previous-' + digest(target))
                if previous.exists(): raise ValueError('Previous receipt already exists; retain stage')
                target.rename(previous)
            temporary.replace(target)
        if entry(destination, target) != value or entry(source, original) != value:
            raise ValueError('Input/output changed during copy')
        if durable:
            for directory in (target.parent, *target.parent.parents):
                directories.add(directory)
                if directory == destination.parent: break
    # Sync newly created directory entries as well as every file/rename. Shared
    # ancestors are flushed once, deepest first, within the cleanup deadline.
    for directory in sorted(directories, key=lambda path: len(path.parts), reverse=True):
        descriptor = os.open(directory, os.O_RDONLY)
        try: os.fsync(descriptor)
        finally: os.close(descriptor)


def verify_source(root, expected_hash):
    manifest_path = root / '.qualification-source.json'
    if not expected_hash or digest(manifest_path) != expected_hash: raise ValueError('Staged source manifest changed')
    manifest = json.loads(manifest_path.read_text())
    actual = inventory(root)
    actual.pop('.qualification-source.json', None)
    if actual != manifest['files']: raise ValueError('Staged source bytes changed')
    return manifest


class OwnedStaging:
    def __init__(self, identity, head, *, base=Path('/private/tmp')):
        self.base = base.resolve()
        self.root = Path(tempfile.mkdtemp(prefix=identity + '-', dir=self.base))
        self.root.chmod(0o700)
        value = self.root.stat()
        self.owner = dict(identity=identity, head=head, uid=os.getuid(), device=value.st_dev, inode=value.st_ino)
        (self.root / 'owner.json').write_text(json.dumps(self.owner))
        self.source, self.output, self.qualified = (self.root / name for name in ('source', 'output', 'qualified'))
        for path in (self.source, self.output, self.qualified): path.mkdir(mode=0o700)
        self.record = dict(root=str(self.root), head=head, receipts_copied=False, removed=False)

    def check(self):
        value = self.root.stat()
        if (self.root.is_symlink() or self.root.parent != self.base or value.st_uid != os.getuid()
                or stat.S_IMODE(value.st_mode) != 0o700 or json.loads((self.root / 'owner.json').read_text()) != self.owner
                or (value.st_dev, value.st_ino) != (self.owner['device'], self.owner['inode'])):
            raise ValueError('Temporary stage ownership changed')

    def stage_source(self, root, names, packages):
        self.check(); expected = {}
        for name in names:
            if source_allowed(name, packages):
                value = entry(root, root / name)
                if value['type'] != 'file': raise ValueError('Tracked source symlink refused')
                expected[name] = value
        if not SOURCE_FILES <= set(expected): raise ValueError('Required runner source inputs missing')
        copy_entries(root, self.source, expected)
        manifest = dict(head=self.owner['head'], files=expected)
        path = self.source / '.qualification-source.json'; path.write_text(json.dumps(manifest, sort_keys=True))
        self.record.update(source_manifest_sha256=digest(path), source_files=expected)
        verify_source(self.source, self.record['source_manifest_sha256'])

    def stage_qualified(self, root, expected, *, destination=None):
        self.check(); verify_inventory(root, expected, retained=True)
        copy_entries(root, destination or self.qualified, expected)
        verify_inventory(destination or self.qualified, expected, retained=True)
        self.record['qualified_inputs'] = expected

    def copy_back(self, source, destination):
        self.check()
        expected = inventory(source, receipts=True)
        if 'host.json' in expected: raise ValueError('Helper cannot replace the host control receipt')
        copy_entries(source, destination, expected, durable=True)
        self.record.update(receipts_copied=True, copied_files=expected)

    def remove(self, *, resources_absent):
        self.check()
        if not resources_absent or not self.record['receipts_copied']:
            raise ValueError('Verified resources and durable receipts required before stage removal')
        shutil.rmtree(self.root); self.record['removed'] = not self.root.exists()


def verify_installed_wheel(wheel, site):
    """Compare only these six installed distributions with their verified wheels.

    Installer-rewritten RECORD is excluded; generated __pycache__ is ignored
    only because every native process uses a fresh cache prefix and -B.
    """
    site = site.resolve()
    with zipfile.ZipFile(wheel) as archive:
        expected, roots = {}, set()
        for entry in archive.infolist():
            if entry.is_dir(): continue
            path = Path(entry.filename)
            if path.is_absolute() or '..' in path.parts or '.data' in path.parts[0]:
                raise ValueError('Unsupported/unsafe native wheel layout')
            if path.name == 'RECORD' and path.parts[0].endswith('.dist-info'): continue
            expected[path.as_posix()] = hashlib.sha256(archive.read(entry)).hexdigest()
            if not path.parts[0].endswith('.dist-info'): roots.add(path.parts[0])
        if not roots: raise ValueError('Wheel has no owned package files')
        for name, digest in expected.items():
            installed = site / name
            if (not installed.is_file() or not installed.resolve().is_relative_to(site)
                    or hashlib.sha256(installed.read_bytes()).hexdigest() != digest):
                raise ValueError('Installed artifact differs from verified wheel: ' + name)
        actual = {path.relative_to(site).as_posix() for root in roots for path in (site / root).rglob('*')
                  if path.is_file() and '__pycache__' not in path.parts}
        owned = {name for name in expected if name.split('/')[0] in roots}
        if actual != owned: raise ValueError('Installed artifact file inventory differs from verified wheel')
    return dict(wheel=wheel.name, verified_files=len(expected), owned_package_files=len(owned))



def verify_owned_environment(root):
    sites = list((root / 'work/environment/lib').glob('python*/site-packages'))
    wheels = sorted((root / 'wheels').glob('*.whl'))
    if len(sites) != 1 or len(wheels) != 6:
        raise ValueError('Exactly one installed environment and six qualified wheels required')
    return [verify_installed_wheel(wheel, sites[0]) for wheel in wheels]


def verify_backend_environment(root):
    """Bind the native backend's three dependencies to current built wheels.

    Version strings alone cannot distinguish an older image's same-version code.
    These prerequisite wheels are separate from the six component artifacts.
    """
    sites = list((root / 'work/environment/lib').glob('python*/site-packages'))
    directory = root / 'work/source/backend-wheels'
    wheels = sorted(directory.glob('*.whl'))
    if len(sites) != 1 or len(wheels) != len(BACKEND_WHEELS):
        raise ValueError('Exactly three installed current backend dependency wheels required')
    result = {}
    for package, version in BACKEND_WHEELS.items():
        matches = list(directory.glob(package + '-' + version + '-*.whl'))
        if len(matches) != 1: raise ValueError('Missing/different backend dependency wheel: ' + package)
        wheel = matches[0]
        result[wheel.name] = dict(sha256=digest(wheel), **verify_installed_wheel(wheel, sites[0]))
    return result


def qualified_inputs(root, host):
    manifest = root / 'qualified-inputs.json'
    if manifest.is_symlink() or not manifest.is_file(): raise ValueError('Regular qualified manifest required')
    if digest(manifest) != host.get('qualified_manifest_sha256'):
        raise ValueError('Qualified installed-input manifest changed')
    expected = json.loads(manifest.read_text())
    verify_inventory(root, expected, retained=True)
    return expected


def record_qualified_inputs(root):
    verify_owned_environment(root)
    verify_backend_environment(root)
    expected = inventory(root, retained=True)
    path = root / 'qualified-inputs.json'
    path.write_text(json.dumps(expected, sort_keys=True))
    return digest(path)
