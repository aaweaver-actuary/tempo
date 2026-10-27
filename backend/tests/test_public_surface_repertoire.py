"""Public repertoire import/identity and supplied-fact semantics; no native work."""
from copy import deepcopy
import os
from pathlib import Path
import subprocess
import sys
import textwrap
from typing import get_type_hints

import chess
import pytest

from app.contracts.product_v02 import repertoire as contracts
from app.repertoire import interfaces as public
from app.repertoire import position_index, snapshot
from test_product_snapshot import capture, repertoire_facts


def test_public_repertoire_exports_preserve_existing_identity_and_types():
    origins = {
        position_index: ("Diagnostic", "PositionIndex", "PositionKey", "PositionNode",
                         "RouteAdjacency", "RouteOccurrence", "build_index", "position_key"),
        snapshot: ("CapturedSnapshotAdapter", "CurrentSnapshotAdapter", "PublishedSnapshotAdapter",
                   "project_capture", "project_published_repertoire", "project_repertoire"),
        contracts: ("DecisionPresentation", "IntroductionGameScope", "RepertoireSnapshot",
                    "RepertoireSnapshotPort", "RouteBoundary"),
    }
    assert set(public.__all__) == {name for names in origins.values() for name in names}
    assert len(public.__all__) == len(set(public.__all__))
    for origin, names in origins.items():
        for name in names:
            assert getattr(public, name) is getattr(origin, name)
    assert get_type_hints(public.build_index) == {
        "snapshot": public.RepertoireSnapshot, "return": public.PositionIndex}
    for adapter in (public.CapturedSnapshotAdapter, public.CurrentSnapshotAdapter, public.PublishedSnapshotAdapter):
        assert get_type_hints(adapter.snapshot) == {
            "repertoire_id": str, "return": public.RepertoireSnapshot}
    assert not hasattr(public, "current_snapshot")
    assert not hasattr(public, "reserve_current_snapshot")


def test_public_repertoire_cold_import_starts_no_infrastructure():
    script = textwrap.dedent('''
        import importlib.abc
        import socket
        import subprocess
        import sys
        forbidden = ("psycopg", "requests", "httpx", "tempo_legacy_database",
                     "app.main", "app.tasks", "app.background_worker", "app.services",
                     "app.product_composition", "celery", "redis", "chess.engine")
        class NoInfrastructure(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if any(fullname == name or fullname.startswith(name + ".") for name in forbidden):
                    raise AssertionError("infrastructure import: " + fullname)
        def no_effect(*args, **kwargs):
            raise AssertionError("public import started a connection or process")
        sys.meta_path.insert(0, NoInfrastructure())
        socket.create_connection = no_effect
        socket.socket.connect = no_effect
        subprocess.Popen = no_effect
        from app.repertoire.interfaces import RepertoireSnapshotPort, build_index
        assert callable(build_index)
        assert RepertoireSnapshotPort.__name__ == "RepertoireSnapshotPort"
        assert not any(name == prefix or name.startswith(prefix + ".")
                       for name in sys.modules for prefix in forbidden)
    ''')
    result = subprocess.run([sys.executable, "-c", script], env=dict(os.environ),
                            cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("adapter,source,revision", [
    (public.CapturedSnapshotAdapter, capture, 3),
    (public.CurrentSnapshotAdapter, repertoire_facts, 8),
    (public.PublishedSnapshotAdapter, repertoire_facts, 3),
])
def test_public_repertoire_adapters_keep_snapshot_and_immutable_lookup_semantics(adapter, source, revision):
    facts = source(); before = deepcopy(facts); calls = []
    def owner_read(repertoire_id):
        calls.append(repertoire_id)
        return facts
    port: public.RepertoireSnapshotPort = adapter(owner_read)
    value = port.snapshot("rep")
    assert calls == ["rep"] and facts == before
    assert isinstance(value, public.RepertoireSnapshot) and value.membership.revision == revision
    index = public.build_index(value)
    node = index.lookup("rep", chess.STARTING_FEN)
    assert isinstance(index, public.PositionIndex) and node.selected_response == "e2e4"
    with pytest.raises(TypeError):
        index.nodes[node.key] = node
    assert value.decision_presentations[0].introduced_before_game is None
    if "tables" in facts:
        facts["tables"]["repertoire_lines"][0]["moves_json"] = "[]"
    else:
        facts["lines"][0]["moves_json"] = "[]"
    assert value.membership.routes[0].moves == ("e2e4", "e7e5", "g1f3")
