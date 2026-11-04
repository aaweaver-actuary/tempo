"""Bounded Lichess history when the provider sends more rows than requested."""
import json
import httpx
import pytest

from test_product_continuous_sync import START, PGN, configuration, provider_fetch


@pytest.mark.parametrize("same_timestamp", [False, True])
def test_oversized_sorted_provider_response_advances_without_loss(same_timestamp):
    rows = [dict(id=f"game{i:04}", createdAt=START*1000+(0 if same_timestamp else i),
        speed="rapid", rated=True, pgn=PGN) for i in range(205)]
    def provider(request):
        selected=[r for r in rows if int(request.url.params["since"]) <= r["createdAt"] < int(request.url.params["until"])]
        return httpx.Response(200,content="\n".join(json.dumps(r) for r in selected),
            headers={"Content-Type":"application/x-ndjson"})
    continuation=None
    found=[]
    for _ in range(10):
        page=provider_fetch("lichess",provider,configuration().window,continuation)
        found.extend(r.source_game_id for r in page.records)
        if page.complete:
            break
        assert page.continuation != continuation
        continuation=page.continuation
    else:
        pytest.fail("oversized response failed to make bounded progress")
    assert len(found)==len(set(found))==205
    assert set(found)=={row["id"] for row in rows}


def test_oversized_response_with_out_of_order_tail_fails_closed():
    from app.games.providers.lichess_acquisition import LichessAcquisitionError
    rows=[dict(id=f"game{i:04}", createdAt=START*1000+i,
        speed="rapid",rated=True,pgn=PGN) for i in range(102)]
    rows[-1]["createdAt"]=START*1000
    def provider(request):
        return httpx.Response(200,content="\n".join(json.dumps(r) for r in rows),
            headers={"Content-Type":"application/x-ndjson"})
    with pytest.raises(LichessAcquisitionError,match="invalid_response"):
        provider_fetch("lichess",provider,configuration().window)


def test_oversized_response_with_conflicting_tail_identity_fails_closed():
    from app.games.providers.lichess_acquisition import LichessAcquisitionError
    rows=[dict(id=f"game{i:04}",createdAt=START*1000+i,
        speed="rapid",rated=True,pgn=PGN) for i in range(102)]
    rows[-1]["id"]=rows[0]["id"]
    def provider(request):
        return httpx.Response(200,content="\n".join(json.dumps(r) for r in rows),
            headers={"Content-Type":"application/x-ndjson"})
    with pytest.raises(LichessAcquisitionError,match="changed_boundary"):
        provider_fetch("lichess",provider,configuration().window)


def test_oversized_filtered_rows_are_bounded_and_accounted():
    rows=[dict(id=f"game{i:04}",createdAt=START*1000+i,
        speed="classical",rated=True,pgn=PGN) for i in range(205)]
    def provider(request):
        selected=[r for r in rows if int(request.url.params["since"]) <= r["createdAt"] < int(request.url.params["until"])]
        return httpx.Response(200,content="\n".join(json.dumps(r) for r in selected),
            headers={"Content-Type":"application/x-ndjson"})
    continuation=None
    rejected=[]
    for _ in range(10):
        page=provider_fetch("lichess",provider,configuration().window,continuation)
        assert not page.records and len(page.rejections)<=100
        rejected.extend(item.source_game_id for item in page.rejections)
        if page.complete: break
        assert page.continuation != continuation
        continuation=page.continuation
    else: pytest.fail("filtered rows did not make bounded progress")
    assert len(rejected)==len(set(rejected))==205
