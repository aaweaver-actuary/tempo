"""F01: exact single-position source rows on the owned native PG fixture."""
import base64
import json
import os
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo
from tempo_learning_transfer_contracts.v1 import (
    CaptureInventory, KeyPart, SourceKey, SourceManifest, StageReceipt, StateFact,
    StoreShape, digest_bytes, facts_digest, record_digest,
)
from tempo_learning_database.adapters.retained_position_store import (
    RetainedPositionConflict, read_position, stage_position,
)
import runpy

ROOT = Path(__file__).resolve().parents[3]
apply_migrations = runpy.run_path(str(ROOT / "database/tooling/apply_postgres_migrations.py"))["apply_migrations"]
TARGET_DDL = (ROOT / "database/tempo_learning_database/ddl/migrations/0004_retained_position.sql").read_text()

# Each selected table is read in full, with its actual primary key and native
# typed row_to_json text. TEXT (including FSRS) is never parsed/reformatted by
# this test's capture codec. This is a small synthetic scope, not a census.
TABLES = {
    "repertoires": (("id", "text"),),
    "cards": (("id", "text"),),
    "daily_queue_days": (("queue_date", "text"),),
    "daily_queue": (("id", "bigint-decimal"),),
    "study_position_epochs": (("repertoire_id", "text"), ("epoch", "text")),
    "study_position_owners": (("repertoire_id", "text"),),
    "study_position_seed_runs": (("repertoire_id", "text"), ("owner_epoch", "text")),
    "study_position_seed_outcomes": (("repertoire_id", "text"), ("owner_epoch", "text"), ("variant", "text"), ("position", "text")),
    "study_position_states": (("repertoire_id", "text"), ("variant", "text"), ("position", "text")),
    "study_position_issued_plans": (("attempt_id", "text"),),
    "study_position_first_responses": (("event_id", "text"),),
    "study_position_grades": (("effect_id", "text"),),
    "reviews": (("id", "bigint-decimal"),),
    "review_attempt_receipts": (("attempt_id", "text"),),
    "review_schedule_snapshots": (("review_id", "bigint-decimal"),),
}


