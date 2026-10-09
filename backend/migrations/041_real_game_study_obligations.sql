-- Canonical decision evidence is independent of exact-history game classification.
-- Keep source generation and atomic published-version fences unchanged.
CREATE OR REPLACE VIEW repertoire_decision_events AS
    SELECT legacy.* FROM repertoire_decision_events_legacy legacy
    LEFT JOIN game_derivation_jobs job ON job.game_id=legacy.game_id
    WHERE COALESCE(job.published_repertoire_version,0)=0 AND EXISTS(SELECT 1 FROM imported_games current_game WHERE current_game.id=legacy.game_id AND current_game.repertoire_scope_generation=(SELECT generation FROM repertoire_game_scope WHERE id=1))
    UNION ALL
    SELECT staged.id,staged.game_id,staged.repertoire_id,staged.card_id,
           staged.ply,staged.fen_key,staged.expected_uci,staged.actual_uci,
           staged.outcome,staged.played_at,staged.updated_at
    FROM repertoire_decision_events_staged staged
    JOIN game_derivation_jobs job ON job.game_id=staged.game_id
        AND job.published_repertoire_version=staged.derivation_version
    WHERE job.published_repertoire_version>0 AND EXISTS(SELECT 1 FROM imported_games current_game WHERE current_game.id=staged.game_id AND current_game.repertoire_scope_generation=(SELECT generation FROM repertoire_game_scope WHERE id=1));

-- Reuse the existing restartable one-game refresh, with no startup traversal.
INSERT INTO background_tasks(id,kind,deduplication_key,priority,payload_json,next_attempt_at,created_at,updated_at)
SELECT 'real-game-obligations-backfill-v1','repertoire_game_refresh','all',90,
       '{"after_game_id":""}',CURRENT_TIMESTAMP::text,CURRENT_TIMESTAMP::text,CURRENT_TIMESTAMP::text
WHERE EXISTS(SELECT 1 FROM imported_games)
ON CONFLICT(kind,deduplication_key) DO UPDATE SET state='queued',
    generation=background_tasks.generation+1,payload_json=excluded.payload_json,
    phase='queued',attempt_count=0,lease_token=NULL,lease_expires_at=NULL,next_attempt_at=excluded.next_attempt_at,
    updated_at=excluded.updated_at;

INSERT INTO tempo_schema_migrations(version) VALUES(41);
