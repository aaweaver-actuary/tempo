import { appendFileSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";

const argumentsToParse = process.argv.slice(2);
const optionValue = (name) => {
  const optionIndex = argumentsToParse.indexOf(name);
  if (optionIndex < 0) return undefined;
  if (!argumentsToParse[optionIndex + 1]) throw new Error(`${name} needs a directory`);
  return argumentsToParse[optionIndex + 1];
};
if (argumentsToParse.some((argument, index) => index % 2 === 0 &&
    !["--directory", "--baseline"].includes(argument)))
  throw new Error(`Unknown performance summary option: ${argumentsToParse.join(" ")}`);
const outputDirectory = optionValue("--directory") ??
  process.env.TEMPO_TEST_TIMING_DIR ?? "test-results/performance";
const baselineDirectory = optionValue("--baseline");

function readArtifact(directory, filename) {
  try {
    return JSON.parse(readFileSync(join(directory, filename), "utf8"));
  } catch (error) {
    if (error?.code === "ENOENT") return null;
    throw error;
  }
}

function collectMetrics(directory) {
  const stageReport = readArtifact(directory, "test-stages-full.json");
  const commit = stageReport?.commit ?? null;
  const environment = stageReport?.environment ?? null;
  const metrics = [];
  const staleArtifacts = [];
  for (const [name, stage] of Object.entries(stageReport?.stages ?? {})) {
    if (typeof stage.duration_seconds !== "number") continue;
    metrics.push({ id: `stage.${name}`, label: name, value: stage.duration_seconds,
      unit: "s", comparisonKey: JSON.stringify(environment), exitCode: stage.exit_code });
  }
  const browserArtifacts = [
    ["browser-chromium.json", (artifact) => {
      const fixtureKey = JSON.stringify([environment, artifact.browser, artifact.fixture]);
      for (const [view, summary] of Object.entries(artifact.viewSwitchSummary ?? {}))
        metrics.push({ id: `view.${view}.p95`, label: `${view} warm switch p95`,
          value: summary.p95, unit: "ms", comparisonKey: fixtureKey });
      for (const [id, label, summary] of [
        ["board-ready.p95", "Board ready p95", artifact.boardReadySummary],
        ["move-to-paint.p95", "Builder move to paint p95", artifact.moveToPaintSummary],
      ]) if (typeof summary?.p95 === "number")
        metrics.push({ id, label, value: summary.p95, unit: "ms", comparisonKey: fixtureKey });
    }],
    ["training-card-chromium.json", (artifact) => {
      if (typeof artifact.summary?.p95 === "number")
        metrics.push({ id: "training-card.p95", label: "Training card advance p95",
          value: artifact.summary.p95, unit: "ms",
          comparisonKey: JSON.stringify([environment, artifact.fixture]) });
    }],
    ["builder-similarity-chromium.json", (artifact) => {
      if (typeof artifact.roundtripSummary?.p95 === "number")
        metrics.push({ id: "builder-query.p95", label: "Builder worker query p95",
          value: artifact.roundtripSummary.p95, unit: "ms",
          comparisonKey: JSON.stringify([environment, artifact.fixture]) });
    }],
  ];
  for (const [filename, addMetrics] of browserArtifacts) {
    const artifact = readArtifact(directory, filename);
    if (!artifact) continue;
    if (!commit || artifact.commit !== commit) {
      staleArtifacts.push(filename);
      continue;
    }
    addMetrics(artifact);
  }
  return { commit, timestamp: stageReport?.timestamp ?? null, environment,
    metrics, staleArtifacts };
}

const current = collectMetrics(outputDirectory);
const baseline = baselineDirectory ? collectMetrics(baselineDirectory) : null;
const baselineMetrics = new Map(baseline?.metrics.map((metric) => [metric.id, metric]) ?? []);
const regressions = [];
const metricRows = current.metrics.map((metric) => {
  const previous = baselineMetrics.get(metric.id);
  const comparable = previous && previous.comparisonKey === metric.comparisonKey &&
    previous.unit === metric.unit && previous.value > 0;
  const percentage = comparable ? (metric.value / previous.value - 1) * 100 : null;
  if (percentage !== null && percentage > 25)
    regressions.push({ metric: metric.label, percentage, current: metric.value,
      baseline: previous.value, unit: metric.unit });
  const change = percentage === null ? (baseline ? "not comparable" : "—") :
    `${Math.abs(percentage).toFixed(1)}% ${percentage >= 0 ? "slower" : "faster"}`;
  return `| ${metric.label} | ${metric.value.toFixed(metric.unit === "s" ? 2 : 1)} ${metric.unit} | ` +
    `${previous && comparable ? `${previous.value.toFixed(metric.unit === "s" ? 2 : 1)} ${metric.unit}` : "—"} | ${change} |`;
});

const lines = [
  "# Tempo performance summary",
  "",
  `Commit: \`${current.commit ?? "unavailable"}\` · Run: ${current.timestamp ?? "unavailable"}`,
  "",
  "| Metric | Current | Baseline | Change |",
  "| --- | ---: | ---: | --- |",
  ...metricRows,
  "",
];
if (!baseline) lines.push("No baseline supplied; no regression verdict.", "");
else if (regressions.length)
  lines.push(`**${regressions.length} metric(s) exceeded 25% degradation.** Review raw samples and runner variance before setting a blocking budget.`, "");
else lines.push("No comparable metric exceeded 25% degradation.", "");
if (current.staleArtifacts.length)
  lines.push(`Stale browser artifacts excluded: ${current.staleArtifacts.join(", ")}.`, "");
const markdown = `${lines.join("\n")}\n`;
mkdirSync(outputDirectory, { recursive: true });
writeFileSync(join(outputDirectory, "performance-summary.md"), markdown);
writeFileSync(join(outputDirectory, "performance-summary.json"), JSON.stringify({
  schema_version: 1, ...current, baselineCommit: baseline?.commit ?? null, regressions,
}, null, 2) + "\n");
if (process.env.GITHUB_STEP_SUMMARY) appendFileSync(process.env.GITHUB_STEP_SUMMARY, markdown);
console.log(join(outputDirectory, "performance-summary.md"));
