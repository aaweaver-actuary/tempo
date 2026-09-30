import { appendFileSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join, relative } from "node:path";

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
  const unitProfile = readArtifact(directory, "unit-files-full.json");
  const commit = stageReport?.commit ?? null;
  const environment = stageReport?.environment ?? null;
  const runStartedAt = Date.parse(stageReport?.timestamp ?? "");
  const belongsToRun = (artifact) => Boolean(commit && artifact?.commit === commit &&
    Number.isFinite(runStartedAt) && Date.parse(artifact.timestamp ?? "") >= runStartedAt);
  const metrics = [];
  const staleArtifacts = [];
  for (const [name, stage] of Object.entries(stageReport?.stages ?? {})) {
    if (typeof stage.duration_seconds !== "number") continue;
    metrics.push({ id: `stage.${name}`, label: name, value: stage.duration_seconds,
      unit: "s", comparisonKey: JSON.stringify(environment), exitCode: stage.exit_code });
  }
  const dockerArtifact = readArtifact(directory, "docker-stages.json");
  if (dockerArtifact) {
    if (!belongsToRun(dockerArtifact)) staleArtifacts.push("docker-stages.json");
    else for (const [stageName, label] of [
      ["container_start", "Docker container start"],
      ["browser", "Docker browser matrix"],
      ["durability", "Docker durability"],
      ["container_stop", "Docker container stop"],
    ]) {
      const stage = dockerArtifact.stages?.[stageName];
      if (typeof stage?.duration_seconds !== "number") continue;
      metrics.push({ id: `docker.${stageName}`, label, value: stage.duration_seconds,
        unit: "s", comparisonKey: JSON.stringify(environment), exitCode: stage.exit_code });
    }
  }
  const browserArtifacts = [
    ["held-drag-chromium.json", (artifact) => {
      for (const summary of artifact.summaries ?? []) {
        const comparisonKey = JSON.stringify([artifact.environment, artifact.fixture, summary.workload, summary.enabled, summary.count]);
        const suffix = `${summary.workload}.${summary.enabled ? "capture-on" : "capture-off"}`;
        for (const [metric, label, value, unit] of [
          ["gap", "frame gap p95", summary.frameGapMs?.p95, "ms"],
          ["displacement", "DOM displacement p95", summary.displacementCssPx?.p95, "CSS px"],
          ["interruptions", "interrupted holds", summary.interruptionCount, "count"],
        ]) if (typeof value === "number") metrics.push({
          id: `held-drag.${suffix}.${metric}`, label: `Held drag ${suffix} ${label}`,
          value, unit, comparisonKey,
        });
      }
    }],
    ["browser-chromium.json", (artifact) => {
      const fixtureKey = JSON.stringify([environment, artifact.browser, artifact.fixture]);
      if (Array.isArray(artifact.longTasks))
        metrics.push({ id: "browser.long-tasks", label: "Long tasks",
          value: artifact.longTasks.length, unit: "count", comparisonKey: fixtureKey });
      for (const [view, summary] of Object.entries(artifact.viewSwitchSummary ?? {}))
        metrics.push({ id: `view.${view}.p95`, label: `${view} warm switch p95`,
          value: summary.p95, unit: "ms", comparisonKey: fixtureKey });
      for (const [id, label, summary] of [
        ["board-ready.p95", "Board ready p95", artifact.boardReadySummary],
        ["move-to-paint.p95", "Builder after-move to rAF p95 (legacy move-to-paint)", artifact.moveToPaintSummary],
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
      const comparisonKey = JSON.stringify([environment, artifact.fixture]);
      if (typeof artifact.roundtripSummary?.p95 === "number")
        metrics.push({ id: "builder-query.p95", label: "Builder worker query p95",
          value: artifact.roundtripSummary.p95, unit: "ms",
          comparisonKey });
      if (typeof artifact.paintSummary?.p95 === "number")
        metrics.push({ id: "builder-query-paint.p95", label: "Builder query to paint p95",
          value: artifact.paintSummary.p95, unit: "ms", comparisonKey });
    }],
  ];
  for (const [filename, addMetrics] of browserArtifacts) {
    const artifact = readArtifact(directory, filename);
    if (!artifact) continue;
    if (!belongsToRun(artifact)) {
      staleArtifacts.push(filename);
      continue;
    }
    addMetrics(artifact);
  }
  const slowestUnitFiles = stageReport?.stages?.unit &&
    Array.isArray(unitProfile?.testResults) &&
    Number.isFinite(runStartedAt) && unitProfile.startTime >= runStartedAt
    ? unitProfile.testResults.map((result) => ({
      file: relative(process.cwd(), result.name),
      durationMilliseconds: Math.max(0, result.endTime - result.startTime),
      status: result.status,
    })).sort((left, right) => right.durationMilliseconds - left.durationMilliseconds).slice(0, 10)
    : [];
  return { commit, timestamp: stageReport?.timestamp ?? null, environment,
    metrics, staleArtifacts, slowestUnitFiles };
}

const current = collectMetrics(outputDirectory);
const baseline = baselineDirectory ? collectMetrics(baselineDirectory) : null;
const baselineMetrics = new Map(baseline?.metrics.map((metric) => [metric.id, metric]) ?? []);
const regressions = [];
const metricRows = current.metrics.map((metric) => {
  const previous = baselineMetrics.get(metric.id);
  const sameFixture = previous && previous.comparisonKey === metric.comparisonKey &&
    previous.unit === metric.unit;
  const percentage = sameFixture && previous.value > 0
    ? (metric.value / previous.value - 1) * 100 : null;
  const newLongTasks = metric.id === "browser.long-tasks" && sameFixture &&
    previous.value === 0 && metric.value > 0;
  if (percentage !== null && percentage > 25)
    regressions.push({ metric: metric.label, percentage, current: metric.value,
      baseline: previous.value, unit: metric.unit });
  if (newLongTasks)
    regressions.push({ metric: metric.label, percentage: null, current: metric.value,
      baseline: 0, unit: metric.unit });
  const change = newLongTasks ? "new long tasks" :
    metric.id === "browser.long-tasks" && sameFixture && metric.value === 0 && previous.value === 0
      ? "no change" : percentage === null ? (baseline ? "not comparable" : "—") :
        `${Math.abs(percentage).toFixed(1)}% ${percentage >= 0 ? "slower" : "faster"}`;
  return `| ${metric.label} | ${metric.value.toFixed(metric.unit === "s" ? 2 : 1)} ${metric.unit} | ` +
    `${metric.exitCode === undefined ? "—" : metric.exitCode === 0 ? "passed" : `failed (${metric.exitCode})`} | ` +
    `${sameFixture ? `${previous.value.toFixed(metric.unit === "s" ? 2 : 1)} ${metric.unit}` : "—"} | ${change} |`;
});

const lines = [
  "# Tempo performance summary",
  "",
  `Commit: \`${current.commit ?? "unavailable"}\` · Run: ${current.timestamp ?? "unavailable"}`,
  "",
  "| Metric | Current | Status | Baseline | Change |",
  "| --- | ---: | --- | ---: | --- |",
  ...metricRows,
  "",
];
if (!baseline) lines.push("No baseline supplied; no regression verdict.", "");
else if (regressions.length)
  lines.push(`**${regressions.length} performance signal(s) warrant review.** A comparable metric exceeded 25% degradation or new long tasks appeared. Check raw samples and runner variance before setting a blocking budget.`, "");
else lines.push("No comparable metric exceeded 25% degradation and no new long tasks appeared.", "");
if (current.staleArtifacts.length)
  lines.push(`Stale performance artifacts excluded: ${current.staleArtifacts.join(", ")}.`, "");
if (current.slowestUnitFiles.length) {
  lines.push("## Slowest unit files", "", "File wall times may overlap across workers.", "",
    "| File | Wall time | Status |", "| --- | ---: | --- |");
  for (const result of current.slowestUnitFiles)
    lines.push(`| \`${result.file}\` | ${(result.durationMilliseconds / 1000).toFixed(2)} s | ${result.status} |`);
  lines.push("");
}
const markdown = `${lines.join("\n")}\n`;
mkdirSync(outputDirectory, { recursive: true });
writeFileSync(join(outputDirectory, "performance-summary.md"), markdown);
writeFileSync(join(outputDirectory, "performance-summary.json"), JSON.stringify({
  schema_version: 1, ...current, baselineCommit: baseline?.commit ?? null, regressions,
}, null, 2) + "\n");
if (process.env.GITHUB_STEP_SUMMARY) appendFileSync(process.env.GITHUB_STEP_SUMMARY, markdown);
console.log(join(outputDirectory, "performance-summary.md"));
