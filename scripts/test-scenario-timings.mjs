import { mkdirSync, renameSync, writeFileSync } from "node:fs";
import { dirname } from "node:path";
import { performance } from "node:perf_hooks";
import { AsyncLocalStorage } from "node:async_hooks";

export function createScenarioTimer(outputPath, metadata, clock = () => performance.now()) {
  const report = { ...metadata, schema_version: 2, timestamp: new Date().toISOString(), stages: {}, details: [] };
  const contexts = new AsyncLocalStorage();
  const origin = clock();
  const seconds = milliseconds => Math.round(milliseconds / 10) / 100;
  const persist = () => {
    mkdirSync(dirname(outputPath), { recursive: true });
    const temporaryPath = `${outputPath}.tmp`;
    writeFileSync(temporaryPath, `${JSON.stringify(report, null, 2)}\n`);
    renameSync(temporaryPath, outputPath);
  };
  function complete(entry, startedAt, failure) {
    entry.duration_seconds = seconds(clock() - startedAt);
    entry.exit_code = failure ? 1 : 0;
    entry.completed = true;
    try { persist(); }
    catch (reportError) {
      throw failure ? new AggregateError([failure, reportError], "PostgreSQL measurement and timing report failed") : reportError;
    }
    if (failure) throw failure;
  }
  function beginDetail(label, category) {
    if (!/^[a-z][a-z0-9_]*$/.test(label) || !["build", "startup", "readiness", "migration", "fixture", "scenario", "restart", "backup", "cleanup", "inspection"].includes(category)) {
      throw new Error("Timing details require fixed labels and known categories");
    }
    const parent = contexts.getStore();
    if (!parent) throw new Error("Timing detail requires an active planned scenario");
    const startedAt = clock();
    const entry = { id: report.details.length, stage: parent.stage, parent_id: parent.detailId ?? null,
      label, category, start_seconds: seconds(startedAt - origin), completed: false };
    report.details.push(entry);
    return { entry, startedAt, context: { stage: parent.stage, detailId: entry.id, counters: entry } };
  }
  const normalizedFailure = error => error instanceof Error ? error : new Error(String(error));
  async function measureScenario(name, action) {
    if (Object.hasOwn(report.stages, name)) throw new Error(`Duplicate timed scenario: ${name}`);
    const startedAt = clock();
    const counters = {};
    let result;
    let failure;
    try { result = await contexts.run({ stage: name, counters }, action); }
    catch (error) { failure = normalizedFailure(error); }
    // Do not persist raw environment values, credentials, or exception payloads.
    report.stages[name] = {
      ...counters, duration_seconds: seconds(clock() - startedAt), exit_code: failure ? 1 : 0,
    };
    try {
      persist();
      console.log(`PostgreSQL scenario ${name}: ${report.stages[name].duration_seconds}s (exit ${report.stages[name].exit_code})`);
    } catch (reportError) {
      failure = failure
        ? new AggregateError([failure, reportError], "PostgreSQL scenario and timing report failed")
        : reportError;
    }
    if (failure) throw failure;
    return result;
  }
  measureScenario.detail = async (label, category, action) => {
    const { entry, startedAt, context } = beginDetail(label, category);
    let result, failure;
    try { result = await contexts.run(context, action); }
    catch (error) { failure = normalizedFailure(error); }
    complete(entry, startedAt, failure);
    return result;
  };
  measureScenario.syncDetail = (label, category, action) => {
    const { entry, startedAt, context } = beginDetail(label, category);
    let result, failure;
    try { result = contexts.run(context, action); }
    catch (error) { failure = normalizedFailure(error); }
    complete(entry, startedAt, failure);
    return result;
  };
  measureScenario.poll = () => {
    const counters = contexts.getStore()?.counters;
    if (counters) counters.poll_count = (counters.poll_count ?? 0) + 1;
  };
  measureScenario.processResult = result => {
    const counters = contexts.getStore()?.counters;
    if (!counters) throw new Error("Process timing requires an active measurement");
    const code = result.code ?? result.status;
    if (Number.isInteger(code)) counters.process_exit_code = code;
    if (["SIGTERM", "SIGKILL", "SIGINT"].includes(result.signal)) counters.process_signal = result.signal;
  };
  measureScenario.wait = async milliseconds => {
    const counters = contexts.getStore()?.counters;
    const startedAt = clock();
    await new Promise(resolve => setTimeout(resolve, milliseconds));
    if (counters) counters.waiting_seconds = (counters.waiting_seconds ?? 0) + (clock() - startedAt) / 1000;
  };
  return measureScenario;
}

// Only fixed operation/script names are recorded, never command arguments or output.
export function postgresCommandTiming(argumentsList) {
  const script = argumentsList.find(argument => /^(?:\/source\/)?scripts\/[a-z_]+\.py$/.test(argument));
  if (script) {
    const label = script.split("/").at(-1).slice(0, -3);
    const category = /migration|upgrade/.test(label) ? "migration"
      : label === "check_postgres_cli_lifecycle" ? "fixture"
        : label === "verify_postgres_backup" ? "backup" : "scenario";
    return { label, category };
  }
  if (argumentsList.includes("build")) return { label: "image_build", category: "build" };
  if (argumentsList.includes("pull")) return { label: "dependency_images", category: "build" };
  if (argumentsList.includes("down")) return { label: "compose_teardown", category: "cleanup" };
  if (argumentsList.includes("stop")) return { label: "compose_shutdown", category: "restart" };
  if (argumentsList.includes("up") || argumentsList.includes("start")) return {
    label: argumentsList.includes("--force-recreate") ? "compose_recreation" : "compose_startup", category: "startup" };
  if (argumentsList.includes("redis-cli")) return { label: "redis_probe", category: "readiness" };
  if (argumentsList.some(argument => argument.includes("pg_dump") || argument.includes("pg_restore"))) return { label: "backup_restore", category: "backup" };
  return { label: "resource_inspection", category: "inspection" };
}