def populate(conn):
    conn.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES ('r','Synthetic','fixture','2026-10-01')")
    conn.execute("""INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,fsrs_card_json,revision,introduced_at)
        VALUES ('card-1','r','response','fen','[]','learning','2026-11-05',%s,2,'2026-10-01T12:00:00Z')""", ('{ "card_id":42, "stability":1.25 }',))
    conn.execute("INSERT INTO daily_queue_days(queue_date,seed,membership_hash,generated_at) VALUES ('2026-10-02',7,'membership','2026-10-02T00:00:00Z')")
    conn.execute("INSERT INTO daily_queue(id,queue_date,card_id,position,status) VALUES (9,'2026-10-02','card-1',0,'completed')")
    conn.execute("INSERT INTO study_position_epochs VALUES ('r','e1','authored','2026-10-01T12:00:00Z')")
    conn.execute("INSERT INTO study_position_owners VALUES ('r','e1','active','2026-10-01T12:00:00Z')")
    conn.execute("""INSERT INTO study_position_seed_runs(repertoire_id,owner_epoch,source_digest,source_token_json,selection_policy,manifest_digest,manifest_count,allocator_preflight_json,seeded_count,status,created_at,completed_at)
        VALUES ('r','e1','authored','{}','v1','manifest',1,'{}',1,'complete','2026-10-01T12:00:00Z','2026-10-01T12:00:00Z')""")
    conn.execute("""INSERT INTO study_position_seed_outcomes(repertoire_id,owner_epoch,variant,position,expected_uci,policy_version,disposition,donor_card_id,donor_card_revision,donor_fingerprint,donor_selection_policy,donor_fsrs_internal_card_id,allocated_fsrs_internal_card_id,clone_fingerprint,introduced_at)
        VALUES ('r','e1','standard','exact-fen','e2e4','p1','seeded','card-1',2,'fingerprint','v1',42,4503599627370496,'clone','2026-10-01T12:00:00Z')""")
    conn.execute("""INSERT INTO study_position_states(repertoire_id,variant,position,expected_uci,policy_version,owner_epoch,source_digest,version,fsrs_internal_card_id,fsrs_json,due_at,introduced_at,donor_card_id,donor_card_revision,donor_fingerprint,donor_selection_policy)
        VALUES ('r','standard','exact-fen','e2e4','p1','e1','authored',3,4503599627370496,%s,'2026-11-05T01:02:03.123456Z','2026-10-01T12:00:00Z','card-1',2,'fingerprint','v1')""", ('{ "card_id":4503599627370496, "stability":1.25 }',))
    conn.execute("""INSERT INTO study_position_issued_plans VALUES ('attempt-1',9,'card-1',2,'r','standard','e1','authored','2026-10-02','plan-digest','{"target":"exact-fen"}','2026-10-02T12:00:00Z')""")
    conn.execute("""INSERT INTO study_position_first_responses VALUES ('event-1','attempt-1',0,'r','standard','exact-fen','e1','payload','{"actual_uci":"e2e4"}','2026-10-02T12:00:30Z','2026-10-02T12:00:31Z')""")
    conn.execute("""INSERT INTO study_position_grades(effect_id,source_kind,event_id,attempt_id,decision_index,effect_kind,repertoire_id,variant,position,owner_epoch,expected_uci,policy_version,source_digest,accepted_study_day,grade,request_digest,accepted_at,fsrs_before_json,fsrs_after_json,version_before,version_after)
        VALUES ('effect-1','study','event-1','attempt-1',0,'response','r','standard','exact-fen','e1','e2e4','p1','authored','2026-10-02','good','effect-digest','2026-10-02T12:01:00Z','before','after',2,3)""")
    conn.execute("""INSERT INTO reviews(id,card_id,rating,reviewed_at,previous_interval,next_interval,internal_rating,guided,source_kind,source_ref,invalidated_at,invalidation_reason)
        VALUES (71,'card-1','good','2026-10-02T12:01:00Z',1,3,'good',0,'position_study','attempt:attempt-1','2026-10-03T12:00:00Z','source_edit'),
               (72,'card-1','again','2026-10-03T12:01:00Z',3,1,'again',0,'study',NULL,NULL,NULL)""")
    conn.execute("""INSERT INTO review_attempt_receipts VALUES ('attempt-1','card-1',9,'correct',0,'2026-10-02T12:01:00Z',71,'complete',NULL,'{}')""")
    conn.execute("INSERT INTO review_schedule_snapshots VALUES (71,'{ ""before"": ""exact"" }'), (72,'{}')")


