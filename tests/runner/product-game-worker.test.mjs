import test from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

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
