import { createHash } from "node:crypto";
import ts from "typescript";
import { readFileSync } from "node:fs";
export const pilotLedger = JSON.parse(readFileSync(new URL("../tests/fixtures/controlled-browser/pilot.json", import.meta.url), "utf8"));
const printer = ts.createPrinter({ removeComments: true });
function proofSequence(file, title) {
  const source = ts.createSourceFile(file, readFileSync(file, "utf8"), ts.ScriptTarget.Latest, true);
  let body;
  const viewportWidths = [];
  function collectViewports(node) {
    if (ts.isForOfStatement(node) && ts.isArrayLiteralExpression(node.expression)) {
      for (const value of node.expression.elements) if (ts.isObjectLiteralExpression(value))
        for (const property of value.properties) if (ts.isPropertyAssignment(property) && property.name.getText(source) === "width") viewportWidths.push(Number(property.initializer.getText(source)));
    }
    ts.forEachChild(node, collectViewports);
  }
  collectViewports(source);
  function find(node) {
    if (ts.isCallExpression(node) && node.expression.getText(source) === "test") {
      const named = node.arguments[0];
      const matched = ts.isStringLiteral(named) ? named.text === title :
        ts.isTemplateExpression(named) && `${named.head.text}390` === title;
      if (matched) body = node.arguments[1].body;
    }
    ts.forEachChild(node, find);
  }
  find(source);
  if (!body) throw new Error(`Missing original/controlled case: ${file}: ${title}`);
  const assertions = [], nativeActions = [];
  function visit(node) {
    if (ts.isCallExpression(node)) {
      const expression = node.expression.getText(source);
      const normalized = printer.printNode(ts.EmitHint.Expression, node, source);
      if (expression.startsWith("expect(") || expression.startsWith("expect.poll(") || expression === "noPageOverflow") assertions.push(normalized);
      if (/\.(click|fill|press|focus|reload|evaluate|boundingBox|setViewportSize|scrollIntoViewIfNeeded|down|up|move)$/.test(expression) || expression === "move") nativeActions.push(normalized);
    }
    ts.forEachChild(node, visit);
  }
  visit(body);
  return { assertions, nativeActions, viewportWidths: title.endsWith("board 390") ? viewportWidths.filter(width => width === 390) : [] };
}
export function pilotProofDetails() {
  return pilotLedger.cases.map(entry => ({ title: entry.originalTitle, ...proofSequence(entry.originalFile, entry.originalTitle) }));
}
export function verifyPilotSource() {
  const identities = [];
  for (const entry of pilotLedger.cases) {
    const original = proofSequence(entry.originalFile, entry.originalTitle);
    const controlled = proofSequence(entry.newFile, entry.newTitle);
    if (!original.assertions.length || (entry.parameters?.viewport === 390 && (original.viewportWidths.length !== 1 || controlled.viewportWidths.length !== 1)) || JSON.stringify(original) !== JSON.stringify(controlled))
      throw new Error(`Assertion/native interaction drift: ${entry.originalTitle}`);
    const fingerprint = createHash("sha256").update(JSON.stringify(original)).digest("hex");
    if (entry.assertion_action_sha256 && entry.assertion_action_sha256 !== fingerprint) throw new Error(`Recorded original proof changed: ${entry.originalTitle}`);
    for (const project of entry.projects) identities.push(`${project}|${entry.newFile}|${entry.newTitle}`);
  }
  if (identities.length !== 9 || new Set(identities).size !== 9) throw new Error("Pilot requires exactly nine unique identities");
  return identities;
}
export const visualTitles = ["Controlled visual board 1280", "Controlled visual phone 390", "Controlled visual tablet menu 768"];
export const studyIntentTitles = ["issue221 native two-tab writes preserve both orders and competing claims", "issue221 native intent survives true reload with original payload and settlement", "issue221 legacy migration requires closed-tab acknowledgement and retains sources", "issue221 denied native storage cannot report saved success", "issue221 native quota failure aborts the intent and permits recovery", "issue186 committed review next-load retry and reload never resubmit the grade", "issue219 wrong tactic survives true reload and guided completion stays Again", "issue219 specialized lost response reload recovers after the issued head advances", "issue219 two tabs retain competing outcomes on one issued head", "issue219 defense recognition survives reload with its immutable assessment"];
studyIntentTitles.push(
  "pr285 rejected native answer permits corrected identity after true reload",
  "pr285 native storage retry reopens after a later transaction failure",
  "pr285 native legacy reimport recovers sequence two without rollback",
  "pr285 native practice reset excludes both records across tabs and reload",
  "pr285 native teaching notification carries committed seen and first clean facts",
  "pr285 recovery ownership replacement queue load survives cancelled receipt recovery",
);
export function supportedIdentities() {
  return [...verifyPilotSource(), ...visualTitles.map(title => `chromium|tests/controlled-browser/visual.spec.ts|${title}`), ...studyIntentTitles.map(title => `chromium|tests/controlled-browser/study-intents.spec.ts|${title}`)];
}
export function reportIdentities(report, executed = false) {
  if (!report || !Array.isArray(report.suites) || !Array.isArray(report.errors) || report.errors.length) throw new Error("Invalid Playwright report/errors");
  const actual = [];
  function visit(suite) {
    if (!suite || !Array.isArray(suite.suites ?? []) || !Array.isArray(suite.specs ?? [])) throw new Error("Invalid Playwright suite");
    for (const spec of suite.specs ?? []) {
      if (typeof spec.title !== "string" || typeof spec.file !== "string" || !Array.isArray(spec.tests) || !spec.tests.length) throw new Error("Invalid Playwright spec");
      const file = spec.file.replaceAll("\\", "/").replace(/^tests\/controlled-browser\//, "");
      if (!["interaction.spec.ts", "phone.spec.ts", "visual.spec.ts", "study-intents.spec.ts"].includes(file)) throw new Error("Unexpected controlled file: " + spec.file);
      for (const test of spec.tests) {
        if (typeof test.projectName !== "string") throw new Error("Missing Playwright project identity");
        actual.push(`${test.projectName}|tests/controlled-browser/${file}|${spec.title}`);
        if (executed && (test.expectedStatus !== "passed" || !Array.isArray(test.results) || test.results.length !== 1 || test.results[0].status !== "passed" || (test.results[0].retry ?? 0) !== 0))
          throw new Error(`Non-passing/skipped/retried controlled identity: ${spec.title} (${test.projectName})`);
      }
    }
    for (const child of suite.suites ?? []) visit(child);
  }
  for (const suite of report.suites) visit(suite);
  if (!actual.length || new Set(actual).size !== actual.length) throw new Error("Missing or duplicate controlled identities");
  if (executed && (!report.stats || report.stats.expected !== actual.length || ["unexpected", "flaky", "skipped"].some(name => report.stats[name] !== 0))) throw new Error("Invalid Playwright result accounting");
  return actual.sort();
}
export function verifyControlledResults(report, expected) {
  const actual = reportIdentities(report, true);
  if (JSON.stringify(actual) !== JSON.stringify([...expected].sort())) throw new Error("Missing or unexpected controlled identities");
  return actual;
}
export function verifyPilotResults(report) { return verifyControlledResults(report, verifyPilotSource()); }
export function parseSelection(args) {
  const files = [], projects = [];
  let list = false, grep;
  for (let index = 0; index < args.length; index++) {
    const argument = args[index];
    if (argument === "--list") { if (list) throw new Error("Duplicate --list"); list = true; }
    else if (argument === "--grep" || argument.startsWith("--grep=")) {
      if (grep !== undefined) throw new Error("Duplicate --grep");
      grep = argument.includes("=") ? argument.slice(7) : args[++index];
      if (typeof grep !== "string" || !grep.length) throw new Error("Missing --grep value");
      new RegExp(grep);
    } else if (argument === "--project" || argument.startsWith("--project=")) {
      const project = argument.includes("=") ? argument.slice(10) : args[++index];
      if (!["chromium", "firefox", "webkit"].includes(project) || projects.includes(project)) throw new Error("Invalid/duplicate project selection");
      projects.push(project);
    } else {
      const file = argument.replace(/^tests\/controlled-browser\//, "");
      if (!["interaction.spec.ts", "phone.spec.ts", "visual.spec.ts", "study-intents.spec.ts"].includes(file) || files.includes(file)) throw new Error("Missing/unknown/duplicate controlled file: " + argument);
      files.push(file);
    }
  }
  const expected = supportedIdentities().filter(identity => {
    const [project, path, title] = identity.split("|");
    return (!projects.length || projects.includes(project)) && (!files.length || files.includes(path.split("/").at(-1))) &&
      (grep === undefined || new RegExp(grep).test(`${project} ${path.split("/").at(-1)} ${title}`));
  });
  if (!expected.length) throw new Error("Zero selected controlled cases");
  const playwrightArgs = [...files, ...projects.map(project => `--project=${project}`), ...(grep === undefined ? [] : ["--grep", grep])];
  return { list, expected: expected.sort(), playwrightArgs };
}
