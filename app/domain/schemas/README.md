# Domain schemas

Zod schemas define the browser-side boundary for controlled API responses,
worker messages, packaged puzzles, engine candidates, and persisted records.
Strict controlled envelopes and tolerant third-party records are deliberate;
keep validation behavior aligned with Python transport models and parity tests.
