"""Sync contracts stay cold and retain identity without a coordinator barrel."""
import ast
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.contracts.transport import game_sync as existing
from app.games.sync import interfaces
from test_public_surface_game_ingestion import cold_import


def test_sync_public_values_keep_exact_existing_contract_identity():
    expected = {"AccountSettings", "ActiveGameFilters", "CompletedGameSyncResult",
        "CompletedProviderSyncResult", "GameProvider", "GameSyncEnqueueResponse",
        "GameSyncJob", "GameSyncJobState", "GameSyncRequest", "GameSyncStatusResponse",
        "ProviderSyncResult", "ProviderSyncState", "ProviderSyncStatus"}
    assert set(interfaces.__all__) == expected
    assert {name for name in vars(interfaces) if not name.startswith("_")} == expected
    for name in expected:
        assert getattr(interfaces, name) is getattr(existing, name)
    assert not hasattr(interfaces, "GameSyncCoordinator")
    assert not hasattr(interfaces, "create_game_sync")
    assert not hasattr(interfaces, "sync_providers")
    assert not hasattr(interfaces, "__getattr__")
    tree = ast.parse(Path(interfaces.__file__).read_text())
    assert not any(isinstance(n, ast.ImportFrom) and any(a.name == "*" for a in n.names)
                   for n in ast.walk(tree))


def test_sync_public_import_does_not_pull_concrete_legacy_worker_provider_or_study():
    cold_import("app.games.sync.interfaces", blocked=("app.main", "app.tasks", "app.product_composition",
        "app.services", "app.study", "app.games.providers", "tempo_legacy_database"))


def test_sync_public_request_and_job_validation_preserve_existing_behavior():
    request = interfaces.GameSyncRequest(lichess_username="explicit", days=3, repair=True)
    assert request.days == 3 and request.repair and request.lichess_username == "explicit"
    with pytest.raises(ValidationError):
        interfaces.GameSyncRequest(days=0)
    row = dict(id="job", status="queued", created_at="2026-10-27", updated_at="2026-10-27")
    assert interfaces.GameSyncJob(**row).result is None
    with pytest.raises(ValidationError, match="Only complete"):
        interfaces.GameSyncJob(**(row | {"status": "complete"}))
