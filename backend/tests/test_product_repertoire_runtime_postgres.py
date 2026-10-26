"""Actual C owner composition on the existing capped disposable PostgreSQL.

No fake current reader, eligibility, publisher or Study effect. Legacy conflict
rows are task-only fixtures. This is a native transaction proof; normal serving
hook adoption, registered HTTP and browser acceptance remain separate.
"""
from dataclasses import dataclass
import json
from pathlib import Path
from uuid import uuid4

import chess
import pytest

from app.contracts.product_v02.analysis import Proposal, ProposalDecision
from app.product_composition.repertoire import audit_projection
from app.product_composition.repertoire_runtime import current_snapshot, retained_changes_factory
from app.product_composition.repertoire_study import (
    current_study_block, reconcile_after_publication, synchronize_current_conflicts,
)
from app.repertoire.changes.retained import current_card_definitions
from tempo_legacy_database.adapters.authored_repertoires.product_changes import SCHEMA_SQL
from test_product_game_lapse import MemoryStore, NOW
from test_product_game_lapse_postgres import owned_postgres

ROOT=Path(__file__).resolve().parents[2]


@pytest.fixture(scope='module')
def c_database(owned_postgres):
    if not (ROOT/'database/tempo_legacy_database/ddl/migrations/054_product_repertoire_review.sql').exists():
        with owned_postgres() as database:
            database.execute_native(SCHEMA_SQL)
            database.execute_native((ROOT/'database/tempo_legacy_database/ddl/study_conflicts.sql').read_text())
    return owned_postgres


@dataclass
class Case:
    transaction: object
    repertoire: str
    winner: str
    loser: str
    affected: int
    unrelated: int


@pytest.fixture
def case(c_database,monkeypatch):
    identity=uuid4().hex
    rep,other=identity+'-rep',identity+'-other'
    winner,loser=identity+'-e4',identity+'-d4'
    with c_database() as database:
        # The retained legacy column helper selects its dialect from this
        # configured flag, even though all operations borrow the actual writer.
        # Bind it to this disposable connection, never an external/live DSN.
        monkeypatch.setenv('TEMPO_DATABASE_WRITE_URL',database.raw.info.dsn)
        for repertoire in (rep,other):
            database.execute_native("INSERT INTO repertoires(id,name,source_name,created_at) VALUES(%s,'C synthetic','fixture',%s)",(repertoire,NOW.isoformat()))
        for card,move in ((winner,'e2e4'),(loser,'d2d4')):
            database.execute_native("""INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,start_fen,moves_json,created_at)
                VALUES(%s,%s,'C legacy source','white',%s,%s,%s)""",(card,rep,chess.STARTING_FEN,json.dumps([move]),NOW.isoformat()))
            database.execute_native("""INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,trained_color,
                state,due_date,introduced_at,revision,interval_days,fsrs_card_json,stability,content_type)
                VALUES(%s,%s,'prefix',%s,%s,'white','learning',%s,%s,1,20,%s,20,'opening')""",
                (card,rep,chess.STARTING_FEN,json.dumps([move]),NOW.date().isoformat(),'2026-10-01T00:00:00Z',MemoryStore().card.fsrs_card_json))
            database.execute_native('INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES(%s,%s)',(rep,card))
            database.execute_native("""INSERT INTO opening_graph_steps(repertoire_id,generation,line_id,decision_index,
                card_id,decision_fen_key,starting_fen,moves_json,trained_color,segment_kind,first_decision_index,
                last_decision_index,decision_fen_keys_json) VALUES(%s,1,%s,0,%s,%s,%s,%s,'white','prefix',0,0,'[]')""",
                (rep,card,card,' '.join(chess.STARTING_FEN.split()[:4]),chess.STARTING_FEN,json.dumps([move])))
        database.execute_native("INSERT INTO opening_graph_publications(repertoire_id,generation,state,published_at) VALUES(%s,1,'ready',%s)",(rep,NOW.isoformat()))
        database.execute_native('INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES(%s,%s)',(other,winner))
        entries=[]
        for repertoire,cycle,position,attempt,failed in ((rep,0,1,'hinted',1),(other,1,0,'clean',0)):
            entry=database.execute_native("""INSERT INTO daily_queue(queue_date,card_id,cycle,position,
                admission_repertoire_id,admission_kind,attempt_state,attempt_failed)
                VALUES(%s,%s,%s,%s,%s,'review',%s,%s) RETURNING id""",
                (NOW.date().isoformat(),winner,cycle,position,repertoire,attempt,failed)).fetchone()[0]
            database.execute_native('UPDATE queue_attempt_origins SET issued_as_head=1 WHERE queue_entry_id=%s',(entry,))
            entries.append(entry)
        database.execute_native("""INSERT INTO reviews(card_id,rating,reviewed_at,previous_interval,next_interval,source_kind,source_ref)
            VALUES(%s,'correct',%s,10,20,'study',%s)""",(winner,'2026-10-25T12:00:00Z',identity+'-prior'))
    return Case(c_database,rep,winner,loser,*entries)


