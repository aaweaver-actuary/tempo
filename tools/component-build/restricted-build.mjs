import { runWithDevelopmentBudget } from '../../scripts/development-budget.mjs';
import { cpSync, mkdtempSync, readFileSync, readdirSync, mkdirSync, writeFileSync, rmSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { resolve, join } from "node:path";
import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { performance } from "node:perf_hooks";
import { repository, checkTypeScript, validateManifest } from "./check-components.mjs";
import { parseSelection, readManifest, selectComponents } from "./selection.mjs";
import { buildRestrictedContracts } from "./restricted-contracts.mjs";
import { buildWorker } from "./worker-qualifier.mjs";

export function restrictedBuild(options) {
  const root = process.env.TEMPO_COMPONENT_ROOT ?? repository;
  const manifest = readManifest(root);
  const selected = selectComponents(manifest, options.componentIds, ["chess-line", "scheduling"]);
  validateManifest(manifest, root, { componentIds: selected.map(component => component.id) });
  if (options.suiteIds || options.consumers || options.positional.length > 1) throw new Error("Unexpected restricted build argument");
  if (options.list) { console.log(JSON.stringify({ selected }, null, 2)); return; }
  for (const component of selected) if (!["library", "contract", "worker"].includes(component.type)) throw new Error("No restricted artifact builder for " + component.id);
  const started = performance.now();
  const workspace = mkdtempSync(join(tmpdir(), "tempo-m02-restricted-"));
  const report = { node: process.version, stages: [], artifacts: [], sourceRevision: "", sourceInputs: {}, selectedComponents: selected.map(component => component.id), complete: false };
  const environment = { ...process.env, UV_CACHE_DIR: process.env.UV_CACHE_DIR ?? join(tmpdir(), "tempo-m02-uv-cache"), PYTHONPATH: "", PYTEST_DISABLE_PLUGIN_AUTOLOAD: "1" };
  delete environment.NODE_PATH;
  delete environment.PYTHONHOME;
  delete environment.VIRTUAL_ENV;
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
    const source = resolve(root, component.root, input);
    if (!existsSync(source)) throw new Error("Missing declared input: " + source);
    const target = resolve(destination, input);
    mkdirSync(resolve(target, ".."), { recursive: true });
    cpSync(source, target, { recursive: true, filter: path => !/[\\/](__pycache__|\.pytest_cache|node_modules|dist|\.venv)([\\/]|$)/.test(path) });
  }
  function fingerprint(path) { return createHash("sha256").update(readFileSync(path)).digest("hex"); }
  function artifact(path) { report.artifacts.push({ name: path.split("/").at(-1), sha256: fingerprint(path) }); }
  const artifactDirectories = [];
  try {
    report.sourceRevision = command("revision", "git", ["rev-parse", "HEAD"], root).trim();
    report.dirty = command("working-tree", "git", ["status", "--porcelain"], root).trim();
    for (const worker of selected.filter(component => component.type === "worker")) {
      const workerDirectory = join(workspace, worker.id + "-artifacts"); mkdirSync(workerDirectory);
      const workerReport = buildWorker(root, worker, join(workspace, worker.id + "-report.json"), workerDirectory);
      report.stages.push({ label: worker.id + "-restricted", ...workerReport });
      report.artifacts.push(...workerReport.artifacts);
      artifactDirectories.push(workerDirectory);
    }
    const npmComponents = selected.filter(component => component.type !== "worker" && existsSync(resolve(root, component.root, "package.json")));
    const npmArtifacts = new Map();
    const remaining = new Set(npmComponents);
    while (remaining.size) {
      const component = [...remaining].find(candidate => candidate.dependencies.every(dependency =>
        !npmComponents.some(owner => owner.artifact.name === dependency) || npmArtifacts.has(dependency)));
      if (!component) throw new Error("Circular npm artifact dependencies");
      remaining.delete(component);
      const prefix = component.id === "chess-line" ? "frontend" : component.id;
      const frontendBuild = join(workspace, component.id + "-build"); mkdirSync(frontendBuild);
      for (const input of [...component.buildInputs, "test.mjs", "tests", "README.md"]) copyInput(component, input, frontendBuild);
      report.sourceInputs[component.id + "Lock"] = fingerprint(join(frontendBuild, "package-lock.json"));
      // Local lockfile links resolve to installed dependency artifacts, never sibling sources.
      for (const dependency of component.dependencies) {
        const artifactPath = npmArtifacts.get(dependency);
        if (!artifactPath) continue;
        const owner = npmComponents.find(candidate => candidate.artifact.name === dependency);
        const dependencyDirectory = join(workspace, owner.id);
        if (!existsSync(dependencyDirectory)) {
          mkdirSync(dependencyDirectory);
          command(prefix + "-dependency-artifact", "tar", ["-xzf", artifactPath, "--strip-components=1", "-C", dependencyDirectory], frontendBuild);
        }
      }
      command(prefix + "-setup", "npm", ["ci", "--ignore-scripts", "--workspaces=false"], frontendBuild);
      checkTypeScript(frontendBuild, component);
      command(prefix + "-build", "npm", ["run", "build"], frontendBuild);
      const pack = JSON.parse(command(prefix + "-pack", "npm", ["pack", "--json", "--ignore-scripts"], frontendBuild))[0];
      const tarball = join(frontendBuild, pack.filename); artifact(tarball); npmArtifacts.set(component.artifact.name, tarball);
      const metadata = JSON.parse(readFileSync(join(frontendBuild, "package.json"), "utf8"));
      const dependencies = { ...metadata.dependencies, [component.artifact.name]: "file:" + tarball };
      for (const name of Object.keys(dependencies)) if (npmArtifacts.has(name)) dependencies[name] = "file:" + npmArtifacts.get(name);
      const frontendConsumer = join(workspace, component.id + "-consumer"); mkdirSync(frontendConsumer);
      writeFileSync(join(frontendConsumer, "package.json"), JSON.stringify({ private: true, type: "module", dependencies, devDependencies: metadata.devDependencies }));
      command(prefix + "-install-artifact", "npm", ["install", "--ignore-scripts", "--workspaces=false"], frontendConsumer);
      cpSync(join(frontendBuild, "tests"), join(frontendConsumer, "tests"), { recursive: true });
      cpSync(join(frontendBuild, "test.mjs"), join(frontendConsumer, "test.mjs"));
      rmSync(join(frontendBuild, "src"), { recursive: true });
      command(prefix + "-installed-tests", "node", ["test.mjs"], frontendConsumer);
      command(prefix + "-zero-selected", "node", ["test.mjs", "no-such-test-identity"], frontendConsumer, 1);
      command(prefix + "-dependencies", "npm", ["ls", "--all", "--json"], frontendConsumer);
      artifactDirectories.push(frontendBuild);
    }
    for (const pythonComponent of selected.filter(component => component.type === "library" && !npmComponents.includes(component))) {
      const pythonBuild = join(workspace, pythonComponent.id + "-build"); mkdirSync(pythonBuild);
      for (const input of [...pythonComponent.buildInputs, "tests", "README.md", "REGRESSIONS.md"]) copyInput(pythonComponent, input, pythonBuild);
      report.sourceInputs[pythonComponent.id + "Lock"] = fingerprint(join(pythonBuild, "uv.lock"));
      const boundary = JSON.parse(command("python-boundary", process.env.TEMPO_COMPONENT_PYTHON ?? "python3", [resolve(repository, "tools/component-build/check-python.py"), pythonBuild, JSON.stringify(pythonComponent.dependencies), JSON.stringify(pythonComponent.publicExports), JSON.stringify(pythonComponent.artifact)], pythonBuild));
      command("python-build-setup", "uv", ["sync", "--locked", "--no-editable", "--python", "3.14"], pythonBuild);
      command("python-build", join(pythonBuild, ".venv/bin/python"), ["-m", "build", "--no-isolation"], pythonBuild);
      const wheelName = readdirSync(join(pythonBuild, "dist")).find(name => name.endsWith(".whl"));
      const wheel = join(pythonBuild, "dist", wheelName);
      for (const name of readdirSync(join(pythonBuild, "dist"))) artifact(join(pythonBuild, "dist", name));
      const lockedRequirements = command("python-export-lock", "uv", ["export", "--locked", "--no-emit-project", "--no-hashes"], pythonBuild);
      const requirements = join(workspace, "requirements.lock.txt"); writeFileSync(requirements, lockedRequirements);
      artifactDirectories.push(join(pythonBuild, "dist"));
      rmSync(join(pythonBuild, "src"), { recursive: true });
      for (const version of ["3.12", "3.14"]) {
        const consumer = join(workspace, pythonComponent.id + "-python-" + version); mkdirSync(consumer);
        const interpreter = join(consumer, ".venv/bin/python");
        command("python-" + version + "-environment", "uv", ["venv", "--python", version, join(consumer, ".venv")], consumer);
        command("python-" + version + "-install", "uv", ["pip", "install", "--python", interpreter, "-r", requirements, wheel], consumer);
        command("python-" + version + "-runtime", interpreter, ["--version"], consumer);
        cpSync(join(pythonBuild, "tests"), join(consumer, "tests"), { recursive: true });
        const guard = `import sys\nfrom importlib.abc import MetaPathFinder\nclass Guard(MetaPathFinder):\n    def find_spec(self, fullname, path=None, target=None):\n        if fullname.split('.')[0] in {'app','fastapi','celery','psycopg','psycopg_pool','redis','httpx','sqlite3'}:\n            raise AssertionError('Unexpected service dependency: ' + fullname)\nsys.meta_path.insert(0, Guard())\ndef reject_services(event, arguments):\n    if event in {'socket.connect','socket.bind','sqlite3.connect'}:\n        raise AssertionError('Service initialization: ' + event)\nsys.addaudithook(reject_services)\nimport importlib\nowned_package = importlib.import_module(${JSON.stringify(boundary.package)})\nassert 'site-packages' in owned_package.__file__\nassert all(${JSON.stringify(repository)} not in path for path in sys.path)\nimport pytest\nraise SystemExit(pytest.main(sys.argv[1:]))\n`;
        command("python-" + version + "-collection", interpreter, ["-I", "-m", "pytest", "tests", "--collect-only", "-q"], consumer);
        const results = join(consumer, "results"); mkdirSync(results);
        command("python-" + version + "-tests", interpreter, ["-I", "-m", "pytest", "tests", "-q", "--junitxml=" + join(results, "regular.xml")], consumer);
        command("python-" + version + "-guarded-runtime", interpreter, ["-I", "-c", guard, "tests", "-q", "--junitxml=" + join(results, "guarded.xml")], consumer);
        command("python-" + version + "-executed-identities", interpreter, [resolve(repository, "tools/component-build/case-results.py"), results], consumer);
        command("python-" + version + "-zero-selected", interpreter, ["-I", "-m", "pytest", "tests", "-k", "no_such_identity", "-q"], consumer, 5);
        command("python-" + version + "-dependencies", "uv", ["pip", "freeze", "--python", interpreter], consumer);
      }
    }
    artifactDirectories.push(...buildRestrictedContracts(selected.filter(component => component.type === "contract"), { root, repository, workspace, command, copyInput, artifact, report, fingerprint }));
    report.complete = true;
  } catch (error) {
    report.error = error.message;
    console.error(error.message);
    process.exitCode = 1;
  } finally {
    const destination = resolve(options.positional[0] ?? resolve(root, "test-results/m02/restricted-build.json"));
    mkdirSync(resolve(destination, ".."), { recursive: true });
    const artifactDirectory = join(resolve(destination, ".."), "artifacts"); mkdirSync(artifactDirectory, { recursive: true });
    for (const directory of artifactDirectories) if (existsSync(directory)) {
      for (const name of readdirSync(directory).filter(name => /\.(tgz|whl|tar\.gz)$/.test(name))) cpSync(join(directory, name), join(artifactDirectory, name));
    }
    rmSync(workspace, { recursive: true, force: true });
    report.wallSeconds = (performance.now() - started) / 1000;
    writeFileSync(destination, JSON.stringify(report, null, 2) + "\n");
    console.log(JSON.stringify({ report: destination, temporaryWorkspaceRemoved: !existsSync(workspace), complete: report.complete }));
  }

}
if (process.argv[1] && resolve(process.argv[1]) === new URL(import.meta.url).pathname) {
  try {
    if (!process.argv.includes('--list') && !process.env.TEMPO_DEVELOPMENT_BUDGET_TOKEN)
      process.exitCode = await runWithDevelopmentBudget(process.execPath, process.argv.slice(1));
    else restrictedBuild(parseSelection(process.argv.slice(2)));
  }
  catch (error) { console.error(error.message); process.exitCode = 1; }
}
