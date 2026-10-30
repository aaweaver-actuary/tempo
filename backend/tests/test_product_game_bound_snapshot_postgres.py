"""Complete game-bound facts and actual short-writer freshness, on owned PostgreSQL."""
from dataclasses import dataclass
import uuid

import chess
import pytest

from tempo_legacy_database.adapters.authored_repertoires import product_snapshot as storage
from test_product_game_lapse_postgres import owned_postgres


@dataclass
class Scope:
    transaction: object
    repertoire: str
    route: str
    other_route: str
    card: str
    other_card: str
    missing_card: str

    def authored(self):
        with self.transaction() as database:
            return storage.read_authored_scope(database,self.repertoire)

    def candidate(self):
        header=self.authored()['repertoire']
        with self.transaction() as database:
            return storage.read_candidate_bindings(database,repertoire_id=self.repertoire,
                expected_header=header,route_ids=(self.route,))


@pytest.fixture
def scope(owned_postgres):
    identity='bound-'+uuid.uuid4().hex
    result=Scope(owned_postgres,identity,identity+'-line',identity+'-other-line',
        identity+'-card',identity+'-other-card',identity+'-absent-card')
    with owned_postgres() as database:
        database.execute_native("INSERT INTO repertoires(id,name,source_name,created_at) VALUES(%s,'Synthetic','fixture','2026-10-30')",(identity,))
        for card in (result.card,result.other_card):
            database.execute_native("""INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,trained_color,due_date)
                VALUES(%s,%s,'prefix',%s,'["e2e4","e7e5"]','white','2026-10-30')""",(card,identity,chess.STARTING_FEN))
            database.execute_native('INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES(%s,%s)',(identity,card))
        for route in (result.route,result.other_route):
            database.execute_native("""INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,start_fen,moves_json,created_at)
                VALUES(%s,%s,'Synthetic route','white',%s,'["e2e4","e7e5"]','2026-10-30')""",(route,identity,chess.STARTING_FEN))
        database.execute_native("INSERT INTO opening_graph_publications(repertoire_id,generation,state,published_at) VALUES(%s,1,'ready','2026-10-30')",(identity,))
        for route,index,card in ((result.route,0,result.card),(result.route,1,result.missing_card),(result.other_route,0,result.other_card)):
            # The actual complete relation cannot depend on these deliberately
            # missing/disagreeing discovery hints.
            database.execute_native("""INSERT INTO opening_graph_steps(repertoire_id,generation,line_id,decision_index,
                card_id,decision_fen_key,starting_fen,moves_json,trained_color,decision_fen_keys_json)
                VALUES(%s,1,%s,%s,%s,'incorrect-hint',%s,'["e2e4","e7e5"]','white','[]')""",
                (identity,route,index,card,chess.STARTING_FEN))
    yield result
    with owned_postgres() as database:
        database.execute_native('DELETE FROM repertoires WHERE id=%s',(identity,))
        database.execute_native('DELETE FROM cards WHERE id=ANY(%s)',([result.card,result.other_card,result.missing_card],))
        database.execute_native('DELETE FROM deleted_cards WHERE card_id=ANY(%s)',([result.card,result.other_card,result.missing_card],))


def test_issue_recovery_complete_authored_scope_and_all_route_bindings_ignore_fen_hints(scope):
    with scope.transaction() as database:
        full=storage.read_repertoire(database,scope.repertoire)
        database.execute_native("SET LOCAL transaction_timeout='250ms'")
        database.execute_native("SET LOCAL lock_timeout='25ms'")
        authored=storage.read_authored_scope(database,scope.repertoire)
    assert authored['repertoire']==full['repertoire']
    assert authored['lines']==full['lines']
    assert (authored['card_count'],authored['membership_count'],authored['graph_count'])==(2,2,3)
    assert 'cards' not in authored and 'graphs' not in authored
    candidate=scope.candidate()
    assert candidate['route_ids']==[scope.route]
    assert candidate['graphs']==[row for row in full['graphs'] if row['line_id']==scope.route]
    assert {row['id'] for row in candidate['cards']}=={scope.card}
    assert {row['card_id'] for row in candidate['memberships']}=={scope.card}
    assert scope.missing_card in {row['card_id'] for row in candidate['graphs']}
    with scope.transaction() as database:
        storage.fence_candidate_bindings(database,candidate)


@pytest.mark.parametrize('mutation', ['add-graph','remove-graph','change-graph','introduce-card','revision-card',
    'pending-card','supersede-card','remove-card','add-missing-card','remove-membership','add-membership','delete-card','block-card'])
