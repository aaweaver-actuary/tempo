"""Fresh-process runtime firewall; not an OS security sandbox.

Source/bytecode reads needed by Python imports are allowed. Domain I/O, service
imports and network/process construction are denied while the firewall is active.
"""
from contextlib import contextmanager
import importlib.abc
import os
from pathlib import Path
import socket
import sys
import sysconfig

# CPython on macOS reads SystemVersion.plist while initializing sysconfig.
# Finish interpreter configuration before inspecting application behavior.
sysconfig.get_config_vars()
PROBABILITY_FIXTURE = str(Path(__file__).resolve().parents[2] / "tests/fixtures/opening-move-probability/examples.json")

from domain_boundary import FORBIDDEN_ROOTS


class NoServiceImports(importlib.abc.MetaPathFinder):
    def __init__(self, certified_modules):
        self.certified_modules = set(certified_modules) | {"app", "app.services"}

    def find_spec(self, fullname, path=None, target=None):
        if fullname in {"urllib", "urllib.parse"}:
            return None  # Pydantic uses URL parsing as a value operation.
        if fullname.split(".")[0] in FORBIDDEN_ROOTS - {"os", "pathlib", "io", "random", "time", "concurrent", "multiprocessing", "subprocess", "shutil", "tempfile", "glob"}:
            raise AssertionError("Forbidden service import: " + fullname)
        if fullname.startswith("app.") and fullname not in self.certified_modules:
            raise AssertionError("Forbidden application import: " + fullname)


def forbidden_runtime(*args, **kwargs):
    raise AssertionError("Forbidden database/network/process initialization or domain filesystem I/O")


@contextmanager
def no_service_runtime(*, certified_modules=()):
    """Restore every patched global after use; audit hooks remain inactive."""
    finder = NoServiceImports(certified_modules)
    active = True

    def audit(event, args):
        if not active:
            return
        if event.startswith(("socket.", "subprocess.", "sqlite3.connect", "os.system", "os.spawn", "os.exec", "os.fork", "os.posix_spawn")) or event in {"os.remove", "os.rename", "os.rmdir", "os.mkdir", "os.chmod", "os.truncate", "os.link", "os.symlink"}:
            forbidden_runtime()
        if event == "open":
            # Imports require reading Python/native code and dist-info metadata.
            path = str(args[0])
            mode = args[1] or ""
            flags = args[2]
            frame = sys._getframe(1)
            application_frame = False
            fixture_frame = False
            while frame is not None:
                module = frame.f_globals.get("__name__", "")
                application_frame |= module == "app" or module.startswith("app.")
                fixture_frame |= module == "test_opening_policy_reach"
                frame = frame.f_back
            fixture_read = path == PROBABILITY_FIXTURE and fixture_frame and not application_frame
            write_flags = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
            if any(flag in mode for flag in "wax+") or flags & write_flags or not (
                path.endswith((".py", ".pyc", ".so", ".pyd", ".dylib"))
                or ".dist-info/" in path
                or fixture_read
            ):
                forbidden_runtime()

    sys.addaudithook(audit)
    original_create_connection = socket.create_connection
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    original_getaddrinfo = socket.getaddrinfo
    socket.create_connection = forbidden_runtime
    socket.socket.connect = forbidden_runtime
    socket.socket.connect_ex = forbidden_runtime
    socket.getaddrinfo = forbidden_runtime
    sys.meta_path.insert(0, finder)
    try:
        yield
    finally:
        active = False
        sys.meta_path.remove(finder)
        socket.create_connection = original_create_connection
        socket.socket.connect = original_connect
        socket.socket.connect_ex = original_connect_ex
        socket.getaddrinfo = original_getaddrinfo
