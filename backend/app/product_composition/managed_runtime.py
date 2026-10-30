"""Per-container-start identity for optional managed Games admission.

Only the image entrypoint initializes it. Trusted preparation reads it outside a
writer; a missing identity disables new Games handoff, never ordinary Study.
Importing this module performs no file/process/connection work.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import secrets
import socket
import tempfile

RUNTIME_IDENTITY_PATH = Path("/tmp/tempo-managed-runtime.json")


def read_runtime_identity(path=RUNTIME_IDENTITY_PATH, *, hostname=None):
    try:
        with Path(path).open() as source:
            text = source.read(2049)
        if len(text) > 2048:
            return None
        value = json.loads(text)
        actual_hostname = hostname or socket.gethostname()
        if (not isinstance(value, dict) or set(value) != {"schema_version", "hostname", "boot_nonce"}
                or value["schema_version"] != 1 or value["hostname"] != actual_hostname
                or not re.fullmatch(r"[0-9a-f]{12}", value["hostname"])
                or not isinstance(value["boot_nonce"], str) or not re.fullmatch(r"[0-9a-f]{32}", value["boot_nonce"])):
            return None
        return value
    except (OSError, ValueError, TypeError):
        return None


def current_managed_runtime_identity(path=RUNTIME_IDENTITY_PATH, *, hostname=None):
    value = read_runtime_identity(path, hostname=hostname)
    return value["hostname"] + ":" + value["boot_nonce"] if value else None


def initialize_runtime_identity(path=RUNTIME_IDENTITY_PATH, *, hostname=None):
    target = Path(path)
    value = {"schema_version": 1, "hostname": hostname or socket.gethostname(), "boot_nonce": secrets.token_hex(16)}
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=target.parent, prefix=".tempo-runtime-", delete=False) as output:
            temporary = Path(output.name)
            os.chmod(temporary, 0o600)
            output.write(json.dumps(value))
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("initialize", "inspect"))
    options = parser.parse_args(argv)
    if options.operation == "initialize":
        initialize_runtime_identity()
        return 0
    value = read_runtime_identity()
    print(json.dumps({"schema_version": 1, "read_only": True, "runtime": value}))
    return 0 if value else 2


if __name__ == "__main__":
    raise SystemExit(main())
