import test from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

test("Games profile remains opt-in and exposes its aggregate allocation conflict", () => {
  const resolved = spawnSync("docker", ["compose", "-f", "docker-compose.yml", "config", "--no-interpolate", "--format", "json"], {
    encoding: "utf8", timeout: 10_000,
  });
  assert.equal(resolved.status, 0, resolved.stderr);
  const compose = JSON.parse(resolved.stdout);
  const policy = JSON.parse(readFileSync(new URL("../../config/resource-budgets.json", import.meta.url), "utf8"));
  const games = compose.services["game-feedback-worker"];
  assert.deepEqual(games.profiles, ["games-feedback"]);
  assert.equal(games.pids_limit, 96);
  const mib = /^(\d+)m$/i.exec(String(games.mem_limit));
  const memory = mib ? Number(mib[1]) : Number(games.mem_limit) / 1024 ** 2;
  assert(Number.isFinite(memory) && memory > 0 && Number(games.cpus) > 0);
  const baseline = Object.values(policy.optionalServices).reduce((sum, item) => ({
    cpus: sum.cpus + item.cpus, memory: sum.memory + item.memoryMiB,
  }), { cpus: 0, memory: 0 });
  assert(Number(games.cpus) + baseline.cpus > 1);
  assert(memory + baseline.memory > 2048);
  const withoutMaia = {
    cpus: baseline.cpus - policy.optionalServices["maia-worker"].cpus + Number(games.cpus),
    memory: baseline.memory - policy.optionalServices["maia-worker"].memoryMiB + memory,
  };
  assert(withoutMaia.cpus <= 1 && withoutMaia.memory <= 2048);
  // The default reader API receives no writer DSN through this opt-in profile.
  assert.equal(compose.services.api.environment.TEMPO_DATABASE_WRITE_URL, undefined);
});

test("stopped and killed optional HTTP worker preserve native ordinary Study issuance/save", { timeout: 60_000 }, () => {
  const evidence = mkdtempSync(join(tmpdir(), "tempo-491-worker-"));
  try {
    const report = join(evidence, "result.xml");
    const result = spawnSync(process.env.TEMPO_PYTHON ?? "python3", ["-m", "pytest",
      "backend/tests/test_product_game_sync_integration.py", "-q", "--rootdir=.",
      "-k", "native_optional_worker_http_stop", `--junitxml=${report}`], {
      encoding: "utf8", timeout: 55_000, maxBuffer: 4 * 1024 * 1024,
    });
    assert.equal(result.error, undefined, String(result.error));
    assert.equal(result.status, 0, result.stdout + result.stderr);
    const xml = readFileSync(report, "utf8");
    assert.match(xml, /tests="2"/);
    assert.match(xml, /failures="0"/);
    assert.match(xml, /skipped="0"/);
  } finally {
    rmSync(evidence, { recursive: true, force: true });
  }
});
