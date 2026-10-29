"""The #503 public entry point preserves identities and has no runtime startup."""

import importlib
from pathlib import Path
import subprocess
import sys
import textwrap
from typing import get_type_hints


def test_presentation_surface_exports_original_owner_identities():
    surface = importlib.import_module("app.study.presentations.interfaces")
    origins = {
        "application": ("Recombination",),
        "models": (
            "AuthoredRouteFacts", "CleanStreak", "ComponentRef", "JoinDecision",
            "JoinEvidence", "JoinHold", "JoinMember", "ParentConnection",
            "RetainedUse", "ReviewEvent", "RouteContext", "RouteJoinPlan", "RouteUse",
        ),
        "ports": (
            "CurrentRoute", "NewPresentation", "Obligation", "PresentationChange",
            "RecombinationStore", "StudyBoundary",
        ),
    }
    expected = {name for names in origins.values() for name in names}
    assert set(surface.__all__) == expected
    assert len(surface.__all__) == len(expected)
    assert {name for name in vars(surface) if not name.startswith("_")} == expected
    for module_name, names in origins.items():
        origin = importlib.import_module(f"app.study.recombination.{module_name}")
        for name in names:
            assert getattr(surface, name) is getattr(origin, name)


def test_presentation_surface_keeps_existing_wire_and_capability_types():
    from app.contracts.product_v02.presentation import JoinPlan, Presentation, PresentationChangeReceipt
    from app.study.presentations.interfaces import (
        CurrentRoute, NewPresentation, Recombination, RouteJoinPlan, StudyBoundary,
    )

    assert issubclass(RouteJoinPlan, JoinPlan)
    assert get_type_hints(NewPresentation)["presentation"] is Presentation
    assert get_type_hints(Recombination.apply_join) == {
        "plan": RouteJoinPlan, "return": PresentationChangeReceipt,
    }
    assert get_type_hints(StudyBoundary.try_current)["return"] == CurrentRoute | None


def test_presentation_surface_cold_import_has_no_runtime_dependencies_or_effects():
    repository = Path(__file__).resolve().parents[2]
    source = textwrap.dedent("""
        import importlib.abc
        import sys
        import threading

        forbidden = (
            'app.main', 'app.tasks', 'app.background_worker', 'app.celery_app',
            'app.command_gateway', 'app.command_dispatch', 'app.prefix_split_commands',
            'app.product_composition', 'app.services', 'app.games', 'app.analysis',
            'tempo_legacy_database', 'psycopg', 'sqlite3', 'celery', 'redis',
            'httpx', 'requests',
        )

        class RejectRuntimeImports(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if any(fullname == prefix or fullname.startswith(prefix + '.')
                       for prefix in forbidden):
                    raise AssertionError('runtime import: ' + fullname)
                return None

        def reject_thread(*args, **kwargs):
            raise AssertionError('thread startup during interface import')

        def reject_effect(event, args):
            if event in {'socket.connect', 'subprocess.Popen', 'sqlite3.connect',
                         'os.system', 'os.fork', 'os.posix_spawn'}:
                raise AssertionError('runtime effect: ' + event)

        sys.meta_path.insert(0, RejectRuntimeImports())
        threading.Thread.start = reject_thread
        sys.addaudithook(reject_effect)
        import app.study.presentations.interfaces as surface
        assert surface.Recombination.__module__ == 'app.study.recombination.application'
        assert not any(name == prefix or name.startswith(prefix + '.')
                       for name in sys.modules for prefix in forbidden)
        print('cold presentation surface: no runtime imports or effects')
    """)
    completed = subprocess.run(
        [sys.executable, "-c", source], cwd=repository, text=True,
        capture_output=True, timeout=15, check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert completed.stdout.strip() == "cold presentation surface: no runtime imports or effects"
