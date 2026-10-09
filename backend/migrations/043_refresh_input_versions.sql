CREATE TABLE analysis_refresh_requests (
    kind TEXT NOT NULL CHECK(kind IN ('repertoire_priority','repertoire_opportunity')),
    repertoire_id TEXT NOT NULL REFERENCES repertoires(id) ON DELETE CASCADE,
    input_version TEXT NOT NULL,
    pending_since TEXT,
    requested_at TEXT NOT NULL,
    PRIMARY KEY(kind,repertoire_id)
);
-- Published finding/evaluation changes are opportunity inputs too. Staging and
-- ordinary lease/status updates do not invalidate a calculation.
CREATE TRIGGER priority_source_findings AFTER INSERT OR DELETE OR UPDATE OF
    status,evidence_json,card_id,analysis_version ON game_findings
    FOR EACH STATEMENT EXECUTE FUNCTION bump_priority_source_epoch();
CREATE TRIGGER priority_source_analysis_publication AFTER UPDATE OF
    analysis_version,analysis_evidence_version ON game_analysis_jobs
    FOR EACH STATEMENT EXECUTE FUNCTION bump_priority_source_epoch();
CREATE TRIGGER priority_source_discovery_window AFTER UPDATE OF discovery_window_days ON settings
    FOR EACH STATEMENT EXECUTE FUNCTION bump_priority_source_epoch();
INSERT INTO tempo_schema_migrations(version) VALUES(43);
