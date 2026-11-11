-- Retire automatic feedback and regrouping without altering their source rows.
-- This is an operator-visible disposition snapshot, not a new runtime queue.
-- Apply only in the normal stopped-writer migration window. Preserved rows keep
-- their exact identifiers, payloads, clocks, receipts and previous states.

CREATE TABLE tempo_retired_work (
    source_table text NOT NULL,
    source_key text NOT NULL,
    disposition text NOT NULL CHECK (disposition IN ('paused', 'historical_retained')),
    reason text NOT NULL,
    source_record jsonb NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (source_table, source_key)
);

INSERT INTO tempo_retired_work(source_table, source_key, disposition, reason, source_record)
SELECT 'operation_receipts', operation_id, 'paused',
       'Optional producer or proposal command retired at schema 64; original receipt retained',
       to_jsonb(receipt)
FROM operation_receipts receipt
WHERE command_name IN (
    'product_games.configure', 'product_games.sync', 'product_games.refresh',
    'product_games.retry_feedback', 'game_evidence.product_accept',
    'game_evidence.receive_legacy', 'game_evidence.admit_legacy',
    'games.accounts.update', 'games.sync.enqueue', 'games.exclusion.set',
    'product.repertoire.preview', 'product.repertoire.decide', 'product.repertoire.audit',
    'studies.create', 'studies.update', 'studies.archive', 'studies.unarchive',
    'studies.exercises.suspend', 'studies.exercises.resume', 'studies.exercises.archive',
    'studies.exercises.create', 'studies.exercises.enroll', 'studies.exercises.train_now',
    'studies.exercises.revise', 'studies.links.create', 'studies.import.commit',
    'studies.bundle.import', 'queue.prepare_next'
) AND state NOT IN ('complete', 'failed');

INSERT INTO tempo_retired_work(source_table, source_key, disposition, reason, source_record)
SELECT 'background_tasks', id, 'paused',
       'Automatic Games-to-Study task retired at schema 64; original task retained',
       to_jsonb(task)
FROM background_tasks task
WHERE kind IN ('product_game_refresh', 'game_sync_record', 'game_sync_window',
               'study_game_feedback')
  AND state IN ('queued', 'leased', 'retrying');

INSERT INTO tempo_retired_work(source_table, source_key, disposition, reason, source_record)
SELECT 'product_feedback_jobs', job_id, 'paused',
       'Automatic provider feedback job retired at schema 64; original job retained',
       to_jsonb(job)
FROM product_feedback_jobs job
WHERE status <> 'complete';

INSERT INTO tempo_retired_work(source_table, source_key, disposition, reason, source_record)
SELECT 'product_feedback_deliveries', operation_id, 'paused',
       'Automatic feedback delivery retired at schema 64; original delivery retained',
       to_jsonb(delivery)
FROM product_feedback_deliveries delivery
WHERE state = 'pending';

INSERT INTO tempo_retired_work(source_table, source_key, disposition, reason, source_record)
SELECT 'study_game_feedback', effect_key, 'paused',
       'Accepted feedback effect paused at schema 64; applied grades and original effect retained',
       to_jsonb(effect)
FROM study_game_feedback effect
WHERE status IN ('pending', 'held', 'applied');

INSERT INTO tempo_retired_work(source_table, source_key, disposition, reason, source_record)
SELECT 'study_recombination_presentations', presentation_id, 'historical_retained',
       'Generated presentation and history retained; future automatic joins and splits retired',
       to_jsonb(presentation)
FROM study_recombination_presentations presentation;

INSERT INTO tempo_schema_migrations(version) VALUES(64);
