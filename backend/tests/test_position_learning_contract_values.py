from dataclasses import FrozenInstanceError
from datetime import date, datetime, timezone

import pytest

from app.study.position_learning.contracts import (
    DueAtIssuance,
    FirstResponse,
    PositionDefinition,
    PositionKey,
    SourceCertificate,
)

START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq -"
NOW = datetime(2026, 11, 2, tzinfo=timezone.utc)


def test_position_identity_is_exact_legal_and_separate_from_answer():
    key = PositionKey("repertoire", START)
    assert (
        PositionDefinition(key, "e2e4", "v1").key
        == PositionDefinition(key, "d2d4", "v2").key
    )
    assert key != PositionKey("other", START)
    for invalid in (
        START + " 0 1",
        START.replace("KQkq", "qkQK"),
        "8/8/8/8/8/8/8/8 w - -",
        START.replace(" w ", " x "),
    ):
        with pytest.raises(ValueError):
            PositionKey("repertoire", invalid)
    with pytest.raises(FrozenInstanceError):
        key.position = START


def test_detached_witness_and_response_require_identity_and_aware_times():
    definition = PositionDefinition(PositionKey("r", START), "e2e4", "v1")
    witness = DueAtIssuance(definition, False, date(2026, 11, 2), "epoch", 1, NOW)
    response = FirstResponse("attempt", 0, definition, "e2e4", False, NOW, "event")
    assert not witness.due and response.correct
    with pytest.raises(ValueError):
        DueAtIssuance(
            definition, True, date(2026, 11, 2), "epoch", 1, NOW.replace(tzinfo=None)
        )
    with pytest.raises(ValueError):
        FirstResponse("attempt", 0, definition, None, False, NOW, "event")
    with pytest.raises(ValueError):
        SourceCertificate("", "revision", "digest", True, True)


def test_backend_reexports_the_standalone_contract_identity():
    from tempo_evidence_math.position_learning import PositionKey as SharedPositionKey

    assert SharedPositionKey is PositionKey


def test_grade_acceptance_requires_owner_day_not_issued_timestamp_offset():
    from zoneinfo import ZoneInfo
    from app.study.position_learning.contracts import AcceptedEvidence

    definition = PositionDefinition(PositionKey("r", START), "e2e4", "v1")
    response = FirstResponse("attempt", 0, definition, "e2e4", False, NOW, "event")
    # UTC is already tomorrow while New York is still on the issued Study day.
    accepted_at = datetime(2026, 11, 3, 1, tzinfo=timezone.utc)
    owner_day = accepted_at.astimezone(ZoneInfo("America/New_York")).date()
    evidence = AcceptedEvidence(
        response,
        True,
        "expected",
        True,
        True,
        "epoch",
        "digest",
        accepted_at,
        owner_day,
    )
    assert evidence.accepted_study_day == date(2026, 11, 2)
    assert evidence.accepted_study_day != evidence.accepted_at.date()
    with pytest.raises(ValueError, match="accepted_study_day_required"):
        AcceptedEvidence(
            response,
            True,
            "expected",
            True,
            True,
            "epoch",
            "digest",
            accepted_at,
            accepted_at,
        )


def test_position_source_public_contract_import_is_inert_and_typed():
    """Pure callers need neither a database driver nor the application owner."""
    import subprocess
    import sys
    from pathlib import Path

    source = Path(__file__).resolve().parents[2] / 'packages/evidence-math/src'
    probe = '''
import sys
sys.path.insert(0, sys.argv[1])
from tempo_evidence_math.position_learning import PreparedPositionSourceView, PositionPublicationFacts
assert PreparedPositionSourceView and PositionPublicationFacts
assert not any(name == 'psycopg' or name.startswith('tempo_storage') or name.startswith('app.') for name in sys.modules)
'''
    subprocess.run([sys.executable, '-I', '-c', probe, str(source)], check=True, timeout=10)
    from typing import get_type_hints
    from tempo_evidence_math.position_learning import PositionPublicationFacts, PreparedPositionSourceView, RouteOption
    assert get_type_hints(PreparedPositionSourceView.routes.fget)['return'] == tuple[RouteOption, ...]
    assert set(get_type_hints(PositionPublicationFacts)) == {'repertoire', 'lines', 'cards', 'memberships', 'graphs'}


def test_fake_preparation_and_fence_pair_remains_detached_from_real_owner():
    from dataclasses import dataclass
    from tempo_evidence_math.position_learning import PositionDecisionBindingView, PositionSourceTokenFacts, PreparedPositionSourceView, RouteOption

    @dataclass(frozen=True)
    class Membership:
        snapshot_id: str = 'snapshot'
        source_revision: str = 'revision'
        digest: str = 'digest'
    @dataclass(frozen=True)
    class Snapshot:
        membership: Membership = Membership()
        decision_presentations: tuple[PositionDecisionBindingView, ...] = ()
    @dataclass(frozen=True)
    class Token:
        original_snapshot_id: str = 'snapshot'
        original_source_revision: str = 'revision'
        original_source_digest: str = 'digest'
        repertoire_id: str = 'rep'
        authored_digest: str = 'authored'
        def as_dict(self) -> PositionSourceTokenFacts:
            return {'original_snapshot_id': self.original_snapshot_id, 'original_source_revision': self.original_source_revision,
                'original_source_digest': self.original_source_digest, 'repertoire_id': self.repertoire_id,
                'authored_digest': self.authored_digest, 'header': {'id': 'rep', 'scope_source_revision': 1,
                    'canonical_prefix_revision': 0, 'canonical_prefix_moves_json': '[]', 'graph_generation': 1,
                    'graph_state': 'ready', 'workspace_id': 'workspace'},
                'integrity': {'integrity_status': 'clean', 'scan_status': 'idle', 'scan_generation': 'scan:1',
                    'checked_at': '2026-11-01T00:00:00Z', 'scan_completed_sources': 1, 'scan_total_sources': 1,
                    'scan_error': None, 'graph_task_generation': 1, 'graph_task_state': 'complete',
                    'scan_task_id': 'scan', 'scan_task_generation': 1, 'scan_task_state': 'complete'}}
    @dataclass(frozen=True)
    class FakeSource:
        snapshot: Snapshot = Snapshot()
        routes: tuple[RouteOption, ...] = ()
        authored_token: Token = Token()
    def fake_prepare() -> PreparedPositionSourceView:
        return FakeSource()
    def fake_fence(source: PreparedPositionSourceView):
        return source.authored_token.authored_digest == 'authored'
    assert fake_fence(fake_prepare())