def learning(database,case):
    cards=tuple(tuple(row) for row in database.execute_native("""SELECT id,revision,state,due_date,
        introduced_at,interval_days,fsrs_card_json,stability FROM cards WHERE id=ANY(%s) ORDER BY id""",
        ([case.winner,case.loser],)).fetchall())
    reviews=tuple(tuple(row) for row in database.execute_native(
        'SELECT * FROM reviews WHERE card_id=ANY(%s) ORDER BY id',([case.winner,case.loser],)).fetchall())
    return cards,reviews


def queue(database,identity):
    return dict(database.execute_native('SELECT * FROM daily_queue WHERE id=%s',(identity,)).fetchone())


def decision(proposal):
    return ProposalDecision(proposal_id=proposal.proposal_id,expected_proposal_revision=proposal.proposal_revision,
        expected_source_revision=proposal.source_revision,display_digest=proposal.display_digest,
        action='accept',edited_proposal=None)


def preview(database,case):
    snapshot=current_snapshot(database,case.repertoire)
    view=audit_projection(snapshot,card_definitions=current_card_definitions(database,case.repertoire))
    choice=next(item for item in view['conflicts'][0]['choices'] if item['move_uci']=='e2e4')
    proposal=Proposal.model_validate_json(json.dumps(choice['proposal']))
    owner=retained_changes_factory(after_publication=reconcile_after_publication)(database,case.repertoire)
    assert owner.preview(proposal).proposal==proposal
    return owner,proposal


def test_c_actual_conflict_scope_readback_and_canonical_approval_preserve_history_and_unrelated_issued_head(case):
    with case.transaction() as database:
        baseline=learning(database,case)
        unrelated=queue(database,case.unrelated)
        receipt=synchronize_current_conflicts(database,case.repertoire)
        assert receipt['status']=='applied'
        assert receipt['receipts'][0]['suspended_queue_ids']==(case.affected,)
        assert learning(database,case)==baseline and queue(database,case.unrelated)==unrelated
        assert queue(database,case.affected)['status']=='blocked'
        assert synchronize_current_conflicts(database,case.repertoire)==receipt
    with case.transaction() as database:
        snapshot=current_snapshot(database,case.repertoire)
        member=snapshot.membership
        assert current_study_block(database,case.repertoire,(case.winner,case.loser),
            source_revision=member.source_revision,revision=member.revision,digest=member.digest)=='blocked'
        assert current_study_block(database,case.repertoire,(case.winner,),
            source_revision=member.source_revision,revision=member.revision,digest='0'*64) is None
        owner,proposal=preview(database,case)
        assert len(proposal.card_changes)==1 and proposal.card_changes[0].presentation_id==case.loser
        accepted=owner.decide(decision(proposal))
        assert accepted.status=='accepted' and owner.decide(decision(proposal))==accepted
        assert not audit_projection(current_snapshot(database,case.repertoire))['conflicts']
        assert learning(database,case)==baseline and queue(database,case.unrelated)==unrelated
        assert database.execute_native('SELECT count(*) FROM product_repertoire_change_receipts WHERE repertoire_id=%s',(case.repertoire,)).fetchone()[0]==1


def test_c_actual_study_callback_failure_rolls_back_source_and_acceptance_receipt(case):
    with case.transaction() as database:
        _,proposal=preview(database,case)
        original=current_snapshot(database,case.repertoire).membership
    def fail(database,repertoire_id,receipt):
        reconcile_after_publication(database,repertoire_id,receipt)
        raise RuntimeError('C disposable callback failure')
    with pytest.raises(RuntimeError,match='C disposable callback failure'):
        with case.transaction() as database:
            owner=retained_changes_factory(after_publication=fail)(database,case.repertoire)
            owner.decide(decision(proposal))
    with case.transaction() as database:
        current=current_snapshot(database,case.repertoire).membership
        assert (current.revision,current.digest,current.source_revision)==(original.revision,original.digest,original.source_revision)
        assert database.execute_native('SELECT count(*) FROM product_repertoire_change_receipts WHERE repertoire_id=%s',(case.repertoire,)).fetchone()[0]==0
        assert len(audit_projection(current_snapshot(database,case.repertoire))['conflicts'])==1