def test_issue_recovery_candidate_fence_rejects_every_changed_complete_relation(scope,mutation):
    expected=scope.candidate()
    with scope.transaction() as database:
        if mutation=='add-graph':
            database.execute_native("""INSERT INTO opening_graph_steps(repertoire_id,generation,line_id,decision_index,card_id,
                decision_fen_key,starting_fen,moves_json,trained_color) VALUES(%s,1,%s,2,%s,'irrelevant',%s,'["e2e4"]','white')""",
                (scope.repertoire,scope.route,scope.card,chess.STARTING_FEN))
        elif mutation=='remove-graph':
            database.execute_native('DELETE FROM opening_graph_steps WHERE line_id=%s AND decision_index=1',(scope.route,))
        elif mutation=='change-graph':
            database.execute_native("UPDATE opening_graph_steps SET moves_json='[\"d2d4\"]' WHERE line_id=%s AND decision_index=0",(scope.route,))
        elif mutation in {'introduce-card','revision-card','pending-card','supersede-card'}:
            expression={'introduce-card':"introduced_at='2026-10-30'",'revision-card':'revision=revision+1',
                'pending-card':'pending_validation=1','supersede-card':"superseded_by='synthetic-successor'"}[mutation]
            database.execute_native('UPDATE cards SET '+expression+' WHERE id=%s',(scope.card,))
        elif mutation=='remove-card':
            database.execute_native('DELETE FROM cards WHERE id=%s',(scope.card,))
        elif mutation=='add-missing-card':
            database.execute_native("""INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,trained_color,due_date,canonical_route_source)
                VALUES(%s,%s,'prefix',%s,'["e2e4"]','white','2026-10-30',0)""",(scope.missing_card,scope.repertoire,chess.STARTING_FEN))
        elif mutation=='remove-membership':
            database.execute_native('DELETE FROM repertoire_cards WHERE repertoire_id=%s AND card_id=%s',(scope.repertoire,scope.card))
        elif mutation=='add-membership':
            database.execute_native("""INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,trained_color,due_date,canonical_route_source)
                VALUES(%s,'white','prefix',%s,'["e2e4"]','white','2026-10-30',0)""",(scope.missing_card,chess.STARTING_FEN))
            database.execute_native('INSERT INTO repertoire_cards(repertoire_id,card_id,canonical_route_source) VALUES(%s,%s,0)',(scope.repertoire,scope.missing_card))
        elif mutation=='delete-card':
            database.execute_native("INSERT INTO deleted_cards(card_id,deleted_at) VALUES(%s,'2026-10-30')",(scope.card,))
        elif mutation=='block-card':
            database.execute_native("""INSERT INTO repertoire_integrity_issues(id,repertoire_id,kind,signature,moves_json,sources_json,created_at,updated_at)
                VALUES(%s,%s,'invalid_source','synthetic','[]','[]','2026-10-30','2026-10-30')""",(scope.repertoire+'-issue',scope.repertoire))
            database.execute_native("""INSERT INTO repertoire_integrity_card_blocks(repertoire_id,card_id,issue_id,scan_generation,published_at)
                VALUES(%s,%s,%s,'synthetic','2026-10-30')""",(scope.repertoire,scope.card,scope.repertoire+'-issue'))
    with pytest.raises(ValueError,match='first_departure_(candidate_bindings|selected_publication)_changed'):
        with scope.transaction() as database:
            storage.fence_candidate_bindings(database,expected)


def test_game_bound_reads_keep_full_source_bounds_and_reject_unproved_route(scope,monkeypatch):
    header=scope.authored()['repertoire']
    with pytest.raises(ValueError,match='first_departure_candidate_routes_changed'):
        with scope.transaction() as database:
            storage.read_candidate_bindings(database,repertoire_id=scope.repertoire,
                expected_header=header,route_ids=('not-an-authored-route',))
    monkeypatch.setattr(storage,'MAX_SOURCE_ROWS',1)
    with pytest.raises(ValueError,match='repertoire_snapshot_exceeds_bound'):
        scope.authored()
    with pytest.raises(ValueError,match='repertoire_snapshot_exceeds_bound'):
        with scope.transaction() as database:
            storage.read_candidate_bindings(database,repertoire_id=scope.repertoire,
                expected_header=header,route_ids=(scope.route,))


def test_game_bound_candidate_reader_keeps_prior_removal_of_graph_count_ceiling():
    # This port check uses a large reported cardinality without allocating an
    # artificial100k-row database. Native cases above own complete row equality;
    # the established full snapshot regression owns redundant-use projection.
    header=dict(id='rep',scope_source_revision=1,canonical_prefix_revision=0,
        canonical_prefix_moves_json='[]',graph_generation=1,graph_state='ready',workspace_id='fixture')
    facts=dict(repertoire=header,route_ids=['route'],cards=[],memberships=[],graphs=[])
    document=dict(exists=True,bounded=True,facts=facts,requested_line_count=1,
        candidate_graph_count=100001)
    class Reader:
        def execute_native(self,statement,parameters):
            assert parameters==('rep',10000,65536,['route'])
            # Source/card/membership sentinels remain in their original CTEs;
            # complete candidate graphs gain no separate truncating LIMIT.
            graph_relation=statement.split('candidate_graphs AS MATERIALIZED (',1)[1].split('), candidate_cards',1)[0]
            assert 'LIMIT' not in graph_relation
            return self
        def fetchone(self):
            return {'snapshot':document}
    assert storage.read_candidate_bindings(Reader(),repertoire_id='rep',
        expected_header=header,route_ids=('route',)) is facts


def test_game_bound_reader_does_not_hide_oversized_non_candidate_source(scope):
    header=scope.authored()['repertoire']
    with scope.transaction() as database:
        # This unrelated physical source is not returned for the chosen route,
        # but the original full-source raw-row bound remains in force.
        database.execute_native('UPDATE cards SET moves_json=%s WHERE id=%s',('x'*65537,scope.other_card))
    for read in (
        lambda database:storage.read_authored_scope(database,scope.repertoire),
        lambda database:storage.read_candidate_bindings(database,repertoire_id=scope.repertoire,
            expected_header=header,route_ids=(scope.route,)),
    ):
        with pytest.raises(ValueError,match='repertoire_snapshot_exceeds_bound'):
            with scope.transaction() as database:
                read(database)
