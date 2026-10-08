import test from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { mkdtempSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { findConflictArtifacts, scanTrackedConflicts } from "../../scripts/check-merge-conflicts.mjs";

function conflict(left, right, width = 7, base = null) {
  return [`${"<".repeat(width)} HEAD`, left,
    ...(base === null ? [] : [`${"|".repeat(width)} base`, base]),
    "=".repeat(width), right, `${">".repeat(width)} main`].join("\n");
}

test("committed conflict guard detects Python and JSON integration conflicts with line diagnostics", () => {
  for (const [path, left, right] of [
    ["schema_version.py", "POSTGRES_SCHEMA_VERSION = 35", "POSTGRES_SCHEMA_VERSION = 36"],
    ["inventory.json", '"families": []', '"families": ["repertoire"]'],
  ]) {
    assert.deepEqual(findConflictArtifacts(`header\n${conflict(left, right)}\n`, path),
      [{ path, startLine: 2, endLine: 6, reason: "Unresolved merge conflict" }]);
  }
});

test("committed conflict guard catches diff3 variable-width CRLF and malformed boundaries", () => {
  assert.equal(findConflictArtifacts(conflict("left", "right", 10, "ancestor").replaceAll("\n", "\r\n")).length, 1);
  for (const marker of [`${"<".repeat(7)} HEAD`, `${">".repeat(7)} main`, `${"|".repeat(7)} base`])
    assert.equal(findConflictArtifacts(marker).length, 1);
  assert.equal(findConflictArtifacts(["<".repeat(7), "left", ">".repeat(7)].join("\n")).length, 1);
});

test("committed conflict guard permits separators and quoted documentation or test strings", () => {
  const examples = ["=".repeat(70), `> ${"<".repeat(7)} HEAD`,
    `const marker = "${"<".repeat(7)} HEAD";`, `\`${">".repeat(7)} main\``, "<".repeat(7)];
  for (const example of examples) assert.deepEqual(findConflictArtifacts(example), []);
});

test("committed conflict guard exemptions bind one exact block and tracked path", () => {
  const example = conflict("intentional left", "intentional right");
  const exemptions = [{ path: "fixture.txt", sha256: createHash("sha256").update(example).digest("hex") }];
  assert.deepEqual(findConflictArtifacts(example, "fixture.txt", exemptions), []);
  assert.equal(findConflictArtifacts(example, "source.py", exemptions).length, 1);
  assert.equal(findConflictArtifacts(example + "\n" + conflict("unexpected", "conflict"), "fixture.txt", exemptions).length, 1);
  assert.equal(findConflictArtifacts(example.replace("intentional right", "changed"), "fixture.txt", exemptions).length, 1);
});

test("committed conflict guard scans tracked text only and never follows symlinks", () => {
  const directory = mkdtempSync(join(tmpdir(), "tempo-conflict-guard-"));
  try {
    function git(...arguments_) {
      const result = spawnSync("git", arguments_, { cwd: directory, encoding: "utf8" });
      assert.equal(result.status, 0, result.stderr);
    }
    git("init", "--quiet");
    writeFileSync(join(directory, "tracked source.py"), conflict("left", "right"));
    writeFileSync(join(directory, "untracked.txt"), conflict("left", "right"));
    writeFileSync(join(directory, "binary.wasm"), Buffer.from(`\0${conflict("left", "right")}`));
    symlinkSync("untracked.txt", join(directory, "linked.txt"));
    git("add", "tracked source.py", "binary.wasm", "linked.txt");
    assert.deepEqual(scanTrackedConflicts(directory).map(finding => finding.path), ["tracked source.py"]);
  } finally { rmSync(directory, { recursive: true, force: true }); }
});
