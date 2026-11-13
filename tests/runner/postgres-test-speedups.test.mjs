import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtempSync, readFileSync, readdirSync, rmSync, mkdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { runInNewContext } from "node:vm";
import { test } from "node:test";
import { Chess } from "chess.js";
import { buildPostgresPlaywrightArguments, parsePostgresTestOptions } from "../../database/tests/postgres-test-options.mjs";
import { maintainedQueueCases, unresolvedQueueCases, excludedQueueCases, verifyMaintainedQueueReceipt, verifyCheckpoint1QueueReceipt, verifyCheckpoint1BrowserReceipt, checkpoint1BrowserCases, checkpoint1NativeChecks, checkpoint1QueueCases, assertDisposableVolumes, backgroundWorkloadConsumers, executeDiagnosticCleanup, executeIsolatedBackgroundWorkload, executePostgresTestPlan, postgresTestStages, restoreBackgroundWorkloadConsumers } from "../../database/tests/postgres-test-plan.mjs";
import { assertNoCompletedFixtureConflict, backgroundPublicationPgn,
  readStudyQueuePublication, repertoireLimitRecreationPgn, studyDurabilityPgn } from "../../database/tests/postgres-test-fixture.mjs";
import { createScenarioTimer, postgresCommandTiming } from "../../scripts/test-scenario-timings.mjs";
import { validatePostgresScenarios } from "../../scripts/ci-run-layer.mjs";
import { stopApplicationsAndVerifyWorkerSignals } from "../../database/tests/redis/check-redis-readiness.mjs";
import { resolvePython } from "../../scripts/resolve-python.mjs";

const root = fileURLToPath(new URL("../../", import.meta.url));
const parse = (args) => parsePostgresTestOptions(args, {}, root);

test("P14 checkpoint has one fixed browser and native selection", () => {
  const options = parse(["--mode", "study", "--p14-checkpoint1"]);
  assert.deepEqual(options.nativeChecks, checkpoint1NativeChecks);
  assert.equal(checkpoint1BrowserCases.length, 11);
  for (const [, title] of checkpoint1BrowserCases) assert(new RegExp(options.browserGrep).test(title));
  for (const args of [["--p14-checkpoint1"], ["--mode", "browser", "--p14-checkpoint1"], ["--mode", "study", "--p14-checkpoint1", "--browser-grep", "other"], ["--mode", "study", "--p14-checkpoint1", "--native-check", "position-grade-effects"], ["--mode", "study", "--p14-checkpoint1", "--p14-checkpoint1"]]) assert.throws(() => parse(args));
});
test("P14 queue observer rejects a foreign project before connecting", () => {
  const result = spawnSync(resolvePython(), ["database/tests/p14-queue-progress-observer.py"], {
    cwd: root, env: { ...process.env, TEMPO_TEST_INSTANCE: "disposable", TEMPO_P14_OBSERVER_PROJECT: "foreign" },
    encoding: "utf8", timeout: 5000,
  });
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /AssertionError/);
  assert.equal(result.stdout, "");
});
test("P14 observer never emits payload, lease or wake secrets", () => {
  const probe = `import json,runpy
m=runpy.run_path('database/tests/p14-queue-progress-observer.py',run_name='observer_probe')
secret='SENTINEL_SECRET_VALUE'
payload=json.dumps({'queue_date':'2026-11-12','_queue_phase':'admit_due','_refresh_cycle_epoch':5,'after_card_id':secret,'other':secret})
row={'id':secret,'deduplication_key':secret,'generation':1,'state':'leased','phase':secret,'payload_json':payload,'attempt_count':1,'max_attempts':5,'next_attempt_at':secret,'lease_token':secret,'lease_expires_at':secret,'has_error':True,'error_digest':'digest','created_at':secret,'updated_at':secret}
event={'task_id':secret,'generation':1,'event':secret,'phase':secret,'has_detail':True,'detail_digest':'digest','created_at':secret}
projection={'queue_date':secret,'state':'ready','generation':1,'refresh_pending':1,'requested_refresh_epoch':5,'published_refresh_epoch':1,'has_error':False,'error_digest':None,'updated_at':secret}
result={'task':m['safe_task'](row),'event':m['safe_event'](event),'projection':m['safe_projection'](projection),'wake':m['safe_wake']([secret,'published','1',secret,secret,secret],secret)}
text=json.dumps(result)
assert secret not in text
assert result['task']['payload']['phase']=='admit_due' and result['task']['payload']['_refresh_cycle_epoch']==5
assert result['task']['lease_present'] and result['wake']['owner_present']
print('safe')`;
  const result = spawnSync(resolvePython(), ["-c", probe], { cwd: root, encoding: "utf8", timeout: 5000,
    env: { ...process.env, PYTHONPATH: "backend:database", TEMPO_TEST_INSTANCE: "disposable",
      TEMPO_P14_OBSERVER_PROJECT: "tempo-pg-regressions-1-12345678",
      TEMPO_DATABASE_READ_URL: "postgresql://tempo_reader@postgres:5432/tempo", TEMPO_REDIS_URL: "redis://redis:6379/0" } });
  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.stdout.trim(), "safe");
});
test("P14 frozen owner bridge refuses foreign or incomplete identities before connecting", () => {
  const script = `import os,runpy
os.environ['PGPASSFILE']='/run/secrets/admin_pgpass'
m=runpy.run_path('backend/tests/native_study_fixture.py'); f=m['owned_postgres'].__wrapped__
def no_connection(*args,**kwargs): raise AssertionError('unexpected connection')
f.__globals__['psycopg'].connect=no_connection
for dsn,project,revision in [('postgresql://postgres@postgres:5432/tempo','tempo-pg-regressions-1-12345678','a'*40),('postgresql://postgres@postgres:5432/tempo_position_test_'+'a'*32,'foreign','a'*40),('postgresql://postgres@localhost:5432/tempo_position_test_'+'a'*32,'tempo-pg-regressions-1-12345678','a'*40),('postgresql://postgres@postgres:5432/tempo_position_test_'+'a'*32+'?hostaddr=127.0.0.1','tempo-pg-regressions-1-12345678','a'*40),('postgresql://postgres@postgres:5432/tempo_position_test_'+'a'*32+'?service=foreign','tempo-pg-regressions-1-12345678','a'*40),('postgresql://postgres@postgres:5432/tempo_position_test_'+'a'*32+'?options=-csearch_path%3Dfoo','tempo-pg-regressions-1-12345678','a'*40)]:
 os.environ.update(TEMPO_NATIVE_FROZEN_BRIDGE_DSN=dsn,TEMPO_TEST_COMPOSE_PROJECT=project,TEMPO_NATIVE_CANDIDATE_REVISION=revision,TEMPO_TEST_INSTANCE='disposable')
 try: next(f(None))
 except RuntimeError as error: assert 'identity' in str(error)
 else: raise AssertionError('foreign target accepted')
for variable in ('PGHOSTADDR','PGSERVICE','PGOPTIONS','PGPASSFILE'):
 os.environ.update(TEMPO_NATIVE_FROZEN_BRIDGE_DSN='postgresql://postgres@postgres:5432/tempo_position_test_'+'a'*32,TEMPO_TEST_COMPOSE_PROJECT='tempo-pg-regressions-1-12345678',TEMPO_NATIVE_CANDIDATE_REVISION='a'*40,TEMPO_TEST_INSTANCE='disposable')
 os.environ[variable]='foreign'
 try: next(f(None))
 except RuntimeError as error: assert 'identity' in str(error)
 else: raise AssertionError('ambient override accepted')
 if variable == 'PGPASSFILE': os.environ[variable]='/run/secrets/admin_pgpass'
 else: del os.environ[variable]
class Cursor:
 def fetchone(self): return ('tempo_position_test_'+'a'*32,'foreign-project')
class Conn:
 def __enter__(self): return self
 def __exit__(self,*args): pass
 def execute(self,*args): return Cursor()
os.environ.update(TEMPO_NATIVE_FROZEN_BRIDGE_DSN='postgresql://postgres@postgres:5432/tempo_position_test_'+'a'*32,TEMPO_TEST_COMPOSE_PROJECT='tempo-pg-regressions-1-12345678',TEMPO_NATIVE_CANDIDATE_REVISION='a'*40,TEMPO_TEST_INSTANCE='disposable')
f.__globals__['psycopg'].connect=lambda *args,**kwargs: Conn()
try: next(f(None))
except RuntimeError as error: assert 'marker differs' in str(error)
else: raise AssertionError('foreign database marker accepted')
class ParentConn(Conn):
 def execute(self, query, *args):
  class Result:
   def fetchone(self): return ('tempo_position_test_'+'a'*32,'tempo-pg-regressions-1-12345678') if 'current_database()' in query else ('foreign-parent',)
  return Result()
f.__globals__['psycopg'].connect=lambda *args,**kwargs: ParentConn()
try: next(f(None))
except RuntimeError as error: assert 'parent cluster marker differs' in str(error)
else: raise AssertionError('foreign parent accepted')`;
  const result = spawnSync(resolvePython(), ["-c", script], { cwd: root, env: { ...process.env, PYTHONPATH: "backend:database" }, encoding: "utf8" });
  assert.equal(result.status, 0, result.stderr);
});
test("P14 bridge accepts composed credential path and rejects foreign passfiles before connecting", () => {
  const script = `import os,runpy,psycopg
m=runpy.run_path('database/tests/position-native-pytest.py')
os.environ.update(TEMPO_NATIVE_POSITION_CHECK='frozen-requeue-cycle',TEMPO_TEST_COMPOSE_PROJECT='tempo-pg-regressions-1-12345678',TEMPO_NATIVE_CANDIDATE_REVISION='a'*40,TEMPO_TEST_INSTANCE='disposable',PGPASSFILE='/run/secrets/admin_pgpass')
def no_connection(*args,**kwargs): raise AssertionError('connect reached')
psycopg.connect=no_connection
os.environ['PGPASSFILE']='/tmp/foreign'
try: m['execute']()
except RuntimeError as error: assert 'ambient libpq' in str(error)
else: raise AssertionError('foreign passfile accepted')
os.environ['PGPASSFILE']='/run/secrets/admin_pgpass'
try: m['execute']()
except AssertionError as error: assert 'connect reached' in str(error)
else: raise AssertionError('composed credential did not reach connection')`;
  const result = spawnSync(resolvePython(), ["-c", script], { cwd: root, env: { ...process.env, PYTHONPATH: "backend:database" }, encoding: "utf8" });
  assert.equal(result.status, 0, result.stderr);
});
test("P14 frozen requeue native selector is exact and cannot widen its mode", () => {
  const options = parse(["--mode", "study", "--browser-grep", "C24", "--native-check", "frozen-requeue-cycle"]);
  assert(postgresTestStages(options).includes("frozen-requeue-cycle"));
  assert.throws(() => parse(["--mode", "durability", "--native-check", "frozen-requeue-cycle"]));
  assert.throws(() => parse(["--mode", "study", "--browser-grep", "C24", "--native-check", "frozen-requeue-cycle", "--native-check", "frozen-requeue-cycle"]));
  const bridge = readFileSync(join(root, "database/tests/position-native-pytest.py"), "utf8");
  assert(bridge.includes("backend/tests/test_frozen_study_attempts_postgres.py::test_native_original_review_requeue_cycle_read_is_bound_and_read_only"));
});
test("P14 queue receipt rejects missing, foreign, duplicate and partial cases", () => {
  const receipt = { case_set: "all", scope: "all-eight", execution: "completed", executed_count: 8, cases: [...checkpoint1QueueCases], excluded_cases: [], unresolved_owner_cases: [...unresolvedQueueCases] };
  verifyCheckpoint1QueueReceipt(receipt);
  for (const change of [r => { r.cases.pop(); }, r => { r.cases[0] = "foreign"; }, r => { r.cases[0] = r.cases[1]; }, r => { r.executed_count = 0; }, r => { r.execution = "starting"; }]) {
    const bad = structuredClone(receipt); change(bad); assert.throws(() => verifyCheckpoint1QueueReceipt(bad));
  }
});
test("P14 browser receipt rejects skipped, retried, duplicate, foreign and zero cases", () => {
  const report = { stats: { expected: 11, skipped: 0, unexpected: 0, flaky: 0 }, suites: [{ specs: checkpoint1BrowserCases.map(([file, title]) => ({ file, title, tests: [{ projectName: "chromium", status: "expected", results: [{ status: "passed", retry: 0 }] }] })) }] };
  verifyCheckpoint1BrowserReceipt(report);
  for (const change of [r => { r.stats.skipped = 1; }, r => { r.suites[0].specs[0].tests[0].results[0].retry = 1; }, r => { r.suites[0].specs[0] = structuredClone(r.suites[0].specs[1]); }, r => { r.suites[0].specs[0].file = "foreign.spec.ts"; }, r => { r.suites[0].specs = []; }, r => { r.suites[0].specs[0].tests[0].results[0].status = "skipped"; }]) {
    const bad = structuredClone(report); change(bad); assert.throws(() => verifyCheckpoint1BrowserReceipt(bad));
  }
});

test("P08 historical rollback selects exactly one case and rejects mixed selectors", () => {
  const options = parse(["--mode", "priority-historical"]);
  assert.deepEqual(postgresTestStages(options), ["compose_config", "image_build", "priority_postgres_startup", "priority_historical_rollback", "cleanup"]);
  assert.equal(options.skipBrowser, true);
  for (const args of [["--mode", "priority-historical", "--browser-grep", "anything"], ["--mode", "priority-historical", "--native-check", "position-grade-effects"], ["--mode", "priority-historical", "--maintained-study-proof"], ["--mode", "priority-historical", "--skip-browser"]]) assert.throws(() => parse(args));
  assert.throws(() => parsePostgresTestOptions(["--mode", "priority-historical"], { TEMPO_PG_STALE_DELIVERY_ONLY: "1" }, root));
});

test("P08 proof failure still runs the owning cleanup stage", async () => {
  const stages = postgresTestStages(parse(["--mode", "priority-historical"])), calls = [];
  const actions = Object.fromEntries(stages.map(stage => [stage, async () => { calls.push(stage); if (stage === "priority_historical_rollback") throw new Error("fixture failure"); }]));
  await assert.rejects(executePostgresTestPlan(stages, actions, async (_name, action) => action()), /fixture failure/);
  assert.equal(calls.at(-1), "cleanup");
});

