"""Verify the installed cards namespace guard in a borrowed transaction."""
import hashlib

_GUARD_SHA256 = '48adec4b20b2ddf5a0b3b8eed76bb2bd36ded6437fec16b1aae4297d06540ee4'


def fence_namespace(database):
    """Hold the schema stable and fail closed if the exact guard is absent.

    This does not validate existing legacy IDs; the caller must preflight them.
    The access-share lock prevents concurrent trigger DDL until caller commit and
    does not serialize ordinary card writes or allocate a position ID.
    """
    database.execute_native('LOCK TABLE public.cards IN ACCESS SHARE MODE')
    row = database.execute_native("""
        SELECT p.prosrc
        FROM pg_catalog.pg_trigger t
        JOIN pg_catalog.pg_class c ON c.oid=t.tgrelid
        JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
        JOIN pg_catalog.pg_proc p ON p.oid=t.tgfoid
        JOIN pg_catalog.pg_namespace pn ON pn.oid=p.pronamespace
        JOIN pg_catalog.pg_attribute a ON a.attrelid=c.oid AND a.attname='fsrs_card_json'
        WHERE n.nspname='public' AND c.relname='cards'
          AND t.tgname='study_position_guard_legacy_fsrs_id'
          AND NOT t.tgisinternal AND t.tgtype=23 AND t.tgenabled IN ('O','A')
          AND t.tgattr::text=a.attnum::text AND t.tgqual IS NULL AND t.tgnargs=0
          AND pn.nspname='public' AND p.proname='study_position_guard_legacy_fsrs_id'
          AND p.proconfig=ARRAY['search_path=pg_catalog']
          AND current_setting('session_replication_role')='origin'
    """).fetchone()
    if row is None or hashlib.sha256(row[0].encode()).hexdigest() != _GUARD_SHA256:
        raise RuntimeError('position_fsrs_namespace_guard_unavailable')
