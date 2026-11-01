"""Separate profile router compositions retain their own supplied operations."""
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.contracts.next_opponent import NextOpponentProfileResponse
from app.games.opponents.http import create_next_opponent_router


def test_next_opponent_routers_keep_supplied_readers_and_http_contract():
    calls = []
    def reader(label):
        def read(speed):
            calls.append((label, speed))
            if speed == 'classical':
                raise HTTPException(503, 'profile unavailable')
            return NextOpponentProfileResponse(availability='unknown', refresh_status='idle',
                                               stale=True, requested_speed=speed, detail=label)
        return read
    apps = [FastAPI(), FastAPI()]
    for app, label in zip(apps, ('first', 'second')):
        app.include_router(create_next_opponent_router(reader(label)))
    first, second = map(TestClient, apps)
    assert first.get('/api/games/next-opponent-profile').json()['detail'] == 'first'
    response = second.get('/api/games/next-opponent-profile?speed=rapid')
    assert response.status_code == 200 and response.json()['requested_speed'] == 'rapid'
    assert response.json()['detail'] == 'second'
    assert first.get('/api/games/next-opponent-profile?speed=bullet').status_code == 422
    assert second.get('/api/games/next-opponent-profile?speed=classical').status_code == 503
    assert calls == [('first', 'auto'), ('second', 'rapid'), ('second', 'classical')]
