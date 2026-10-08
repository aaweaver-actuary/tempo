import { protectRegressionSuite } from "./verification-stages.mjs";
import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { readFileSync, readdirSync, mkdirSync, writeFileSync, appendFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

export const mandatoryLayers = ["frontend", "backend", "build", "postgres", "browser"];
export const allLayers = [...mandatoryLayers, "visual", "quarantine"];
export const inventory = JSON.parse(readFileSync(new URL("./ci-verification-inventory.json", import.meta.url), "utf8"));
export const escapeRegex = text => text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

export function changedPathsFromNameStatus(output) {
  const tokens = output.split("\0");
  if (tokens.at(-1) === "") tokens.pop();
  const paths = [];
  for (let offset = 0; offset < tokens.length;) {
    const status = tokens[offset++];
    if (!/^[ACDMR][0-9]*$/.test(status)) throw new Error(`Uncertain change status: ${status}`);
    const count = /^[RC]/.test(status) ? 2 : 1;
    for (let index = 0; index < count; index++) {
      const path = tokens[offset++];
      if (!path || path.startsWith("/") || path.includes("..")) throw new Error("Invalid comparison path");
      paths.push(path);
    }
  }
  return [...new Set(paths)].sort();
}

export function validateInventory(files, sourceInventory = inventory) {
  const mappedPaths = new Set();
  for (const mapping of sourceInventory.sources) {
    if (!mapping.paths?.length || !mapping.families?.length || new Set(mapping.families).size !== mapping.families.length
      || mapping.families.some(family => family === "pinned" || !Object.hasOwn(sourceInventory.families, family)))
      throw new Error("Invalid source mapping: require unique complete browser families");
    for (const path of mapping.paths) {
      if (typeof path !== "string" || !path || path.startsWith("/") || path.includes("..") || mappedPaths.has(path))
        throw new Error(`Invalid source mapping: duplicate or ambiguous path ${path}`);
      mappedPaths.add(path);
    }
  }
  const classified = Object.values(sourceInventory.families).flat();
  if (new Set(classified).size !== classified.length) throw new Error("A browser spec belongs to multiple inventory families");
  for (const file of files) if (!classified.includes(file.startsWith("tests/browser/") ? file.slice("tests/browser/".length) : file)) throw new Error(`Unclassified browser spec: ${file}. Register its complete family before planning.`);
}

export function collectCases(report) {
  if (report.errors?.length) throw new Error(`Browser collection failed: ${JSON.stringify(report.errors)}`);
  const cases = [];
  function visit(suite, parents = []) {
    const titles = [...parents, suite.title];
    for (const spec of suite.specs ?? []) for (const test of spec.tests ?? []) {
      const titleParts = [test.projectName, ...titles.filter(Boolean), spec.title];
      const collectedTags = spec.tags ?? [];
      const optionalTagPattern = collectedTags.length ? `(?: (?:${collectedTags.map(escapeRegex).join("|")}))*` : "";
      const selectionPattern = `^${titleParts.map(escapeRegex).join(`${optionalTagPattern} `)}${optionalTagPattern}$`;
      cases.push({ grep: selectionPattern, tags: collectedTags, id: `${spec.id}:${test.projectName}`, file: spec.file.replaceAll("\\", "/"), title: spec.title,
        fullTitle: [...titleParts, ...collectedTags].join(" "), project: test.projectName });
    }
    for (const child of suite.suites ?? []) visit(child, titles);
  }
  for (const suite of report.suites ?? []) visit(suite);
  if (!cases.length || new Set(cases.map(item => item.id)).size !== cases.length) throw new Error("Browser inventory is empty or duplicated");
  return cases.sort((left, right) => left.id.localeCompare(right.id));
}

export function verificationPlan({ paths, comparisonAvailable = true, complete = false, files, cases, pinnedCases = [], quarantine = [], sourceInventory = inventory }) {
  validateInventory([...files, ...cases.map(item => item.file), ...pinnedCases.map(item => item.file)], sourceInventory);
  const reasons = [];
  const families = new Set();
  let broad = complete || !comparisonAvailable;
  let visual = broad;
  if (complete) reasons.push("complete verification requested");
  if (!comparisonAvailable) reasons.push("comparison history missing or uncertain");
  for (const path of paths) {
    // Exact reviewed consumer mappings narrow browser families. Core tests still
    // run in full; shared fixtures and unknown executable paths stay conservative.
    const mapping = sourceInventory.sources.find(entry => entry.paths.includes(path));
    const specFamily = Object.entries(sourceInventory.families).find(([, specs]) => path.startsWith("tests/browser/") && specs.includes(path.slice("tests/browser/".length)))?.[0];
    const rendering = /\.(css|scss|svg|png|jpe?g|webp)$/.test(path) || /(?:layout|chessboard|board-|visual|theme|pieces)/i.test(path);
    if (rendering || (path.startsWith("app/") && path.endsWith(".tsx"))) visual = true;
    const ordinaryProse = (path.startsWith("docs/") && path.endsWith(".md")) || /(?:^|\/)README\.md$/.test(path) || sourceInventory.prosePaths?.includes(path);
    const standaloneCoreTest = /^tests\/unit\/[^/]+\.test\.tsx?$/.test(path) || /^backend\/tests\/test_[^/]+\.py$/.test(path);
    if (mapping) { mapping.families.forEach(family => families.add(family)); reasons.push(`${path}: reviewed consumer families; ${mapping.reason}`); }
    else if (specFamily && specFamily !== "pinned") { families.add(specFamily); reasons.push(`${path}: complete ${specFamily} browser family`); }
    else if (specFamily === "pinned") { visual = true; reasons.push(`${path}: pinned rendering verification`); }
    else if (ordinaryProse) reasons.push(`${path}: prose; core and critical verification still required`);
    else if (standaloneCoreTest) reasons.push(`${path}: standalone test; complete core and critical verification still required`);
    else { broad = true; reasons.push(`${path}: shared or unclassified path; broad verification`); }
  }
  if (broad) { Object.keys(sourceInventory.families).filter(family => family !== "pinned").forEach(family => families.add(family)); visual = true; }
  const critical = new Set();
  for (const required of sourceInventory.critical) {
    const matches = cases.filter(item => item.file === required.file && item.title === required.title);
    if (!matches.length) throw new Error(`Missing critical browser coverage: ${required.covers}`);
    matches.forEach(item => critical.add(item.id));
  }
  validateQuarantine(quarantine, cases);
  const quarantined = new Set(quarantine.map(entry => entry.id));
  if ([...critical].some(id => quarantined.has(id))) throw new Error("Critical coverage cannot be quarantined");
  const selectedSpecs = new Set([...families].flatMap(family => sourceInventory.families[family]));
  const replacementCoverage = new Set(quarantine.flatMap(entry => entry.requiredCoverage));
  const collection = cases.map(item => ({ ...item, critical: critical.has(item.id), quarantined: quarantined.has(item.id),
    selected: !quarantined.has(item.id) && (critical.has(item.id) || selectedSpecs.has(item.file) || replacementCoverage.has(item.id)),
    nightly: true, release: true }));
  const selected = collection.filter(item => item.selected);
  // Partial selection binds project/file/title and permits only collected tags
  // at suite/test boundaries. Complete selection runs the unfiltered inventory.
  const browserGrep = selected.map(item => item.grep ?? `^${escapeRegex(item.fullTitle)}$`).join("|");
  const plan = { version: 1, scope: broad ? "complete" : "targeted", comparisonAvailable, paths, reasons,
    families: [...families].sort(), jobs: Object.fromEntries(allLayers.map(layer => [layer,
      { required: layer !== "quarantine" && (layer !== "visual" || visual), applicable: layer === "quarantine" ? quarantine.length > 0 : layer !== "visual" || visual,
        reason: layer === "visual" && !visual ? "No rendering change or broad coverage trigger" : layer === "quarantine" ? "Confirmed harness defects only" : "Required verification" }])),
    browserGrep, collection, quarantine,
    pinnedCollection: pinnedCases.map(item => ({ ...item, selected: visual, nightly: true, release: true })) };
  return { ...plan, hash: createHash("sha256").update(JSON.stringify(plan)).digest("hex") };
}

export function validateQuarantine(entries, cases, now = new Date()) {
  for (const entry of entries) {
    if (!entry.confirmedHarnessDefect || !/^https:\/\/github\.com\/.+\/issues\/\d+$/.test(entry.issue ?? "") || !entry.owner || !entry.requiredCoverage?.length
      || !Number.isFinite(Date.parse(entry.expires)) || Date.parse(entry.expires) <= now.getTime()
      || !cases.some(item => item.id === entry.id)) throw new Error("Quarantine requires a confirmed harness defect, repair issue, owner, expiry and required coverage");
    for (const id of entry.requiredCoverage) if (id === entry.id || !cases.some(item => item.id === id) || entries.some(other => other.id === id)) throw new Error("Quarantine replacement coverage must remain required");
  }
}

function git(args) {
  const result = spawnSync("git", args, { encoding: "utf8" });
  if (result.status !== 0) throw new Error(result.stderr || "Comparison history unavailable");
  return result.stdout.trimEnd();
}

export function createPlan({ base, complete = false } = {}) {
  protectRegressionSuite("tests"); protectRegressionSuite("backend/tests");
  let paths = [], comparisonAvailable = false;
  if (base) {
    try { paths = changedPathsFromNameStatus(git(["diff", "--name-status", "-z", "--find-renames", git(["merge-base", base, "HEAD"]), "HEAD"])); comparisonAvailable = true; }
    catch { /* fail conservatively into full coverage */ }
  }
  const files = readdirSync("tests/browser").filter(file => file.endsWith(".spec.ts"));
  const result = spawnSync("npx", ["playwright", "test", "--list", "--reporter=json"], {
    encoding: "utf8", env: { ...process.env, TEMPO_DOCKER_URL: "http://127.0.0.1:1" }, maxBuffer: 20 * 1024 * 1024,
  });
  if (result.status !== 0) throw new Error(`Browser collection failed: ${result.stderr}\n${result.stdout}`);
  const cases = collectCases(JSON.parse(result.stdout));
  const pinned = spawnSync("npx", ["playwright", "test", "--config", "playwright.visual.config.ts", "--list", "--reporter=json"], {
    encoding: "utf8", env: { ...process.env, TEMPO_VISUAL_RUNNER: "linux-pinned" }, maxBuffer: 20 * 1024 * 1024,
  });
  if (pinned.status !== 0) throw new Error(`Pinned collection failed: ${pinned.stderr}\n${pinned.stdout}`);
  const pinnedCases = collectCases(JSON.parse(pinned.stdout));
  const quarantine = JSON.parse(readFileSync("scripts/ci-quarantine.json", "utf8"));
  return { ...verificationPlan({ paths, comparisonAvailable, complete, files, cases, pinnedCases, quarantine }), commit: git(["rev-parse", "HEAD"]) };
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const args = process.argv.slice(2);
  if (args.some(argument => !["--complete", "--base"].includes(argument) && argument !== args[args.indexOf("--base") + 1])) throw new Error("Unknown planning argument");
  const plan = createPlan({ base: args.includes("--base") ? args[args.indexOf("--base") + 1] : undefined, complete: args.includes("--complete") });
  mkdirSync("test-results/ci", { recursive: true });
  writeFileSync("test-results/ci/plan.json", JSON.stringify(plan, null, 2));
  writeFileSync("test-results/ci/collection.json", JSON.stringify({ browser: plan.collection, pinned: plan.pinnedCollection, core: "All frontend/backend unit tests, engine smoke and Rust/build checks run on every PR" }, null, 2));
  console.log(`${plan.scope}: ${plan.collection.filter(item => item.selected).length}/${plan.collection.length} regular browser cases; ${plan.collection.filter(item => item.critical).length} global critical; visual=${plan.jobs.visual.applicable}`);
  if (process.env.GITHUB_OUTPUT) appendFileSync(process.env.GITHUB_OUTPUT, `visual=${plan.jobs.visual.applicable}\nquarantine=${plan.jobs.quarantine.applicable}\nscope=${plan.scope}\n`);
}
