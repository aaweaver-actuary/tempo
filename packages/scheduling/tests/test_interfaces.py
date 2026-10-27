"""The public scheduling directory preserves the existing pure API."""

import subprocess
import sys
import textwrap


def test_scheduling_interface_keeps_exact_existing_exports():
    import tempo_scheduling as existing
    import tempo_scheduling.interfaces as surface

    assert surface.__all__ == ("Schedule", "schedule_review", "unlock_ready")
    assert {name for name in vars(surface) if not name.startswith("_")} == set(surface.__all__)
    for name in surface.__all__:
        assert getattr(surface, name) is getattr(existing, name)
    assert not hasattr(surface, "Scheduler")


def test_scheduling_interface_cold_import_needs_no_backend_or_runtime():
    source = textwrap.dedent("""
        import importlib.abc
        import sys
        import threading
        forbidden = ('app', 'tempo_legacy_database', 'sqlite3', 'psycopg',
                     'celery', 'redis', 'httpx', 'requests')
        class RejectRuntime(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
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
        import tempo_scheduling.interfaces as surface
        assert surface.Schedule.__module__ == 'tempo_scheduling._scheduler'
        print('cold scheduling interface: pure')
    """)
    result = subprocess.run([sys.executable, "-c", source], text=True,
                            capture_output=True, timeout=15, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "cold scheduling interface: pure"
