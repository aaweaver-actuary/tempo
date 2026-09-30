import { mkdirSync, renameSync, writeFileSync } from "node:fs";
import { dirname } from "node:path";
import { performance } from "node:perf_hooks";

export function createScenarioTimer(outputPath, metadata, clock = () => performance.now()) {
  const report = { ...metadata, schema_version: 1, timestamp: new Date().toISOString(), stages: {} };
  return async function measureScenario(name, action) {
    if (Object.hasOwn(report.stages, name)) throw new Error(`Duplicate timed scenario: ${name}`);
    const startedAt = clock();
    let result;
    let failure;
    try { result = await action(); }
    catch (error) { failure = error instanceof Error ? error : new Error(String(error)); }
    // Do not persist raw environment values, credentials, or exception payloads.
    report.stages[name] = {
      duration_seconds: Math.round((clock() - startedAt) / 10) / 100,
      exit_code: failure ? 1 : 0,
    };
    try {
      mkdirSync(dirname(outputPath), { recursive: true });
      const temporaryPath = `${outputPath}.tmp`;
      writeFileSync(temporaryPath, `${JSON.stringify(report, null, 2)}\n`);
      renameSync(temporaryPath, outputPath);
      console.log(`PostgreSQL scenario ${name}: ${report.stages[name].duration_seconds}s (exit ${report.stages[name].exit_code})`);
    } catch (reportError) {
      failure = failure
        ? new AggregateError([failure, reportError], "PostgreSQL scenario and timing report failed")
        : reportError;
    }
    if (failure) throw failure;
    return result;
  };
}
