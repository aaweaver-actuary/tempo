import { protectRegressionSuite } from "./verification-stages.mjs";
import { postgresTestStages } from "./postgres-test-plan.mjs";
import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { readFileSync, readdirSync, mkdirSync, writeFileSync, appendFileSync } from "node:fs";
import { validateMigrationInventory } from "./check-migration-inventory.mjs";
import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";

export const mandatoryLayers = ["frontend", "backend", "build", "postgres", "browser"];
export const allLayers = [...mandatoryLayers, "lifecycle", "visual", "quarantine"];
export const inventory = JSON.parse(readFileSync(new URL("./ci-verification-inventory.json", import.meta.url), "utf8"));
export const escapeRegex = text => text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

// Tier controls qualification; scope controls coverage. They are independent.
export function verificationContext(eventName, event = {}, complete = false, commit = "", ref = null) {
  const pullRequest = eventName === "pull_request" ? event.pull_request : null;
  if (eventName === "pull_request" && (!pullRequest || typeof pullRequest.draft !== "boolean" || !pullRequest.head?.sha || !pullRequest.base?.sha)) throw new Error("Missing PR verification context");
  const requested = complete || pullRequest?.labels?.some(label => label.name === "ci:full");
  return { tier: pullRequest?.draft && !requested ? "development" : "qualification",
    draft: pullRequest?.draft ?? false, head: pullRequest?.head.sha ?? commit,
    base: pullRequest?.base.sha ?? null, commit, ref, event: eventName,
    complete: !!requested || !pullRequest };
}

export function planHash(plan) {
  const { hash: omittedHash, ...content } = plan;
  void omittedHash;
  return createHash("sha256").update(JSON.stringify(content)).digest("hex");
}

function developmentSelection(paths, comparisonAvailable, sourceInventory) {
  const selected = new Set(), frontend = new Set(), backend = new Set();
  let allFrontend = false, allBackend = false, broadBrowser = false;
  const expand = () => { allLayers.filter(layer => layer !== "quarantine").forEach(layer => selected.add(layer)); allFrontend = allBackend = broadBrowser = true; };
  if (!comparisonAvailable) expand();
  for (const path of paths) {
    const prose = (path.startsWith("docs/") && path.endsWith(".md")) || /(?:^|\/)README\.md$/.test(path) || sourceInventory.prosePaths?.includes(path);
    if (prose) continue;
    if (/^tests\/unit\/.*\.test\.tsx?$/.test(path)) {
      selected.add("frontend"); if (existsSync(path)) frontend.add(path); else allFrontend = true;
    } else if (/^backend\/tests\/(?:.*\/)?(?:test_[^/]+|[^/]+_test)\.py$/.test(path)) {
      selected.add("backend"); if (existsSync(path)) backend.add(path); else allBackend = true;
    } else if (sourceInventory.development?.harnessPaths.includes(path)) {
      selected.add("frontend"); sourceInventory.development.harnessTests.forEach(file => frontend.add(file));
    } else if (sourceInventory.development?.postgresFixturePaths.includes(path)) {
      ["frontend", "postgres"].forEach(layer => selected.add(layer));
      sourceInventory.development.harnessTests.forEach(file => frontend.add(file));
    } else if (path.startsWith("app/")) {
      if (!sourceInventory.sources.some(mapping => mapping.paths.includes(path))) broadBrowser = true;
      allFrontend = true; ["frontend", "build", "browser"].forEach(layer => selected.add(layer));
      if (/\.(tsx|css|scss|svg|png|jpe?g|webp)$/.test(path)) selected.add("visual");
    } else if (path.startsWith("backend/app/") || path.startsWith("backend/migrations/")) {
      if (!path.startsWith("backend/app/services/") || /(?:database|background_runtime|database_executor|redis_admission_gate)\.py$/.test(path)) broadBrowser = true;
      allBackend = true; ["backend", "postgres", "browser"].forEach(layer => selected.add(layer));
      if (lifecycleApplicability({ paths: [path], comparisonAvailable: true, complete: false, sourceInventory }).applicable) selected.add("lifecycle");
    } else if (/^tests\/browser\/.*\.spec\.ts$/.test(path)) {
      selected.add(/(?:visual|performance)\.spec\.ts$/.test(path) ? "visual" : "browser");
    } else expand();
  }
  return { selected, broadBrowser, core: { frontend: allFrontend ? "all" : [...frontend].sort(), backend: allBackend ? "all" : [...backend].sort() } };
}

