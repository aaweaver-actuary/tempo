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
      const normalized = printer.printNode(ts.EmitHint.Expression, node, source).replace(/\s+/g, " ");
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
export function verifyPilotResults(report) {
  const expected = verifyPilotSource();
  const actual = [];
  function visit(suite) {
    for (const spec of suite.specs ?? []) for (const test of spec.tests ?? []) {
      actual.push(`${test.projectName}|tests/controlled-browser/${spec.file.split(/[\\/]/).at(-1)}|${spec.title}`);
      if (test.expectedStatus !== "passed" || test.results.length !== 1 || test.results[0].status !== "passed")
        throw new Error(`Non-passing/skipped/retried pilot identity: ${spec.title} (${test.projectName})`);
    }
    for (const child of suite.suites ?? []) visit(child);
  }
  for (const suite of report.suites ?? []) visit(suite);
  if (report.errors?.length || new Set(actual).size !== actual.length || JSON.stringify(actual.sort()) !== JSON.stringify(expected.sort()))
    throw new Error("Missing, duplicate or unexpected controlled identities");
  return actual;
}
