-- study-priority-v1.0.0 rollback. Stop application writers first and verify a
-- current backup. This removes only migration 49's indexes/version marker;
-- accepted reviews, scheduling, evidence and receipts are retained in place.
BEGIN;
DO $$ BEGIN
  IF (SELECT MAX(version) FROM public.tempo_schema_migrations) <> 49
     OR (SELECT COUNT(*) FROM public.tempo_schema_migrations) <> 49 THEN
    RAISE EXCEPTION 'Rollback requires the complete schema-49 history and no later migrations';
  END IF;
END $$;
DROP INDEX public.idx_operation_retry_due;
DROP INDEX public.idx_operation_lease_due;
DROP INDEX public.idx_operation_queue_due;
DELETE FROM public.tempo_schema_migrations WHERE version=49;
COMMIT;