export function lifecycleApplicability({ paths, comparisonAvailable, complete, sourceInventory = inventory }) {
  const lifecycleRules = sourceInventory.lifecycle;
  const selectionReasons = [];
  if (complete) selectionReasons.push("Complete verification requested");
  if (!comparisonAvailable) selectionReasons.push("Comparison history missing or uncertain");
  if (!lifecycleRules) selectionReasons.push("Lifecycle source classification unavailable");
  else for (const path of paths) {
    const sensitive = lifecycleRules.sensitivePaths.includes(path)
      || lifecycleRules.sensitivePrefixes.some(prefix => path.startsWith(prefix))
      || lifecycleRules.sensitivePatterns.some(pattern => new RegExp(pattern).test(path));
    const ordinary = lifecycleRules.ordinaryPaths.includes(path)
      || lifecycleRules.ordinaryPrefixes.some(prefix => path.startsWith(prefix))
      || lifecycleRules.ordinaryPatterns.some(pattern => new RegExp(pattern).test(path));
    if (sensitive) selectionReasons.push(`${path}: lifecycle-sensitive source`);
    else if (!ordinary) selectionReasons.push(`${path}: unclassified infrastructure; lifecycle required`);
  }
  return { applicable: selectionReasons.length > 0,
    reason: selectionReasons.length ? selectionReasons.join("; ") : "No lifecycle-sensitive change or complete verification request" };
}

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

