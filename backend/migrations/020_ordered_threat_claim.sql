-- A sparse foreground relation and the ordinary queued order are read separately.
CREATE INDEX idx_threat_candidate_requests_role_request
    ON threat_candidate_requests(role,request_id);
CREATE INDEX idx_threat_analysis_requests_state_created
    ON threat_analysis_requests(state,created_at,id);
INSERT INTO tempo_schema_migrations(version) VALUES (20);
