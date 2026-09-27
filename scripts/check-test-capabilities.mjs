import { spawnSync } from "node:child_process";
import { createServer } from "node:net";

const requestedChecks = process.argv.slice(2);
const supportedChecks = new Set(["--docker", "--loopback"]);
if (requestedChecks.some((check) => !supportedChecks.has(check)))
  throw new Error(`Unknown test capability check: ${requestedChecks.join(" ")}`);
const checkDocker = requestedChecks.length === 0 || requestedChecks.includes("--docker");
const checkLoopback = requestedChecks.length === 0 || requestedChecks.includes("--loopback");
const failures = [];

if (checkDocker) {
  const dockerResult = spawnSync("docker", ["info", "--format", "{{.ServerVersion}}"], {
    encoding: "utf8", timeout: 5_000,
  });
  if (dockerResult.error || dockerResult.status !== 0) {
    const detail = dockerResult.error?.message ?? dockerResult.stderr.trim() ?? "unknown error";
    failures.push(`Docker daemon access failed: ${detail}`);
  }
}

if (checkLoopback) {
  const bindError = await new Promise((resolve) => {
    const server = createServer();
    server.once("error", (error) => resolve(error.message));
    server.listen(0, "127.0.0.1", () => server.close(() => resolve(null)));
  });
  if (bindError) failures.push(`127.0.0.1 loopback bind failed: ${bindError}`);
}

if (failures.length > 0) {
  console.error(`Test capability preflight failed before any tests ran:\n- ${failures.join("\n- ")}`);
  console.error("Ensure Docker is running. In Codex, launch the entire make full command with sandbox_permissions: require_escalated from the start; use the same elevated path for Docker or browser scopes.");
  process.exitCode = 1;
} else {
  console.log("Test capability preflight passed: required Docker and loopback access is available.");
}
