import threading

import pytest
from tempo_storage import connections as database

from app.services.activity_gate import ApplicationActivityGate, BackgroundContractError

START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


def test_foreground_review_preempts_each_background_database_slice():
    gate = ApplicationActivityGate()
    section_started = threading.Event()
    second_started = threading.Event()
    release = threading.Event()

    def background_slice():
        with gate.background_job("test", "slice"):
            with gate.background_database_section():
                section_started.set()
                assert release.wait(timeout=2)
            with gate.background_database_section():
                second_started.set()

    worker = threading.Thread(target=background_slice)
    worker.start()
    assert section_started.wait(timeout=1)
    with gate.foreground():
        release.set()
        assert not second_started.wait(timeout=0.1)
    worker.join(timeout=2)
    assert not worker.is_alive()
    assert second_started.is_set()


def test_background_connection_contract_rejects_foreground_connection():
    gate = ApplicationActivityGate()
    with gate.background_job("test", "contract"):
        with pytest.raises(BackgroundContractError):
            with database.connection():
                pass
