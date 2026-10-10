import { spawnSync } from "node:child_process";
import { createServer } from "node:net";
import { pinnedPlaywrightImage } from "./pinned-playwright-image.mjs";

const requestedChecks = process.argv.slice(2);
const supportedChecks = new Set(["--docker", "--loopback", "--workspace-mount"]);
if (requestedChecks.some((check) => !supportedChecks.has(check)))
  throw new Error(`Unknown test capability check: ${requestedChecks.join(" ")}`);
const checkWorkspaceMount = requestedChecks.includes("--workspace-mount");
const checkDocker = requestedChecks.length === 0 || requestedChecks.includes("--docker") || checkWorkspaceMount;
const checkLoopback = requestedChecks.length === 0 || requestedChecks.includes("--loopback");
const failures = [];

if (checkDocker) {
  const dockerResult = spawnSync("docker", ["version", "--format", "{{.Server.Version}}"], {
    encoding: "utf8", timeout: 5_000,
  });
  if (dockerResult.error || dockerResult.status !== 0 || !dockerResult.stdout?.trim()) {
    const detail = dockerResult.error?.message || dockerResult.stderr.trim() || "missing Docker server version";
    failures.push(`Docker daemon access failed: ${detail}`);
  } else if (checkWorkspaceMount) {
    const mountResult = spawnSync("docker", [
      "run", "--platform", "linux/arm64", "--rm",
      "-v", `${process.cwd()}:/workspace:ro`,
      "--entrypoint", "sh", pinnedPlaywrightImage,
      "-c", "test -f /workspace/package-lock.json && test -f /workspace/scripts/test-visual.mjs",
    ], { encoding: "utf8", timeout: 120_000 });
    if (mountResult.error || mountResult.status !== 0) {
      const detail = mountResult.error?.message || mountResult.stderr.trim() || "required project files are not visible inside the container";
      failures.push(`Docker cannot read this checkout through its bind mount: ${detail}. Use a Docker-shared checkout path.`);
    }
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
  if (checkDocker) console.error("Ensure the Docker daemon is running and accessible.");
  console.error("In Codex, launch the entire requested Make target with sandbox_permissions: require_escalated from the start.");
  process.exitCode = 1;
} else {
  const availableCapabilities = [
    checkDocker ? "Docker daemon" : null,
    checkWorkspaceMount ? "Docker checkout bind mount" : null,
    checkLoopback ? "127.0.0.1 loopback bind" : null,
  ].filter(Boolean);
  console.log(`Test capability preflight passed: ${availableCapabilities.join(" and ")} available.`);
}
