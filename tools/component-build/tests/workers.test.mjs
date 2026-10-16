import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync, symlinkSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { workerQualifier, checkWorker, buildWorker } from "../worker-qualifier.mjs";
import { checkComponents, repository } from "../check-components.mjs";
import { spawnSync } from "node:child_process";

function fixture(callback) {
  const root = mkdtempSync(join(tmpdir(), "tempo-worker-hook-test-"));
  mkdirSync(join(root, "worker"));
  const component = { id: "fixture", type: "worker", root: "worker", artifact: { qualifier: "qualify.mjs" } };
  const put = source => writeFileSync(join(root, "worker/qualify.mjs"), source);
  try { return callback({ root, component, put }); }
  finally { rmSync(root, { recursive: true, force: true }); }
}

test("worker qualifier validates containment and rejects missing or symlinked hooks", () => fixture(({ root, component, put }) => {
  assert.throws(() => workerQualifier(root, component), /Missing/);
  put('console.log("unused")');
  assert.match(workerQualifier(root, component).path, /qualify\.mjs$/);
  for (const qualifier of ["../escape.mjs", "/tmp/qualify.mjs"]) assert.throws(() => workerQualifier(root, { ...component, artifact: { qualifier } }), /escaping/);
  writeFileSync(join(root, "outside.mjs"), "");
  rmSync(join(root, "worker/qualify.mjs"));
  symlinkSync(join(root, "outside.mjs"), join(root, "worker/qualify.mjs"));
  assert.throws(() => workerQualifier(root, component), /Symlink/);
}));

test("worker boundary propagates hook failure and rejects empty reports", () => fixture(({ root, component, put }) => {
  put("process.exit(7)");
  assert.throws(() => checkWorker(root, component), /failed/);
  put("console.log('{}')");
  assert.throws(() => checkWorker(root, component), /Empty/);
  put("console.log(JSON.stringify({success:true,files:2,exports:['infer']}))");
  assert.equal(checkWorker(root, component).files, 2);
}));

test("worker restricted hook requires executed cases and verifies installed artifact bytes", () => fixture(({ root, component, put }) => {
  const artifactDirectory = join(root, "artifacts"); mkdirSync(artifactDirectory);
  const reportPath = join(root, "report.json");
  const prefix = `import {writeFileSync} from 'node:fs'; import {createHash} from 'node:crypto'; const a=process.argv; const report=a[a.indexOf('--report')+1]; const output=a[a.indexOf('--artifacts')+1]; writeFileSync(output+'/worker.tgz','artifact');`;
  put(prefix + `writeFileSync(report,JSON.stringify({complete:true,artifacts:[{name:'worker.tgz',sha256:createHash('sha256').update('artifact').digest('hex')}],cases:{success:true,passed:0,failed:0,executedIdentities:[]}}));`);
  assert.throws(() => buildWorker(root, component, reportPath, artifactDirectory), /zero-case/);
  put(prefix + `writeFileSync(report,JSON.stringify({complete:true,artifacts:[{name:'worker.tgz',sha256:createHash('sha256').update('artifact').digest('hex')}],cases:{success:true,passed:1,failed:0,executedIdentities:['installed artifact runs']}}));`);
  assert.equal(buildWorker(root, component, reportPath, artifactDirectory).cases.passed, 1);
  put(prefix + `writeFileSync(report,JSON.stringify({complete:true,artifacts:[{name:'worker.tgz',sha256:'0'.repeat(64)}],cases:{success:true,passed:1,failed:0,executedIdentities:['installed artifact runs']}}));`);
  assert.throws(() => buildWorker(root, component, reportPath, artifactDirectory), /hash mismatch/);
}));

test("implemented worker routes npm metadata to its own checker and lists without executing it", () => fixture(({ root, component, put }) => {
  Object.assign(component, { maintainer: "Tempo", status: "implemented", publicExports: ["infer"], dependencies: [], buildInputs: ["qualify.mjs", "package.json"], buildCommand: "node qualify.mjs restricted", tests: [{ owner: "fixture", kind: "node", command: "node test.mjs", inputs: ["qualify.mjs"] }], toolchain: {}, dataOwnership: {}, artifact: { ...component.artifact, name: "@tempo/fixture", version: "1.0.0" } });
  writeFileSync(join(root, "components.json"), JSON.stringify({ schemaVersion: 1, components: [component] }));
  writeFileSync(join(root, "worker/package.json"), JSON.stringify({ name: "@tempo/fixture", version: "1.0.0" }));
  put("console.log(JSON.stringify({success:true,files:1,exports:['infer']}))");
  assert.equal(checkComponents(root, "/python-must-not-run", { componentIds: ["fixture"] })[0].success, true);
  put("throw new Error('listing must not execute qualifiers')");
  const environment = { ...process.env, TEMPO_COMPONENT_ROOT: root, TEMPO_COMPONENT_PYTHON: "/python-must-not-run" };
  delete environment.NODE_TEST_CONTEXT;
  for (const entrypoint of ["check-components.mjs", "restricted-build.mjs", "test-lane.mjs"]) {
    const result = spawnSync(process.execPath, [join(repository, "tools/component-build", entrypoint), "--component", "fixture", "--list"], { env: environment, encoding: "utf8" });
    assert.equal(result.status, 0, result.stdout + result.stderr);
    assert.match(result.stdout, /fixture/);
  }
}));
