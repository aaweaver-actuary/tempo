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


def _current_boot_process():
    """Linux container boot/PID1 start, read only by trusted outer preparation."""
    try:
        with Path("/proc/sys/kernel/random/boot_id").open() as source:
            boot = source.read(64).strip().replace("-", "")
        with Path("/proc/1/stat").open() as source:
            stat = source.read(4097)
        if len(stat) > 4096 or not re.fullmatch(r"[0-9a-f]{32}", boot):
            return None
        # comm may contain spaces/parentheses; fields after its final ')' start
        # with state (field3), so starttime (field22) is index19.
        started = stat.rsplit(")", 1)[1].split()[19]
        return boot + ":" + started if re.fullmatch(r"[0-9]{1,20}", started) else None
    except (OSError, ValueError, IndexError):
        return None


def read_runtime_identity(path=RUNTIME_IDENTITY_PATH, *, hostname=None, boot_process=None):
    try:
        with Path(path).open() as source:
            text = source.read(2049)
        if len(text) > 2048:
            return None
        value = json.loads(text)
        actual_hostname = hostname or socket.gethostname()
        actual_boot = boot_process or _current_boot_process()
        if (not isinstance(value, dict) or set(value) != {"schema_version", "hostname", "boot_nonce", "boot_process"}
                or value["schema_version"] != 1 or value["hostname"] != actual_hostname
                or actual_boot is None or value["boot_process"] != actual_boot
                or not re.fullmatch(r"[0-9a-f]{12}", value["hostname"])
                or not isinstance(value["boot_nonce"], str) or not re.fullmatch(r"[0-9a-f]{32}", value["boot_nonce"])):
            return None
        return {name: value[name] for name in ("schema_version", "hostname", "boot_nonce")}
    except (OSError, ValueError, TypeError):
        return None


def current_managed_runtime_identity(path=RUNTIME_IDENTITY_PATH, *, hostname=None, boot_process=None):
    value = read_runtime_identity(path, hostname=hostname, boot_process=boot_process)
    return value["hostname"] + ":" + value["boot_nonce"] if value else None


def initialize_runtime_identity(path=RUNTIME_IDENTITY_PATH, *, hostname=None, boot_process=None):
    target = Path(path)
    current_boot = boot_process or _current_boot_process()
    if current_boot is None:
        raise ValueError("managed_runtime_boot_identity_unavailable")
    value = {"schema_version": 1, "hostname": hostname or socket.gethostname(),
             "boot_nonce": secrets.token_hex(16), "boot_process": current_boot}
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
        try:
            initialize_runtime_identity()
            return 0
        except (OSError, ValueError):
            # Best effort invalidation. A retained file still cannot match this
            # new PID1 start; ordinary backend execution remains independent.
            try:
                RUNTIME_IDENTITY_PATH.unlink(missing_ok=True)
            except OSError:
                pass
            return 2
    value = read_runtime_identity()
    print(json.dumps({"schema_version": 1, "read_only": True, "runtime": value}))
    return 0 if value else 2


if __name__ == "__main__":
    raise SystemExit(main())
