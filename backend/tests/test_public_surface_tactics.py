"""Tactics puzzle surface preserves values while excluding mutation startup."""

from pathlib import Path
import subprocess
import sys
import textwrap

import chess
import pytest


def test_tactics_surface_exports_original_puzzle_operations_only():
    from app.services import puzzles
    from app.tactics import interfaces as surface

    assert surface.__all__ == (
        "LichessPuzzle", "load_packaged_decks", "parse_lichess_puzzle_row", "validate_puzzle_record",
    )
    assert {name for name in vars(surface) if not name.startswith("_")} == set(surface.__all__)
    for name in surface.__all__:
        assert getattr(surface, name) is getattr(puzzles, name)
    assert not hasattr(surface, "capture_tactic")


def test_tactics_surface_retains_setup_move_and_illegal_solution_behavior():
    from app.tactics.interfaces import parse_lichess_puzzle_row, validate_puzzle_record

    row = {"PuzzleId": "synthetic", "FEN": chess.STARTING_FEN,
           "Moves": "e2e4 e7e5", "Rating": "1000", "Popularity": "10"}
    puzzle = parse_lichess_puzzle_row(row)
    assert validate_puzzle_record(row) == puzzle.training_position()
    assert puzzle.training_position()[1] == ["e7e5"]
    with pytest.raises(ValueError, match="Illegal puzzle move"):
        validate_puzzle_record({**row, "Moves": "e2e4 e7e4"})


def test_tactics_surface_cold_import_excludes_commands_storage_and_producers():
    source = textwrap.dedent("""
        import importlib.abc
        import sys
        import threading
        allowed_services = {'app.services', 'app.services.puzzles'}
        forbidden = ('app.main', 'app.tasks', 'app.background_worker', 'app.celery_app',
                     'app.command_gateway', 'app.command_dispatch', 'app.tactic_commands',
                     'app.product_composition', 'app.games', 'app.analysis',
                     'tempo_legacy_database', 'sqlite3', 'psycopg', 'celery',
                     'redis', 'httpx', 'requests')
        class RejectRuntime(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if fullname.startswith('app.services.') and fullname not in allowed_services:
                    raise AssertionError('unrelated legacy service: ' + fullname)
                if any(fullname == name or fullname.startswith(name + '.')
                       for name in forbidden):
                    raise AssertionError('runtime import: ' + fullname)
                return None
        def reject_thread(*args, **kwargs):
            raise AssertionError('thread startup')
        def reject_effect(event, args):
            if event in {'socket.connect', 'subprocess.Popen', 'sqlite3.connect',
                         'os.system', 'os.fork', 'os.posix_spawn'}:
                raise AssertionError('runtime effect: ' + event)
        sys.meta_path.insert(0, RejectRuntime())
        threading.Thread.start = reject_thread
        sys.addaudithook(reject_effect)
        import app.tactics.interfaces as surface
        assert surface.LichessPuzzle.__module__ == 'app.services.puzzles'
        print('cold tactics interface: no mutation startup')
    """)
    result = subprocess.run([sys.executable, "-c", source],
                            cwd=Path(__file__).resolve().parents[2], text=True,
                            capture_output=True, timeout=15, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "cold tactics interface: no mutation startup"