def capture(conn):
    rows, original = {}, {}
    for table in TABLES:
        original[table] = [raw for (raw,) in conn.execute(sql.SQL("SELECT row_to_json(t)::text FROM {} t ORDER BY 1").format(sql.Identifier(table)))]
        rows[table] = [json.loads(raw) for raw in original[table]]
    def source_key(table, row, authority="legacy"):
        return SourceKey(authority=authority, store=table, primary_key=tuple(KeyPart(name=n, codec=c, value=str(row[n])) for n,c in TABLES[table]))
    by_table = {table: [source_key(table, row) for row in values] for table,values in rows.items()}
    refs = {
        "cards": ("repertoires",), "daily_queue": ("daily_queue_days", "cards"),
        "study_position_owners": ("study_position_epochs",), "study_position_seed_runs": ("study_position_epochs",),
        "study_position_seed_outcomes": ("study_position_seed_runs", "cards"),
        "study_position_states": ("study_position_epochs", "cards"),
        "study_position_issued_plans": ("study_position_epochs", "daily_queue", "cards"),
        "study_position_first_responses": ("study_position_states", "study_position_issued_plans"),
        "study_position_grades": ("study_position_first_responses", "study_position_epochs", "study_position_states"),
        "reviews": ("cards",), "review_attempt_receipts": ("cards", "daily_queue", "reviews"),
        "review_schedule_snapshots": ("reviews",),
    }
    facts, source_shapes, target_shapes = [], [], []
    for table, pk in TABLES.items():
        shape = lambda authority: StoreShape(authority=authority,store=table,primary_key=pk,schema_evidence="legacy-migrations-001-through-059")
        source_shapes.append(shape("legacy")); target_shapes.append(shape("learning"))
        for row, row_text in zip(rows[table], original[table]):
            raw = row_text.encode("utf-8")
            # Restrict references to matching review where applicable.
            targets = [key for dep in refs.get(table, ()) for key in by_table[dep]]
            if table == "review_schedule_snapshots":
                targets = [k for k in targets if k.primary_key[0].value == str(row["review_id"])]
            if table == "review_attempt_receipts":
                targets = [k for k in targets if k.store != "reviews" or k.primary_key[0].value == str(row["review_id"])]
            facts.append(StateFact(family="position" if table.startswith("study_position") else "cards_history", source=source_key(table,row),
                codec="postgres-row-json-utf8-v1",original_base64=base64.b64encode(raw).decode(),original_digest=digest_bytes(raw),
                references=tuple(targets),required=True,disposition="EXACT",target_owner="Learning",target_key=source_key(table,row,"learning"),
                translation_proof=None,archive_readback=None,blocker=None))
    facts = tuple(facts)
    inventories = tuple(CaptureInventory(shape=shape, expected_count=len(rows[shape.store]),
        expected_digest=facts_digest(tuple(f for f in facts if f.source.store == shape.store)),completeness_evidence="synthetic-full-table") for shape in source_shapes)
    manifest = SourceManifest(version=1,transfer_id="transfer-1",source_authority="legacy",target_authority="learning",
        source_sha="5d879ffce599af7a2eaf9627e9e1c7ea82e9af5a",source_schema="sha256:"+"1"*64,target_schema="sha256:"+"2"*64,
        source_watermark="synthetic-snapshot",scope="representative",source_inventory=inventories,target_shapes=tuple(target_shapes),
        family_evidence=(),capture="warm",fence_id=None,study_timezone="America/New_York",scheduler_identity="synthetic",
        family_inventory=("content","cards_history","position","issued","queue","games","tactics","browser"),unresolved=(),facts=facts)
    digest = record_digest(manifest)
    receipt = StageReceipt(version=1,transfer_id="transfer-1",manifest_digest=digest,batch_id="batch-1",batch_digest=digest,
                           cursor=None,target_generation="generation-1",receipt_id="receipt-1")
    return rows, manifest, receipt


