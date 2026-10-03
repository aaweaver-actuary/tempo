"""SQLite compatibility migrations for the canonical publication version model."""


def initialize_scope_schema(database) -> None:
    for table, columns in {
        'cards': {'canonical_route_source': 'INTEGER NOT NULL DEFAULT 1 CHECK(canonical_route_source IN (0,1))'},
        'repertoire_cards': {'canonical_route_source': 'INTEGER NOT NULL DEFAULT 1 CHECK(canonical_route_source IN (0,1))'},
        'imported_games': {'repertoire_scope_generation': 'BIGINT NOT NULL DEFAULT 0'},
        'game_findings': {'game_scope_generation': 'BIGINT NOT NULL DEFAULT 0'},
        'repertoire_opportunities': {
            'canonical_scope_source_revision': 'BIGINT NOT NULL DEFAULT -1',
            'canonical_scope_preview_id': 'TEXT',
            'game_scope_generation': 'BIGINT NOT NULL DEFAULT 0',
        },
    }.items():
        existing = {row[1] for row in database.execute(f'PRAGMA table_info({table})')}
        for column, definition in columns.items():
            if column not in existing:
                database.execute(f'ALTER TABLE {table} ADD COLUMN {column} {definition}')
    database.execute('CREATE TABLE IF NOT EXISTS repertoire_game_scope(id INTEGER PRIMARY KEY CHECK(id=1),generation BIGINT NOT NULL)')
    database.execute('INSERT OR IGNORE INTO repertoire_game_scope VALUES(1,0)')
    for table in ('repertoire_lines', 'repertoire_cards'):
        for event in ('insert', 'update', 'delete'):
            database.execute(f'DROP TRIGGER IF EXISTS canonical_source_{table}_{event}')
    for event in ('insert', 'update', 'delete'):
        database.execute(f'DROP TRIGGER IF EXISTS canonical_source_card_{event}')
    # Mark only cards whose complete contents match a materialized graph step.
    # Repeated startup never reclassifies a card promoted by an explicit edit.
    migrated = database.execute("SELECT 1 FROM sqlite_master WHERE type='trigger' AND name='canonical_card_promote'").fetchone()
    if not migrated:
        for table in ('repertoire_card_priority_generations', 'repertoire_card_introduction_priorities'):
            database.execute(f"UPDATE {table} SET evidence_json=json_set(evidence_json,'$.game_scope_generation',0)")
        database.execute("UPDATE cards SET canonical_route_source=0 WHERE EXISTS(SELECT 1 FROM opening_graph_steps step WHERE step.card_id=cards.id AND step.starting_fen=cards.start_fen AND step.moves_json=cards.moves_json)")
        database.execute("UPDATE repertoire_cards SET canonical_route_source=0 WHERE EXISTS(SELECT 1 FROM opening_graph_steps step JOIN cards card ON card.id=step.card_id WHERE step.card_id=repertoire_cards.card_id AND step.repertoire_id=repertoire_cards.repertoire_id AND card.canonical_route_source=0)")
    for table in ('repertoire_lines', 'repertoire_cards'):
        for event in ('INSERT', 'UPDATE', 'DELETE'):
            source = 'OLD' if event == 'DELETE' else 'NEW'
            affected = 'id IN (OLD.repertoire_id,NEW.repertoire_id)' if event == 'UPDATE' else f'id={source}.repertoire_id'
            if table == 'repertoire_cards':
                condition = 'OLD.canonical_route_source=1 OR NEW.canonical_route_source=1' if event == 'UPDATE' else f'{source}.canonical_route_source=1'
            elif event == 'UPDATE':
                condition = 'OLD.repertoire_id IS NOT NEW.repertoire_id OR OLD.start_fen IS NOT NEW.start_fen OR OLD.moves_json IS NOT NEW.moves_json OR OLD.trained_color IS NOT NEW.trained_color'
            else:
                condition = '1'
            database.execute(f'CREATE TRIGGER canonical_source_{table}_{event.lower()} AFTER {event} ON {table} WHEN {condition} BEGIN UPDATE repertoires SET scope_source_revision=scope_source_revision+1 WHERE {affected}; END')
    structural_change = 'OLD.start_fen IS NOT NEW.start_fen OR OLD.moves_json IS NOT NEW.moves_json OR OLD.content_type IS NOT NEW.content_type OR OLD.trained_color IS NOT NEW.trained_color'
    database.execute(f'CREATE TRIGGER IF NOT EXISTS canonical_card_promote AFTER UPDATE ON cards WHEN ({structural_change}) BEGIN UPDATE cards SET canonical_route_source=1 WHERE id=NEW.id AND canonical_route_source=0; UPDATE repertoire_cards SET canonical_route_source=1 WHERE card_id=NEW.id AND canonical_route_source=0; END')
    database.execute(f"CREATE TRIGGER canonical_source_card_update AFTER UPDATE ON cards WHEN (OLD.content_type='opening' OR NEW.content_type='opening') AND (OLD.moves_json<>'[]' OR NEW.moves_json<>'[]') AND (({structural_change}) OR ((OLD.canonical_route_source=1 OR NEW.canonical_route_source=1) AND (OLD.archived IS NOT NEW.archived OR OLD.repertoire_id IS NOT NEW.repertoire_id OR OLD.canonical_route_source IS NOT NEW.canonical_route_source))) BEGIN UPDATE repertoires SET scope_source_revision=scope_source_revision+1 WHERE (id=OLD.repertoire_id AND OLD.canonical_route_source=1 AND NOT EXISTS(SELECT 1 FROM repertoire_cards owner_link WHERE owner_link.card_id=OLD.id AND owner_link.repertoire_id=OLD.repertoire_id AND owner_link.canonical_route_source=0)) OR (id=NEW.repertoire_id AND NEW.canonical_route_source=1 AND NOT EXISTS(SELECT 1 FROM repertoire_cards owner_link WHERE owner_link.card_id=NEW.id AND owner_link.repertoire_id=NEW.repertoire_id AND owner_link.canonical_route_source=0)) OR id IN (SELECT repertoire_id FROM repertoire_cards WHERE card_id=NEW.id AND canonical_route_source=1); END")
    for event, source in (('INSERT', 'NEW'), ('DELETE', 'OLD')):
        database.execute(f'CREATE TRIGGER canonical_source_card_{event.lower()} AFTER {event} ON cards WHEN {source}.canonical_route_source=1 AND {source}.content_type=\'opening\' AND {source}.moves_json<>\'[]\' BEGIN UPDATE repertoires SET scope_source_revision=scope_source_revision+1 WHERE (id={source}.repertoire_id AND NOT EXISTS(SELECT 1 FROM repertoire_cards owner_link WHERE owner_link.card_id={source}.id AND owner_link.repertoire_id={source}.repertoire_id AND owner_link.canonical_route_source=0)) OR id IN (SELECT repertoire_id FROM repertoire_cards WHERE card_id={source}.id AND canonical_route_source=1); END')
    for event in ('INSERT', 'DELETE', 'UPDATE'):
        source = 'OLD' if event == 'DELETE' else 'NEW'
        condition = f" WHEN {source}.id NOT IN ('__tactics__','__endgames__','__game_mistakes__','__game_tactics__','__captured_tactics__')"
        if event == 'UPDATE':
            condition += ' AND (OLD.canonical_prefix_moves_json IS NOT NEW.canonical_prefix_moves_json OR OLD.scope_source_revision IS NOT NEW.scope_source_revision OR OLD.is_main IS NOT NEW.is_main)'
        database.execute(f'CREATE TRIGGER IF NOT EXISTS canonical_game_scope_{event.lower()} AFTER {event} ON repertoires{condition} BEGIN UPDATE repertoire_game_scope SET generation=generation+1 WHERE id=1; END')
    database.execute('CREATE TRIGGER IF NOT EXISTS canonical_game_insert AFTER INSERT ON imported_games BEGIN UPDATE imported_games SET repertoire_scope_generation=(SELECT generation FROM repertoire_game_scope WHERE id=1) WHERE id=NEW.id; END')
    for table in ('game_repertoire_matches', 'repertoire_comparisons', 'repertoire_decision_events'):
        database.execute(f'CREATE VIEW IF NOT EXISTS current_{table} AS SELECT publication.* FROM {table} publication JOIN imported_games game ON game.id=publication.game_id WHERE game.repertoire_scope_generation=(SELECT generation FROM repertoire_game_scope WHERE id=1)')
    database.execute("CREATE VIEW IF NOT EXISTS current_game_findings AS SELECT * FROM game_findings WHERE kind NOT IN ('repertoire lapse','repertoire gap') OR game_scope_generation=(SELECT generation FROM repertoire_game_scope WHERE id=1)")

    database.execute('CREATE TRIGGER IF NOT EXISTS canonical_finding_insert AFTER INSERT ON game_findings BEGIN UPDATE game_findings SET game_scope_generation=(SELECT generation FROM repertoire_game_scope WHERE id=1) WHERE id=NEW.id; END')
    database.execute('CREATE VIEW IF NOT EXISTS current_gameplay_card_priorities AS SELECT priority.* FROM gameplay_card_priorities priority WHERE priority.finding_id IS NULL OR EXISTS(SELECT 1 FROM current_game_findings finding WHERE finding.id=priority.finding_id)')
    database.execute('CREATE TRIGGER IF NOT EXISTS canonical_opportunity_insert AFTER INSERT ON repertoire_opportunities BEGIN UPDATE repertoire_opportunities SET game_scope_generation=(SELECT generation FROM repertoire_game_scope WHERE id=1) WHERE id=NEW.id; END')

    from .canonical_scope_freshness import coverage_scope_predicate, opportunity_scope_predicate
    database.execute(f'CREATE VIEW IF NOT EXISTS current_repertoire_opportunities AS SELECT opportunity.* FROM repertoire_opportunities opportunity WHERE {opportunity_scope_predicate()}')
    for table in ('repertoire_card_priority_generations','repertoire_card_introduction_priorities'):
        predicate = coverage_scope_predicate(database, settings='publication.evidence_json', repertoire_id='publication.repertoire_id')
        predicate += " AND COALESCE(CAST(json_extract(publication.evidence_json,'$.game_scope_generation') AS BIGINT),0)=(SELECT generation FROM repertoire_game_scope WHERE id=1)"
        database.execute(f'CREATE VIEW IF NOT EXISTS current_{table} AS SELECT publication.* FROM {table} publication WHERE {predicate}')
