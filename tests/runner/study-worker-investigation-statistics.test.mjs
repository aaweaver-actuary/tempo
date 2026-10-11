import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, writeFileSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { execFileSync } from 'node:child_process';

test('study_worker_statistics_exclude_contaminated_attempts_warmups_and_probes_without_reducing_paired_counts', () => {
  const evidenceDirectory = mkdtempSync(join(tmpdir(), 'tempo-worker-statistics-'));
  try {
    const rows = [];
    const foregroundSample = (iteration, contended, extra, flags = {}) => ({
      type: 'sample', workload: 'statistics-fixture', scenario: 'foreground', iteration,
      warmup: iteration < 0, contended, durationMs: 100, ...flags,
      requests: [
        ...(contended ? [{ kind: 'repertoireAlternatives', sentAt: 0, computeMs: 1000 }] : []),
        ...['queue', 'workspace', 'resolveTrainingRoute', 'deck'].map(kind => ({
          kind, sentAt: 1, runningAt: 2 + extra, replyAt: 11 + extra, outcome: 'fulfilled',
        })),
      ],
    });
    for (let iteration = 0; iteration < 50; iteration++) {
      rows.push(foregroundSample(iteration, false, 0), foregroundSample(iteration, true, iteration + 1));
      rows.push({ type: 'sample', workload: 'statistics-fixture', scenario: 'pipeline', iteration,
        warmup: false, durationMs: iteration + 1, requests: [], longTasks: [] });
    }
    rows.push(foregroundSample(0, false, 0, { contaminated: true }), foregroundSample(0, true, 10000, { contaminated: true }));
    rows.push(foregroundSample(-1, false, 0), foregroundSample(-1, true, 10000));
    rows.push({ type: 'sample', workload: 'statistics-fixture', scenario: 'pipeline', iteration: 0,
      contaminated: true, durationMs: 10000, requests: [], longTasks: [] });
    writeFileSync(join(evidenceDirectory, 'raw-statistics.jsonl'), rows.map(row => JSON.stringify(row)).join('\n') + '\n');
    writeFileSync(join(evidenceDirectory, 'raw-stopped-probe.jsonl'), JSON.stringify(foregroundSample(0, true, 10000)) + '\n');
    execFileSync(process.execPath, [fileURLToPath(new URL('../../scripts/summarize-study-worker-contention.mjs', import.meta.url)), evidenceDirectory]);
    const report = JSON.parse(readFileSync(join(evidenceDirectory, 'summary.json'), 'utf8'));
    assert.equal(report.results.length, 2);
    const foreground = report.results.find(result => result.scenario === 'foreground');
    for (const task of foreground.tasks) {
      assert.equal(task.pairs.length, 50);
      assert.deepEqual(task.extraTotal, { n: 50, p50: 25, p95: 48, maximum: 50, minimum: 1 });
      assert.ok(task.pairs.every(pair => pair.verifiedOverlap));
    }
    assert.deepEqual(report.results.find(result => result.scenario === 'pipeline').total,
      { n: 50, p50: 25, p95: 48, maximum: 50, minimum: 1 });
  } finally {
    rmSync(evidenceDirectory, { recursive: true, force: true });
  }
});
