from datetime import datetime, timezone
import json

import pytest

from tempo_game_evidence.providers import AccountImport, _page
from tempo_game_evidence.transport import RemoteFailure, retry_seconds
from .factories import pgn


def config(**changes):
    return AccountImport(workspace_id="fixture",provider="lichess",account="Learner",
                         since=datetime(2026,10,1,tzinfo=timezone.utc),until=datetime(2026,10,2,tzinfo=timezone.utc),**changes)


def test_A_snapshot_version_validation_preserves_provider_json_datetime_roundtrip():
    value=config()
    assert AccountImport.model_validate_json(value.model_dump_json())==value


def test_lichess_boundary_overlap_is_deduplicated_without_skipping_games():
    value=config(page_size=2)
    timestamp=int(value.since.timestamp()*1000)
    games=[{"id":name,"createdAt":timestamp,"pgn":pgn(["e2e4"]).decode()} for name in ("a","b")]
    raw=b"\n".join(json.dumps(game).encode() for game in games)
    records,cursor,complete=_page(value,{"since_ms":timestamp,"seen":[]},raw)
    assert len(records)==2 and not complete
    with pytest.raises(RemoteFailure,match="saturated_timestamp"):
        _page(value,cursor,raw)


def test_lichess_requires_verified_order_and_window():
    value=config()
    with pytest.raises(RemoteFailure,match="unverified_provider"):
        _page(value,{"since_ms":int(value.since.timestamp()*1000),"seen":[]},b'{"createdAt":0}')


def test_retry_after_is_not_truncated_below_provider_delay():
    assert retry_seconds("3600",minimum=60)==3600
    assert retry_seconds(None,minimum=60)==60


def test_chesscom_months_are_bounded_and_half_open():
    value=config().model_copy(update={"provider":"chess.com"})
    raw=json.dumps({"games":[{"uuid":"a","end_time":int(value.since.timestamp()),"pgn":pgn(["e2e4"]).decode()},
                             {"uuid":"b","end_time":int(value.until.timestamp()),"pgn":"ignored"}]}).encode()
    records,cursor,complete=_page(value,{"year":2026,"month":10,"offset":0},raw)
    assert len(records)==1 and records[0][0]=="a" and complete


@pytest.mark.parametrize('since,until',[
    ('2026-10-01T00:00:00+02:00','2026-10-01T01:00:00+02:00'),
    ('2026-09-30T23:00:00-02:00','2026-10-01T00:00:00-02:00'),
    ('2027-01-01T00:00:00+02:00','2027-01-01T01:00:00+02:00'),
])
def test_equivalent_import_windows_are_canonical_utc(since,until):
    from tempo_game_evidence.source import identity
    value=AccountImport(workspace_id='fixture',provider='chess.com',account='Learner',since=datetime.fromisoformat(since),until=datetime.fromisoformat(until))
    canonical=AccountImport(workspace_id='fixture',provider='chess.com',account='Learner',
        since=value.since.astimezone(timezone.utc),until=value.until.astimezone(timezone.utc))
    assert value.since.utcoffset().total_seconds()==0
    assert value.model_dump(mode='json')==canonical.model_dump(mode='json')
    assert identity(value.model_dump(mode='json'))==identity(canonical.model_dump(mode='json'))


@pytest.mark.parametrize('days,accepted',[(366,True),(367,False)])
def test_import_window_duration_and_naive_rejection(days,accepted):
    from datetime import timedelta
    start=datetime(2026,1,1,tzinfo=timezone.utc)
    arguments=dict(workspace_id='fixture',provider='chess.com',account='Learner',since=start,until=start+timedelta(days=days))
    if accepted:
        assert AccountImport(**arguments).since==start
    else:
        with pytest.raises(ValueError,match='366'):
            AccountImport(**arguments)
    arguments.update(since=start.replace(tzinfo=None))
    with pytest.raises(ValueError,match='timezone'):
        AccountImport(**arguments)


def test_retry_after_http_date_is_preserved():
    from email.utils import format_datetime
    from datetime import timedelta
    assert retry_seconds(format_datetime(datetime.now(timezone.utc)+timedelta(hours=1)),minimum=60)>3500
