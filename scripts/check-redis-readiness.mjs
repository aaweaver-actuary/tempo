// Persisted Redis startup proof inside the CLI runner's disposable target.
import assert from "node:assert/strict";
import { existsSync, mkdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { atomicJson } from "./tempo-deployment.mjs";
import { applicationServices, configurationFingerprint, createRuntime, executeLifecycle } from "./tempo-runtime.mjs";

// Observe the existing lifecycle stop boundary; do not add a restart or alter its deadline.
export async function stopApplicationsAndVerifyWorkerSignals({ runtime, compose, docker }) {
  const workerServices = ["defense-engine", "maia-worker"];
  const inspectWorkers = async () => {
    const ids = (await compose(["ps", "-aq", ...workerServices])).stdout.trim().split(/\s+/).filter(Boolean);
    assert.equal(ids.length, workerServices.length, "Both real workers must exist at the lifecycle stop boundary");
    const containers = JSON.parse((await docker(["inspect", ...ids])).stdout);
    assert.deepEqual(containers.map(container => container.Config.Labels["com.docker.compose.service"]).sort(), [...workerServices].sort());
    return containers;
  };
  for (const container of await inspectWorkers()) {
    assert.equal(container.HostConfig.Init, true, "Real workers must use Docker init");
    assert.equal(container.State.Running, true, "Signal proof requires a running worker");
  }
  await runtime.stopApplications();
  for (const container of await inspectWorkers()) {
    assert.equal(container.State.Running, false, "Lifecycle stop must finish before Redis recreation");
    assert.equal(container.State.ExitCode, 143, `${container.Config.Labels["com.docker.compose.service"]} must exit through SIGTERM, without forced-kill exit 137`);
  }
  console.log("PASS test_postgres_lifecycle_real_workers_stop_through_sigterm_without_forced_kill");
}

async function waitForFixtureCondition(description, probe, measure) {
  const deadline = performance.now() + 30_000;
  let observation;
  while (performance.now() < deadline) {
    measure?.poll();
    observation = await probe();
    if (observation.ready) return observation;
    if (measure) await measure.wait(100);
    else await new Promise(resolveWait => setTimeout(resolveWait, 100));
  }
  throw new Error(`${description} was not observed: ${JSON.stringify(observation)}`);
}

export async function verifyPersistedRedisReadiness({ target, runtime, run, compose, docker, directory, revision,
  images, readHistory, expectedHistory, expectedSchema, commandLog, measure = null }) {
  assert(target.disposable && target.project.startsWith("tempo-pg-regressions-"));
  const waitForCondition = (description, probe) => waitForFixtureCondition(description, probe, measure);
  const fixturePrefix = "tempo:redis-readiness-fixture:";
  const redis = (args, options = {}) => compose(["exec", "-T", "redis", "redis-cli", "-e", "--raw", ...args],
    { timeout: 5000, ...options });
  const persistence = async () => Object.fromEntries((await redis(["INFO", "persistence"])).stdout.trim().split(/\r?\n/)
    .filter(line => line.includes(":")).map(line => line.split(":")));
  await stopApplicationsAndVerifyWorkerSignals({ runtime, compose, docker });
  // RDB AOF bases process events while loading even with a small fixture.
  // Per-key delay keeps LOADING observable; CONFIG SET releases it immediately
  // after the assertions, rather than waiting for a fixed sleep or large data.
  await redis(["EVAL", "for index=1,256 do redis.call('SET',ARGV[1]..index,string.rep('x',1024)) end return 256", "0", fixturePrefix]);
  await redis(["CONFIG", "SET", "rdbcompression", "no"]);
  const originalRewrites = Number((await persistence()).aof_rewrites);
  await redis(["BGREWRITEAOF"]);
  await waitForCondition("completed Redis RDB AOF base rewrite", async () => {
    const info = await persistence();
    return { ready: info.aof_rewrite_in_progress === "0" && info.aof_rewrite_scheduled === "0"
      && info.aof_last_bgrewrite_status === "ok" && Number(info.aof_rewrites) > originalRewrites, info };
  });

  for (const variant of ["strict", "legacy"]) {
    const fixtureDirectory = join(directory, `redis-loading-${variant}`);
    mkdirSync(fixtureDirectory, { recursive: true });
    const overlay = join(fixtureDirectory, "redis.json");
    atomicJson(overlay, { services: { redis: {
      command: [...runtime.configuration.services.redis.command,
        "--key-load-delay", "250000", "--loading-process-events-interval-bytes", "1024"],
      healthcheck: { test: variant === "strict" ? ["CMD", "redis-cli", "-e", "ping"] : ["CMD", "redis-cli", "ping"] },
    } } });
    const fixtureTarget = { ...target, composeFiles: [...target.composeFiles, overlay] };
    const loadingStateDirectory = join(fixtureDirectory, "state");
    const loading = createRuntime(fixtureTarget, { run, stateDirectory: loadingStateDirectory, revision,
      evidence: { commit: revision, disposable_runner: target.project }, log: console.log });
    await loading.inspectTarget();
    const loadingPreparedImages = { revision, images, configFingerprint: configurationFingerprint(loading.configuration) };
    const loadingRuntime = createRuntime(fixtureTarget, { run, stateDirectory: loadingStateDirectory, revision,
      redisReadinessOptions: measure ? { wait: measure.wait, poll: measure.poll } : {},
      readinessOptions: measure ? { wait: measure.wait, poll: measure.poll } : {},
      preparedImages: loadingPreparedImages, evidence: { commit: revision, disposable_runner: target.project }, log: console.log });
    await loadingRuntime.inspectTarget();
    const firstCommand = commandLog.length;
    let deploymentFinished = false;
    let deploymentFailure;
    const deploy = () => executeLifecycle({ recreate: true }, loadingRuntime);
    const deployment = (measure ? measure.detail(`redis_loading_${variant}`, "scenario", deploy) : deploy()).then(() => { deploymentFinished = true; }, error => {
      deploymentFinished = true; deploymentFailure = error;
    });
    let fixtureFailure;
    try {
      await waitForCondition(`${variant} Redis real persisted LOADING reply`, async () => {
        const response = await redis(["PING"], { allowFailure: true });
        const reply = (response.stdout || response.stderr).trim();
        return { ready: reply.startsWith("LOADING"), code: response.code, reply };
      });
      const healthObservation = await waitForCondition(`${variant} Redis healthcheck during LOADING`, async () => {
        const id = (await compose(["ps", "-q", "redis"], { timeout: 5000 })).stdout.trim();
        const container = id ? JSON.parse((await docker(["inspect", id], { timeout: 5000 })).stdout)[0] : null;
        const health = container?.State?.Health;
        const loadingProbe = health?.Log?.find(probe => probe.Output.includes("LOADING"));
        return { ready: Boolean(loadingProbe && (variant === "legacy" ? health.Status === "healthy" : loadingProbe.ExitCode !== 0)),
          status: health?.Status, probe: loadingProbe };
      });
      const stillLoading = await redis(["PING"], { allowFailure: true });
      assert.equal((stillLoading.stdout || stillLoading.stderr).startsWith("LOADING"), true);
      assert.equal(deploymentFinished, false, "deployment remains blocked during persisted loading");
      assert.equal(existsSync(join(loadingStateDirectory, "deployment.json")), false);
      assert.equal(healthObservation.probe.ExitCode, variant === "legacy" ? 0 : 1);
      if (variant === "strict") assert.notEqual(healthObservation.status, "healthy");
      if (variant === "legacy") await waitForCondition("CLI PONG verification after falsely healthy legacy Redis", async () => {
        if (deploymentFinished) throw deploymentFailure ?? new Error("Deployment finished while Redis was still loading");
        const journal = JSON.parse(readFileSync(join(loadingStateDirectory, "operation.json"), "utf8"));
        return { ready: journal.phase === "checking_redis", phase: journal.phase };
      });
      const journal = JSON.parse(readFileSync(join(loadingStateDirectory, "operation.json"), "utf8"));
      assert.equal(journal.phase, "checking_redis");
      assert(!(await loadingRuntime.runningServices()).some(service => [...applicationServices, "postgres-backup"].includes(service)));
      assert(!commandLog.slice(firstCommand).some(call => call.args.includes("scripts/apply_postgres_migrations.py")
        || (call.args.includes("up") && call.args.includes("foreground-worker"))), "no schema or application work before PONG");
    } catch (error) { fixtureFailure = error; }
    finally {
      try { await redis(["CONFIG", "SET", "key-load-delay", "0"]); }
      catch (error) { fixtureFailure = fixtureFailure ? new AggregateError([fixtureFailure, error], "Redis loading assertion and release failed") : error; }
      await deployment;
      if (deploymentFailure) fixtureFailure = fixtureFailure
        ? new AggregateError([fixtureFailure, deploymentFailure], "Redis loading assertion and deployment failed") : deploymentFailure;
      try {
        await loadingRuntime.stopApplications();
        await compose(["up", "-d", "--no-build", "--no-deps", "--force-recreate", "--wait", "--wait-timeout", "30", "redis"], { timeout: 45_000 });
      } catch (error) { fixtureFailure = fixtureFailure ? new AggregateError([fixtureFailure, error], "Redis loading fixture restoration failed") : error; }
    }
    if (fixtureFailure) throw fixtureFailure;
    assert.equal((await redis(["PING"])).stdout.trim(), "PONG");
    const keys = await redis(["EVAL", "for index=1,256 do if redis.call('GET',ARGV[1]..index)~=string.rep('x',1024) then return 0 end end return 1", "0", fixturePrefix]);
    assert.equal(keys.stdout.trim(), "1", "all persisted fixture values survive recreation");
    assert.deepEqual(await readHistory(), expectedHistory, "Redis readiness preserves authoritative PostgreSQL study history");
    assert.equal(JSON.parse(readFileSync(join(loadingStateDirectory, "deployment.json"), "utf8")).schema, expectedSchema);
    console.log(`PASS Tempo CLI waits for persisted Redis loading before migration or application startup (${variant} healthcheck)`);
  }
  await redis(["EVAL", "for index=1,256 do redis.call('DEL',ARGV[1]..index) end return 256", "0", fixturePrefix]);
}