test("P08 historical stage binds installed image and always attempts owned sentinel cleanup", () => {
  const source = readFileSync(join(root, "database/tests/test-postgres-docker.mjs"), "utf8");
  const start = source.indexOf("  priority_historical_rollback: async () => {");
  const end = source.indexOf("  schema_migrations: async () => {", start);
  const stage = source.slice(start, end);
  assert(start >= 0 && end > start);
  assert.match(stage, /baseline\.kind !== "installed-receipt"/);
  assert.match(stage, /baseline\.images\.migration/);
  assert.match(stage, /finally\s*\{[\s\S]*--sentinel-drop/);
  assert.match(stage, /sentinel_schema, 65/);
  assert.match(stage, /accepted_reviews_retained, 60/);
  const startupStart = source.indexOf("  priority_postgres_startup: async () => {");
  const startupEnd = source.indexOf("  service_health: async () => {", startupStart);
  const startup = source.slice(startupStart, startupEnd);
  assert(startupStart >= 0 && startupEnd > startupStart);
  assert.match(startup, /"--wait", "postgres"/);
  assert.match(startup, /"--no-deps", "schema"/);
  assert(!startup.includes('"api"') && !startup.includes('"redis"') && !startup.includes('"web"'));
  assert.match(source, /options\.mode === 'priority-historical' \? \['schema'\] : \[\]/);
});

test("P08 strict historical copy contract refuses a changed source schema", () => {
  const script = `import runpy
m=runpy.run_path('database/tests/check_postgres_study_priority.py')
f=m['require_copy_contract']; f.__globals__['table_contract']=lambda database, table: (database, table)
try: f('current','historical')
except RuntimeError as error: assert 'Schema or foreign keys differ' in str(error)
else: raise AssertionError('Mismatched copy was accepted')`;
  const result = spawnSync(resolvePython(), ["-c", script], { cwd: root, env: { ...process.env, PYTHONPATH: "backend:database" }, encoding: "utf8" });
  assert.equal(result.status, 0, result.stderr);
});

test("P08 sentinel refuses foreign database and project identities before connecting", () => {
  const script = `import os,runpy
m=runpy.run_path('database/tests/check_postgres_study_priority.py'); f=m['require_owned_sentinel']
for name,project in [('live','tempo-pg-regressions-1-12345678'),('tempo_priority_sentinel_'+'a'*32,'tempo')]:
 os.environ['TEMPO_TEST_COMPOSE_PROJECT']=project
 try: f('must-not-connect',name)
 except RuntimeError: pass
 else: raise AssertionError('foreign target accepted')`;
  const result = spawnSync(resolvePython(), ["-c", script], { cwd: root, env: { ...process.env, PYTHONPATH: "backend:database" }, encoding: "utf8" });
  assert.equal(result.status, 0, result.stderr);
});

test("disposable_health_probe_does_not_hold_foreground_admission", () => {
  const composeSource = readFileSync(join(root, "docker-compose.postgres.test.yml"), "utf8");
  const apiConfiguration = composeSource.slice(composeSource.indexOf("  api:"), composeSource.indexOf("  foreground-worker:"));
  const configuredHealthProbeCommand = JSON.parse(apiConfiguration.match(/test: (\[.*\])/)[1]);
  assert.deepEqual(configuredHealthProbeCommand.slice(0, 3), ["CMD", "python", "-c"]);
  // Execute the configured probe with only transport observed. This catches an
  // ordinary URL request that would acquire the API's foreground request lease.
  const probeExecution = spawnSync("python3", ["-c", `
import json,sys,urllib.request
from types import SimpleNamespace
def observe(request, timeout):
    request = urllib.request.Request(request) if isinstance(request, str) else request
    print(json.dumps({"url": request.full_url, "headers": dict(request.header_items()), "timeout": timeout}))
    return SimpleNamespace(close=lambda: None)
urllib.request.urlopen = observe
exec(sys.argv[1])
`, configuredHealthProbeCommand[3]], { encoding: "utf8" });
  assert.equal(probeExecution.status, 0, probeExecution.stderr);
  const emittedHealthRequest = JSON.parse(probeExecution.stdout);
  assert.equal(emittedHealthRequest.url, "http://127.0.0.1:8000/api/health");
  assert.equal(emittedHealthRequest.timeout, 3, "The existing health deadline remains bounded");
  assert.equal(emittedHealthRequest.headers["X-tempo-work-class"], "background",
    "Housekeeping health reads must not acquire foreground admission while awaiting worker readiness");
});

test("burial_publication_fixture_reads_do_not_hold_foreground_admission", async () => {
  const source = readFileSync(join(root, "tests/browser/workspace-flows.spec.ts"), "utf8");
  const publicationPolls = source.slice(source.indexOf('  await expect.poll(async () => {'), source.indexOf('  await page.goto("/");'))
    .replace('(item: { name: string })', '(item)');
  const paths = [];
  await runInNewContext(`(async () => { ${publicationPolls} })()`, {
    api: "http://owned/api",
    request: { get: async (url, options) => {
      assert.equal(options?.headers?.["X-Tempo-Work-Class"], "background",
        "Fixture publication reads must not renew the foreground lease they wait to clear");
      paths.push(url);
      return { json: async () => url.endsWith('/repertoires')
        ? { repertoires: [{ name: "bury-training", graph_state: "ready", integrity_status: "clean", integrity_scan_status: "idle" }] }
        : { projection: { state: "ready", refresh_pending: false }, cards: [{}, {}] } };
    } },
    expect: { poll: (read, options) => ({ toEqual: async value => {
      assert.equal(options.timeout, 20_000, "Publication keeps its existing deadline");
      assert.equal(JSON.stringify(await read()), JSON.stringify(value));
    } }) },
  });
  assert.deepEqual(paths, ["http://owned/api/repertoires", "http://owned/api/queue/today"]);
});

test("training_transition_cleanup_waits_for_restored_background_readiness", async () => {
  const source = readFileSync(join(root, "tests/browser/training-critical-path.spec.ts"), "utf8");
  const cleanupSource = source.slice(source.indexOf('    const ownedConsumers ='), source.lastIndexOf('\n  }\n});'));
  const events = [];
  let releaseHealth;
  const heldHealth = new Promise(resolve => { releaseHealth = resolve; });
  let healthReads = 0;
  let completed = false;
  const expected = value => ({ toBe: other => assert.equal(value, other) });
  expected.poll = (read, options) => ({ toBe: async ready => {
    assert.equal(options.timeout, 60_000, "Restoration retains the existing readiness deadline");
    const unavailable = await read();
    assert.notEqual(unavailable, ready, "A worker-unavailable response cannot finish cleanup");
    assert.equal(await read(), ready);
  } });
  const cleanup = runInNewContext(`(async () => { ${cleanupSource} })()`, {
    docker: action => { events.push(action); },
    fixture: action => { events.push(action); return JSON.stringify({ restored_queue_positions: 1, runtime_reordered_positions: 0, runtime_removed_entries: 0 }); },
    expect: expected, displacedQueuePositions: [[1, 0, 64]], api: "http://owned/api",
    request: { get: async url => {
      assert.equal(url, "http://owned/api/health");
      events.push("health");
      return healthReads++ === 0 ? heldHealth : { ok: () => true };
    } },
  }).then(() => { completed = true; });
  await new Promise(resolve => setImmediate(resolve));
  try {
    assert.deepEqual(events, ["stop", "cleanup", "start", "health"]);
    assert.equal(completed, false, "Cleanup must remain pending until restored workers are ready");
  } finally {
    releaseHealth({ ok: () => false, text: async () => "Required Celery workers are unavailable: background" });
    await cleanup;
  }
  assert.deepEqual(events, ["stop", "cleanup", "start", "health", "health"]);
  assert.equal(completed, true);
});

function studyImportPollingFixture(queueResponses, { integrityStatus = "clean", systemResponse } = {}) {
  const source = readFileSync(join(root, "database/tests/test-postgres-docker.mjs"), "utf8");
  const getSource = source.slice(source.indexOf("async function get("), source.indexOf("async function postCommand("));
  const pollingSource = source.slice(source.indexOf("async function pollForStudyableImport("), source.indexOf("function stableStudyState("));
  const observed = { polls: 0, waits: [], queueReads: 0 };
  const context = {
    JSON, assertNoCompletedFixtureConflict, readStudyQueuePublication,
    measureScenario: { poll: () => { observed.polls += 1; }, wait: async interval => { observed.waits.push(interval); } },
    apiRequest: async (path, options) => {
      assert.equal(options.headers["X-Tempo-Work-Class"], "background");
      let status = 200, body;
      if (path === "system/tasks") {
        status = systemResponse?.status ?? 200;
        body = systemResponse?.body ?? { tasks: [{ kind: "opening_graph_rebuild", deduplication_key: "fixture", state: "complete" }] };
      } else if (path === "repertoires/fixture/integrity") {
        body = { scan_status: "idle", status: integrityStatus };
      } else {
        assert.equal(path, "queue/today");
        const response = queueResponses[Math.min(observed.queueReads++, queueResponses.length - 1)];
        status = response.status;
        body = response.body;
      }
      return { ok: status >= 200 && status < 300, status,
        json: async () => body, text: async () => typeof body === "string" ? body : JSON.stringify(body) };
    },
  };
  return { observed, run: () => runInNewContext(`${getSource}\n${pollingSource}\npollForStudyableImport("fixture")`, context) };
}

test("study_import_waits_for_pending_head_issuance_without_masking_service_failures", async () => {
  const readyQueue = { cards: [{ id: "issued-card" }], projection: { state: "ready", refresh_pending: false } };
  const pendingDetail = "Queue changed or attempt issuance is pending. Retry loading the queue.";
  const fixture = studyImportPollingFixture([
    { status: 200, body: readyQueue },
    { status: 503, body: { detail: pendingDetail } },
    { status: 200, body: readyQueue },
  ]);
  const queue = await fixture.run();
  assert.equal(queue, readyQueue);
  assert.equal(fixture.observed.queueReads, 5, "A pending issuance resets the three consecutive settled samples");
  assert.deepEqual(fixture.observed.waits, [250, 250, 250, 250]);
  for (const failure of [
    { status: 500, body: { detail: pendingDetail } },
    { status: 503, body: { detail: "Database unavailable" } },
    { status: 503, body: "invalid JSON" },
  ]) {
    await assert.rejects(studyImportPollingFixture([failure]).run(), /queue\/today: HTTP (500|503)/);
  }
  await assert.rejects(studyImportPollingFixture([{ status: 200, body: readyQueue }], {
    systemResponse: { status: 503, body: { detail: pendingDetail } },
  }).run(), /system\/tasks: HTTP 503/);
});

test("study_import_pending_issuance_remains_bounded_and_preserves_integrity_failures", async () => {
  const pending = { status: 503, body: { detail: "Queue changed or attempt issuance is pending. Retry loading the queue." } };
  const fixture = studyImportPollingFixture([pending]);
  await assert.rejects(fixture.run(), /graph, integrity, and queue work did not settle/);
  assert.equal(fixture.observed.queueReads, 180);
  assert.equal(fixture.observed.waits.length, 180);
  assert.equal(fixture.observed.waits.reduce((sum, interval) => sum + interval, 0), 45_000);
  await assert.rejects(studyImportPollingFixture([pending], { integrityStatus: "needs_repair" }).run(), /integrity|conflict/i);
});

const fullStages = [
  "compose_config", "image_build", "maintenance_cli", "startup", "service_health", "game_review_reader",
  "background_budget", "operation_recovery", "schema_migrations", "background_diagnostics", "deployment_lifecycle", "background_workloads",
  "command_recreation", "backup_restore", "browser",
  "study_isolation", "study_durability", "cleanup",
];
const browserStages = ["compose_config", "image_build", "startup", "service_health", "browser", "cleanup"];
const directMeasurement = async (_name, action) => action();

function temporaryDirectory(testContext) {
  const directory = mkdtempSync(join(tmpdir(), "tempo-runner-speed-"));
  testContext.after(() => rmSync(directory, { recursive: true, force: true }));
  return directory;
}

test("import readiness preserves its bound and settled samples across pending queue-head issuance", async () => {
  const runnerSource = readFileSync(join(root, "database/tests/test-postgres-docker.mjs"), "utf8");
  const pollStart = runnerSource.indexOf("async function pollForStudyableImport(");
  const pollEnd = runnerSource.indexOf("\nfunction stableStudyState", pollStart);
  const queue = { projection: { state: "ready", refresh_pending: false } };
  for (const indefinitelyPending of [false, true]) {
    let queueReads = 0, polls = 0, waits = 0;
    const apiRequest = async () => {
      queueReads += 1;
      const pending = indefinitelyPending || queueReads === 3;
      return new Response(JSON.stringify(pending ? {
        detail: "Queue changed or attempt issuance is pending. Retry loading the queue.",
      } : queue), { status: pending ? 503 : 200 });
    };
    const context = {
      assertNoCompletedFixtureConflict, readStudyQueuePublication, apiRequest,
      get: async path => {
        if (path === "system/tasks") return { tasks: [{ kind: "opening_graph_rebuild", deduplication_key: "scope", state: "complete" }] };
        if (path.includes("integrity")) return { scan_status: "idle", status: "clean" };
        const response = await apiRequest();
        if (!response.ok) throw new Error(`${path}: HTTP ${response.status} ${await response.text()}`);
        return response.json();
      },
      measureScenario: {
        poll: () => { polls += 1; },
        wait: async interval => { assert.equal(interval, 250); waits += 1; },
      },
    };
    const poll = runInNewContext(`(${runnerSource.slice(pollStart, pollEnd)})`, context);
    if (indefinitelyPending) {
      await assert.rejects(poll("scope"), /did not settle for scope/);
      assert.equal(polls, 180); assert.equal(waits, 180); assert.equal(queueReads, 180);
    } else {
      assert.deepEqual(await poll("scope"), queue);
      assert.equal(queueReads, 6); assert.equal(polls, 6); assert.equal(waits, 5);
    }
  }
});

test("queue publication polling propagates service failures instead of reporting readiness", async () => {
  for (const [status, body] of [[500, { detail: "database unavailable" }],
    [503, { detail: "database unavailable" }], [503, { detail: "other pending work" }], [503, "invalid JSON"]]) {
    await assert.rejects(readStudyQueuePublication(new Response(JSON.stringify(body), { status })), /queue\/today: HTTP/);
  }
});

test("default PostgreSQL gate retains every recovery check and one unfiltered browser matrix", () => {
  const options = parse([]);
  assert.equal(options.mode, "full");
  assert.deepEqual(postgresTestStages(options), fullStages);
  assert.deepEqual(buildPostgresPlaywrightArguments(options), ["playwright", "test"]);
});

test("focused PostgreSQL browser execution excludes maintenance and durability scenarios", () => {
  assert.deepEqual(postgresTestStages(parse(["--mode", "browser"])), browserStages);
  const options = parse(["--browser-grep", "Builder [review]"]);
  assert.equal(options.mode, "browser");
  assert.deepEqual(postgresTestStages(options), browserStages);
  assert.deepEqual(buildPostgresPlaywrightArguments(options), ["playwright", "test", "--grep", "Builder [review]"]);
});

test("durability mode and legacy skip-browser retain ordinary recovery without deployment lifecycle", () => {
  const expected = fullStages.filter((stage) => !["browser", "study_isolation", "deployment_lifecycle"].includes(stage));
  assert.deepEqual(postgresTestStages(parse(["--mode", "durability"])), expected);
  assert.deepEqual(postgresTestStages(parse(["--skip-browser"])), expected);
  assert.equal(parse(["--mode", "durability"]).skipBrowser, true);
});

test("retired adaptive priority benchmark mode is rejected before setup", () => {
  assert.throws(() => parse(["--mode", "priority-benchmark"]), /--mode must be/);
});

test("standalone lifecycle mode requires the complete rehearsal without parent startup or browser work", () => {
  assert.deepEqual(postgresTestStages(parse(["--mode", "lifecycle"])), [
    "compose_config", "image_build", "maintenance_cli", "deployment_lifecycle", "cleanup",
  ]);
  assert.throws(() => parse(["--mode", "lifecycle", "--browser-file", "studies.spec.ts"]), /cannot be combined/);
});

test("current lifecycle has its own complete plan and cannot qualify the full historical gate", () => {
  const current = parse(["--mode", "lifecycle-current"]);
  assert.equal(current.skipBrowser, true);
  const planned = postgresTestStages(current);
  assert.deepEqual(planned, ["compose_config", "image_build", "maintenance_cli", "deployment_lifecycle_current", "cleanup"]);
  const full = postgresTestStages({ mode: "lifecycle" });
  const plan = { commit: "candidate", hash: "plan", jobs: { lifecycle: { mode: "lifecycle", planned_stages: full } } };
  const report = { runner: "postgres", mode: "lifecycle-current", commit: "candidate", plan_hash: "plan", planned_stages: planned,
    stages: Object.fromEntries(planned.map(stage => [stage, { exit_code: 0, duration_seconds: 1 }])) };
  assert.throws(() => validatePostgresScenarios("lifecycle", plan, report), /missing, failed or mismatched/);
});

test("current lifecycle invokes the explicitly bounded current65 checker profile", async () => {
  const source = readFileSync(join(root, "database/tests/test-postgres-docker.mjs"), "utf8");
  const start = source.indexOf("  deployment_lifecycle_current: async () => {");
  const end = source.indexOf("  deployment_lifecycle: async () => {", start);
  const calls = [];
  const action = runInNewContext(`({${source.slice(start, end)}})`, {
    project: "owned", environment: {}, candidateRevision: "candidate", resourceLimits: "limits", buildLabels: "labels", join,
    process: { cwd: () => root }, measureScenario: {}, verifyTempoCliLifecycle: async args => calls.push(args),
  });
  await action.deployment_lifecycle_current();
  assert.equal(calls.length, 1); assert.equal(calls[0].profile, "current65");
});

test("current65 preflight does not inject the historical schema62 image default", () => {
  const source = readFileSync(join(root, "database/tests/test-postgres-docker.mjs"), "utf8");
  const start = source.indexOf('if (stages.includes("deployment_lifecycle") || stages.includes("deployment_lifecycle_current")) {');
  const end = source.indexOf("\n// Docker Desktop only shares", start);
  assert(start >= 0 && end > start);
  for (const historical of [false, true]) {
    const env = {}, seen = [];
    runInNewContext(source.slice(start, end), { stages: [historical ? "deployment_lifecycle" : "deployment_lifecycle_current"],
      process: { env }, resolve, options: { mode: historical ? "lifecycle" : "lifecycle-current" }, checkLifecycleSource: input => seen.push({ ...input, env: { ...env } }) });
    assert.equal(seen[0].profile, historical ? "full" : "current65");
    assert.equal(Boolean(env.TEMPO_LIFECYCLE_SCHEMA62_IMAGES), historical);
  }
  const env = { TEMPO_LIFECYCLE_SCHEMA62_IMAGES: "/explicit-override" };
  assert.throws(() => runInNewContext(source.slice(start, end), { stages: ["deployment_lifecycle_current"], process: { env }, resolve,
    options: { mode: "lifecycle-current" }, checkLifecycleSource: () => { if (env.TEMPO_LIFECYCLE_SCHEMA62_IMAGES) throw new Error("historical override refused"); } }), /historical override refused/);
});

test("current lifecycle rejects unsupported source and fixture overrides before admission", () => {
  const source = readFileSync(join(root, "database/tests/test-postgres-docker.mjs"), "utf8");
  const start = source.indexOf('if (options.mode === "lifecycle-current") {');
  const end = source.indexOf('if (process.env.TEMPO_RELEASE_CANDIDATE_MODE)', start);
  assert(start >= 0 && end < source.indexOf("process.exit(await runWithDevelopmentBudget"));
  for (const [version, environment, staleOnly] of [[62, {}, false], [65, {}, false], [66, { TEMPO_LIFECYCLE_P0_59_ONLY: "1" }, false], [66, {}, true]]) {
    assert.throws(() => runInNewContext(source.slice(start, end), {
      options: { mode: "lifecycle-current" }, process: { env: environment }, staleOnly,
      readFileSync: () => "source", schemaVersionFromSource: () => version,
    }), /requires schema 66/);
  }
});

test("standalone lifecycle prepares only missing dependency images on cold and warm Docker daemons", async () => {
  const runnerSource = readFileSync(join(root, "database/tests/test-postgres-docker.mjs"), "utf8");
  const imageBuildStartIndex = runnerSource.indexOf("  image_build: async () => {");
  const maintenanceCliStartIndex = runnerSource.indexOf("  maintenance_cli: async () => {", imageBuildStartIndex);
  assert(imageBuildStartIndex >= 0 && maintenanceCliStartIndex > imageBuildStartIndex);
  for (const mode of ["lifecycle", "lifecycle-current", "durability", "full", "browser"]) {
    for (const cachedServices of [[], ["postgres"], ["postgres", "redis"]]) {
      const servicesWithAvailableImages = new Set(cachedServices);
      const imagePreparationCommands = [];
      const registryAcquisitions = [];
      const imagePreparationContext = { candidateMode: undefined, borrowedCandidate: null, candidateMetadataPath: null, stages: [], environment: {}, waitForStudyQuiet:async()=>{},recordBudgetResource:async()=>{},project:'owned-project',
        managedComposeBuild:async (_prepare,execute)=>execute(['compose','build']), options: { mode }, compose: ["compose"], resourcesCreated: false,
        run: (command, args) => {
          assert.equal(command, "docker");
          imagePreparationCommands.push(args);
          if (args.includes("build")) servicesWithAvailableImages.add("application");
          if (args.includes("pull")) {
            const pullArguments = args.slice(args.indexOf("pull") + 1);
            assert.deepEqual(Array.from(pullArguments), ["--policy", "missing", "postgres", "redis"],
              "Dependency preparation must use Compose's missing-only policy for exactly PostgreSQL and Redis");
            for (const service of pullArguments.slice(2)) {
              if (servicesWithAvailableImages.has(service)) continue;
              registryAcquisitions.push(service);
              servicesWithAvailableImages.add(service);
            }
          }
        } };
      await runInNewContext(`({${runnerSource.slice(imageBuildStartIndex, maintenanceCliStartIndex)}})`, imagePreparationContext).image_build();
      assert(imagePreparationContext.resourcesCreated, "Owned image cleanup is armed before preparation");
      assert(servicesWithAvailableImages.has("application"));
      if (["lifecycle", "lifecycle-current"].includes(mode)) {
        assert.equal(imagePreparationCommands.filter(args => args.includes("pull")).length, 1);
        assert.deepEqual(registryAcquisitions, ["postgres", "redis"].filter(service => !cachedServices.includes(service)),
          "Only uncached dependencies may require registry access");
        for (const service of ["postgres", "redis"])
          assert(servicesWithAvailableImages.has(service), `Rehearsal requires the ${service} image before child startup`);
      } else {
        assert(!imagePreparationCommands.some(args => args.includes("pull")), "Other modes retain their existing startup prerequisites");
      }
      assert(!imagePreparationCommands.some(args => args.includes("up")), "Image preparation must not start parent services");
    }
  }
});

test("standalone lifecycle propagates missing dependency acquisition failures with cleanup armed", async () => {
  const runnerSource = readFileSync(join(root, "database/tests/test-postgres-docker.mjs"), "utf8");
  const imageBuildStartIndex = runnerSource.indexOf("  image_build: async () => {");
  const maintenanceCliStartIndex = runnerSource.indexOf("  maintenance_cli: async () => {", imageBuildStartIndex);
  assert(imageBuildStartIndex >= 0 && maintenanceCliStartIndex > imageBuildStartIndex);
  const acquisitionFailure = new Error("Missing Redis image could not be acquired");
  const imagePreparationContext = { candidateMode: undefined, borrowedCandidate: null, candidateMetadataPath: null, stages: [], environment: {}, waitForStudyQuiet:async()=>{},recordBudgetResource:async()=>{},project:'owned-project',
        managedComposeBuild:async (_prepare,execute)=>execute(['compose','build']), options: { mode: "lifecycle" }, compose: ["compose"], resourcesCreated: false,
    run: (_command, args) => {
      if (args.includes("pull")) throw acquisitionFailure;
    } };
  const actions = runInNewContext(`({${runnerSource.slice(imageBuildStartIndex, maintenanceCliStartIndex)}})`, imagePreparationContext);
  await assert.rejects(actions.image_build(), error => error === acquisitionFailure);
  assert(imagePreparationContext.resourcesCreated, "Acquisition failure must leave owned image cleanup armed");
});

test("maintenance image cleanup is armed before build timing publication can fail", async () => {
  const source = readFileSync(join(root, "database/tests/test-postgres-docker.mjs"), "utf8");
  const start = source.indexOf("  maintenance_cli: async () => {");
  const end = source.indexOf("  startup: async () => {", start);
  const publicationFailure = new Error("Timing publication failed after the image was built");
  const context = { candidateMode: undefined, borrowedCandidate: null, managedBuild:async build=>build("fixture-builder"), buildResourceArguments:()=>[], maintenanceImageCleanupRequired: false,
    candidateRevision: "candidate", maintenanceImage: "owned-maintenance", run: () => { throw publicationFailure; } };
  const actions = runInNewContext(`({${source.slice(start, end)}})`, context);
  await assert.rejects(actions.maintenance_cli(), error => error === publicationFailure);
  assert.equal(context.maintenanceImageCleanupRequired, true, "The image must remain owned by teardown after successful build / failed recording");
});

test("maintenance cleanup accepts only an absent owned image and preserves other removal failures", async () => {
  const source = readFileSync(join(root, "database/tests/test-postgres-docker.mjs"), "utf8");
  const helperStart = source.indexOf("function isMissingMaintenanceImage(result) {");
  const helperEnd = source.indexOf("\n}\n", helperStart) + 2;
  const isMissingMaintenanceImage = runInNewContext(`(${source.slice(helperStart, helperEnd)})`, { maintenanceImage: "owned-maintenance" });
  const start = source.indexOf("  cleanup: async () =>");
  const end = source.indexOf("\n};", start);
  for (const [removal, accepted] of [
    [{ status: 0 }, true],
    [{ status: 1, stderr: "Error response from daemon: No such image: owned-maintenance\n" }, true],
    [{ status: 1, stderr: "Error response from daemon: No such image: owned-maintenance:latest\n" }, true],
    [{ status: 1, stderr: "Error response from daemon: No such image: another-owner\n" }, false],
    [{ status: 1, stderr: "Permission denied" }, false],
    [{ error: new Error("Docker unavailable"), status: null }, false],
  ]) {
    const calls = [];
    const context = { candidateSuiteTags: [], retainedCandidateTags: [], candidateMode: null, candidatePublished: false, recordBudgetResource:async()=>{},reconcileDevelopmentStartup:()=>{},removeOwnedMaintenanceHelpContainer:()=>{},project:"owned-project",executeDiagnosticCleanup, isMissingMaintenanceImage, resourcesCreated: false, maintenanceImageCleanupRequired: true,
      maintenanceImage: "owned-maintenance", environment: {}, secretsDirectory: "owned-secrets", process: { env: {} },
      spawnSync: (command, args) => { calls.push([command, Array.from(args)]); return removal; },
      rmSync: path => calls.push(["secrets", path]) };
    const actions = runInNewContext(`({${source.slice(start, end)}})`, context);
    if (accepted) await actions.cleanup();
    else await assert.rejects(actions.cleanup(), /resource cleanup failed/);
    assert.deepEqual(calls, [["docker", ["image", "rm", "owned-maintenance"]], ["secrets", "owned-secrets"]]);
  }
});

test("lifecycle rehearsal restores full-mode applications after failure and never starts the standalone parent", async () => {
  const source = readFileSync(join(root, "database/tests/test-postgres-docker.mjs"), "utf8");
  const start = source.indexOf("  deployment_lifecycle: async () => {");
  const end = source.indexOf("  background_workloads: async () => {", start);
  assert(start >= 0 && end > start);
  for (const mode of ["full", "lifecycle"]) for (const shouldFail of [false, true]) {
    const calls = [];
    const rehearsalFailure = new Error("rehearsal failed");
    const context = { options: { mode }, project: "fixture", environment: {}, candidateRevision: "revision",
      measureScenario: directMeasurement,
      resourceLimits:"resource-limits.json",buildLabels: "labels", compose: ["compose"], join, process: { cwd: () => root },
      verifyTempoCliLifecycle: async () => { calls.push("rehearsal"); if (shouldFail) throw rehearsalFailure; },
      run: (_command, args) => calls.push(args.includes("stop") ? "stop" : "restore"),
      waitForReady: async () => calls.push("ready") };
    const actions = runInNewContext(`({${source.slice(start, end)}})`, context);
    if (shouldFail) await assert.rejects(actions.deployment_lifecycle(), error => error === rehearsalFailure);
    else await actions.deployment_lifecycle();
    assert.deepEqual(calls, mode === "full" ? ["stop", "rehearsal", "restore", "ready"] : ["rehearsal"]);
  }
});

test("valid opponent-branch durability and background fixtures prescribe one White response per position", () => {
  for (const [pgn, expectedGameCount, initialResponse] of [
    [studyDurabilityPgn, 3, "e2e4"], [backgroundPublicationPgn, 1, "c2c4"],
  ]) {
    const prescribedResponses = new Map();
    const games = pgn.trim().split(/(?=\[Event )/).filter(Boolean);
    assert.equal(games.length, expectedGameCount);
    for (const game of games) {
      const parsed = new Chess();
      parsed.loadPgn(game);
      const board = new Chess();
      for (const move of parsed.history({ verbose: true })) {
        if (board.turn() === "w") {
          const position = board.fen().split(" ").slice(0, 4).join(" ");
          const response = `${move.from}${move.to}${move.promotion ?? ""}`;
          const priorResponse = prescribedResponses.get(position);
          assert(priorResponse === undefined || priorResponse === response,
            `${position} prescribes ${priorResponse} and ${response}`);
          prescribedResponses.set(position, response);
        }
        board.move(move);
      }
      assert.equal(board.turn(), "b", "Every line ends after a trained-player response");
    }
    assert.equal(prescribedResponses.get(new Chess().fen().split(" ").slice(0, 4).join(" ")), initialResponse);
  }
});

test("repertoire limit recreation fixture includes its final White response", () => {
  const board = new Chess();
  board.loadPgn(repertoireLimitRecreationPgn);
  assert.equal(board.turn(), "b", "Recreation must reach study admission without a missing White response");
  const runnerSource = readFileSync(join(root, "database/tests/test-postgres-docker.mjs"), "utf8");
  assert.match(runnerSource, /importFixture\("repertoire-limit-recreation\.pgn", repertoireLimitRecreationPgn\)/);
});

test("repertoire limit recreation fixture survives backup then leaves unrelated study state intact", async () => {
  const runnerSource = readFileSync(join(root, "database/tests/test-postgres-docker.mjs"), "utf8");
  const scenarioStart = runnerSource.indexOf('  command_recreation: async () => {');
  const scenarioEnd = runnerSource.indexOf('  browser: async () => {', scenarioStart);
  assert(scenarioStart > 0 && scenarioEnd > scenarioStart);
  const fixtureRows = new Map([['unrelated', { id: 'unrelated', new_cards_per_day: null }]]);
  let settings = { new_cards_per_day: 10 };
  let backupVerified = false;
  let overrideReplayed = false;
  const context = {
    measureScenario: { poll() {}, wait: async () => {} },
    assert, compose: ['compose'], randomBytes: () => Buffer.from('fixture'),
    workloadConsumers: ['background-worker','background-scheduler'],
    repertoireLimitRecreationPgn, limitRecreationRepertoireId: null,
    console: { log() {} },
    importFixture: async () => {
      fixtureRows.set('recreation', { id: 'recreation', new_cards_per_day: null });
      return { repertoire_id: 'recreation' };
    },
    waitForStudyableImport: async () => {}, waitForReady: async () => {},
    get: async (path) => {
      if (path === 'settings') return { ...settings };
      if (path === 'repertoires') return { repertoires: [...fixtureRows.values()] };
      if (path === 'queue/today') return { cards: [...fixtureRows.keys()].map(id => ({ queue_entry_id: id })) };
      if (path.startsWith('operations/')) return { state: 'complete' };
      throw new Error(`Unexpected read: ${path}`);
    },
    apiRequest: async (path, options) => {
      assert.equal(path, 'settings');
      settings = JSON.parse(options.body);
      return { ok: true, body: { cancel: async () => {} } };
    },
    confirm: async () => ({}),
    postCommand: async (path, payload, options) => {
      if (options.method === 'DELETE') {
        assert(backupVerified, 'Persisted override must be present when backup is verified');
        assert.equal(path, 'repertoires/recreation');
        fixtureRows.delete('recreation');
        return {};
      }
      assert.equal(path, 'repertoires/recreation/settings');
      const repertoire = fixtureRows.get('recreation');
      overrideReplayed = repertoire.new_cards_per_day !== null;
      repertoire.new_cards_per_day = payload.new_cards_per_day;
      repertoire.effective_new_cards_per_day = payload.new_cards_per_day;
      return {};
    },
    run: (command, argumentsList) => {
      if (argumentsList.includes('/source/database/tooling/verify_postgres_backup.py')) {
        assert.equal(fixtureRows.get('recreation').new_cards_per_day, 11);
        backupVerified = true;
      }
    },
  };
  // Execute the real scenario bodies with only I/O replaced. This protects the
  // fixture lifecycle, including replay and backup ordering, without Docker.
  const actions = runInNewContext(`({${runnerSource.slice(scenarioStart, scenarioEnd)}})`, context);
  await actions.command_recreation();
  assert(overrideReplayed);
  assert(fixtureRows.has('recreation'), 'Keep the fixture for backup verification');
  await actions.backup_restore();
  assert(backupVerified);
  assert.deepEqual([...fixtureRows.keys()], ['unrelated'], 'Owned recreation entries cannot precede the next study workflow');
});

test("full browser coverage creates a fresh durability database before study commands", () => {
  const browserIndex = fullStages.indexOf("browser");
  assert.equal(fullStages[browserIndex + 1], "study_isolation");
  assert.equal(fullStages[browserIndex + 2], "study_durability");
  assert.equal(browserStages.includes("study_isolation"), false);
});

test("full and durability modes cannot silently narrow their browser coverage", () => {
  for (const mode of ["full", "durability", "lifecycle", "lifecycle-current"]) {
    assert.throws(() => parse(["--mode", mode, "--browser-grep", "Builder"]), /cannot be combined/);
  }
  assert.throws(() => parse(["--skip-browser", "--mode", "full"]), /cannot be combined/);
  assert.throws(() => parse(["--skip-browser", "--mode", "browser"]), /cannot be combined/);
  assert.throws(() => parsePostgresTestOptions([], { TEMPO_PG_BROWSER_GREP: "Builder" }), /clear inherited/);
});

test("invalid and duplicate mode or browser options fail before infrastructure work", () => {
  for (const args of [["--mode"], ["--mode", "unknown"], ["--mode", "browser", "--mode", "full"],
    ["--browser-grep", ""], ["--browser-grep", "["], ["--unexpected"],
    ["--browser-grep", "A", "--browser-grep", "B"]]) {
    assert.throws(() => parse(args));
  }
  assert.throws(() => postgresTestStages({ mode: "unknown" }), /Unknown/);
});

test("file focus is explicit, discrete, and restricted to an existing browser basename", (context) => {
  const directory = temporaryDirectory(context);
  mkdirSync(join(directory, "tests/browser"), { recursive: true });
  writeFileSync(join(directory, "tests/browser/fixture.spec.ts"), "");
  const options = parsePostgresTestOptions(["--browser-file", "fixture.spec.ts"], {}, directory);
  assert.equal(options.mode, "browser");
  assert.deepEqual(buildPostgresPlaywrightArguments(options), ["playwright", "test", join("tests", "browser", "fixture.spec.ts")]);
  for (const file of ["../fixture.spec.ts", "..\\fixture.spec.ts", "/fixture.spec.ts", "missing.spec.ts"]) {
    assert.throws(() => parsePostgresTestOptions(["--browser-file", file], {}, directory));
  }
});

for (const mode of ["full", "browser", "durability", "lifecycle", "lifecycle-current"]) {
  test(`${mode} --list exposes the executable plan without Docker, ports, secrets, or timing files`, (context) => {
    const directory = temporaryDirectory(context);
    const result = spawnSync(process.execPath, [resolve(root, "database/tests/test-postgres-docker.mjs"), "--list", "--mode", mode], {
      cwd: directory, encoding: "utf8", timeout: 5_000,
      env: { PATH: "", TEMPO_TEST_TIMING_DIR: join(directory, "timings") },
    });
    assert.equal(result.status, 0, result.stderr);
    assert.deepEqual(JSON.parse(result.stdout).stages, postgresTestStages({ mode }));
    assert.equal(JSON.parse(result.stdout).mode, mode);
    assert.deepEqual(readdirSync(directory), []);
  });

  test(`${mode} executor invokes exactly its planned actions once, including cleanup`, async () => {
    const called = [];
    const stages = postgresTestStages({ mode });
    const actions = Object.fromEntries(stages.map((name) => [name, async () => { called.push(name); }]));
    await executePostgresTestPlan(stages, actions, directMeasurement);
    assert.deepEqual(called, stages);
  });
}

test("packaged Games reader runs once on the host after ready owned services and before cleanup", async () => {
  const source = readFileSync(join(root, "database/tests/test-postgres-docker.mjs"), "utf8");
  const start = source.indexOf("  game_review_reader: async () => {");
  const end = source.indexOf("  background_budget: async () => {", start);
  assert(start >= 0 && end > start);
  const calls = [];
  const environment = { isolated: "kept", TEMPO_DATABASE_READ_URL: undefined };
  const project = "tempo-pg-regressions-123-abc";
  const action = runInNewContext(`({${source.slice(start, end)}})`, {
    resolvePython: () => "owned-python", resolve: path => resolve(root, path), environment, project,
    process: { env: { TEMPO_DEVELOPMENT_BUDGET_TOKEN: "owned-token" } },
    run: (command, args, options) => calls.push({ command, args: Array.from(args), env: options.env }),
  }).game_review_reader;
  for (const mode of ["full", "durability", "browser", "study"]) {
    calls.length = 0;
    const stages = postgresTestStages({ mode, nativeChecks: ["browser", "study"].includes(mode) ? ["game-review-reader"] : [] });
    assert(stages.indexOf("game_review_reader") > stages.indexOf("service_health"));
    assert.equal(stages.filter(stage => stage === "game_review_reader").length, 1);
    const events = [];
    const actions = Object.fromEntries(stages.map(stage => [stage, () => events.push(stage)]));
    actions.game_review_reader = async () => { events.push("game_review_reader"); await action(); };
    await executePostgresTestPlan(stages, actions, directMeasurement);
    assert.deepEqual(events, stages);
    assert.equal(calls.length, 1);
    assert.equal(calls[0].command, "owned-python");
    assert.deepEqual(calls[0].args, ["database/tests/check_postgres_game_review_reader.py"]);
    assert.equal(calls[0].env.isolated, "kept");
    assert.equal(calls[0].env.PYTHONPATH, ["backend", "database", "backend/tests"].map(path => resolve(root, path)).join(":"));
    assert.equal(calls[0].env.TEMPO_TEST_INSTANCE, "disposable");
    assert.equal(calls[0].env.TEMPO_TEST_COMPOSE_PROJECT, project);
    assert.equal(calls[0].env.TEMPO_DEVELOPMENT_BUDGET_TOKEN, "owned-token");
  }
  for (const mode of ["browser", "study", "lifecycle"]) {
    assert(!postgresTestStages({ mode }).includes("game_review_reader"));
  }
});

for (const missing of [false, true]) {
  test(`packaged Games reader ${missing ? "missing action" : "failure"} fails the owned plan and cleans up`, async () => {
    const called = [];
    const stages = postgresTestStages({ mode: "durability" });
    const actions = Object.fromEntries(stages.map(stage => [stage, () => called.push(stage)]));
    if (missing) delete actions.game_review_reader;
    else actions.game_review_reader = () => { called.push("game_review_reader"); throw new Error("reader failed"); };
    await assert.rejects(executePostgresTestPlan(stages, actions, directMeasurement),
      missing ? /Missing PostgreSQL test action: game_review_reader/ : /reader failed/);
    assert.deepEqual(called, missing ? ["cleanup"] : [...stages.slice(0, stages.indexOf("game_review_reader") + 1), "cleanup"]);
  });
}

test("scenario failure captures diagnostics before cleanup and never runs later tests", async () => {
  const called = [];
  const failure = new Error("browser failed");
  await assert.rejects(executePostgresTestPlan(["browser", "study_durability", "cleanup"], {
    browser: () => { called.push("browser"); throw failure; },
    study_durability: () => called.push("study_durability"),
    cleanup: () => called.push("cleanup"),
  }, directMeasurement, () => called.push("diagnostics")), (error) => error === failure);
  assert.deepEqual(called, ["browser", "diagnostics", "cleanup"]);
});

test("failed startup still cleans up and a cleanup failure cannot mask the original failure", async () => {
  const failure = new Error("startup failed");
  const cleanupFailure = new Error("cleanup failed");
  await assert.rejects(executePostgresTestPlan(["startup", "cleanup"], {
    startup: () => { throw failure; }, cleanup: () => { throw cleanupFailure; },
  }, directMeasurement), (error) => error instanceof AggregateError
    && error.errors[0] === failure && error.errors[1] === cleanupFailure);
});

test("cleanup failure after otherwise passing checks still fails the invocation", async () => {
  await assert.rejects(executePostgresTestPlan(["browser", "cleanup"], {
    browser: () => {}, cleanup: () => { throw new Error("cleanup failed"); },
  }, directMeasurement), /cleanup failed/);
});

test("falsy rejection reasons cannot turn a failing scenario into success", async () => {
  await assert.rejects(executePostgresTestPlan(["browser", "cleanup"], {
    browser: () => Promise.reject(null), cleanup: () => {},
  }, directMeasurement), /null/);
});

test("missing or duplicate planned actions fail while cleanup still runs exactly once", async () => {
  let cleanups = 0;
  const actions = { startup: () => {}, cleanup: () => { cleanups += 1; } };
  await assert.rejects(executePostgresTestPlan(["missing", "cleanup"], actions, directMeasurement), /Missing/);
  await assert.rejects(executePostgresTestPlan(["startup", "startup", "cleanup"], actions, directMeasurement), /unique/);
  assert.equal(cleanups, 2);
});

test("timings retain successful, failed, and cleanup durations without recording secret-bearing errors", async (context) => {
  const directory = temporaryDirectory(context);
  const outputPath = join(directory, "nested", "postgres-scenarios-full.json");
  let now = 1_000;
  const measure = createScenarioTimer(outputPath, { mode: "full", planned_stages: ["startup", "browser", "cleanup"] }, () => now);
  assert.equal(await measure("startup", async () => { now += 250; return 42; }), 42);
  await assert.rejects(measure("browser", async () => { now += 1_000; throw new Error("secret-canary"); }), /secret-canary/);
  await measure("cleanup", () => { now += 100; });
  const serialized = readFileSync(outputPath, "utf8");
  const report = JSON.parse(serialized);
  assert.deepEqual(report.stages, {
    startup: { duration_seconds: 0.25, exit_code: 0 },
    browser: { duration_seconds: 1, exit_code: 1 },
    cleanup: { duration_seconds: 0.1, exit_code: 0 },
  });
  assert.equal(report.mode, "full");
  assert.equal(serialized.includes("secret-canary"), false);
  assert.deepEqual(readdirSync(join(directory, "nested")), ["postgres-scenarios-full.json"]);
  await assert.rejects(measure("startup", () => {}), /Duplicate/);
});

test("nested PostgreSQL timings preserve start order, parent identity, process outcomes and secret exclusion", async context => {
  const outputPath = join(temporaryDirectory(context), "timings.json");
  let now = 1000;
  const measure = createScenarioTimer(outputPath, { planned_stages: ["deployment_lifecycle"] }, () => now);
  await measure("deployment_lifecycle", async () => {
    assert.equal(await measure.detail("rehearsal", "scenario", async () => {
      now += 100;
      assert.equal(measure.syncDetail("compose_shutdown", "restart", () => {
        now += 200;
        measure.processResult({ status: 143, stdout: "secret-canary", args: ["secret-canary"] });
        return 42;
      }), 42);
      await assert.rejects(measure.detail("migration_rejected", "migration", () => {
        now += 50;
        throw new Error("secret-canary");
      }), /secret-canary/);
      return "verified";
    }), "verified");
  });
  const serialized = readFileSync(outputPath, "utf8");
  const report = JSON.parse(serialized);
  assert.deepEqual(report.details.map(({ id, parent_id, label, exit_code, completed }) =>
    ({ id, parent_id, label, exit_code, completed })), [
    { id: 0, parent_id: null, label: "rehearsal", exit_code: 0, completed: true },
    { id: 1, parent_id: 0, label: "compose_shutdown", exit_code: 0, completed: true },
    { id: 2, parent_id: 0, label: "migration_rejected", exit_code: 1, completed: true },
  ]);
  assert.deepEqual(report.details.map(entry => entry.start_seconds), [0, 0.1, 0.3]);
  assert.deepEqual(report.details.map(entry => entry.duration_seconds), [0.35, 0.2, 0.05]);
  assert.equal(report.details[1].process_exit_code, 143);
  assert.deepEqual(Object.keys(report.stages), ["deployment_lifecycle"]);
  assert.equal(report.stages.deployment_lifecycle.exit_code, 0, "Expected rejection is still asserted by the scenario");
  assert(!serialized.includes("secret-canary"));
});

test("nested PostgreSQL timers keep concurrent Redis observation and deployment ownership separate", async context => {
  const outputPath = join(temporaryDirectory(context), "timings.json");
  const measure = createScenarioTimer(outputPath, {});
  let releaseDeployment;
  await measure("deployment_lifecycle", async () => {
    const deployment = measure.detail("redis_loading", "scenario", async () => {
      await new Promise(resolveDeployment => { releaseDeployment = resolveDeployment; });
      await measure.detail("redis_probe", "readiness", () => { measure.poll(); });
    });
    await measure.detail("loading_observation", "readiness", () => { measure.poll(); });
    releaseDeployment();
    await deployment;
  });
  const { details } = JSON.parse(readFileSync(outputPath, "utf8"));
  assert.deepEqual(details.map(entry => entry.parent_id), [null, null, 0]);
  assert.deepEqual(details.map(entry => entry.poll_count ?? 0), [0, 1, 1]);
});

test("nested PostgreSQL waiting records elapsed time and polls without changing the requested interval", async context => {
  context.mock.timers.enable({ apis: ["setTimeout"] });
  const outputPath = join(temporaryDirectory(context), "timings.json");
  let now = 0;
  const measure = createScenarioTimer(outputPath, {}, () => now);
  const execution = measure("startup", () => measure.detail("api_ready", "readiness", async () => {
    measure.poll();
    await measure.wait(1000);
    measure.poll();
  }));
  now = 1000;
  context.mock.timers.tick(1000);
  await execution;
  const { details } = JSON.parse(readFileSync(outputPath, "utf8"));
  assert.equal(details[0].waiting_seconds, 1);
  assert.equal(details[0].poll_count, 2);
  assert.equal(details[0].duration_seconds, 1);
});

test("nested timing write failures propagate and cleanup executes even when its measurement also fails", async context => {
  const directory = temporaryDirectory(context);
  const blockedPath = join(directory, "file");
  writeFileSync(blockedPath, "blocked");
  const measure = createScenarioTimer(join(blockedPath, "timings.json"), {});
  const failure = new Error("migration failed");
  let cleaned = false;
  await assert.rejects(executePostgresTestPlan(["schema_migrations", "cleanup"], {
    schema_migrations: () => measure.detail("schema_check", "migration", () => { throw failure; }),
    cleanup: () => measure.syncDetail("compose_teardown", "cleanup", () => { cleaned = true; }),
  }, measure), error => {
    assert(error instanceof AggregateError);
    assert.equal(error.errors[0].errors[0].errors[0], failure);
    return true;
  });
  assert(cleaned);
});

test("PostgreSQL command timings classify fixed operations without retaining arguments or credentials", () => {
  for (const [args, expected] of [
    [["image", "inspect", "secret-canary"], { label: "image_inspection", category: "inspection" }],
    [["compose", "-p", "secret-canary", "config"], { label: "compose_configuration", category: "inspection" }],
    [["volume", "ls", "secret-canary"], { label: "volume_inspection", category: "inspection" }],
    [["context", "inspect", "secret-canary"], { label: "daemon_context", category: "inspection" }],
    [["compose", "-p", "secret-canary", "ps"], { label: "container_inventory", category: "inspection" }],
    [["compose", "build", "secret-canary"], { label: "image_build", category: "build" }],
    [["compose", "stop", "maia-worker"], { label: "compose_shutdown", category: "restart" }],
    [["compose", "up", "--force-recreate"], { label: "compose_recreation", category: "startup" }],
    [["compose", "up", "--no-recreate", "--wait", "--wait-timeout", "180", "postgres"],
      { label: "postgres_ready", category: "readiness" }],
    [["run", "migration", "database/tooling/apply_postgres_migrations.py", "--writer-passfile", "secret-canary"],
      { label: "apply_postgres_migrations", category: "migration" }],
    [["run", "schema", "/source/database/tests/check_postgres_deletion.py"],
      { label: "check_postgres_deletion", category: "scenario" }],
    [["exec", "sh", "pg_dump --password=secret-canary"], { label: "backup_restore", category: "backup" }],
    [["inspect", "secret-canary"], { label: "resource_inspection", category: "inspection" }],
  ]) {
    assert.deepEqual(postgresCommandTiming(args), expected);
    assert(!JSON.stringify(postgresCommandTiming(args)).includes("secret-canary"));
  }
});

test("nested timing cannot qualify failed, missing or stale durability and lifecycle inventories", () => {
  for (const [layer, mode] of [["postgres", "durability"], ["lifecycle", "lifecycle"]]) {
    const planned_stages = postgresTestStages({ mode });
    const plan = { commit: "candidate", hash: "immutable-plan", jobs: { [layer]: { mode, planned_stages } } };
    const report = { runner: "postgres", mode, commit: plan.commit, plan_hash: plan.hash, planned_stages,
      acceptance_scope: mode, unresolved_owner_cases: mode === 'durability' ? unresolvedQueueCases : [], historical_priority_rollback: mode === 'durability' ? 'not executed' : null, stages: Object.fromEntries(planned_stages.map(stage => [stage, { exit_code: 0, duration_seconds: 1 }])),
      details: [...(mode === "durability" ? ["prefix_split_http_accept_replay", "prefix_split_http_restart_replay"].map(label => ({stage:"study_durability", label, category:"scenario", completed:true, exit_code:0})) : []), { label: "expected_migration_rejection", category: "migration", exit_code: 1, completed: true }] };
    assert.doesNotThrow(() => validatePostgresScenarios(layer, plan, report));
    for (const modify of [
      candidate => { candidate.stages[planned_stages[0]].exit_code = 1; },
      candidate => { delete candidate.stages[planned_stages[0]]; },
      candidate => { candidate.commit = "stale"; },
      candidate => { candidate.plan_hash = "stale"; },
      candidate => { candidate.planned_stages = candidate.planned_stages.slice(1); },
    ]) {
      const invalid = structuredClone(report);
      modify(invalid);
      assert.throws(() => validatePostgresScenarios(layer, plan, invalid), /missing, failed or mismatched/);
    }
  }
});

test("packaged Games reader cannot be missing, skipped, failed, or omitted from the qualification plan", () => {
  const planned_stages = postgresTestStages({ mode: "durability" });
  const plan = { commit: "candidate", hash: "immutable-plan", jobs: { postgres: { mode: "durability", planned_stages } } };
  const report = { runner: "postgres", mode: "durability", commit: plan.commit, plan_hash: plan.hash, planned_stages,
    acceptance_scope: 'durability', unresolved_owner_cases: unresolvedQueueCases, historical_priority_rollback: 'not executed', details: ["prefix_split_http_accept_replay", "prefix_split_http_restart_replay"].map(label => ({stage:"study_durability", label, category:"scenario", completed:true, exit_code:0})), stages: Object.fromEntries(planned_stages.map(stage => [stage, { exit_code: 0 }])) };
  assert.doesNotThrow(() => validatePostgresScenarios("postgres", plan, report));
  for (const modify of [
    candidate => { delete candidate.stages.game_review_reader; },
    candidate => { candidate.stages.game_review_reader = { skipped: true }; },
    candidate => { candidate.stages.game_review_reader.exit_code = 1; },
    candidate => { candidate.planned_stages = candidate.planned_stages.filter(stage => stage !== "game_review_reader"); },
  ]) {
    const invalid = structuredClone(report);
    modify(invalid);
    assert.throws(() => validatePostgresScenarios("postgres", plan, invalid), /missing, failed or mismatched/);
  }
  const stalePlan = structuredClone(plan);
  stalePlan.jobs.postgres.planned_stages = planned_stages.filter(stage => stage !== "game_review_reader");
  assert.throws(() => validatePostgresScenarios("postgres", stalePlan, report), /planned|inventory|stage/i);
});

test("completed invalid study fixtures fail immediately instead of polling for impossible admission", () => {
  assert.throws(() => assertNoCompletedFixtureConflict({
    scan_status: "idle", status: "needs_repair", issues: [{ kind: "conflicting_move" }],
  }, "fixture-id"), /fixture-id.*needs_repair.*conflicting_move/);
});

test("pending integrity generations and clean fixtures are not rejected as completed conflicts", () => {
  for (const scanStatus of ["queued", "running", "retrying"]) {
    assert.doesNotThrow(() => assertNoCompletedFixtureConflict({ scan_status: scanStatus, status: "needs_repair" }, "fixture"));
  }
  assert.doesNotThrow(() => assertNoCompletedFixtureConflict({ scan_status: "idle", status: "clean" }, "fixture"));
  assert.doesNotThrow(() => assertNoCompletedFixtureConflict({ scan_status: "idle", status: "unchecked" }, "fixture"));
});

test("full tier remains unfiltered while UI and browser tiers use browser-only PostgreSQL execution", () => {
  for (const tier of ["full", "ui", "browser"]) {
    const result = spawnSync(process.execPath, ["scripts/test-all.mjs", "--list", tier], {
      cwd: root, encoding: "utf8", timeout: 5_000,
    });
    assert.equal(result.status, 0, result.stderr);
    const { stages } = JSON.parse(result.stdout);
    const postgres = stages.filter((stage) => stage.name === "postgres_docker");
    assert.equal(postgres.length, 1);
    assert.deepEqual(postgres[0].args, tier === "full"
      ? ["database/tests/test-postgres-docker.mjs"]
      : ["database/tests/test-postgres-docker.mjs", "--mode", "browser"]);
  }
});

test("Make browser and durability select different scopes without changing the release entry point", () => {
  for (const [target, expected] of [
    ["browser", "node database/tests/test-postgres-docker.mjs --mode browser"],
    ["docker-durability", "node database/tests/test-postgres-docker.mjs --mode durability"],
    ["full", "node scripts/test-all.mjs full"],
  ]) {
    const result = spawnSync("make", ["-n", target], { cwd: root, encoding: "utf8", timeout: 5_000 });
    assert.equal(result.status, 0, result.stderr);
    assert(result.stdout.includes(expected));
  }
  const focusedWrapper = readFileSync(join(root, "database/tests/run-focused-postgres-browser.mjs"), "utf8");
  assert(focusedWrapper.includes('"--mode", "browser", flag, value'));
});

test("Docker context excludes generated test credentials and local cache churn", () => {
  const patterns = readFileSync(join(root, ".dockerignore"), "utf8").split(/\r?\n/);
  for (const expected of [".tempo-pg-test-secrets-*", ".dev-copies", ".pytest_cache", "**/__pycache__", "test-results"]) {
    assert(patterns.includes(expected), `Missing Docker exclusion: ${expected}`);
  }
});

test("disposable PostgreSQL backup handles INT and TERM while waiting on its sleeper", () => {
  const composeSource = readFileSync(join(root, "docker-compose.postgres.test.yml"), "utf8");
  const backupServiceSource = composeSource.match(/^  postgres-backup:\r?\n([\s\S]*?)(?=^  \S)/m)?.[1];
  assert(backupServiceSource, "Disposable backup service must remain in the Compose lifecycle");
  assert.match(backupServiceSource, /^    entrypoint: \[sh\]$/m);
  const quotedBackupCommand = backupServiceSource.match(/^    command: \[-c, (".*")\]$/m)?.[1];
  assert(quotedBackupCommand, "Backup idle command must be one shell argument");
  const backupCommand = JSON.parse(quotedBackupCommand);
  assert.equal(backupCommand, "trap 'exit 0' INT TERM; sleep infinity & wait",
    "Install both shutdown traps before waiting on the background sleeper; PID 1 cannot rely on default signal handling");
  const syntaxCheck = spawnSync("sh", ["-n", "-c", backupCommand], { encoding: "utf8", timeout: 5_000 });
  assert.ifError(syntaxCheck.error);
  assert.equal(syntaxCheck.status, 0, syntaxCheck.stderr);
});

test("PostgreSQL compositions omit retired compute services", () => {
  for (const file of ["docker-compose.yml", "docker-compose.postgres.yml", "docker-compose.postgres.test.yml"]) {
    const source = readFileSync(join(root, file), "utf8");
    for (const service of ["defense-engine", "maia-worker"]) assert.doesNotMatch(source, new RegExp(`^  ${service}:$`, "m"));
  }
});

test("real background worker stops gracefully and rejects forced kill or missing worker", async () => {
  for (const fault of [null, "forced_kill", "missing", "already_stopped"]) {
    let stopped = false;
    const proof = stopApplicationsAndVerifyWorkerSignals({
      runtime: { stopApplications: async () => { stopped = true; } },
      compose: async () => ({ stdout: fault === "missing" ? "" : "worker\n" }),
      docker: async () => ({ stdout: JSON.stringify([{ Config: { Labels: { "com.docker.compose.service": "background-worker" } },
        State: { Running: !stopped && fault !== "already_stopped", ExitCode: stopped ? fault === "forced_kill" ? 137 : 0 : 0 } }]) }),
    });
    if (fault) await assert.rejects(proof, /must|requires/); else await proof;
  }
});

test("scenario dispatch builds once and all startup paths forbid implicit rebuilds", () => {
  const source = readFileSync(join(root, "database/tests/test-postgres-docker.mjs"), "utf8");
  assert.equal(source.split("await managedComposeBuild(").length - 1, 1);
  assert.equal(source.includes('[...compose, "up", "--build"'), false);
  assert.equal(source.includes('[...compose, "up", "-d"'), false);
  assert(source.includes('[...compose, "up", "--no-build", "-d"]'));
  assert(source.includes('[...compose, "down", "--rmi", "local", "-v"]'));
  assert(source.includes('executePostgresTestPlan(stages, actions, measureScenario'));
});

test("CI isolates cancellation, caches dependencies and requires split complete verification", () => {
  const workflow = readFileSync(join(root, ".github/workflows/pages.yml"), "utf8");
  assert(workflow.includes("github.event.pull_request.number || github.ref"));
  assert(workflow.includes("cancel-in-progress: ${{ github.event_name == 'pull_request' }}"));
  const layers = readFileSync(join(root, ".github/workflows/verify-layer.yml"), "utf8");
  assert(layers.includes("cache: pip"));
  assert(layers.includes("uses: Swatinem/rust-cache@v2"));
  assert(workflow.includes("needs: [plan, frontend, backend, build, postgres, lifecycle, browser, visual, quarantine]"));
  assert(workflow.includes("node scripts/ci-quality.mjs"));
  assert(workflow.includes("node scripts/ci-verification-plan.mjs --base \"$TEMPO_BASE\" --complete"));
  assert(!workflow.includes("tempo-pages-deployment"));
});


test("timing write failure preserves the original scenario error and still permits cleanup", async (context) => {
  const directory = temporaryDirectory(context);
  const blockedDirectory = join(directory, "not-a-directory");
  writeFileSync(blockedDirectory, "blocked");
  const measure = createScenarioTimer(join(blockedDirectory, "report.json"), {});
  const testFailure = new Error("original scenario failure");
  let cleaned = false;
  await assert.rejects(executePostgresTestPlan(["browser", "cleanup"], {
    browser: () => { throw testFailure; },
    cleanup: () => { cleaned = true; },
  }, measure), (error) => {
    assert(error instanceof AggregateError);
    assert(error.errors[0] instanceof AggregateError);
    assert.equal(error.errors[0].errors[0], testFailure);
    return true;
  });
  assert.equal(cleaned, true);
});

test("background workload prevents scheduler claims and restores dispatch after failure", async () => {
  const runningConsumers = new Set(["background-worker", "background-scheduler"]);
  const queuedFixture = { state: "queued" };
  const dispatchQueuedFixture = () => {
    if (runningConsumers.has("background-scheduler")) queuedFixture.state = "leased";
  };
  const measurementFailure = new Error("workload interrupted after measurement");
  await assert.rejects(executeIsolatedBackgroundWorkload({
    stopConsumers: () => {
      for (const consumer of backgroundWorkloadConsumers) runningConsumers.delete(consumer);
    },
    measureWorkload: () => {
      dispatchQueuedFixture();
      assert.equal(queuedFixture.state, "queued", "scheduler must not claim measurement-owned rows");
      throw measurementFailure;
    },
    restoreConsumers: () => {
      for (const consumer of backgroundWorkloadConsumers) runningConsumers.add(consumer);
    },
  }), error => error === measurementFailure);
  dispatchQueuedFixture();
  assert.equal(queuedFixture.state, "leased", "normal dispatch resumes after fixture cleanup");
  assert.deepEqual([...runningConsumers].sort(), ["background-scheduler", "background-worker"]);
});

for (const failWorkload of [false, true]) {
  test(`background workload isolates consumers and restores them after ${failWorkload ? "failure" : "success"}`, async () => {
    const { executeIsolatedBackgroundWorkload } = await import("../../database/tests/postgres-test-plan.mjs");
    const events = [];
    const benchmarkFailure = new Error("benchmark failed");
    const result = executeIsolatedBackgroundWorkload({
      stopConsumers: async () => { events.push("consumers stopped and verified"); },
      measureWorkload: async () => {
        events.push("seed, measure, cleanup");
        if (failWorkload) throw benchmarkFailure;
      },
      restoreConsumers: async () => { events.push("consumers restored and ready"); },
    });
    if (failWorkload) await assert.rejects(result, (error) => error === benchmarkFailure);
    else await result;
    assert.deepEqual(events, ["consumers stopped and verified", "seed, measure, cleanup", "consumers restored and ready"]);
  });
}

test("background workload restores partially stopped consumers without masking the isolation failure", async () => {
  const { executeIsolatedBackgroundWorkload } = await import("../../database/tests/postgres-test-plan.mjs");
  const events = [];
  const isolationFailure = new Error("consumer stop failed");
  await assert.rejects(executeIsolatedBackgroundWorkload({
    stopConsumers: () => { events.push("stop"); throw isolationFailure; },
    measureWorkload: () => { events.push("benchmark"); },
    restoreConsumers: () => { events.push("restore"); },
  }), (error) => error === isolationFailure);
  assert.deepEqual(events, ["stop", "restore"]);
});

test("background workload preserves benchmark and consumer restoration failures", async () => {
  const { executeIsolatedBackgroundWorkload } = await import("../../database/tests/postgres-test-plan.mjs");
  const benchmarkFailure = new Error("benchmark failed");
  const restorationFailure = new Error("consumer restoration failed");
  await assert.rejects(executeIsolatedBackgroundWorkload({
    stopConsumers: () => {},
    measureWorkload: () => { throw benchmarkFailure; },
    restoreConsumers: () => { throw restorationFailure; },
  }), (error) => error instanceof AggregateError
    && error.errors[0] === benchmarkFailure && error.errors[1] === restorationFailure);
});

test("pr102 background restoration recovers API health before confirming restored workers", async () => {
  let apiHealthy = false;
  const runningConsumers = new Set();
  const events = [];
  await restoreBackgroundWorkloadConsumers({
    startConsumers: (services) => {

      for (const service of services) runningConsumers.add(service);
      events.push(services);
    },
    waitForReady: async () => {
      assert(runningConsumers.has("background-worker"));
      assert(runningConsumers.has("background-scheduler"));
      apiHealthy = true;
      events.push("original readiness passed");
    },
    verifyConsumers: (state) => {
      assert.equal(state, "running");
      assert.deepEqual([...runningConsumers].sort(), [...backgroundWorkloadConsumers].sort());
      events.push("verified all running");
    },
  });
  assert.deepEqual(events, [["background-worker", "background-scheduler"], "original readiness passed",
    "verified all running"]);
  const source = readFileSync(join(root, "database/tests/test-postgres-docker.mjs"), "utf8");
  // The retired priority benchmark and Defense proof have no restoration call sites.
  assert.equal(source.match(/restoreConsumers: \(\) => restoreBackgroundWorkloadConsumers\(/g)?.length, 3);
});


test("pr102 background restoration preserves readiness rejection before verification", async () => {
  const readinessFailure = new Error("API is still unhealthy");
  const events = [];
  await assert.rejects(restoreBackgroundWorkloadConsumers({
    startConsumers: (services) => events.push(services),
    waitForReady: () => { throw readinessFailure; },
    verifyConsumers: () => { throw new Error("Must not verify before readiness"); },
  }), error => error === readinessFailure);
  assert.deepEqual(events, [["background-worker", "background-scheduler"]]);
});


test("study_opening_server_restart_quiesces_all_database_clients_before_restart", () => {
  const source = readFileSync(join(root, "database/tests/test-postgres-docker.mjs"), "utf8");
  const restartProof = source.slice(source.indexOf("async function verifyStudyOpeningAdmission()"),
    source.indexOf("function verifyProjectIsUnused()"));
  assert(restartProof.includes("executeIsolatedBackgroundWorkload"),
    "The new database restart must use the existing stop/work/restore boundary");
  for (const client of ["api", "foreground-worker", "background-worker", "background-scheduler"]) assert(restartProof.includes(`"${client}"`),
    `${client} can retain a stale database connection or submit work during restart`);
  assert(restartProof.includes('"stop", ...postgresClients'));
  assert(restartProof.includes('"up", "--no-build", "-d", "--no-deps", ...postgresClients'));
  assert(restartProof.includes("restoreConsumers: async () =>"));
  assert(restartProof.includes("await waitForReady()"));
});

test("study_opening_server_restart_refreshes_proxy_before_public_readiness", () => {
  const source = readFileSync(join(root, "database/tests/test-postgres-docker.mjs"), "utf8");
  const restartProof = source.slice(source.indexOf("async function verifyStudyOpeningAdmission()"),
    source.indexOf("function verifyProjectIsUnused()"));
  const restoreClients = restartProof.indexOf('"up", "--no-build", "-d", "--no-deps", ...postgresClients');
  const refreshProxy = restartProof.indexOf('"restart", "web"');
  const publicReadiness = restartProof.indexOf("await waitForReady()");
  assert(restoreClients >= 0 && refreshProxy > restoreClients && refreshProxy < publicReadiness,
    "The proxy must resolve the restored API address before public readiness is tested");
});

test("historical source upgrade proof runs in regular durability upgrade stage", () => {
  const source = readFileSync(join(root, "database/tests/test-postgres-docker.mjs"), "utf8");
  const upgradeAction = source.slice(source.indexOf("  schema_migrations: async () => {"), source.indexOf("  background_diagnostics: async () => {"));
  assert(upgradeAction.includes("check_postgres_historical_source_upgrade.py"));
  for (const mode of ["durability", "full"]) assert(postgresTestStages({ mode }).includes("schema_migrations"));
  assert(!postgresTestStages({ mode: "browser" }).includes("schema_migrations"));
});


test("redis_fixture_native_isolation_regressions_run_in_regular_durability", () => {
  const runner = readFileSync("database/tests/test-postgres-docker.mjs", "utf8");
  assert(runner.includes('"/source/database/tests/check_postgres_redis_fixture_isolation.py"'));
});

test('study_priority_allocated_native_entry_preserves_sibling_imports_and_cgroup_checks',async()=>{
  const {allocatedNativeProofSource}=await import('../../database/tests/postgres-maintenance-proof.mjs');
  const directory=mkdtempSync(join(tmpdir(),'tempo-native-proof-import-'));
  try{
    writeFileSync(join(directory,'sibling.py'),'VALUE = 173\n');
    const entry=join(directory,'entry.py');
    writeFileSync(entry,"import sibling\nassert sibling.VALUE == 173\nprint('original native proof completed')\n");
    for(const limit of [{cpus:1.5,memoryMiB:1536},{cpus:0.95,memoryMiB:1024}]){
      const source=allocatedNativeProofSource(entry,'allocated_import_fixture',limit);
      const expectedCpu=String(limit.cpus*100000)+' 100000';
      const expectedMemory=String(limit.memoryMiB*1024*1024);
      for(const [cpu,memory,passes]of [[expectedCpu,expectedMemory,true],['20000 100000',expectedMemory,false],[expectedCpu,'268435456',false]]){
        const fixtures=`import pathlib\noriginal_read=pathlib.Path.read_text\ndef read(path,*a,**kw):\n if str(path)=='/sys/fs/cgroup/cpu.max': return ${JSON.stringify(cpu)}\n if str(path)=='/sys/fs/cgroup/memory.max': return ${JSON.stringify(memory)}\n return original_read(path,*a,**kw)\npathlib.Path.read_text=read\n`;
        const result=spawnSync('python3',['-I','-c',fixtures+source],{cwd:tmpdir(),encoding:'utf8'});
        if(passes){assert.equal(result.status,0,result.stderr);assert.match(result.stdout,/original native proof completed/);}
        else{assert.notEqual(result.status,0);assert.doesNotMatch(result.stdout,/original native proof completed/);}
      }
    }
  }finally{rmSync(directory,{recursive:true,force:true});}
});

test('study_priority_restoration_captures_output_and_checks_every_owned_consumer',async()=>{
  const {verifyRestoredPostgresConsumers}=await import('../../database/tests/postgres-maintenance-proof.mjs');
  const clients=['api','foreground-worker','background-worker','background-scheduler','web'];
  const execute=present=>(command,args,options)=>{
    assert.equal(command,'docker');assert.deepEqual(args,['compose','-p','owned','ps','--format','json']);
    return spawnSync(process.execPath,['-e',`for(const Service of ${JSON.stringify(present)})console.log(JSON.stringify({Service,State:'running'}));`],options);
  };
  verifyRestoredPostgresConsumers(execute(clients),['compose','-p','owned'],clients);
  assert.throws(()=>verifyRestoredPostgresConsumers(execute(clients.slice(1)),['compose','-p','owned'],clients),/api was not restored/);
});

test('study_priority_background_isolation_restores_consumers_after_api_readiness', async () => {
  assert.deepEqual(backgroundWorkloadConsumers, ['background-worker', 'background-scheduler']);
  for(const essential of ['api','foreground-worker','web'])assert(!backgroundWorkloadConsumers.includes(essential));
  const events=[];
  await restoreBackgroundWorkloadConsumers({startConsumers:services=>events.push(services),
    waitForReady:()=>events.push('api ready'),verifyConsumers:()=>events.push('all restored')});
  assert.deepEqual(events,[['background-worker','background-scheduler'],'api ready',
    'all restored']);
});

test('study_priority_profile_selection_cannot_understate_diagnostic_native_caps', async () => {
  const {postgresProofAllocations}=await import('../../database/tests/postgres-maintenance-proof.mjs');
  assert.equal(typeof postgresProofAllocations,'function');
  const budgets={developmentServices:{schema:{cpus:0.2}},priorityRehearsalServices:{schema:{cpus:1.5}},
    diagnosticRehearsalServices:{schema:{cpus:0.95}}};
  const paths={priority:'priority.json',diagnostic:'diagnostic.json'};
  assert.equal(postgresProofAllocations(['run','schema'],paths,budgets),budgets.developmentServices);
  assert.equal(postgresProofAllocations(['-f',paths.priority,'run','schema'],paths,budgets),budgets.priorityRehearsalServices);
  assert.equal(postgresProofAllocations(['-f',paths.diagnostic,'run','schema'],paths,budgets),budgets.diagnosticRehearsalServices);
  assert.throws(()=>postgresProofAllocations(['-f',paths.priority,'-f',paths.diagnostic,'run','schema'],paths,budgets),/Conflicting/);
});

for (const [runnerName, functionEnd] of [
  ['test-postgres-docker.mjs', '\nconst workloadConsumers'],
]) {
  for (const contention of ['reserved_builder', 'native_inventory_to_start_race']) {
    test(`study_priority_${runnerName.replaceAll('.', '_')}_${contention}_cannot_overcommit`, async () => {
      const { acquireDevelopmentBudget, assertDevelopmentCapacity, resourceBudgets, withDevelopmentStartup } =
        await import('../../scripts/development-budget.mjs');
      const { existsSync } = await import('node:fs');
      const parent = mkdtempSync(join(tmpdir(), 'tempo-native-admission-regression-'));
      const directory = join(parent, 'lease');
      const lease = await acquireDevelopmentBudget({ directory });
      const admissionLane = join(directory, 'admission-lane');
      const builderLane = join(directory, 'builder-lane');
      const source = readFileSync(join(root, runnerName === 'test-postgres-docker.mjs' ? 'database/tests' : 'scripts', runnerName), 'utf8');
      const start = source.indexOf('function run(command,');
      const end = source.indexOf(functionEnd, start);
      assert(start >= 0 && end > start, 'Execute the actual owning runner command path');
      let competingAllocation = contention === 'reserved_builder';
      if (competingAllocation) mkdirSync(builderLane);
      const events = [];
      const inventory = args => {
        assert.equal(args[0], 'ps');
        events.push('inventory-empty');
        // A same-owner sibling reaches its startup barrier after this snapshot.
        // Respect shared admission if the runner has already acquired it.
        if (contention === 'native_inventory_to_start_race' && !existsSync(admissionLane)) {
          competingAllocation = true;
          events.push('sibling-reserved-full-development-allocation');
        }
        return '';
      };
      const context = {
        project: 'owned-native', environment: {}, env: {}, resourceBudgets,
        priorityResourceLimits: 'priority.json', diagnosticResourceLimits: 'diagnostic.json',
        postgresProofAllocations: () => resourceBudgets.developmentServices,
        assertDevelopmentCapacity: (services, project, _docker, allocations) =>
          assertDevelopmentCapacity(services, project, inventory, allocations),
        withDevelopmentStartup: (services, project, start, options) => withDevelopmentStartup(services,project,start,{...options,directory,token:lease.token,docker:inventory}),
        postgresCommandTiming: () => ({ label: 'start', category: 'test' }),
        measureScenario: { syncDetail: (_label, _category, operation) => operation(), processResult: () => {} },
        spawnSync: (command, args) => {
          assert.equal(command, 'docker');assert(args.includes('up'));
          events.push('docker-start');
          assert.equal(competingAllocation, false,
            `${runnerName} launched Docker while a sibling owns the full 2048 MiB allocation; ${events.join(' -> ')}`);
          return { status: 0 };
        },
      };
      try {
        const run = runInNewContext(`(${source.slice(start, end)})`, context);
        try { await run('docker', ['compose', 'up', '--wait', 'postgres']); }
        catch (error) {
          if (/occupied|admission|exceed the aggregate/.test(String(error))) return;
          throw error;
        }
        assert(events.includes('docker-start'), 'The actual start path must execute or explicitly defer');
      } finally {
        rmSync(builderLane, { recursive: true, force: true });
        await lease.release();
        rmSync(parent, { recursive: true, force: true });
      }
    });
  }
}

async function nativeStartupFixture(action) {
  const budget=await import('../../scripts/development-budget.mjs');
  const {existsSync}=await import('node:fs');
  const parent=mkdtempSync(join(tmpdir(),'tempo-native-startup-owner-'));
  const directory=join(parent,'lease'),lease=await budget.acquireDevelopmentBudget({directory});
  const project='owned-startup',state={containers:[],inspectionFails:false};
  const container=(service='postgres',cpus=.4,memoryMiB=352)=>({Id:'owned-container',State:{Running:true},
    HostConfig:{NanoCpus:cpus*1e9,Memory:memoryMiB*1024**2},Config:{Labels:{'tempo.resource-owner':lease.token,
      'tempo.resource-class':'development','com.docker.compose.project':project,'com.docker.compose.service':service}}});
  const docker=args=>{
    if(state.inspectionFails)throw new Error('Injected Docker inspection uncertainty');
    if(args[0]==='ps')return state.containers.filter(value=>args.includes('-aq')||value.State.Running).map(value=>value.Id).join('\n');
    if(args[0]==='inspect')return JSON.stringify(state.containers);
    throw new Error('Unexpected injected Docker command '+args.join(' '));
  };
  const options={directory,token:lease.token,docker},paths={lane:join(directory,'admission-lane'),journal:join(directory,'resources',encodeURIComponent('startup:'+project))};
  try{return await action({budget,lease,state,container,options,paths,project,existsSync});}
  finally{ // All Docker state is injected; only this test's private mock lease is removed.
    rmSync(parent,{recursive:true,force:true});
  }
}

test('native_startup_exception_retains_atomic_admission_until_exact_absence_reconciliation',()=>nativeStartupFixture(async({budget,lease,options,paths,project,existsSync})=>{
  assert.throws(()=>budget.withDevelopmentStartup(['postgres'],project,()=>{assert(existsSync(paths.journal));throw new Error('Ambiguous startup');},options),/Ambiguous startup/);
  assert(existsSync(paths.lane));assert(existsSync(paths.journal));await assert.rejects(lease.release());
  budget.reconcileDevelopmentStartup(project,options);assert(!existsSync(paths.lane));assert(!existsSync(paths.journal));await lease.release();
}));

test('native_startup_partial_failure_and_post_start_inspection_uncertainty_retain_the_reservation',()=>nativeStartupFixture(async({budget,state,container,options,paths,project,existsSync})=>{
  assert.throws(()=>budget.withDevelopmentStartup(['postgres'],project,()=>{state.containers=[container()];return{status:1};},options),/startup failed/);
  assert.throws(()=>budget.reconcileDevelopmentStartup(project,options),/resources remain/);assert(existsSync(paths.lane));assert(existsSync(paths.journal));
  state.containers=[];budget.reconcileDevelopmentStartup(project,options);
  assert.throws(()=>budget.withDevelopmentStartup(['postgres'],project,()=>{state.inspectionFails=true;return{status:0};},options),/inspection uncertainty/);
  assert(existsSync(paths.lane));assert(existsSync(paths.journal));state.inspectionFails=false;budget.reconcileDevelopmentStartup(project,options);
}));

test('native_startup_visible_success_preserves_result_and_ownership_until_project_cleanup',()=>nativeStartupFixture(async({budget,lease,state,container,options,paths,project,existsSync})=>{
  const expected={status:0,stdout:'unchanged stdout',stderr:'unchanged stderr'};
  const actual=budget.withDevelopmentStartup(['postgres'],project,()=>{assert(existsSync(paths.lane));state.containers=[container()];return expected;},options);
  assert.equal(actual,expected);assert(!existsSync(paths.lane));assert(existsSync(paths.journal));await assert.rejects(lease.release());
  state.containers=[];budget.reconcileDevelopmentStartup(project,options);await lease.release();
}));

test('native_startup_one_off_compose_run_adds_allocation_even_with_same_service_already_running',()=>nativeStartupFixture(async({budget,state,container,options,paths,project,existsSync})=>{
  state.containers=[container('schema',1,1024)];let starts=0;
  assert.throws(()=>budget.withDevelopmentStartup(['schema'],project,()=>{starts++;return{status:0};},
    {...options,oneOff:true,allocations:{schema:{cpus:1.5,memoryMiB:1536}}}),/exceed the aggregate/);
  assert.equal(starts,0);assert(!existsSync(paths.lane));assert(!existsSync(paths.journal));
}));

test('native_startup_reconciliation_never_reclaims_changed_owner_or_missing_pid_claims',()=>nativeStartupFixture(async({budget,options,paths,project,existsSync})=>{
  assert.throws(()=>budget.withDevelopmentStartup(['postgres'],project,()=>{throw new Error('ambiguous');},options),/ambiguous/);
  const original=JSON.parse(readFileSync(paths.journal,'utf8'));
  writeFileSync(paths.journal,JSON.stringify({...original,pid:2147483647}));
  assert.throws(()=>budget.reconcileDevelopmentStartup(project,options),/ownership mismatch/);assert(existsSync(paths.lane));assert(existsSync(paths.journal));
  assert.throws(()=>budget.reconcileDevelopmentStartup(project,{...options,claimPid:2147483647}),/ownership mismatch/,'Caller cannot override the actual cleanup process identity');
  writeFileSync(paths.journal,JSON.stringify(original));const ownerPath=join(paths.lane,'startup-owner.json');const owner=JSON.parse(readFileSync(ownerPath,'utf8'));
  writeFileSync(ownerPath,JSON.stringify({...owner,token:'foreign'}));assert.throws(()=>budget.reconcileDevelopmentStartup(project,options),/ownership mismatch/);assert(existsSync(paths.journal));
  writeFileSync(ownerPath,JSON.stringify(owner));budget.reconcileDevelopmentStartup(project,options);
}));

test('native_startup_missing_lane_identity_and_changed_success_owner_keep_uncertainty_blocked',()=>nativeStartupFixture(async({budget,options,paths,project,existsSync})=>{
  assert.throws(()=>budget.withDevelopmentStartup(['postgres'],project,()=>{throw new Error('ambiguous');},options),/ambiguous/);
  const ownerPath=join(paths.lane,'startup-owner.json'),original=readFileSync(ownerPath,'utf8');rmSync(ownerPath);
  assert.throws(()=>budget.reconcileDevelopmentStartup(project,options),/unverified/);assert(existsSync(paths.journal));
  writeFileSync(ownerPath,original);budget.reconcileDevelopmentStartup(project,options);
  assert.throws(()=>budget.withDevelopmentStartup(['postgres'],project,()=>{const owner=JSON.parse(readFileSync(ownerPath,'utf8'));writeFileSync(ownerPath,JSON.stringify({...owner,token:'foreign'}));return{status:0};},options),/ownership mismatch/);
  assert(existsSync(paths.lane));assert(existsSync(paths.journal));writeFileSync(ownerPath,original);budget.reconcileDevelopmentStartup(project,options);
}));

test('native_startup_rejects_inherited_lease_mismatch_before_any_external_effect',()=>nativeStartupFixture(async({budget,options,paths,project,existsSync})=>{
  let starts=0;assert.throws(()=>budget.withDevelopmentStartup(['postgres'],project,()=>{starts++;return{status:0};},{...options,token:'foreign'}),/ownership changed/);
  assert.equal(starts,0);assert(!existsSync(paths.lane));assert(!existsSync(paths.journal));
}));

test('native_actual_standalone_help_partial_start_has_owned_identity_and_verified_cleanup_before_reconciliation',()=>nativeStartupFixture(async({budget,state,container,options,paths,project,existsSync})=>{
  const source=readFileSync(join(root,'database/tests/test-postgres-docker.mjs'),'utf8'),name=project+'-maintenance-help',commands=[];
  const begin=source.indexOf('  maintenance_cli: async () => {'),end=source.indexOf('  startup: async () => {',begin);
  const action=runInNewContext(`({${source.slice(begin,end)}})`,{candidateMode:null,borrowedCandidate:null,maintenanceImageCleanupRequired:false,maintenanceImage:'owned-maintenance',maintenanceHelpContainer:name,project,
    candidateRevision:'test-revision',process:{env:{TEMPO_DEVELOPMENT_BUDGET_TOKEN:options.token}},console:{log(){}},managedBuild:async build=>build('owned-builder'),run:(_command,args)=>commands.push(Array.from(args))});
  await action.maintenance_cli();const args=commands.find(args=>args[0]==='run');assert(args,'Exercise actual standalone maintenance start arguments');
  assert.equal(args[args.indexOf('--name')+1],name);
  const context={project,maintenanceHelpContainer:name,environment:{},assert,process:{env:{TEMPO_DEVELOPMENT_BUDGET_TOKEN:options.token}},
    postgresCommandTiming:()=>({label:'start',category:'test'}),measureScenario:{syncDetail:(_label,_category,call)=>call(),processResult(){}},
    withDevelopmentStartup:(services,p,start,opts)=>{assert.equal(opts.oneOff,true);assert.equal(opts.allocations['maintenance-help'].memoryMiB,256);assert.equal(opts.allocations['maintenance-help'].cpus,undefined);return budget.withDevelopmentStartup(services,p,start,{...options,...opts});},
    spawnSync:(_command,actualArgs)=>{assert.equal(actualArgs[0],'run');const labels=Object.fromEntries(actualArgs.flatMap((value,index)=>value==='--label'?[actualArgs[index+1].split(/=(.*)/s).slice(0,2)]:[]));
      const raw=container('maintenance-help',0,256);raw.Name='/'+name;raw.Config.Labels=labels;state.containers=[raw];return{error:new Error('response lost after Docker creation'),status:null};}};
  const start=source.indexOf('function run(command,'),finish=source.indexOf('\nconst workloadConsumers',start),run=runInNewContext(`(${source.slice(start,finish)})`,context);
  assert.throws(()=>run('docker',args),/failed/);assert(existsSync(paths.lane));assert(existsSync(paths.journal));
  assert.throws(()=>budget.reconcileDevelopmentStartup(project,options),/resources remain/);
  let removalWorks=false,removals=0;
  context.spawnSync=(_command,call)=>{
    if(call[0]==='container'){
      const value=state.containers.find(value=>call[2]===name||call[2]===value.Id);
      return value?{status:0,stdout:JSON.stringify([value])}:{status:1,stderr:'Error response from daemon: No such container: '+call[2]};
    }
    assert.equal(call[0],'rm');assert.equal(call.at(-1),'owned-container');removals++;if(removalWorks)state.containers=[];return{status:0};
  };
  const cleanupStart=source.indexOf('function removeOwnedMaintenanceHelpContainer()'),cleanupEnd=source.indexOf('\nfunction run(',cleanupStart),cleanup=runInNewContext(`(${source.slice(cleanupStart,cleanupEnd)})`,context);
  state.containers[0].Config.Labels['tempo.resource-owner']='foreign';assert.throws(cleanup,/ownership mismatch/);assert.equal(removals,0);
  state.containers[0].Config.Labels['tempo.resource-owner']=options.token;assert.throws(cleanup,/still exists/);assert(existsSync(paths.journal));assert(existsSync(paths.lane));
  removalWorks=true;cleanup();budget.reconcileDevelopmentStartup(project,options);assert(!existsSync(paths.journal));assert(!existsSync(paths.lane));assert.equal(removals,2);
}));

test("rehearsal volume validation fails closed on installed or colliding targets", () => {
  const project = "tempo-pg-regressions-fixture";
  const keys = ["data", "redis", "backups"];
  const stack = { name: project, volumes: Object.fromEntries(keys.map(key => [key, { name: `${project}_${key}` }])),
    services: { postgres: { volumes: [{ type: "volume", source: "data", target: "/var/lib/postgresql" }] },
      redis: { volumes: [{ type: "volume", source: "redis", target: "/data" }] },
      backup: { volumes: [{ type: "volume", source: "backups", target: "/backups" }] } } };
  const mounts = [["postgres", "/var/lib/postgresql", "data"], ["redis", "/data", "redis"], ["backup", "/backups", "backups"]];
  assert.doesNotThrow(() => assertDisposableVolumes(stack, project, keys, mounts));
  for (const change of [s => {s.volumes.backups.name = "tempo-postgres-backups";},
    s => {s.volumes.data.external = true;}, s => {s.services.backup.volumes[0].source = "data";},
    s => {s.volumes.extra = {name: "tempo-postgres-data"};}]) {
    const unsafe = structuredClone(stack); change(unsafe);
    assert.throws(() => assertDisposableVolumes(unsafe, project, keys, mounts), /Unsafe|Unexpected/);
  }
  assert.throws(() => assertDisposableVolumes(stack, "tempo", keys, mounts), /identity/);
});

test("disposable volume collision check fails closed before image build", () => {
  const source = readFileSync(join(root, "database/tests/test-postgres-docker.mjs"), "utf8");
  const check = source.slice(source.indexOf("function verifyDisposableVolumesAbsent("), source.indexOf("function verifyProjectIsUnused("));
  for (const result of [{status: 0, stderr: ""}, {status: 1, stderr: "permission denied"}, {error: new Error("offline")}]) {
    assert.throws(() => runInNewContext(`${check}\nverifyDisposableVolumesAbsent(["owned_volume"])`,
      { spawnSync: () => result, environment: {} }), /absence is unverified/);
  }
  assert.doesNotThrow(() => runInNewContext(`${check}\nverifyDisposableVolumesAbsent(["owned_volume"])`,
    { spawnSync: () => ({status: 1, stderr: "Error: No such volume: owned_volume"}), environment: {} }));
  const config = source.slice(source.indexOf("  compose_config: async () => {"), source.indexOf("  image_build: async () => {"));
  assert(config.includes("verifyDisposableVolumesAbsent"));
});

test('position native pytest selection is finite and cleanup survives failure', async () => {
  const options = parse(['--mode', 'browser', '--native-check', 'position-grade-effects']);
  const stages = postgresTestStages(options);
  assert(stages.indexOf('position-grade-effects') < stages.indexOf('browser'));
  assert.equal(stages.at(-1), 'cleanup');
  for (const args of [
    ['--mode', 'browser', '--native-check', 'position-grade-effects', '--native-check', 'position-grade-effects'],
    ['--mode', 'browser', '--native-check', 'arbitrary-pytest'],
    ['--mode', 'full', '--native-check', 'position-grade-effects'],
  ]) assert.throws(() => parse(args));
  const observed = [];
  const actions = Object.fromEntries(stages.map(stage => [stage, async () => {
    observed.push(stage);
    if (stage === 'position-grade-effects') throw new Error('native failed');
  }]));
  await assert.rejects(executePostgresTestPlan(stages, actions, (_stage, run) => run()), /native failed/);
  assert.equal(observed.at(-1), 'cleanup');
  assert(!observed.includes('browser'));
  const result = spawnSync('python3', ['database/tests/test_position_native_pytest_bridge.py'], {cwd: root, encoding: 'utf8'});
  assert.equal(result.status, 0, result.stderr);
});


test('position introductions selects the exact owned native bridge and cleans up on failure', async () => {
  const stages = postgresTestStages(parse(['--mode', 'browser', '--native-check', 'position-introductions']));
  assert(stages.indexOf('position-introductions') < stages.indexOf('browser'));
  assert(!stages.includes('position-grade-effects'));
  for (const args of [
    ['--mode', 'browser', '--native-check', 'position-introductions', '--native-check', 'position-introductions'],
    ['--mode', 'browser', '--native-check', 'backend/tests/test_position_introductions.py'],
    ['--mode', 'full', '--native-check', 'position-introductions'],
  ]) assert.throws(() => parse(args));
  const observed = [];
  const actions = Object.fromEntries(stages.map(stage => [stage, async () => {
    observed.push(stage);
    if (stage === 'position-introductions') throw new Error('introduction failed');
  }]));
  await assert.rejects(executePostgresTestPlan(stages, actions, (_stage, run) => run()), /introduction failed/);
  assert.equal(observed.at(-1), 'cleanup');
  assert(!observed.includes('browser'));
  const source = readFileSync(join(root, 'database/tests/test-postgres-docker.mjs'), 'utf8');
  assert(source.includes('TEMPO_NATIVE_POSITION_CHECK=${check}'));
  assert(source.includes('checks: {[check]: evidence}'));
});


test('position queue admission selects only its exact bridge and blocks browser after failure', async () => {
  const check = 'position-queue-admission';
  const stages = postgresTestStages(parse(['--mode', 'browser', '--native-check', check]));
  assert(stages.indexOf(check) < stages.indexOf('browser'));
  assert(!stages.includes('position-introductions'));
  assert(!stages.includes('position-grade-effects'));
  for (const args of [
    ['--mode', 'browser', '--native-check', check, '--native-check', check],
    ['--mode', 'browser', '--native-check', 'backend/tests/test_position_queue_admission.py::test_native_refresh_admits_due_carrier_and_reuses_pending'],
    ['--mode', 'full', '--native-check', check],
  ]) assert.throws(() => parse(args));
  const observed = [];
  const actions = Object.fromEntries(stages.map(stage => [stage, async () => {
    observed.push(stage);
    if (stage === check) throw new Error('admission failed');
  }]));
  await assert.rejects(executePostgresTestPlan(stages, actions, (_stage, run) => run()), /admission failed/);
  assert.equal(observed.at(-1), 'cleanup');
  assert(!observed.includes('browser'));
});

test('prefix split uses only its full-schema bridge and stops before browser on proof failure', async () => {
  const check = 'prefix-split-placement';
  const stages = postgresTestStages(parse(['--mode', 'browser', '--native-check', check]));
  assert(stages.indexOf(check) < stages.indexOf('browser'));
  assert(!stages.includes('position-queue-admission'));
  for (const args of [
    ['--mode', 'browser', '--native-check', check, '--native-check', check],
    ['--mode', 'full', '--native-check', check],
    ['--mode', 'browser', '--native-check', 'database/tests/native_review/test_postgres.py'],
  ]) assert.throws(() => parse(args));
  const observed = [];
  const actions = Object.fromEntries(stages.map(stage => [stage, async () => {
    observed.push(stage);
    if (stage === check) throw new Error('split failed');
  }]));
  await assert.rejects(executePostgresTestPlan(stages, actions, (_stage, run) => run()), /split failed/);
  assert.equal(observed.at(-1), 'cleanup');
  assert(!observed.includes('browser'));
  const source = readFileSync(join(root, 'database/tests/test-postgres-docker.mjs'), 'utf8');
  assert(source.includes("check === 'prefix-split-placement'"));
  assert(source.includes('/source/database/tests/native-split-pytest.py'));
});

test("game review reader alias is finite and preserves prefix selection and cleanup", async () => {
  for (const mode of ["browser", "study"]) {
    const options = parse(["--mode", mode, "--browser-grep", "Q0", "--native-check", "prefix-split-placement", "--native-check", "game-review-reader"]);
    const stages = postgresTestStages(options);
    assert(stages.indexOf("prefix-split-placement") < stages.indexOf("game_review_reader"));
    assert(stages.indexOf("game_review_reader") < stages.indexOf("browser"));
    assert(!stages.includes("game-review-reader"));
    const called = [];
    const actions = Object.fromEntries(stages.map(stage => [stage, () => {
      called.push(stage);
      if (stage === "game_review_reader") throw new Error("reader proof failed");
    }]));
    await assert.rejects(executePostgresTestPlan(stages, actions, directMeasurement), /reader proof failed/);
    assert.deepEqual(called, [...stages.slice(0, stages.indexOf("game_review_reader") + 1), "cleanup"]);
  }
  for (const mode of ["full", "durability", "lifecycle", "lifecycle-current"]) {
    assert.throws(() => parse(["--mode", mode, "--native-check", "game-review-reader"]));
    assert.throws(() => postgresTestStages({ mode, nativeChecks: ["game-review-reader"] }));
  }
  for (const checks of [["game-review-reader", "game-review-reader"], ["game_review_reader"],
    ["database/tests/check_postgres_game_review_reader.py"], ["arbitrary-stage"]]) {
    assert.throws(() => parse(["--mode", "browser", ...checks.flatMap(check => ["--native-check", check])]));
    assert.throws(() => postgresTestStages({ mode: "browser", nativeChecks: checks }));
  }
});


test('selected Study durability retains current history and quota proofs without retired portable imports', () => {
  const source = readFileSync(join(root, 'database/tests/test-postgres-docker.mjs'), 'utf8');
  const action = source.slice(source.indexOf('  study_durability: async () => {'), source.indexOf('  cleanup: async () =>'));
  for (const proof of ['verifyForegroundAndStudyDurability', 'verifyStudyBurialRetainsQuota', 'verifyBlockedBurialRecovery'])
    assert(action.includes(proof), `Missing retained ${proof}`);
  assert(!action.includes('check_postgres_stalemate_swindles.py'));
  assert(postgresTestStages({mode: 'study'}).includes('study_durability'));
});


test("P04 maintained queue receipt distinguishes pending native replacement from unresolved owner", () => {
  const receipt = { case_set: "maintained-six", scope: "partial", execution: "completed", executed_count: 6,
    cases: maintainedQueueCases, excluded_cases: excludedQueueCases, unresolved_owner_cases: unresolvedQueueCases };
  assert.equal(excludedQueueCases[0], "test_postgres_paused_feedback_history_preserves_ordinary_study");
  assert.deepEqual(unresolvedQueueCases, ["test_postgres_retained_review_reconciliation_preserves_original_attempt"]);
  verifyMaintainedQueueReceipt(receipt);
  assert.throws(() => verifyMaintainedQueueReceipt({ ...receipt, excluded_cases: unresolvedQueueCases }));
  assert.throws(() => verifyMaintainedQueueReceipt({ ...receipt, unresolved_owner_cases: excludedQueueCases }));
});


test('P07 position atomicity bridge is explicitly selectable and cannot widen mode', () => {
  const options = parse(['--mode', 'study', '--browser-grep', 'P07', '--native-check', 'position-review-atomicity']);
  assert(postgresTestStages(options).includes('position-review-atomicity'));
  assert.throws(() => parse(['--mode','durability','--native-check','position-review-atomicity']));
  assert.throws(() => parse(['--mode','study','--browser-grep','P07','--native-check','position-review-atomicity','--native-check','position-review-atomicity']));
  const source = readFileSync('database/tests/test-postgres-docker.mjs', 'utf8');
  assert(source.includes("'position-grade-effects', 'position-review-atomicity', 'position-introductions'"));
});

test('P07 HTTP split report requires both exact completed cases and correct scope', async () => {
  const { verifyStudyDurabilityScenario } = await import('../../database/tests/postgres-test-plan.mjs');
  const details = ['prefix_split_http_accept_replay','prefix_split_http_restart_replay'].map(label => ({stage:'study_durability', label, category:'scenario', completed:true, exit_code:0}));
  const report = {acceptance_scope:'durability', unresolved_owner_cases:unresolvedQueueCases, historical_priority_rollback:'not executed', details};
  assert.doesNotThrow(() => verifyStudyDurabilityScenario(report, 'durability'));
  for (const invalid of [
    {...report, acceptance_scope:'full'}, {...report, details:[]},
    {...report, details:[details[0],details[0],details[1]]},
    {...report, details:[details[0], {...details[1], skipped:true}]},
    {...report, details:[details[0], {...details[1], retries:1}]},
    {...report, details:[details[0], {...details[1], exit_code:1}]},
  ]) assert.throws(() => verifyStudyDurabilityScenario(invalid, 'durability'));
});


test('P07 CI PostgreSQL consumer rejects missing duplicate and partial HTTP evidence', () => {
  const mode = 'durability', planned_stages = postgresTestStages({mode});
  const plan = {commit:'candidate', hash:'plan', jobs:{postgres:{mode, planned_stages}}};
  const labels = ['prefix_split_http_accept_replay','prefix_split_http_restart_replay'];
  const report = {runner:'postgres',mode,commit:plan.commit,plan_hash:plan.hash,planned_stages,acceptance_scope:mode, unresolved_owner_cases:unresolvedQueueCases, historical_priority_rollback:'not executed',
    stages:Object.fromEntries(planned_stages.map(stage => [stage,{exit_code:0}])),
    details:labels.map(label => ({stage:'study_durability',label,category:'scenario',completed:true,exit_code:0}))};
  assert.doesNotThrow(() => validatePostgresScenarios('postgres',plan,report));
  for (const modify of [r => {r.details.pop();}, r => {r.details.push({...r.details[0]});},
    r => {r.details[0].skipped=true;}, r => {r.acceptance_scope='partial-maintained-study-not-full-E02';},
    r => {r.stages.cleanup.skipped=true;}, r => {r.unresolved_owner_cases=[];}, r => {r.historical_priority_rollback='passed';}]) {
    const bad=structuredClone(report); modify(bad);
    assert.throws(() => validatePostgresScenarios('postgres',plan,bad));
  }
});


test('P07 original split rows survive removed membership and foreign card owner', async () => {
  const {originalPrefixSplitRows} = await import('../../database/tests/postgres-test-plan.mjs');
  const expected={source_card_id:'original',source_revision:4,shortened_card_id:'parent',continuation_card_id:'child'};
  const snapshot={tables:{repertoire_cards:[{repertoire_id:'current',card_id:'parent'}],
    cards:[{id:'original',repertoire_id:'foreign',archived:1}],
    prefix_splits:[{...expected,extra:'unchanged'}, {source_card_id:'other'}]}};
  assert.deepEqual(originalPrefixSplitRows(snapshot,'original'),[expected]);
  assert.deepEqual(originalPrefixSplitRows(snapshot,'absent'),[]);
});

test('P07 accepts only the original operation receipt identity', async () => {
  const {requireOriginalOperationReceipt} = await import('../../database/tests/postgres-test-plan.mjs');
  const receipt={operation_id:'original',state:'complete',response:{applied:true}};
  assert.strictEqual(requireOriginalOperationReceipt(receipt,'original'),receipt);
  for (const bad of [{...receipt,operation_id:'foreign'}, {...receipt,operation_id:undefined}, null])
    assert.throws(() => requireOriginalOperationReceipt(bad,'original'), /identity/);
});

test('P07 polling rejects foreign initial and completed operation identities', async () => {
  const {requireOriginalOperationReceipt} = await import('../../database/tests/postgres-test-plan.mjs');
  const source = readFileSync('database/tests/test-postgres-docker.mjs', 'utf8');
  const body = source.slice(source.indexOf('async function pollForConfirmation('), source.indexOf('\nasync function waitForStudyableImport('));
  let receipt = {operation_id:'foreign', state:'complete', response:{applied:true}};
  const poll = runInNewContext(`(${body})`, {assert, requireOriginalOperationReceipt,
    get: async () => receipt, measureScenario: {poll() {}, wait: async () => {}}, encodeURIComponent, Error});
  const accepted = id => new Response(JSON.stringify({operation_id:id, state:'queued'}), {status:202});
  await assert.rejects(poll(accepted('foreign'), 'original'), /identity/);
  await assert.rejects(poll(accepted('original'), 'original'), /identity/);
  receipt = {operation_id:'original', state:'complete', response:{applied:true}};
  assert.deepEqual(await poll(accepted('original'), 'original'), {applied:true});
});
