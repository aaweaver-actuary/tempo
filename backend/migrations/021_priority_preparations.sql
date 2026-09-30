CREATE TABLE repertoire_priority_preparations (
    repertoire_id TEXT NOT NULL REFERENCES repertoires(id) ON DELETE CASCADE,
    generation BIGINT NOT NULL,
    source_version TEXT NOT NULL,
    scoring_version BIGINT NOT NULL,
    calculated_at TEXT NOT NULL,
    expected_count BIGINT,
    status TEXT NOT NULL CHECK (status IN ('preparing', 'ready')),
    PRIMARY KEY (repertoire_id, generation)
);

CREATE TABLE priority_source_epoch (
    id BIGINT PRIMARY KEY CHECK (id = 1),
    version BIGINT NOT NULL
);
INSERT INTO priority_source_epoch(id,version) VALUES (1,0);

CREATE TABLE priority_repertoire_source_epochs (
    repertoire_id TEXT PRIMARY KEY REFERENCES repertoires(id) ON DELETE CASCADE,
    version BIGINT NOT NULL
);

CREATE FUNCTION bump_priority_repertoire_source(target_repertoire_id TEXT)
RETURNS void LANGUAGE plpgsql AS $$
BEGIN
    IF target_repertoire_id IS NOT NULL AND EXISTS (
        SELECT 1 FROM repertoires WHERE id=target_repertoire_id
    ) THEN
        INSERT INTO priority_repertoire_source_epochs(repertoire_id,version)
        VALUES(target_repertoire_id,1)
        ON CONFLICT(repertoire_id) DO UPDATE
        SET version=priority_repertoire_source_epochs.version+1;
    END IF;
END;
$$;

CREATE FUNCTION priority_repertoire_source_changed() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    old_repertoire_id TEXT;
    new_repertoire_id TEXT;
    affected_repertoire_id TEXT;
BEGIN
    IF TG_OP <> 'INSERT' THEN
        old_repertoire_id := OLD.repertoire_id;
    END IF;
    IF TG_OP <> 'DELETE' THEN
        new_repertoire_id := NEW.repertoire_id;
    END IF;
    FOR affected_repertoire_id IN
        SELECT DISTINCT repertoire_id FROM (
            SELECT old_repertoire_id AS repertoire_id
            UNION SELECT new_repertoire_id
        ) affected WHERE repertoire_id IS NOT NULL ORDER BY repertoire_id
    LOOP
        PERFORM bump_priority_repertoire_source(affected_repertoire_id);
    END LOOP;
    RETURN NULL;
END;
$$;

CREATE FUNCTION priority_card_source_changed() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    affected_repertoire_id TEXT;
    source_card_id TEXT;
    old_owner_id TEXT;
    new_owner_id TEXT;
BEGIN
    IF TG_OP='DELETE' THEN
        source_card_id := OLD.id;
        old_owner_id := OLD.repertoire_id;
    ELSE
        source_card_id := NEW.id;
        new_owner_id := NEW.repertoire_id;
        IF TG_OP='UPDATE' THEN
            old_owner_id := OLD.repertoire_id;
        END IF;
    END IF;
    FOR affected_repertoire_id IN
        SELECT DISTINCT repertoire_id FROM (
            SELECT link.repertoire_id FROM repertoire_cards link
            WHERE link.card_id=source_card_id
            UNION
            SELECT old_owner_id
            UNION
            SELECT new_owner_id
        ) affected WHERE repertoire_id IS NOT NULL ORDER BY repertoire_id
    LOOP
        PERFORM bump_priority_repertoire_source(affected_repertoire_id);
    END LOOP;
    RETURN NULL;
END;
$$;

CREATE FUNCTION priority_review_source_changed() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    affected_repertoire_id TEXT;
    old_card_id TEXT;
    new_card_id TEXT;
BEGIN
    IF TG_OP='DELETE' THEN
        old_card_id := OLD.card_id;
    ELSE
        new_card_id := NEW.card_id;
        IF TG_OP='UPDATE' THEN
            old_card_id := OLD.card_id;
        END IF;
    END IF;
    FOR affected_repertoire_id IN
        SELECT DISTINCT repertoire_id FROM (
            SELECT link.repertoire_id FROM repertoire_cards link
            WHERE link.card_id IN (old_card_id,new_card_id)
            UNION
            SELECT card.repertoire_id FROM cards card
            WHERE card.id IN (old_card_id,new_card_id)
        ) affected WHERE repertoire_id IS NOT NULL ORDER BY repertoire_id
    LOOP
        PERFORM bump_priority_repertoire_source(affected_repertoire_id);
    END LOOP;
    RETURN NULL;
END;
$$;

