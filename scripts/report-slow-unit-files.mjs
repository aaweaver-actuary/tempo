import { readFileSync } from "node:fs";
import { join, relative } from "node:path";

const tier = process.argv[2] ?? "full";
const count = Number(process.argv[3] ?? "10");
if (!Number.isInteger(count) || count < 1) throw new Error("Count must be a positive integer");
const outputDirectory = process.env.TEMPO_TEST_TIMING_DIR ?? "test-results/performance";
const profilePath = join(outputDirectory, `unit-files-${tier}.json`);
const profile = JSON.parse(readFileSync(profilePath, "utf8"));
const slowestFiles = profile.testResults
  .map((result) => ({
    file: relative(process.cwd(), result.name),
    durationMilliseconds: Math.max(0, result.endTime - result.startTime),
    status: result.status,
  }))
  .sort((left, right) => right.durationMilliseconds - left.durationMilliseconds)
  .slice(0, count);
console.log(`Slowest unit files from ${profilePath} (file wall time; workers may overlap):`);
for (const result of slowestFiles) {
  console.log(`${(result.durationMilliseconds / 1000).toFixed(2)}s  ${result.status.padEnd(6)}  ${result.file}`);
}