export function verificationPlan({ paths, comparisonAvailable = true, complete = false, files, cases, pinnedCases = [], quarantine = [], sourceInventory = inventory, tier = "qualification", draft = false, head = null, base = null, commit = null, event = "local", ref = null }) {
  if (!["development", "qualification"].includes(tier)) throw new Error("Unknown verification tier");
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
    const standaloneCoreTest = /^tests\/unit\/.*\.test\.tsx?$/.test(path) || /^backend\/tests\/(?:.*\/)?(?:test_[^/]+|[^/]+_test)\.py$/.test(path);
    if (mapping) { mapping.families.forEach(family => families.add(family)); reasons.push(`${path}: reviewed consumer families; ${mapping.reason}`); }
    else if (specFamily && specFamily !== "pinned") { families.add(specFamily); reasons.push(`${path}: complete ${specFamily} browser family`); }
    else if (specFamily === "pinned") { visual = true; reasons.push(`${path}: pinned rendering verification`); }
    else if (ordinaryProse) reasons.push(`${path}: reviewed prose; qualification retains core and critical coverage`);
    else if (standaloneCoreTest) reasons.push(`${path}: standalone regression; direct development execution and full qualification core`);
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
  // Partial selection binds project/file/title and permits only collected tags
  // at suite/test boundaries. Complete selection runs the unfiltered inventory.
  const lifecycle = lifecycleApplicability({ paths, comparisonAvailable, complete, sourceInventory });
  const jobs = Object.fromEntries(allLayers.map(layer => [layer,
      { required: layer !== "quarantine" && (layer !== "visual" || visual), applicable: layer === "quarantine" ? quarantine.length > 0 : layer !== "visual" || visual,
        reason: layer === "visual" && !visual ? "No rendering change or broad coverage trigger" : layer === "quarantine" ? "Confirmed harness defects only" : "Required verification" }]));
  jobs.lifecycle = { ...lifecycle, required: lifecycle.applicable };
  for (const [layer, mode] of [["postgres", "durability"], ["lifecycle", "lifecycle"]]) {
    jobs[layer].mode = mode;
    jobs[layer].planned_stages = postgresTestStages({ mode });
  }
  const regressionFiles = { frontend: [], backend: [] };
  for (const path of paths.filter(path => existsSync(path))) {
    if (/^tests\/unit\/.*\.test\.tsx?$/.test(path)) regressionFiles.frontend.push(path);
    else if (/^tests\/unit\/.*\.test\./.test(path)) throw new Error(`Regression file is outside the regular Vitest collection: ${path}`);
    else if (/^backend\/tests\/(?:.*\/)?(?:test_[^/]+|[^/]+_test)\.py$/.test(path)) regressionFiles.backend.push(path);
    else if (/^tests\/runner\/.*\.test\.mjs$/.test(path)) {
      const owners = sourceInventory.development?.runnerOwners?.[path];
      if (!owners?.length || owners.some(owner => !existsSync(owner))) throw new Error(`Register regular-suite ownership for runner regression: ${path}`);
      regressionFiles.frontend.push(...owners);
    }
  }
  for (const layer of ["frontend", "backend"]) regressionFiles[layer] = [...new Set(regressionFiles[layer])].sort();
  let core = { frontend: "all", backend: "all" };
  if (tier === "development") {
    const development = developmentSelection(paths, comparisonAvailable, sourceInventory);
    core = development.core;
    if (!development.broadBrowser) {
      families.clear();
      for (const path of paths) {
        sourceInventory.sources.find(mapping => mapping.paths.includes(path))?.families.forEach(family => families.add(family));
        const family = Object.entries(sourceInventory.families).find(([, specs]) => path.startsWith("tests/browser/") && specs.includes(path.slice("tests/browser/".length)))?.[0];
        if (family && family !== "pinned") families.add(family);
      }
    }
    const developmentSpecs = new Set([...families].flatMap(family => sourceInventory.families[family]));
    collection.forEach(item => { item.selected = !item.quarantined && (item.critical || developmentSpecs.has(item.file) || replacementCoverage.has(item.id)); });
    for (const layer of allLayers) {
      const applicable = layer === "quarantine" ? development.selected.has("browser") && quarantine.length > 0 : development.selected.has(layer);
      jobs[layer] = { ...jobs[layer], applicable, required: applicable && layer !== "quarantine",
        reason: applicable ? "Affected development boundary" : "Development: runtime layer explicitly inapplicable" };
    }
    collection.forEach(item => { if (!jobs.browser.applicable) item.selected = false; });
  }
  const plan = { version: 2, tier, draft, head, base, commit, event, ref, core, regressionFiles, scope: (tier === "development" ? mandatoryLayers.every(layer => jobs[layer].required) && jobs.lifecycle.required && jobs.visual.required && collection.every(item => item.selected || item.quarantined) && core.frontend === "all" && core.backend === "all" : broad && lifecycle.applicable) ? "complete" : "targeted", comparisonAvailable, paths, reasons,
    families: [...families].sort(), jobs,
    browserGrep: collection.filter(item => item.selected).map(item => item.grep ?? `^${escapeRegex(item.fullTitle)}$`).join("|"), collection, quarantine,
    pinnedCollection: pinnedCases.map(item => ({ ...item, selected: jobs.visual.applicable, nightly: true, release: true })) };
  return { ...plan, hash: planHash(plan) };
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

export function createPlan({ base, complete = false, context = {} } = {}) {
  validateMigrationInventory(readdirSync("backend/migrations"), readFileSync("backend/app/schema_version.py", "utf8"));
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
  const commit = git(["rev-parse", "HEAD"]);
  if (context.commit && context.commit !== commit) throw new Error("Planner checkout differs from captured integration revision");
  return verificationPlan({ paths, comparisonAvailable, complete, files, cases, pinnedCases, quarantine, ...context, commit });
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const args = process.argv.slice(2);
  if (args.some(argument => !["--complete", "--base"].includes(argument) && argument !== args[args.indexOf("--base") + 1])) throw new Error("Unknown planning argument");
  const context = process.env.GITHUB_EVENT_PATH ? verificationContext(process.env.GITHUB_EVENT_NAME,
    JSON.parse(readFileSync(process.env.GITHUB_EVENT_PATH, "utf8")), args.includes("--complete"), process.env.GITHUB_SHA, process.env.GITHUB_REF) : {};
  const plan = createPlan({ context, base: context.base ?? (args.includes("--base") ? args[args.indexOf("--base") + 1] : undefined), complete: context.complete ?? args.includes("--complete") });
  mkdirSync("test-results/ci", { recursive: true });
  writeFileSync("test-results/ci/plan.json", JSON.stringify(plan, null, 2));
  writeFileSync("test-results/ci/collection.json", JSON.stringify({ browser: plan.collection, pinned: plan.pinnedCollection, core: plan.core }, null, 2));
  console.log(`${plan.scope}: ${plan.collection.filter(item => item.selected).length}/${plan.collection.length} regular browser cases; ${plan.collection.filter(item => item.critical).length} global critical; visual=${plan.jobs.visual.applicable}; lifecycle=${plan.jobs.lifecycle.applicable}`);
  if (process.env.GITHUB_OUTPUT) appendFileSync(process.env.GITHUB_OUTPUT, `commit=${plan.commit}\ntier=${plan.tier}\n${["frontend", "backend", "build", "postgres", "browser"].map(layer => `${layer}=${plan.jobs[layer].applicable}`).join("\n")}\nvisual=${plan.jobs.visual.applicable}\nlifecycle=${plan.jobs.lifecycle.applicable}\nquarantine=${plan.jobs.quarantine.applicable}\nscope=${plan.scope}\n`);
}
