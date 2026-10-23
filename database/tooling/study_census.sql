-- Metadata-only PostgreSQL census for a single explicitly selected database.
-- Estimated rows and statistics are diagnostic signals, not usage or deletion proof.
BEGIN READ ONLY;
SET LOCAL statement_timeout = '10s';
SET LOCAL lock_timeout = '1s';

-- Server, database, connected role, and active schema identity; no connection details.
SELECT pg_catalog.current_database() AS database_name,
       current_user AS connected_role,
       pg_catalog.current_schema() AS active_schema,
       setting AS server_version
FROM pg_catalog.pg_settings
WHERE name = 'server_version';

-- Non-system schema names visible in the catalogs.
SELECT n.nspname AS schema_name
FROM pg_catalog.pg_namespace AS n
WHERE n.nspname <> 'information_schema'
  AND n.nspname NOT LIKE 'pg\_%' ESCAPE '\'
ORDER BY n.nspname;

-- Relation sizes and reltuples estimates for ordinary, partitioned, and materialized tables.
-- reltuples is an estimate; negative values mean unknown and are returned as NULL.
SELECT n.nspname AS schema_name,
       c.relname AS relation_name,
       CASE c.relkind WHEN 'r' THEN 'table'
                      WHEN 'p' THEN 'partitioned table'
                      WHEN 'm' THEN 'materialized view' END AS relation_kind,
       pg_catalog.pg_get_userbyid(c.relowner) AS owner_name,
       CASE WHEN c.reltuples >= 0 THEN c.reltuples ELSE NULL END AS estimated_rows,
       pg_catalog.pg_relation_size(c.oid) AS relation_bytes,
       pg_catalog.pg_total_relation_size(c.oid) AS total_bytes
FROM pg_catalog.pg_class AS c
JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
WHERE c.relkind IN ('r', 'p', 'm')
  AND n.nspname <> 'information_schema'
  AND n.nspname NOT LIKE 'pg\_%' ESCAPE '\'
ORDER BY n.nspname, c.relname;

-- Index metadata and sizes for the same non-system relations.
SELECT table_schema.nspname AS table_schema,
       table_rel.relname AS table_name,
       index_schema.nspname AS index_schema,
       index_rel.relname AS index_name,
       pg_catalog.pg_get_userbyid(index_rel.relowner) AS index_owner,
       idx.indisprimary AS is_primary,
       idx.indisunique AS is_unique,
       idx.indisvalid AS is_valid,
       pg_catalog.pg_relation_size(index_rel.oid) AS index_bytes
FROM pg_catalog.pg_index AS idx
JOIN pg_catalog.pg_class AS table_rel ON table_rel.oid = idx.indrelid
JOIN pg_catalog.pg_namespace AS table_schema ON table_schema.oid = table_rel.relnamespace
JOIN pg_catalog.pg_class AS index_rel ON index_rel.oid = idx.indexrelid
JOIN pg_catalog.pg_namespace AS index_schema ON index_schema.oid = index_rel.relnamespace
WHERE table_rel.relkind IN ('r', 'p', 'm')
  AND table_schema.nspname <> 'information_schema'
  AND table_schema.nspname NOT LIKE 'pg\_%' ESCAPE '\'
ORDER BY table_schema.nspname, table_rel.relname, index_rel.relname;

-- Declared foreign-key relationships; these are catalog constraints, not inferred identities.
SELECT source_ns.nspname AS source_schema,
       source_rel.relname AS source_table,
       fk.conname AS constraint_name,
       target_ns.nspname AS target_schema,
       target_rel.relname AS target_table,
       fk.convalidated AS is_validated,
       fk.condeferrable AS is_deferrable,
       fk.condeferred AS initially_deferred
FROM pg_catalog.pg_constraint AS fk
JOIN pg_catalog.pg_class AS source_rel ON source_rel.oid = fk.conrelid
JOIN pg_catalog.pg_namespace AS source_ns ON source_ns.oid = source_rel.relnamespace
JOIN pg_catalog.pg_class AS target_rel ON target_rel.oid = fk.confrelid
JOIN pg_catalog.pg_namespace AS target_ns ON target_ns.oid = target_rel.relnamespace
WHERE fk.contype = 'f'
  AND source_rel.relkind IN ('r', 'p', 'm')
  AND source_ns.nspname <> 'information_schema'
  AND source_ns.nspname NOT LIKE 'pg\_%' ESCAPE '\'
