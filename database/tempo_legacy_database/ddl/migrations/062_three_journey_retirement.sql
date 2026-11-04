-- Review candidate: four exclusively retired stores; all shared state remains.
-- RESTRICT aborts the transaction if an unreviewed dependency remains.
DROP TRIGGER next_opponent_input_change ON imported_games;
DROP INDEX idx_next_opponent_account_time;
DROP INDEX idx_next_opponent_speed_rating_time;
DROP FUNCTION invalidate_next_opponent_inputs() RESTRICT;
DROP FUNCTION next_opponent_game_time(text) RESTRICT;
DROP TABLE next_opponent_accounts RESTRICT;
DROP TABLE next_opponent_snapshots RESTRICT;
DROP FUNCTION protect_next_opponent_snapshot() RESTRICT;
DROP TABLE defense_attempts RESTRICT;
DROP TABLE endgame_attempts RESTRICT;
INSERT INTO tempo_schema_migrations(version) VALUES (62);