def test_f01_retained_position_atomic_exact_replay_and_negative_cases():
    identity = os.environ.get("TEMPO_LEARNING_TEST_FIXTURE", "")
    if not identity.startswith("tempo-learning-"):
        pytest.fail("requires owned Learning PostgreSQL runner")
    admin = os.environ["TEMPO_LEARNING_TEST_ADMIN"]
    name = "w02a_" + uuid4().hex
    with psycopg.connect(admin, autocommit=True) as root:
        root.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        dsn = make_conninfo(admin, dbname=name)
        apply_migrations(dsn)
        with psycopg.connect(dsn, autocommit=True) as conn:
            with conn.transaction():
                populate(conn)
                rows, manifest, receipt = capture(conn)
            namespace = frozenset(row[0] for row in conn.execute("SELECT (fsrs_card_json::jsonb->>'card_id')::bigint FROM cards WHERE fsrs_card_json IS NOT NULL"))
            assert namespace == frozenset({42})
            # Verify source has the entire original review/invalidations set.
            assert len(rows["reviews"]) == 2 and rows["reviews"][0]["invalidated_at"] is not None
            assert conn.execute("SELECT max(version) FROM tempo_schema_migrations").fetchone() == (59,)
            conn.execute("CREATE SCHEMA target")
            conn.execute("SET search_path TO target")
            conn.execute(TARGET_DDL)
            with pytest.raises(RetainedPositionConflict, match="active transaction"):
                stage_position(conn, manifest, receipt, legacy_fsrs_ids=namespace)
            with pytest.raises(RuntimeError, match="rollback stage"):
                with conn.transaction():
                    stage_position(conn, manifest, receipt, legacy_fsrs_ids=namespace)
                    raise RuntimeError("rollback stage")
            for table in ("retained_position_batches", "retained_position_states", "retained_position_facts"):
                assert conn.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))).fetchone()[0] == 0
            with conn.transaction():
                assert stage_position(conn, manifest, receipt, legacy_fsrs_ids=namespace) == receipt
                assert stage_position(conn, manifest, receipt, legacy_fsrs_ids=namespace) == receipt
                row = read_position(conn, "r", "standard", "exact-fen")
                source = rows["study_position_states"][0]
                assert row[3:10] == tuple(source[n] for n in ("expected_uci","policy_version","owner_epoch","source_digest","version","fsrs_internal_card_id","fsrs_json"))
                assert row[10].isoformat() == source["due_at"] and row[11].isoformat() == source["introduced_at"]
                assert row[12:16] == tuple(source[n] for n in ("donor_card_id","donor_card_revision","donor_fingerprint","donor_selection_policy"))
                captured = conn.execute("SELECT source_key,target_key,original_bytes,original_digest,references_json FROM retained_position_facts").fetchall()
                expected = {(f.source.store, tuple(p.value for p in f.source.primary_key)): f for f in manifest.facts}
                assert len(captured) == len(expected)
                for source_key, target_key, raw, digest, references in captured:
                    key = json.loads(source_key)
                    fact = expected[(key["store"], tuple(p["value"] for p in key["primary_key"]))]
                    assert bytes(raw) == base64.b64decode(fact.original_base64) and digest == fact.original_digest
                    assert json.loads(target_key) == fact.target_key.model_dump(mode="json")
                    assert [json.loads(r) for r in references] == [r.model_dump(mode="json") for r in fact.references]
            with pytest.raises(RetainedPositionConflict, match="immutable batch conflict"):
                with conn.transaction():
                    stage_position(conn, manifest, receipt.model_copy(update={"receipt_id":"other"}), legacy_fsrs_ids=namespace)
            with pytest.raises(RetainedPositionConflict, match="collision"):
                with conn.transaction():
                    stage_position(conn, manifest, receipt, legacy_fsrs_ids=frozenset({42,4503599627370496}))
            # A changed source snapshot under the same transfer/batch is a conflict.
            state_fact = next(f for f in manifest.facts if f.source.store == "study_position_states")
            altered = json.loads(base64.b64decode(state_fact.original_base64)); altered["source_digest"] = "changed"
            altered_raw = json.dumps(altered).encode()
            altered_fact = state_fact.model_copy(update={"original_base64":base64.b64encode(altered_raw).decode(), "original_digest":digest_bytes(altered_raw)})
            altered_facts = tuple(altered_fact if f is state_fact else f for f in manifest.facts)
            altered_inventories = tuple(i.model_copy(update={"expected_digest":facts_digest(tuple(f for f in altered_facts if f.source.store == i.shape.store))}) for i in manifest.source_inventory)
            altered_manifest = SourceManifest.model_validate({**manifest.model_dump(), "facts": altered_facts, "source_inventory":altered_inventories})
            changed_digest = record_digest(altered_manifest)
            changed_receipt = receipt.model_copy(update={"manifest_digest":changed_digest,"batch_digest":changed_digest})
            with pytest.raises(RetainedPositionConflict, match="immutable batch conflict"):
                with conn.transaction():
                    stage_position(conn, altered_manifest, changed_receipt, legacy_fsrs_ids=namespace)
            # Target-only staging did not mutate original source rows.
            conn.execute("SET search_path TO public")
            _, source_after, _ = capture(conn)
            assert source_after == manifest
            conn.execute("SET search_path TO target")
            from tempo_learning_database.adapters.retained_position_store import _state
            state_fact = next(f for f in manifest.facts if f.source.store == "study_position_states")
            malformed = json.loads(base64.b64decode(state_fact.original_base64)); malformed["donor_card_id"] = ""
            raw = json.dumps(malformed).encode()
            bad = state_fact.model_copy(update={"original_base64":base64.b64encode(raw).decode(),"original_digest":digest_bytes(raw)})
            with pytest.raises(RetainedPositionConflict, match="ambiguous"):
                _state(manifest.model_copy(update={"facts":tuple(bad if f is state_fact else f for f in manifest.facts)}), frozenset({42}))
    finally:
        with psycopg.connect(admin, autocommit=True) as root:
            root.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))
