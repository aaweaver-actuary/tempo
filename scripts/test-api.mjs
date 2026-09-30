import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { resolve } from "node:path";
import { spawn } from "node:child_process";
import { resolvePython } from "./resolve-python.mjs";
const directory = mkdtempSync(join(tmpdir(), "tempo-browser-tests-"));
const python = resolvePython();
const apiPort = process.env.TEMPO_BROWSER_API_PORT ?? "8001";
const databasePath = join(directory, "tempo.db");
const { createIsolatedTestEnvironment } = await import("./test-environment.mjs");
const environment = createIsolatedTestEnvironment(process.env, {
  PYTHONPATH: "backend",
  TEMPO_TEST_INSTANCE: "disposable",
  TEMPO_DB_PATH: databasePath,
});
if (environment.TEMPO_DATABASE_READ_URL || environment.TEMPO_DATABASE_WRITE_URL
  || resolve(environment.TEMPO_DB_PATH) !== resolve(databasePath)) {
  rmSync(directory, { recursive: true, force: true });
  throw new Error("Refusing to start the local browser API without its isolated SQLite database");
}
const api = spawn(python, ["-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", apiPort], { stdio: "inherit", env: environment });
const cleanup = () => { api.kill("SIGTERM"); };
process.on("SIGINT", cleanup);
process.on("SIGTERM", cleanup);
api.on("error", (error) => {
  console.error(`Could not start the isolated SQLite browser API: ${error.message}`);
  rmSync(directory, { recursive: true, force: true });
  process.exit(1);
});
api.on("exit", (code) => {
  if (resolve(environment.TEMPO_DB_PATH) !== resolve(directory, "tempo.db")) {
    console.error("Refusing to clean up a database outside this browser test run");
    process.exit(1);
  }
  rmSync(directory, { recursive: true, force: true });
  process.exit(code ?? 0);
});
