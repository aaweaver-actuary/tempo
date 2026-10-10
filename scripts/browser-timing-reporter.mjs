import { writeFileSync } from "node:fs";

function unionDuration(spans) {
  let duration = 0, previousEnd = -Infinity;
  for (const { start, end } of [...spans].sort((left, right) => left.start - right.start || left.end - right.end)) {
    duration += Math.max(0, end - Math.max(start, previousEnd));
    previousEnd = Math.max(previousEnd, end);
  }
  return duration;
}

// Hooks contain waits; these are overlapping diagnostic spans, never additive stages.
export function summarizeBrowserSpans(spans) {
  const fixtures = spans.filter(span => span.kind === "fixture_hook");
  const waits = spans.filter(span => span.kind === "poll_wait");
  const fixtureHookMs = unionDuration(fixtures), pollWaitMs = unionDuration(waits);
  const observedMs = unionDuration(spans);
  return { fixtureHookMs, pollWaitMs, overlapMs: fixtureHookMs + pollWaitMs - observedMs, observedMs };
}

export default class BrowserTimingReporter {
  tests = [];
  active = new Map();
  onTestBegin(test) { this.active.set(test.id, []); }
  onStepEnd(test, result, step) {
    const kind = ["fixture", "hook"].includes(step.category) ? "fixture_hook"
      : /poll|waitFor|toBeVisible|toBeHidden|toHave|toPass/i.test(step.title) ? "poll_wait" : null;
    if (kind) this.active.get(test.id)?.push({ kind, title: step.title,
      start: step.startTime.getTime(), end: step.startTime.getTime() + step.duration });
  }
  onTestEnd(test, result) {
    const spans = this.active.get(test.id) ?? [];
    this.tests.push({ id: test.id, project: test.parent.project()?.name, file: test.location.file,
      title: test.titlePath(), status: result.status, retry: result.retry, durationMs: result.duration,
      ...summarizeBrowserSpans(spans), spans });
    this.active.delete(test.id);
  }
  onEnd() {
    writeFileSync(`${process.env.TEMPO_CI_REPORT}.spans.json`, JSON.stringify({ version: 1,
      accounting: "Per-test unions of overlapping hook/fixture and named polling/wait assertion spans; nested in test duration. Uninstrumented work remains unclassified.",
      tests: this.tests }, null, 2));
  }
}
