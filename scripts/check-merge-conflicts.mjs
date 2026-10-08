import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { lstatSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

// Deliberate complete examples require the exact tracked path and block digest.
// No file-wide exemptions: another conflict in that file must still fail.
const intentionalExamples = [];

export function findConflictArtifacts(content, path = "", examples = intentionalExamples) {
  const lines = content.split(/\r?\n/);
  const findings = [];
  let opening = null;
  function report(start, end, reason) {
    const block = lines.slice(start, end + 1).join("\n");
    const digest = createHash("sha256").update(block).digest("hex");
    if (!examples.some(example => example.path === path && example.sha256 === digest))
      findings.push({ path, startLine: start + 1, endLine: end + 1, reason });
  }
  for (let index = 0; index < lines.length; index++) {
    const marker = /^[\t ]*(<{7,}|\|{7,}|={7,}|>{7,})(?:[\t ]+(.*))?[\t ]*$/.exec(lines[index]);
    if (!marker) continue;
    const symbol = marker[1][0];
    const label = marker[2]?.trim();
    if (symbol === "<") {
      if (opening) report(opening.index, index - 1, "Unclosed conflict opening");
      opening = { index, labeled: Boolean(label), separator: false };
    } else if (symbol === "=" && !label && opening) {
      opening.separator = true;
    } else if (symbol === ">") {
      if (opening) {
        report(opening.index, index, opening.separator ? "Unresolved merge conflict" : "Malformed conflict block");
        opening = null;
      } else if (label) report(index, index, "Orphaned conflict closing");
    } else if (symbol === "|" && label && !opening) {
      report(index, index, "Orphaned diff3 base marker");
    }
  }
  if (opening?.labeled || opening?.separator)
    report(opening.index, lines.length - 1, "Unclosed conflict opening");
  return findings;
}

export function scanTrackedConflicts(directory = process.cwd()) {
  const tracked = spawnSync("git", ["ls-files", "--stage", "-z"], { cwd: directory, encoding: "utf8" });
  if (tracked.error || tracked.status !== 0)
    throw new Error(tracked.error?.message || tracked.stderr || "Cannot enumerate tracked files");
  const findings = [];
  for (const entry of tracked.stdout.split("\0").filter(Boolean)) {
    const separator = entry.indexOf("\t");
    const mode = entry.slice(0, 6);
    const path = entry.slice(separator + 1);
    if (mode === "120000" || mode === "160000") continue;
    const absolutePath = join(directory, path);
    let metadata;
    try { metadata = lstatSync(absolutePath); }
    catch (error) { if (error.code === "ENOENT") continue; throw error; }
    if (!metadata.isFile()) continue;
    const bytes = readFileSync(absolutePath);
    if (bytes.includes(0)) continue;
    findings.push(...findConflictArtifacts(bytes.toString("utf8"), path));
  }
  return findings;
}

export function assertNoTrackedConflicts(directory = process.cwd()) {
  const findings = scanTrackedConflicts(directory);
  if (findings.length) throw new Error(findings.map(finding =>
    `${finding.path}:${finding.startLine}-${finding.endLine}: ${finding.reason}`).join("\n"));
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  try {
    assertNoTrackedConflicts();
    console.log("No unresolved merge-conflict artifacts in tracked text files.");
  } catch (error) {
    console.error(error.message);
    process.exitCode = 1;
  }
}
