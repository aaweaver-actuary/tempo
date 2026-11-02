"""Real issued Italian branches: saved clean proof and opponent cue, both orders.

Each parameter needs a newly empty task-owned N2 database. Uses the actual
source, donor seed, queue, evidence and review owners; only connection routing
is redirected to the disposable reader. No browser or operator claim.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json

import chess
import psycopg
import pytest
from fsrs import Card

from database.tests.position_study.acceptance import _dsn, _migrate
from tempo_evidence_math.position_learning import Hold, SourceCertificate
from tempo_legacy_database.adapters.postgres_store import PostgresConnection, tempo_row_factory
from tempo_legacy_database.adapters.authored_repertoires.position_study_source import prepare_position_source, fence_position_source
from tempo_legacy_database.adapters.study.position_seed_inventory import DonorFact, InventoryRequest, SourceFacts, read_card_page
from tempo_legacy_database.adapters.study.position_seed import seed_shadow_page
from app.repertoire.interfaces.position_schedule_occurrences import inventory_from_snapshot


LINES = {
    'knight': 'e4 e5 Nf3 Nc6 Bc4 Nf6 Ng5',
    'giuoco': 'e4 e5 Nf3 Nc6 Bc4 Bc5 c3',
}


def _moves(san):
    board = chess.Board()
    moves = []
    for token in san.split():
        move = board.parse_san(token)
        moves.append(move.uci())
        board.push(move)
    return tuple(moves)


def seed_disposable_source(raw, dsn, routes, labels, *, repertoire_id='italian', now=None):
    """Seed two legal authored routes in an empty owned DB and return real owner facts.

    The caller owns the connection, database lifecycle, routing and transactions.
    This uses the real migration, source, reader, namespace and seed owners;
    its direct active-owner update is a synthetic fixture, not operator proof.
    """
    if len(routes) != 2 or len(labels) != 2:
        raise ValueError('two fixture routes required')
    now = now or datetime.now(timezone.utc)
    day = now.date().isoformat()
    _migrate(raw)
    db = PostgresConnection(raw)
    raw.execute('INSERT INTO settings(id) VALUES(1)')
    raw.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES(%s,%s,'n2',%s)", (repertoire_id,repertoire_id+' fixture',now.isoformat()))
    raw.execute("INSERT INTO opening_graph_publications(repertoire_id,generation,state,published_at) VALUES(%s,3,'ready',%s)", (repertoire_id,now.isoformat()))
    physical = {}
    for index, moves in enumerate(routes):
        card, route = f'card-{index}', f'route-{index}'
        raw_moves = json.dumps(moves)
        blob = Card(card_id=42+index,due=now-timedelta(days=2-index)).to_json()
        physical[card] = blob
        raw.execute("INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,start_fen,moves_json,created_at) VALUES(%s,%s,%s,'white',%s,%s,%s)", (route,repertoire_id,labels[index],chess.STARTING_FEN,raw_moves,now.isoformat()))
        raw.execute("""INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,fsrs_card_json,introduced_at,revision,content_type,trained_color)
            VALUES(%s,%s,'prefix',%s,%s,'learning',%s,%s,%s,1,'opening','white')""", (card,repertoire_id,chess.STARTING_FEN,raw_moves,day,blob,(now-timedelta(days=3)).isoformat()))
        raw.execute('INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES(%s,%s)', (repertoire_id,card))
        raw.execute("""INSERT INTO opening_graph_steps(repertoire_id,generation,line_id,decision_index,card_id,decision_fen_key,starting_fen,moves_json,trained_color)
            VALUES(%s,3,%s,0,%s,%s,%s,%s,'white')""", (repertoire_id,route,card,chess.STARTING_FEN,chess.STARTING_FEN,raw_moves))
    raw.execute("""INSERT INTO repertoire_integrity_state(repertoire_id,status,scan_status,scan_generation,checked_at,scan_completed_sources,scan_total_sources)
        VALUES(%s,'clean','idle','scan:2',%s,2,2)""", (repertoire_id,now.isoformat()))
    for ident,kind,generation,payload in [('graph','opening_graph_rebuild',3,{}),('scan','integrity_scan',2,{'repertoire_id':repertoire_id,'graph_generation':3})]:
        raw.execute("""INSERT INTO background_tasks(id,kind,deduplication_key,generation,state,payload_json,next_attempt_at,created_at,updated_at)
            VALUES(%s,%s,%s,%s,'complete',%s,%s,%s,%s)""", (ident,kind,repertoire_id,generation,json.dumps(payload),now.isoformat(),now.isoformat(),now.isoformat()))
    raw.execute('GRANT USAGE ON SCHEMA public TO tempo_reader,tempo_writer')
    raw.execute('GRANT SELECT ON ALL TABLES IN SCHEMA public TO tempo_reader')
    raw.execute('GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA public TO tempo_writer')
    raw.execute('GRANT USAGE,SELECT,UPDATE ON ALL SEQUENCES IN SCHEMA public TO tempo_writer')
    raw.commit()
    source = prepare_position_source(db,repertoire_id)
    assert not isinstance(source, Hold), source
    assert fence_position_source(db,source) is None
    membership = source.snapshot.membership
    cert = SourceCertificate(membership.snapshot_id,membership.source_revision,membership.digest,True,True)
    inventory = inventory_from_snapshot(source.snapshot,source_certificate=cert)
    assert not isinstance(inventory, Hold), inventory
    raw.commit()
    with psycopg.connect(dsn,user='tempo_reader',row_factory=tempo_row_factory) as reader_raw:
        reader_raw.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        rows = read_card_page(PostgresConnection(reader_raw,read_only=True),candidate_card_ids=('card-0','card-1'))
        donors = tuple(DonorFact(**(dict(row) | {'owner_repertoire_ids':tuple(row['owner_repertoire_ids']),
            'archived':bool(row['archived']), 'pending_validation':bool(row['pending_validation']),
            'integrity_blocked':bool(row['integrity_blocked']), 'deleted':bool(row['deleted'])})) for row in rows)
    request = InventoryRequest((SourceFacts(source.snapshot,inventory),),donors)
    for _ in range(10):
        raw.execute('BEGIN')
        seed = seed_shadow_page(db,source,request,epoch=repertoire_id+'-epoch',now=now,page_size=100)
        raw.commit()
        if seed['stage'] == 'complete': break
    assert seed['stage'] == 'complete' and seed['held'] == 0
    raw.execute("UPDATE study_position_owners SET mode='active' WHERE repertoire_id=%s", (repertoire_id,))
    raw.execute('INSERT INTO daily_queue_days(queue_date,seed,membership_hash,generated_at) VALUES(%s,1,%s,%s)', (day,repertoire_id,now.isoformat()))
    raw.commit()
    return db, source, physical, day


@pytest.mark.parametrize('first_line', ['knight', 'giuoco'], ids=['knight-first', 'giuoco-first'])
def test_real_clean_italian_branches_issue_opponent_cue(monkeypatch, first_line):
    from app.contracts.opening_evidence import OpeningEvidenceCheckpoint
    from app.queue_commands import issue_queue_head
    from app.services import postgres_opening_evidence as evidence, learning_review_adapter
    from app.services.opening_decision_evidence import decision_manifest
    from app.services.position_queue_admission import prepare_due, admit_due
    from app.services.queue_next_card import prepare_queue_head
    from tempo_legacy_database import connections
    from tempo_legacy_database.adapters.opening_evidence import storage as evidence_storage
    from tempo_legacy_database.adapters.study.position_issued_plans import read_clean_presentation_proof
    from tempo_legacy_database.adapters.study.recombination import prepare_recombination_review

    dsn = _dsn()
    now = datetime.now(timezone.utc)
    day = now.date().isoformat()
    other_line = 'giuoco' if first_line == 'knight' else 'knight'
    ordered = (first_line, other_line)
    routes = [_moves(LINES[name]) for name in ordered]
    assert routes[0][:5] == routes[1][:5] and routes[0][5] != routes[1][5]

    @contextmanager
    def reader(*args, **kwargs):
        with psycopg.connect(dsn, user='tempo_reader', row_factory=tempo_row_factory) as conn:
            yield PostgresConnection(conn, read_only=True)

    monkeypatch.setenv('TEMPO_DATABASE_READ_URL', dsn)
    monkeypatch.setenv('TEMPO_DATABASE_WRITE_URL', dsn)
    monkeypatch.setattr(connections, 'read_connection', reader)
    monkeypatch.setattr(connections, 'background_read_connection', reader)
    monkeypatch.setattr(evidence_storage, 'read_connection', reader)
    monkeypatch.setattr(learning_review_adapter, 'read_connection', reader)
    with psycopg.connect(dsn, row_factory=tempo_row_factory) as raw:
        db, _, physical, day = seed_disposable_source(raw, dsn, routes, ordered, now=now)

        def issue():
            proposal = prepare_due(db,day,{})
            assert isinstance(proposal,dict) and 'candidate' in proposal, proposal
            assert admit_due(db,day,proposal) is None
            raw.commit()
            row = raw.execute("SELECT id,card_id FROM daily_queue WHERE status='queued' ORDER BY position,id LIMIT 1").fetchone()
            assert row is not None
            entry,card = row['id'],row['card_id']
            raw.commit()
            envelope = {'queue_date':day,'entry_id':entry,'card_id':card,'expected_revision':1}
            prepared = prepare_queue_head(envelope)
            assert prepared.mode == 'position', prepared
            result = issue_queue_head(db,prepared)
            assert result['issued'], result
            raw.commit()
            replay = prepare_queue_head(envelope)
            assert replay.mode == 'replay' and replay.proposal.to_wire() == result['position_presentation']
            return replay.proposal,entry,card

        first,entry,card = issue()
        assert first.route_id == 'route-0' and card == 'card-0'
        scope = raw.execute("""SELECT s.*,q.effective_trained_color FROM opening_evidence_presentations s
            JOIN opening_evidence_queue_contexts q ON q.presentation_snapshot_id=s.id
            WHERE q.queue_entry_id=%s AND q.repertoire_id='italian'""", (entry,)).fetchone()
        assert scope is not None
        manifest = evidence.issued_position_manifest(first,decision_manifest(evidence._manifest_snapshot(dict(scope)),'italian'))
        assert len(manifest['decisions']) == 4
        stamp = datetime.now(timezone.utc).isoformat()
        events = [dict(sequence=i+1,decision_index=i,decision_id=d['decision_id'],expected_uci=d['expected_uci'],
            kind='first_response',observed_at=stamp,response_uci=d['expected_uci'],disposition='expected')
            for i,d in enumerate(manifest['decisions'])]
        completion = dict(attempt_id=first.attempt_id,manifest=manifest,origin_queue_entry_id=entry,
            queue_entry_id=entry,started_at=stamp,study_timezone='UTC',source='live',events=events,
            terminal={'state':'complete','final_sequence':len(events),'ended_at':stamp})
        raw.commit()
        validated = OpeningEvidenceCheckpoint.model_validate(completion)
        prepared_manifest = evidence.prepare_checkpoint(validated)
        with reader() as read_db:
            position_checkpoint = evidence.prepare_position_checkpoint(read_db,validated)
        payload = {'card_id':card,'review':{'outcome':'correct','guided':False,'queue_entry_id':entry,
            'expected_revision':1,'attempt_id':first.attempt_id,'opening_evidence_completion':completion}}
        payload = prepare_recombination_review(payload,read_connection=reader)
        payload.update(prepared_manifest=prepared_manifest,prepared_position_checkpoint=position_checkpoint)
        raw.execute('BEGIN')
        saved = learning_review_adapter.submit_legacy_review(db,payload)
        raw.commit()
        assert saved['persisted'] and saved['queue_entry_id'] == entry
        proof = read_clean_presentation_proof(db,first.attempt_id)
        assert not isinstance(proof,Hold), proof
        assert proof.attempt_id == first.attempt_id
        before = [tuple(row) for row in raw.execute('SELECT effect_id,grade FROM study_position_grades ORDER BY effect_id').fetchall()]
        assert len(before) == 4 and all(row[1] == 'good' for row in before)
        raw.commit()
        second,second_entry,second_card = issue()
        assert second.route_id == 'route-1' and second_card == 'card-1'
        assert second_entry != entry and second.attempt_id != first.attempt_id
        assert second.skipped_prefix_proof_ids[0] == first.attempt_id
        assert second.start_ply == second.opponent_cue_ply == 5
        assert second.opponent_cue_uci == routes[1][5] == second.moves_uci[0]
        assert second.opponent_cue_uci != routes[0][5]
        board = chess.Board()
        for move in routes[1][:5]: board.push_uci(move)
        assert chess.Board(second.starting_fen) == board
        assert chess.Move.from_uci(second.opponent_cue_uci) in board.legal_moves
        board.push_uci(second.opponent_cue_uci)
        assert second.decision_slots[0].original_ply == 6
        assert chess.Board(second.decision_slots[0].fen) == board
        assert [tuple(row) for row in raw.execute('SELECT effect_id,grade FROM study_position_grades ORDER BY effect_id').fetchall()] == before
        assert {row['id']:row['fsrs_card_json'] for row in raw.execute('SELECT id,fsrs_card_json FROM cards').fetchall()} == physical
        raw.commit()
