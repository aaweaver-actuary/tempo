import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync, mkdirSync, writeFileSync, rmSync, symlinkSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join, resolve} from 'node:path';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
const repository = resolve(fileURLToPath(new URL('../../../', import.meta.url)));
const checker = join(repository, 'tools/component-build/check-python.py');
const python = process.env.TEMPO_COMPONENT_PYTHON ?? 'python3';
function fixture(t, declared = false) {
  const directory = mkdtempSync(join(tmpdir(), 'tempo-k-wheel-inputs-'));
  t.after(() => rmSync(directory, {recursive: true, force: true}));
  const root = join(directory, 'services/fixture'), database = join(directory, 'database/tempo_fixture_database');
  mkdirSync(join(root, 'src/tempo_fixture'), {recursive: true});
  mkdirSync(join(database, 'adapters'), {recursive: true});
  writeFileSync(join(root, 'src/tempo_fixture/__init__.py'), '__all__ = []\n');
  writeFileSync(join(database, '__init__.py'), '');
  const deps = declared ? ['tempo_learning_remediation'] : [];
  writeFileSync(join(root, 'pyproject.toml'), `[project]\nname = "tempo-fixture"\nversion = "0.1.0"\ndependencies = ${JSON.stringify(declared ? ['tempo-learning-remediation==0.1.0'] : [])}\n`);
  const inputs = ['src', '../../database/tempo_fixture_database'];
  const check = () => spawnSync(python, [checker, root, JSON.stringify(deps), '[]', JSON.stringify({name: 'tempo-fixture', version: '0.1.0'}), JSON.stringify(inputs)], {encoding: 'utf8'});
  return {root, database, directory, check};
}
test('Python centralized database auditing separates wheel runtime from retained sdist qualification inputs', t => {
  const f = fixture(t);
  for (const [directory, source] of [['tests', 'import importlib.util\n'], ['tooling', 'import fixture_plan\n']]) {
    mkdirSync(join(f.database, directory)); writeFileSync(join(f.database, directory, 'check.py'), source);
  }
  const passed = f.check(); assert.equal(passed.status, 0, passed.stderr);
  writeFileSync(join(f.database, 'adapters/runtime.py'), 'import importlib.util\n');
  const rejected = f.check(); assert.notEqual(rejected.status, 0); assert.match(rejected.stderr, /dynamic loader/);
});
test('Python wheel exclusions never hide nested runtime code or source symlink escapes', t => {
  const f = fixture(t);
  mkdirSync(join(f.database, 'adapters/tests')); writeFileSync(join(f.database, 'adapters/tests/code.py'), 'import undeclared_runtime\n');
  assert.match(f.check().stderr, /undeclared dependency/);
  rmSync(join(f.database, 'adapters/tests'), {recursive: true});
  mkdirSync(join(f.database, 'tests'));
  writeFileSync(join(f.directory, 'outside.py'), ''); symlinkSync(join(f.directory, 'outside.py'), join(f.database, 'tests/escaped.py'));
  assert.match(f.check().stderr, /source escape/);
});
test('Python bundled remediation import requires its exact declared distribution', t => {
  for (const declared of [false, true]) {
    const f = fixture(t, declared); writeFileSync(join(f.database, 'adapters/migrate.py'), 'import tempo_remediation_database\n');
    const result = f.check();
    if (declared) assert.equal(result.status, 0, result.stderr);
    else { assert.notEqual(result.status, 0); assert.match(result.stderr, /undeclared dependency tempo_remediation_database/); }
    writeFileSync(join(f.database, 'adapters/migrate.py'), 'import tempo_unrecognized_database\n');
    assert.match(f.check().stderr, /undeclared dependency tempo_unrecognized_database/);
  }
});
test('Centralized wheel hooks exclude qualification directories while sdist hooks retain complete database inputs', () => {
  const source = `
from pathlib import Path
import runpy, sys, types
from tempfile import TemporaryDirectory
base = types.ModuleType('hatchling.builders.hooks.plugin.interface')
base.BuildHookInterface = object
sys.modules['hatchling.builders.hooks.plugin.interface'] = base
repository = Path(sys.argv[1])
for owner, name, excludes_cache in [('services/learning/runtime', 'tempo_learning_database', True), ('services/learning/remediation', 'tempo_remediation_database', True), ('services/content', 'tempo_content_database', False), ('services/game-evidence', 'tempo_game_evidence_database', False)]:
    hook = runpy.run_path(str(repository / owner / 'hatch_build.py'))['CustomBuildHook']
    with TemporaryDirectory() as directory:
        root = Path(directory)
        database = root / 'database' / name
        database.mkdir(parents=True)
        for entry in ['__init__.py', 'ddl', 'adapters', 'tests', 'tooling', '__pycache__']:
            path = database / entry
            if '.' in entry: path.write_text('')
            else: path.mkdir()
        instance = hook()
        instance.root = str(root)
        instance.target_name = 'wheel'
        data = {'force_include': {}}
        instance.initialize(None, data)
        assert {Path(path).name for path in data['force_include']} == {'__init__.py', 'ddl', 'adapters'} | (set() if excludes_cache else {'__pycache__'})
        instance.target_name = 'sdist'
        data = {'force_include': {}}
        instance.initialize(None, data)
        assert data['force_include'] == {str(database): 'database/' + name}
`;
  const result = spawnSync(python, ['-B', '-c', source, repository], {encoding: 'utf8'});
  assert.equal(result.status, 0, result.stdout + result.stderr);
});
