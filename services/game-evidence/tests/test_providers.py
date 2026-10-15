from datetime import datetime, timezone
import json

import pytest

from tempo_game_evidence.providers import AccountImport, _page
from tempo_game_evidence.transport import RemoteFailure, retry_seconds
from .factories import pgn


def config(**changes):
    return AccountImport(workspace_id="fixture",provider="lichess",account="Learner",
                         since=datetime(2026,10,1,tzinfo=timezone.utc),until=datetime(2026,10,2,tzinfo=timezone.utc),**changes)


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
