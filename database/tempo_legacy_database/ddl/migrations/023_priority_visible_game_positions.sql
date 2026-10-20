-- Versioned position indexing writes the backing table and makes it visible
-- through game_derivation_jobs. The old trigger on the read view saw neither.
DROP TRIGGER priority_source_positions ON game_position_occurrences;
DROP TRIGGER priority_source_game_publications ON game_derivation_jobs;

CREATE TRIGGER priority_source_game_publications
AFTER UPDATE OF published_position_version,published_repertoire_version
ON game_derivation_jobs
FOR EACH ROW
WHEN (OLD.published_position_version IS DISTINCT FROM NEW.published_position_version
      OR OLD.published_repertoire_version IS DISTINCT FROM NEW.published_repertoire_version)
EXECUTE FUNCTION bump_priority_source_epoch();

-- Direct legacy imports/repairs and edits to an already published staged
-- version also change the view. Unpublished staging does not.
CREATE FUNCTION priority_visible_position_source_changed() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    old_visible BOOLEAN := FALSE;
    new_visible BOOLEAN := FALSE;
BEGIN
    IF TG_OP='UPDATE' AND OLD IS NOT DISTINCT FROM NEW THEN
        RETURN NULL;
    END IF;
    IF TG_OP<>'INSERT' THEN
        IF TG_TABLE_NAME='game_position_occurrences_legacy' THEN
            old_visible := COALESCE((SELECT published_position_version
                                     FROM game_derivation_jobs WHERE game_id=OLD.game_id),0)=0;
        ELSE
            old_visible := COALESCE((SELECT published_position_version
                                     FROM game_derivation_jobs WHERE game_id=OLD.game_id),0)
                           =OLD.derivation_version;
        END IF;
    END IF;
    IF TG_OP<>'DELETE' THEN
        IF TG_TABLE_NAME='game_position_occurrences_legacy' THEN
            new_visible := COALESCE((SELECT published_position_version
                                     FROM game_derivation_jobs WHERE game_id=NEW.game_id),0)=0;
        ELSE
            new_visible := COALESCE((SELECT published_position_version
                                     FROM game_derivation_jobs WHERE game_id=NEW.game_id),0)
                           =NEW.derivation_version;
        END IF;
    END IF;
    IF old_visible OR new_visible THEN
        UPDATE priority_source_epoch SET version=version+1 WHERE id=1;
    END IF;
    RETURN NULL;
END;
$$;

CREATE TRIGGER priority_source_positions_legacy
AFTER INSERT OR UPDATE OR DELETE ON game_position_occurrences_legacy
FOR EACH ROW EXECUTE FUNCTION priority_visible_position_source_changed();
CREATE TRIGGER priority_source_positions_staged
AFTER INSERT OR UPDATE OR DELETE ON game_position_occurrences_staged
FOR EACH ROW EXECUTE FUNCTION priority_visible_position_source_changed();

-- Preparations produced before position publications were fenced may already
-- contain stale evidence. One transactional bump invalidates them on upgrade.
UPDATE priority_source_epoch SET version=version+1 WHERE id=1;

INSERT INTO tempo_schema_migrations(version) VALUES (23);
