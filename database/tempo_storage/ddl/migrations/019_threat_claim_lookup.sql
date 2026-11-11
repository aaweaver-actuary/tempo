-- The foreground-priority EXISTS check probes by request, not candidate.
CREATE INDEX idx_threat_candidate_requests_request_role
    ON threat_candidate_requests(request_id,role);
INSERT INTO tempo_schema_migrations(version) VALUES (19);
