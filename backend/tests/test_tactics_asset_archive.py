"""Private archive fixtures: immutable publication and full readback."""

from app.services import tactics_asset_archive as archive
from app.services.tactics_asset_inventory import inventory_tactics_asset_bytes

C = b'{"version":1,"packs":[{"id":"a","asset":"data/a.json"}]}'
P = b'[{"PuzzleId":"p1","FEN":"fixture","Moves":""}]'
ASSETS = (("data/a.json", P),)
INV = inventory_tactics_asset_bytes(catalog_bytes=C, pack_assets=ASSETS)


def run(root):
    return archive.archive_tactics_generation(
        archive_root=root, catalog_bytes=C, pack_assets=ASSETS, inventory=INV
    )


def test_archive_retry_and_conflicts(tmp_path):
    root = tmp_path / "archive"
    root.mkdir(mode=0o700)
    first = run(root)
    assert first.status == "ARCHIVED_READBACK"
    assert run(root) == first
    blob = root / "blobs" / INV.packs[0].asset.sha256
    blob.write_bytes(b"wrong")
    assert run(root).code == "IMMUTABLE_CONFLICT"
    assert blob.read_bytes() == b"wrong"


def test_interrupted_publication_then_retry(tmp_path, monkeypatch):
    root = tmp_path / "archive"
    root.mkdir(mode=0o700)
    actual = archive._publish

    def interrupted(parent, name, content):
        if name.endswith(".json"):
            raise OSError("injected interruption")
        return actual(parent, name, content)

    monkeypatch.setattr(archive, "_publish", interrupted)
    assert run(root).code == "ARCHIVE_IO_FAILED"
    assert not list((root / "generations").iterdir())
    monkeypatch.setattr(archive, "_publish", actual)
    assert run(root).status == "ARCHIVED_READBACK"


def test_manifest_conflict_and_readback_failure(tmp_path, monkeypatch):
    root = tmp_path / "archive"
    root.mkdir(mode=0o700)
    assert run(root).status == "ARCHIVED_READBACK"
    manifest = root / "generations" / (INV.inventory_id + ".json")
    manifest.write_bytes(b"wrong")
    assert run(root).code == "IMMUTABLE_CONFLICT"
    manifest.unlink()
    actual = archive._read
    calls = 0

    def corrupt(parent, name, length):
        nonlocal calls
        calls += 1
        value = actual(parent, name, length)
        if calls == 5:
            return b"bad"
        return value

    monkeypatch.setattr(archive, "_read", corrupt)
    assert run(root).code == "ARCHIVE_READBACK_FAILED"


def test_unsupported_root_and_inventory_mismatch(tmp_path, monkeypatch):
    root = tmp_path / "archive"
    root.mkdir(mode=0o700)
    monkeypatch.setattr(archive, "_local_apfs", lambda fd: False)
    assert run(root).code == "ARCHIVE_UNSUPPORTED"
    assert (
        archive.archive_tactics_generation(
            archive_root=root, catalog_bytes=C + b" ", pack_assets=ASSETS, inventory=INV
        ).code
        == "IMMUTABLE_CONFLICT"
    )


def test_failed_second_directory_open_closes_first_on_retry(tmp_path, monkeypatch):
    import os
    import pytest

    root = tmp_path / "archive"
    root.mkdir(mode=0o700)
    actual = archive._directory
    opened = []

    def fail_second(parent, name):
        if name == "generations":
            raise OSError("injected directory open failure")
        fd = actual(parent, name)
        opened.append(fd)
        return fd

    monkeypatch.setattr(archive, "_directory", fail_second)
    for _ in range(20):
        assert run(root).code == "ARCHIVE_IO_FAILED"
        with pytest.raises(OSError):
            os.fstat(opened[-1])
    monkeypatch.setattr(archive, "_directory", actual)
    assert run(root).status == "ARCHIVED_READBACK"


def test_unqualified_architecture_fails_before_ffi(monkeypatch):
    monkeypatch.setattr(archive.sys, "platform", "darwin")
    monkeypatch.setattr(archive.platform, "machine", lambda: "x86_64")

    def forbidden(*args, **kwargs):
        raise AssertionError("unqualified architecture must not invoke FFI")

    monkeypatch.setattr(archive.ctypes, "CDLL", forbidden)
    assert archive._local_apfs(-1) is False


def test_directory_fstat_failure_closes_new_descriptor(tmp_path, monkeypatch):
    import os
    import pytest

    parent = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    opened = []
    real_open, real_fstat = os.open, os.fstat

    def tracked_open(path, *args, **kwargs):
        fd = real_open(path, *args, **kwargs)
        if path == "blobs":
            opened.append(fd)
        return fd

    def failed_fstat(fd):
        if fd in opened:
            raise OSError("injected fstat failure")
        return real_fstat(fd)

    try:
        monkeypatch.setattr(archive.os, "open", tracked_open)
        monkeypatch.setattr(archive.os, "fstat", failed_fstat)
        with pytest.raises(OSError, match="injected fstat failure"):
            archive._directory(parent, "blobs")
        assert len(opened) == 1
        with pytest.raises(OSError):
            real_fstat(opened[0])
    finally:
        os.close(parent)