ORDER BY source_ns.nspname, source_rel.relname, fk.conname;

-- Directly dependent non-system views and materialized views, from rewrite dependencies.
SELECT DISTINCT source_ns.nspname AS referenced_schema,
       source_rel.relname AS referenced_relation,
       view_ns.nspname AS dependent_schema,
       view_rel.relname AS dependent_view,
       CASE view_rel.relkind WHEN 'v' THEN 'view'
                             WHEN 'm' THEN 'materialized view' END AS dependent_kind
FROM pg_catalog.pg_depend AS dep
JOIN pg_catalog.pg_rewrite AS rw
  ON dep.classid = 'pg_catalog.pg_rewrite'::regclass
 AND rw.oid = dep.objid
JOIN pg_catalog.pg_class AS source_rel ON source_rel.oid = dep.refobjid
JOIN pg_catalog.pg_namespace AS source_ns ON source_ns.oid = source_rel.relnamespace
JOIN pg_catalog.pg_class AS view_rel ON view_rel.oid = rw.ev_class
JOIN pg_catalog.pg_namespace AS view_ns ON view_ns.oid = view_rel.relnamespace
WHERE dep.refclassid = 'pg_catalog.pg_class'::regclass
  AND rw.rulename = '_RETURN'
  AND source_rel.relkind IN ('r', 'p', 'm')
  AND source_ns.nspname <> 'information_schema'
  AND source_ns.nspname NOT LIKE 'pg\_%' ESCAPE '\'
  AND view_rel.relkind IN ('v', 'm')
  AND view_ns.nspname <> 'information_schema'
  AND view_ns.nspname NOT LIKE 'pg\_%' ESCAPE '\'
ORDER BY source_ns.nspname, source_rel.relname, view_ns.nspname, view_rel.relname;

-- Non-internal triggers attached to the reported relation kinds; trigger bodies are not read.
SELECT n.nspname AS relation_schema,
       c.relname AS relation_name,
       tg.tgname AS trigger_name,
       tg.tgenabled AS enabled_state
FROM pg_catalog.pg_trigger AS tg
JOIN pg_catalog.pg_class AS c ON c.oid = tg.tgrelid
JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
WHERE NOT tg.tgisinternal
  AND c.relkind IN ('r', 'p', 'm')
  AND n.nspname <> 'information_schema'
  AND n.nspname NOT LIKE 'pg\_%' ESCAPE '\'
ORDER BY n.nspname, c.relname, tg.tgname;

-- Existence only: never read migration rows, checksums, or other application-table values.
SELECT EXISTS (
    SELECT 1
    FROM pg_catalog.pg_class AS c
    JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
    WHERE n.nspname = 'learning'
      AND c.relname = 'migration_history'
      AND c.relkind IN ('r', 'p')
) AS learning_migration_history_exists;

-- Scan counters are cumulative since the statistics reset; a zero is not proof of disuse.
SELECT n.nspname AS schema_name,
       c.relname AS relation_name,
       stats.seq_scan,
       stats.idx_scan,
       stats.seq_tup_read,
       stats.idx_tup_fetch
FROM pg_catalog.pg_stat_all_tables AS stats
JOIN pg_catalog.pg_class AS c ON c.oid = stats.relid
JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
WHERE c.relkind IN ('r', 'p', 'm')
  AND n.nspname <> 'information_schema'
  AND n.nspname NOT LIKE 'pg\_%' ESCAPE '\'
ORDER BY n.nspname, c.relname;

-- Database statistics reset time; counters can be NULL or reset independently of observed usage.
SELECT datname AS database_name,
       stats_reset AS statistics_reset_at
FROM pg_catalog.pg_stat_database
WHERE datid = (SELECT oid
               FROM pg_catalog.pg_database
               WHERE datname = pg_catalog.current_database());

ROLLBACK;
