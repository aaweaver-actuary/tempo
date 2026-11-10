-- Compact derived evidence only; no startup scan or source acquisition.
CREATE TABLE next_opponent_snapshots (
    version TEXT PRIMARY KEY,
    account TEXT NOT NULL,
    method_version TEXT NOT NULL,
    profile_json TEXT NOT NULL,
    published_at TEXT NOT NULL
);
CREATE TABLE next_opponent_accounts (
    account TEXT PRIMARY KEY,
    input_generation BIGINT NOT NULL DEFAULT 0,
    published_generation BIGINT,
    published_method TEXT,
    profile_version TEXT REFERENCES next_opponent_snapshots(version),
    next_evidence_at TIMESTAMPTZ
);
CREATE FUNCTION protect_next_opponent_snapshot() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Next-opponent snapshots are immutable';
END $$;
CREATE TRIGGER next_opponent_snapshot_immutable BEFORE UPDATE OR DELETE ON next_opponent_snapshots
FOR EACH ROW EXECUTE FUNCTION protect_next_opponent_snapshot();

-- Provider dates have explicit offsets; pin UTC for historical naive ISO dates.
-- Invalid historical data is excluded rather than breaking a foreground request.
CREATE FUNCTION next_opponent_game_time(value TEXT) RETURNS TIMESTAMPTZ
LANGUAGE plpgsql IMMUTABLE STRICT SET timezone='UTC' AS $$
BEGIN
    IF value !~ '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}' THEN RETURN NULL; END IF;
    RETURN value::timestamptz;
EXCEPTION WHEN data_exception THEN RETURN NULL;
END $$;
CREATE INDEX idx_next_opponent_account_time ON imported_games
(lower(trim(username)), next_opponent_game_time(played_at) DESC, id DESC)
WHERE provider='lichess' AND rated=1 AND adaptive_excluded=0;
CREATE INDEX idx_next_opponent_speed_rating_time ON imported_games
(lower(trim(username)), speed, next_opponent_game_time(played_at) DESC, id DESC)
WHERE provider='lichess' AND rated=1 AND adaptive_excluded=0 AND player_rating>0;

-- Every relevant source mutation fences in-flight computations, including raw
-- recovery/import writes. Model publication never needs to lock game rows.
CREATE FUNCTION invalidate_next_opponent_inputs() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE before_row JSONB; after_row JSONB; affected_account TEXT;
BEGIN
    IF TG_OP <> 'INSERT' THEN before_row=to_jsonb(OLD); END IF;
    IF TG_OP <> 'DELETE' THEN after_row=to_jsonb(NEW); END IF;
    IF TG_OP='UPDATE' AND
       ROW(OLD.provider,OLD.username,OLD.played_at,OLD.speed,OLD.rated,OLD.adaptive_excluded,
           OLD.player_rating,OLD.opponent_rating,OLD.rating_change,OLD.time_control) IS NOT DISTINCT FROM
       ROW(NEW.provider,NEW.username,NEW.played_at,NEW.speed,NEW.rated,NEW.adaptive_excluded,
           NEW.player_rating,NEW.opponent_rating,NEW.rating_change,NEW.time_control) THEN RETURN NEW; END IF;
    FOR affected_account IN
        SELECT DISTINCT lower(trim(source->>'username')) FROM (VALUES(before_row),(after_row)) AS sources(source)
        WHERE source->>'provider'='lichess' AND source->>'rated'='1'
          AND source->>'adaptive_excluded'='0' AND trim(source->>'username')<>''
        ORDER BY 1
    LOOP
        INSERT INTO next_opponent_accounts(account,input_generation) VALUES(affected_account,1)
        ON CONFLICT(account) DO UPDATE SET input_generation=next_opponent_accounts.input_generation+1;
    END LOOP;
    IF TG_OP='DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
END $$;
CREATE TRIGGER next_opponent_input_change AFTER INSERT OR UPDATE OR DELETE ON imported_games
FOR EACH ROW EXECUTE FUNCTION invalidate_next_opponent_inputs();
INSERT INTO tempo_schema_migrations(version) VALUES(44);
