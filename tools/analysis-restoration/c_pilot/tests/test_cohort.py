from datetime import datetime, timezone
import json
import pytest
from tempo_analysis_pilot.cohort import select, export_game


def game(index,provider='chess.com',color='white'):
    return {'provider':provider,'provider_game_id':str(index),'username':'LearnerCC' if provider=='chess.com' else 'LearnerLi',
        'played_at':f'2026-10-{index+1:02d}T12:00:00+00:00','rated':True,'speed':'rapid','result':'1-0','color':color,
        'opening_name':'opening','start_fen':'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1',
        'moves_json':'["e2e4","e7e5"]','game_url':'https://www.chess.com/game/live/123','time_control':'600'}


def choose(games):
    return select(games,accounts={'chess.com':'LearnerCC','lichess':'LearnerLi'},since=datetime(2026,10,1,tzinfo=timezone.utc),until=datetime(2026,11,1,tzinfo=timezone.utc),primary=4,supplemental=2)


def test_C3_real_cohort_selection_is_order_independent_and_deduplicated():
    games=[game(index,provider,color) for index,(provider,color) in enumerate([('chess.com','white'),('chess.com','black'),('lichess','white'),('lichess','black'),('chess.com','white'),('lichess','black')])]
    first,counts=choose(games)
    assert choose(list(reversed(games))+[games[0]])[0]==first
    assert len(first)==6 and counts['eligible_available_games']==6
    assert len({(g['provider'],g['color']) for g,_,_ in first})==4


def test_C3_conflicting_provider_identity_cannot_hide_different_moves():
    first=game(1);other={**first,'moves_json':'["d2d4"]'}
    with pytest.raises(ValueError,match='conflicting_provider_identity'):choose([first,other])


def test_C3_local_pgn_reconstruction_preserves_moves_color_and_unknown_opponent():
    import io,chess.pgn
    record=game(1,color='black');raw=export_game(record);parsed=chess.pgn.read_game(io.StringIO(raw.decode()))
    assert parsed.headers['Black']=='LearnerCC'
    assert parsed.headers['White']=='Unknown opponent'
    assert [move.uci() for move in parsed.mainline_moves()]==['e2e4','e7e5']
    assert parsed.headers['Site']==record['game_url']


def test_C3_illegal_capture_is_rejected_without_fabricating_moves():
    with pytest.raises(ValueError,match='illegal_captured_move'):export_game({**game(1),'moves_json':'["e2e5"]'})


def test_C3_subday_window_is_valid_but_366_days_plus_one_second_is_rejected():
    from datetime import timedelta
    since=datetime(2026,10,1,tzinfo=timezone.utc)
    assert select([],accounts={'chess.com':'LearnerCC'},since=since,until=since+timedelta(hours=1))[1]['selected']==0
    with pytest.raises(ValueError,match='bounded_aware_cohort_required'):
        select([],accounts={'chess.com':'LearnerCC'},since=since,until=since+timedelta(days=366,seconds=1))
