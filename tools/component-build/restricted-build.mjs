import { cpSync, mkdtempSync, readFileSync, readdirSync, mkdirSync, writeFileSync, rmSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { resolve, join } from "node:path";
import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { performance } from "node:perf_hooks";
import { repository, checkTypeScript, validateManifest } from "./check-components.mjs";

const manifest = JSON.parse(readFileSync(resolve(repository, "components.json"), "utf8"));
const workspace = mkdtempSync(join(tmpdir(), "tempo-m02-restricted-"));
const report = { node: process.version, stages: [], artifacts: [], sourceRevision: "", sourceInputs: {}, complete: false };
const environment = { ...process.env, UV_CACHE_DIR: process.env.UV_CACHE_DIR ?? join(tmpdir(), "tempo-m02-uv-cache"), PYTHONPATH: "", PYTEST_DISABLE_PLUGIN_AUTOLOAD: "1" };
delete environment.NODE_PATH;
delete environment.PYTHONHOME;
function command(label, executable, args, cwd, expected = 0) {
  const started = performance.now();
  const result = spawnSync(executable, args, { cwd, env: environment, encoding: "utf8", maxBuffer: 8 * 1024 * 1024 });
  const stage = { label, command: [executable, ...args], seconds: (performance.now() - started) / 1000, exitCode: result.status, stdout: result.stdout, stderr: result.stderr };
  report.stages.push(stage);
  console.log(JSON.stringify({ label, seconds: stage.seconds, exitCode: result.status }));
  if (result.status !== expected) throw new Error(label + " failed: " + result.stdout + result.stderr);
  return result.stdout;
}
function copyInput(component, input, destination) {
  const source = resolve(repository, component.root, input);
  if (!existsSync(source)) throw new Error("Missing declared input: " + source);
  const target = resolve(destination, input);
  mkdirSync(resolve(target, ".."), { recursive: true });
  cpSync(source, target, { recursive: true, filter: path => !/[\\/](__pycache__|\.pytest_cache|node_modules|dist|\.venv)([\\/]|$)/.test(path) });
}
function fingerprint(path) { return createHash("sha256").update(readFileSync(path)).digest("hex"); }
function artifact(path) { report.artifacts.push({ name: path.split("/").at(-1), sha256: fingerprint(path) }); }
try {
  report.sourceRevision = command("revision", "git", ["rev-parse", "HEAD"], repository).trim();
  report.dirty = command("working-tree", "git", ["status", "--porcelain"], repository).trim();
  validateManifest(manifest);
  const chess = manifest.components.find(component => component.id === "chess-line");
  const frontendBuild = join(workspace, "chess-build"); mkdirSync(frontendBuild);
  for (const input of [...chess.buildInputs, "test.mjs", "tests", "README.md"]) copyInput(chess, input, frontendBuild);
  report.sourceInputs.chessLock = fingerprint(join(frontendBuild, "package-lock.json"));
  command("frontend-setup", "npm", ["ci", "--ignore-scripts", "--workspaces=false"], frontendBuild);
  checkTypeScript(frontendBuild, chess);
  command("frontend-build", "npm", ["run", "build"], frontendBuild);
  const pack = JSON.parse(command("frontend-pack", "npm", ["pack", "--json", "--ignore-scripts"], frontendBuild))[0];
  const tarball = join(frontendBuild, pack.filename); artifact(tarball);
  const frontendConsumer = join(workspace, "chess-consumer"); mkdirSync(frontendConsumer);
  writeFileSync(join(frontendConsumer, "package.json"), JSON.stringify({ private: true, type: "module", dependencies: { "@tempo/chess-line": "file:" + tarball, "chess.js": "1.4.0" } }));
  // The artifact runtime tree is locked to the selected package's runtime versions.
  command("frontend-install-artifact", "npm", ["install", "--ignore-scripts", "--workspaces=false"], frontendConsumer);
  cpSync(join(frontendBuild, "tests"), join(frontendConsumer, "tests"), { recursive: true });
  cpSync(join(frontendBuild, "test.mjs"), join(frontendConsumer, "test.mjs"));
  rmSync(join(frontendBuild, "src"), { recursive: true });
  command("frontend-installed-tests", "node", ["test.mjs"], frontendConsumer);
  command("frontend-zero-selected", "node", ["test.mjs", "no-such-test-identity"], frontendConsumer, 1);
  command("frontend-dependencies", "npm", ["ls", "--all", "--json"], frontendConsumer);
  const scheduling = manifest.components.find(component => component.id === "scheduling");
  const pythonBuild = join(workspace, "scheduling-build"); mkdirSync(pythonBuild);
  for (const input of [...scheduling.buildInputs, "tests", "README.md", "REGRESSIONS.md"]) copyInput(scheduling, input, pythonBuild);
  report.sourceInputs.schedulingLock = fingerprint(join(pythonBuild, "uv.lock"));
  command("python-boundary", process.env.TEMPO_COMPONENT_PYTHON ?? "python3", [resolve(repository, "tools/component-build/check-python.py"), pythonBuild, JSON.stringify(scheduling.dependencies), JSON.stringify(scheduling.publicExports)], pythonBuild);
  command("python-build-setup", "uv", ["sync", "--locked", "--no-editable", "--python", "3.14"], pythonBuild);
  command("python-build", join(pythonBuild, ".venv/bin/python"), ["-m", "build", "--no-isolation"], pythonBuild);
  const wheelName = readdirSync(join(pythonBuild, "dist")).find(name => name.endsWith(".whl"));
  const wheel = join(pythonBuild, "dist", wheelName);
  for (const name of readdirSync(join(pythonBuild, "dist"))) artifact(join(pythonBuild, "dist", name));
  const lockedRequirements = command("python-export-lock", "uv", ["export", "--locked", "--no-emit-project", "--no-hashes"], pythonBuild);
  const requirements = join(workspace, "requirements.lock.txt"); writeFileSync(requirements, lockedRequirements);
  rmSync(join(pythonBuild, "src"), { recursive: true });
  for (const version of ["3.12", "3.14"]) {
    const consumer = join(workspace, "python-" + version); mkdirSync(consumer);
    const interpreter = join(consumer, ".venv/bin/python");
    command("python-" + version + "-environment", "uv", ["venv", "--python", version, join(consumer, ".venv")], consumer);
    command("python-" + version + "-install", "uv", ["pip", "install", "--python", interpreter, "-r", requirements, wheel], consumer);
    command("python-" + version + "-runtime", interpreter, ["--version"], consumer);
    cpSync(join(pythonBuild, "tests"), join(consumer, "tests"), { recursive: true });
    const guard = `import sys\nfrom importlib.abc import MetaPathFinder\nclass Guard(MetaPathFinder):\n    def find_spec(self, fullname, path=None, target=None):\n        if fullname.split('.')[0] in {'app','fastapi','celery','psycopg','psycopg_pool','redis','httpx','sqlite3'}:\n            raise AssertionError('Unexpected service dependency: ' + fullname)\nsys.meta_path.insert(0, Guard())\ndef reject_services(event, arguments):\n    if event in {'socket.connect','socket.bind','sqlite3.connect'}:\n        raise AssertionError('Service initialization: ' + event)\nsys.addaudithook(reject_services)\nimport tempo_scheduling\nassert 'site-packages' in tempo_scheduling.__file__\nassert all(${JSON.stringify(repository)} not in path for path in sys.path)\nimport pytest\nraise SystemExit(pytest.main(sys.argv[1:]))\n`;
    command("python-" + version + "-collection", interpreter, ["-I", "-m", "pytest", "tests", "--collect-only", "-q"], consumer);
    command("python-" + version + "-tests", interpreter, ["-I", "-m", "pytest", "tests", "-q"], consumer);
    command("python-" + version + "-guarded-runtime", interpreter, ["-I", "-c", guard, "tests", "-q"], consumer);
    command("python-" + version + "-zero-selected", interpreter, ["-I", "-m", "pytest", "tests", "-k", "no_such_identity", "-q"], consumer, 5);
    command("python-" + version + "-dependencies", "uv", ["pip", "freeze", "--python", interpreter], consumer);
  }
  report.complete = true;
} catch (error) {
  report.error = error.message;
  console.error(error.message);
  process.exitCode = 1;
} finally {
  const destination = resolve(process.argv[2] ?? resolve(repository, "test-results/m02/restricted-build.json"));
  mkdirSync(resolve(destination, ".."), { recursive: true });
  writeFileSync(destination, JSON.stringify(report, null, 2) + "\n");
  const artifactDirectory = join(resolve(destination, ".."), "artifacts"); mkdirSync(artifactDirectory, { recursive: true });
  for (const directory of [join(workspace, "chess-build"), join(workspace, "scheduling-build/dist")]) if (existsSync(directory)) {
    for (const name of readdirSync(directory).filter(name => /\.(tgz|whl|tar\.gz)$/.test(name))) cpSync(join(directory, name), join(artifactDirectory, name));
  }
  rmSync(workspace, { recursive: true, force: true });
  console.log(JSON.stringify({ report: destination, temporaryWorkspaceRemoved: !existsSync(workspace), complete: report.complete }));
}
