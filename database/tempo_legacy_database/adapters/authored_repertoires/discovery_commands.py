"""Existing discovery mutations and locked source facts; caller owns transaction."""


def dismiss(database, evidence_json, now, opportunity_id):
    database.execute_native(
        "UPDATE repertoire_opportunities SET status='dismissed',"
        "dismissed_evidence_json=%s,updated_at=%s WHERE id=%s",
        (evidence_json, now, opportunity_id),
    )


def acknowledge(database, now, updated_at, opportunity_id, repertoire_id):
    return database.execute_native(
        "UPDATE repertoire_opportunities SET seen_at=%s,snoozed_until=NULL,updated_at=%s "
        "WHERE id=%s AND repertoire_id=%s AND status='active' RETURNING id",
        (now, updated_at, opportunity_id, repertoire_id),
    ).fetchone()


def snooze(database, seen_at, snoozed_until, now, opportunity_id, repertoire_id):
    return database.execute_native(
        "UPDATE repertoire_opportunities SET seen_at=COALESCE(seen_at,%s),"
        "snoozed_until=%s,updated_at=%s "
        "WHERE id=%s AND repertoire_id=%s AND status='active' RETURNING id",
        (seen_at, snoozed_until, now, opportunity_id, repertoire_id),
    ).fetchone()


def locked_opportunity_card(database, opportunity_id, repertoire_id):
    return database.execute_native(
        "SELECT card_id FROM repertoire_opportunities "
        "WHERE id=%s AND repertoire_id=%s FOR UPDATE",
        (opportunity_id, repertoire_id),
    ).fetchone()


def locked_card_kind(database, card_id):
    return database.execute_native(
        "SELECT kind FROM cards WHERE id=%s FOR UPDATE", (card_id,),
    ).fetchone()


def prefix_split_cards(database, source_card_id):
    return database.execute_native(
            "SELECT shortened_card_id,continuation_card_id FROM prefix_splits "
            "WHERE source_card_id=%s", (source_card_id,),
        ).fetchone()


def linked_repertoire_rows(database, card_ids):
    rows = database.execute_native(
            "SELECT repertoire_id FROM repertoire_cards WHERE card_id=ANY(%s::text[])",
            (card_ids,),
        )
    return (row for row in rows)


def repertoire(database, repertoire_id):
    return database.execute_native(
        "SELECT 1 FROM repertoires WHERE id=%s", (repertoire_id,),
    ).fetchone()
