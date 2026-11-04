-- REVIEW-ONLY CANDIDATE: exclusive retired attempt stores. No shared Study,
-- Tactics, Games, queue, receipt, card, review, or envelope rows are deleted.
-- RESTRICT is deliberate: an unreviewed catalog dependency aborts the cut.
DROP TABLE defense_attempts RESTRICT;
DROP TABLE endgame_attempts RESTRICT;
INSERT INTO tempo_schema_migrations(version) VALUES (62);
