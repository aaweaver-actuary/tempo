"""Explicit evidence exports retain identity and the lazy cold-import contract."""

import subprocess
import sys
import textwrap

import pytest


def test_evidence_interfaces_keep_all_existing_named_export_identities():
    import tempo_evidence_math as existing
    import tempo_evidence_math.interfaces as surface

    assert tuple(surface.__all__) == tuple(existing.__all__)
    assert len(surface.__all__) == len(set(surface.__all__))
    for name in surface.__all__:
        assert getattr(surface, name) is getattr(existing, name)


def test_evidence_interfaces_reject_undeclared_export_bypasses():
    import tempo_evidence_math.interfaces as surface

    for name in ("os", "datetime", "_graph", "identifier", "Scheduler", "missing_export"):
        with pytest.raises(AttributeError):
            getattr(surface, name)


def test_evidence_interfaces_cold_arithmetic_retains_standard_library_closure():
    source = textwrap.dedent("""
        import importlib.abc
        import sys
        import threading
        forbidden = ('app', 'tempo_legacy_database', 'sqlite3', 'psycopg',
                     'chess', 'pydantic', 'pydantic_core', 'celery', 'redis',
                     'httpx', 'requests')
        class RejectRuntime(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if any(fullname == name or fullname.startswith(name + '.')
                       for name in forbidden):
                    raise AssertionError('nonstandard/runtime import: ' + fullname)
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
        from tempo_evidence_math.interfaces import ProbabilityEvidence, route_probability
        result = route_probability(ProbabilityEvidence.exact(0.5), (ProbabilityEvidence.exact(0.4),))
        assert result.lower == result.upper == 0.2
        assert 'tempo_evidence_math._readiness' not in sys.modules
        assert 'tempo_evidence_math._graph' not in sys.modules
        assert 'tempo_evidence_math._valuation' not in sys.modules
        print('cold evidence arithmetic: standard library only')
    """)
    result = subprocess.run([sys.executable, "-c", source], text=True,
                            capture_output=True, timeout=15, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "cold evidence arithmetic: standard library only"
