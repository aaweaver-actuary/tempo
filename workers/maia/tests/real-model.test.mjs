import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createModel, inspectModel } from '../model.mjs';
import { positions, jobFor } from '../fixtures/jobs.mjs';

test('real bundled model preserves complete legal universes, both orientations, all promotions, en passant, castling, two ratings, and conditional subsets', { timeout: 180000 }, async () => {
  const identity = await inspectModel();
  assert(identity);
  const model = await createModel();
  try {
    for (const position of positions) {
      const job = jobFor(position);
      const predictions = await model.infer(job);
      assert.deepEqual(predictions.map(item => item.move).sort(), [...job.legal_moves].sort(), position.name);
      assert(predictions.every(item => Number.isFinite(item.probability) && item.probability >= 0), position.name);
      assert(Math.abs(predictions.reduce((sum, item) => sum + item.probability, 0) - 1) <= 1e-9, position.name);
    }
    const job = jobFor(positions[0]);
    assert.deepEqual(await model.infer(job), await model.infer(job), 'same runtime inference is deterministic');
    await assert.rejects(model.infer({ ...job, rating: 1150 }), error => error.code === 'unsupported_rating');
    await assert.rejects(model.infer({ ...job, fen: '7k/6Q1/5K2/8/8/8/8/8 b - - 0 1' }), error => error.code === 'terminal_position');
  } finally { await model.dispose(); }
});
