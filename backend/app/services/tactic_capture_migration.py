"""Narrow compatibility repair, also available after verified legacy import."""


def normalize_game_tactic_cards(database) -> int:
    changed = database.execute(
        """UPDATE cards SET content_type='tactic'
           WHERE content_type='tactics' AND (repertoire_id='__game_tactics__' OR EXISTS(
             SELECT 1 FROM game_findings finding WHERE finding.card_id=cards.id
               AND finding.kind='tactical miss' AND finding.id=cards.source_ref))""",
    ).rowcount
    database.execute(
        """UPDATE daily_queue SET card_bucket='tactic' WHERE card_bucket='tactics'
           AND card_id IN (SELECT id FROM cards WHERE content_type='tactic'
           AND (repertoire_id='__game_tactics__' OR EXISTS(
             SELECT 1 FROM game_findings finding WHERE finding.card_id=cards.id
               AND finding.kind='tactical miss' AND finding.id=cards.source_ref)))""",
    )
    return changed
