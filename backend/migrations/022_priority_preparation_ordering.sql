-- A null value identifies an unverified preparation written before card IDs
-- became the durable ordinal order. The dispatcher replaces such work with a
-- fresh fenced generation; it never mixes those rows into a new preparation.
ALTER TABLE repertoire_priority_preparations ADD COLUMN ordering_version BIGINT;

INSERT INTO tempo_schema_migrations(version) VALUES (22);
