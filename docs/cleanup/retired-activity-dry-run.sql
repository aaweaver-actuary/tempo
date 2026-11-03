-- Read-only census for the legacy PostgreSQL schema at source aa7d0dfe.
-- Run only after verifying database identity, schema and authorization. Results
-- are observations, not a deletion allowlist. Stop on missing schema/timeout.
BEGIN READ ONLY;
SET LOCAL statement_timeout = '3s';

-- Actual enforced foreign keys involving the candidate tables.
SELECT conrelid::regclass AS referencing_table, conname,
       confrelid::regclass AS referenced_table, pg_get_constraintdef(oid) AS definition
FROM pg_constraint
WHERE contype = 'f'
  AND (conrelid IN ('public.background_activity'::regclass, 'public.background_tasks'::regclass)
       OR confrelid IN ('public.background_activity'::regclass, 'public.background_tasks'::regclass))
ORDER BY 1, 2;

-- Age bounds are text timestamps in this schema; no age alone proves eligibility.
SELECT source, paused, promoted, COUNT(*) AS rows,
       MIN(updated_at) AS oldest_update, MAX(updated_at) AS newest_update
FROM public.background_activity
GROUP BY source, paused, promoted ORDER BY source, paused, promoted;

-- Source/job associations are logical, not FKs. Unknown sources remain unknown.
SELECT a.source, a.paused, a.promoted,
       CASE a.source
         WHEN 'durable' THEN t.id IS NOT NULL
         WHEN 'sync' THEN s.id IS NOT NULL
         WHEN 'derivation' THEN d.game_id IS NOT NULL
         WHEN 'integrity' THEN i.repertoire_id IS NOT NULL
         WHEN 'coverage' THEN c.id IS NOT NULL
         WHEN 'game_analysis' THEN g.game_id IS NOT NULL
         WHEN 'threat_analysis' THEN h.id IS NOT NULL
         ELSE NULL
       END AS mapped_job_exists,
       COUNT(*) AS rows
FROM public.background_activity a
LEFT JOIN public.background_tasks t ON a.source = 'durable' AND t.id = a.work_id
LEFT JOIN public.game_sync_jobs s ON a.source = 'sync' AND s.id = a.work_id
LEFT JOIN public.game_derivation_jobs d ON a.source = 'derivation' AND d.game_id = a.work_id
LEFT JOIN public.repertoire_integrity_jobs i ON a.source = 'integrity' AND i.repertoire_id = a.work_id
LEFT JOIN public.repertoire_coverage_runs c ON a.source = 'coverage' AND c.id = a.work_id
LEFT JOIN public.game_analysis_jobs g ON a.source = 'game_analysis' AND g.game_id = a.work_id
LEFT JOIN public.threat_analysis_requests h ON a.source = 'threat_analysis' AND h.id = a.work_id
GROUP BY 1, 2, 3, 4 ORDER BY 1, 2, 3, 4;

-- Marker is a conservative text presence test, not proof of valid JSON or ownership.
SELECT kind, state, (position('"origin_import_operation_id"' in payload_json) > 0) AS import_marker_present,
       COUNT(*) AS rows, MIN(created_at) AS oldest_created, MAX(updated_at) AS newest_update
FROM public.background_tasks GROUP BY 1, 2, 3 ORDER BY 1, 2, 3;

-- No row-level receipt association is assumed; inspect recovery population first.
SELECT command_name, state, COUNT(*) AS rows, MIN(created_at) AS oldest_created,
       MAX(updated_at) AS newest_update
FROM public.operation_receipts
WHERE command_name IN ('activity.control', 'activity.progress', 'activity.task.retry', 'discovery.accept')
GROUP BY command_name, state ORDER BY command_name, state;

-- Bounded exact-key preview for the historically observed retired source only.
-- These are candidates for investigation, not permission to remove any row.
SELECT source, work_id, paused, promoted, updated_at
FROM public.background_activity WHERE source = 'priority'
ORDER BY work_id LIMIT 100;
ROLLBACK;
