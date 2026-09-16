import { spawnSync } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
const directory = mkdtempSync(join(tmpdir(), "tempo-docker-tests-"));
const env = { ...process.env, TEMPO_TEST_DATA: directory };
const composeArgs = ["compose", "-p", "tempo-regressions", "-f", "docker-compose.test.yml"];
function run(command, args, extra = {}) {
  const result = spawnSync(command, args, { stdio: "inherit", env: { ...env, ...extra } });
  if (result.error || result.status !== 0) throw new Error(`${command} ${args.join(" ")} failed`);
}
let exitCode = 0;
try {
  run("docker", ["info", "--format", "{{.ServerVersion}}"]);
  run("docker", [...composeArgs, "up", "--build", "-d"]);
  let ready = false;
  for (let attempt = 0; attempt < 60; attempt++) {
    try { if ((await fetch("http://127.0.0.1:4180/api/health")).ok) { ready = true; break; } } catch { /* startup */ }
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  if (!ready) throw new Error("Docker Tempo did not become healthy");
  run("npx", ["playwright", "test"], { TEMPO_DOCKER_URL: "http://127.0.0.1:4180" });
} catch (error) { console.error(error.message); exitCode = 1; }
finally {
  spawnSync("docker", [...composeArgs, "down"], { stdio: "inherit", env });
  rmSync(directory, { recursive: true });
}
process.exit(exitCode);