CREATE FUNCTION priority_candidate_source_changed() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    old_node_id TEXT;
    new_node_id TEXT;
    affected_repertoire_id TEXT;
BEGIN
    IF TG_OP='DELETE' THEN
        old_node_id := OLD.node_id;
    ELSE
        new_node_id := NEW.node_id;
        IF TG_OP='UPDATE' THEN
            old_node_id := OLD.node_id;
        END IF;
    END IF;
    FOR affected_repertoire_id IN
        SELECT DISTINCT node.repertoire_id FROM repertoire_coverage_nodes node
        WHERE node.id IN (old_node_id,new_node_id)
        ORDER BY node.repertoire_id
    LOOP
        PERFORM bump_priority_repertoire_source(affected_repertoire_id);
    END LOOP;
    RETURN NULL;
END;
$$;

CREATE FUNCTION bump_priority_source_epoch() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    UPDATE priority_source_epoch SET version=version+1 WHERE id=1;
    RETURN NULL;
END;
$$;

CREATE TRIGGER priority_source_lines AFTER INSERT OR UPDATE OR DELETE ON repertoire_lines
    FOR EACH ROW EXECUTE FUNCTION priority_repertoire_source_changed();
CREATE TRIGGER priority_source_links AFTER INSERT OR UPDATE OR DELETE ON repertoire_cards
    FOR EACH ROW EXECUTE FUNCTION priority_repertoire_source_changed();
CREATE TRIGGER priority_source_cards AFTER INSERT OR DELETE OR UPDATE OF
    state,introduced_at,start_fen,moves_json,content_type,archived ON cards
    FOR EACH ROW EXECUTE FUNCTION priority_card_source_changed();
CREATE TRIGGER priority_source_runs AFTER INSERT OR DELETE OR UPDATE OF
    status,created_at ON repertoire_coverage_runs
    FOR EACH ROW EXECUTE FUNCTION priority_repertoire_source_changed();
CREATE TRIGGER priority_source_nodes AFTER INSERT OR DELETE OR UPDATE OF
    explorer_status,explorer_games,maia_status ON repertoire_coverage_nodes
    FOR EACH ROW EXECUTE FUNCTION priority_repertoire_source_changed();
CREATE TRIGGER priority_source_candidates AFTER INSERT OR DELETE OR UPDATE OF
    node_id,move_uci,explorer_probability,maia_probability ON repertoire_coverage_candidates
    FOR EACH ROW EXECUTE FUNCTION priority_candidate_source_changed();
CREATE TRIGGER priority_source_games AFTER INSERT OR DELETE OR UPDATE OF
    played_at,speed,color,adaptive_excluded ON imported_games
    FOR EACH STATEMENT EXECUTE FUNCTION bump_priority_source_epoch();
CREATE TRIGGER priority_source_positions AFTER INSERT OR DELETE OR UPDATE OF
    game_id,fen_key,move_uci ON game_position_occurrences
    FOR EACH STATEMENT EXECUTE FUNCTION bump_priority_source_epoch();
CREATE TRIGGER priority_source_misses AFTER INSERT OR DELETE OR UPDATE OF
    game_id,repertoire_id,card_id,outcome,played_at ON repertoire_decision_events
    FOR EACH ROW EXECUTE FUNCTION priority_repertoire_source_changed();
CREATE TRIGGER priority_source_reviews AFTER INSERT OR DELETE OR UPDATE OF
    card_id,source_kind,reviewed_at ON reviews
    FOR EACH ROW EXECUTE FUNCTION priority_review_source_changed();
CREATE TRIGGER priority_source_settings AFTER INSERT OR DELETE OR UPDATE OF
    coverage_horizon_fullmoves,coverage_path_floor ON settings
    FOR EACH STATEMENT EXECUTE FUNCTION bump_priority_source_epoch();

CREATE TABLE repertoire_priority_prepared_rows (
    repertoire_id TEXT NOT NULL,
    generation BIGINT NOT NULL,
    ordinal BIGINT NOT NULL,
    card_id TEXT NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
    completed_line_ids_json TEXT NOT NULL,
    completion_mass DOUBLE PRECISION NOT NULL,
    frontier_decisions_json TEXT NOT NULL,
    frontier_reach DOUBLE PRECISION NOT NULL,
    priority_score DOUBLE PRECISION NOT NULL,
    evidence_json TEXT NOT NULL,
    PRIMARY KEY (repertoire_id, generation, ordinal),
    UNIQUE (repertoire_id, generation, card_id),
    FOREIGN KEY (repertoire_id, generation)
        REFERENCES repertoire_priority_preparations(repertoire_id, generation) ON DELETE CASCADE
);

INSERT INTO tempo_schema_migrations(version) VALUES (21);
