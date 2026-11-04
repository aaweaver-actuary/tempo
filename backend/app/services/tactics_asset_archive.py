"""Private APFS archive for supplied Tactics bytes; no historical authority.

Blobs are immutable raw bytes under blobs/<sha256>; generations/<inventory_id>.json
contains the canonical T2 inventory. The owner supplies and protects the root.
Only local macOS arm64 APFS is qualified here; other platforms fail closed.
"""
from dataclasses import asdict, dataclass
import ctypes
import errno
import json
import os
import platform
from pathlib import Path
import stat
import sys
import uuid

from .tactics_asset_inventory import AssetInventoryBlocked, inventory_tactics_asset_bytes


@dataclass(frozen=True)
class AssetGenerationBlocked:
    status: str
    code: str
    logical_path: str | None = None
    pack_id: str | None = None
    inventory_id: str | None = None


def blocked(code, path=None, pack=None, inventory=None):
    return AssetGenerationBlocked('BLOCK', code, path, pack, inventory)


@dataclass(frozen=True)
class ArchiveRootIdentity:
    configured_absolute_path: str
    opened_device: int
    opened_inode: int


@dataclass(frozen=True)
class ArchiveReadbackReceipt:
    status: str
    inventory_id: str
    manifest_sha256: str
    ordered_blobs: tuple[tuple[str, int], ...]
    archive_root_identity: ArchiveRootIdentity
    receipt_codec: str = 'T-ASSET.archive.v1'


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def _hash(data):
    import hashlib
    return hashlib.sha256(data).hexdigest()


def _open_absolute(path):
    if not isinstance(path, Path) or not path.is_absolute() or '..' in path.parts:
        raise ValueError('absolute path required')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in path.parts[1:]:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        return fd
    except BaseException:
        os.close(fd)
        raise


def _local_apfs(fd):
    # Only the 64-bit Darwin arm64 statfs64 ABI has been qualified here.
    # Fail before loading libc on every other architecture.
    if sys.platform != 'darwin' or platform.machine() != 'arm64' or ctypes.sizeof(ctypes.c_void_p) != 8:
        return False
    class StatFS(ctypes.Structure):
        _fields_ = [('bsize', ctypes.c_uint32), ('iosize', ctypes.c_int32),
                    ('blocks', ctypes.c_uint64), ('bfree', ctypes.c_uint64),
                    ('bavail', ctypes.c_uint64), ('files', ctypes.c_uint64),
                    ('ffree', ctypes.c_uint64), ('fsid', ctypes.c_int32 * 2),
                    ('owner', ctypes.c_uint32), ('type', ctypes.c_uint32),
                    ('flags', ctypes.c_uint32), ('subtype', ctypes.c_uint32),
                    ('fstype', ctypes.c_char * 16), ('mnton', ctypes.c_char * 1024),
                    ('mntfrom', ctypes.c_char * 1024), ('flags_ext', ctypes.c_uint32),
                    ('reserved', ctypes.c_uint32 * 7)]
    info = StatFS()
    libc = ctypes.CDLL(None, use_errno=True)
    fstatfs = libc.fstatfs64
    fstatfs.argtypes = [ctypes.c_int, ctypes.POINTER(StatFS)]
    fstatfs.restype = ctypes.c_int
    return fstatfs(fd, ctypes.byref(info)) == 0 and bool(info.flags & 0x1000) and info.fstype == b'apfs' and not (info.flags_ext & 2)


def _directory(parent, name):
    try:
        os.mkdir(name, 0o700, dir_fd=parent)
        os.fsync(parent)
    except FileExistsError:
        pass
    fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
    s = os.fstat(fd)
    if s.st_uid != os.geteuid() or s.st_mode & 0o077:
        os.close(fd)
        raise PermissionError('archive directory is not private')
    return fd


def _read(parent, name, expected_length):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    try:
        s = os.fstat(fd)
        if not stat.S_ISREG(s.st_mode) or s.st_size != expected_length:
            return None
        data = bytearray()
        while len(data) <= expected_length:
            chunk = os.read(fd, min(65536, expected_length + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        return bytes(data) if len(data) == expected_length else None
    finally:
        os.close(fd)


def _publish(parent, name, content):
    try:
        existing = _read(parent, name, len(content))
    except FileNotFoundError:
        existing = None
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            return False
        raise
    else:
        return existing == content
    temp = '.stage-' + uuid.uuid4().hex
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temp, name, src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False)
        except FileExistsError:
            pass
        os.fsync(parent)
        try:
            return _read(parent, name, len(content)) == content
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                return False
            raise
    finally:
        os.unlink(temp, dir_fd=parent)
        os.fsync(parent)


def archive_tactics_generation(*, archive_root: Path, catalog_bytes: bytes,
                               pack_assets: tuple[tuple[str, bytes], ...], inventory):
    actual = inventory_tactics_asset_bytes(catalog_bytes=catalog_bytes, pack_assets=pack_assets)
    if isinstance(actual, AssetInventoryBlocked) or actual != inventory:
        return blocked('IMMUTABLE_CONFLICT', inventory=getattr(inventory, 'inventory_id', None))
    inventory_id = inventory.inventory_id
    root = blobs = generations = None
    try:
        root = _open_absolute(archive_root)
        root_stat = os.fstat(root)
        if root_stat.st_uid != os.geteuid() or root_stat.st_mode & 0o077 or not _local_apfs(root):
            return blocked('ARCHIVE_UNSUPPORTED', inventory=inventory_id)
        identity = ArchiveRootIdentity(str(archive_root), root_stat.st_dev, root_stat.st_ino)
        blobs = _directory(root, 'blobs')
        generations = _directory(root, 'generations')
        by_path = dict(pack_assets)
        raw_values = (catalog_bytes,) + tuple(by_path[p.asset.logical_path] for p in inventory.packs)
        asset_values = (inventory.catalog,) + tuple(p.asset for p in inventory.packs)
        for asset, raw in zip(asset_values, raw_values):
            if not _publish(blobs, asset.sha256, raw):
                return blocked('IMMUTABLE_CONFLICT', asset.logical_path, inventory=inventory_id)
        manifest = _canonical(asdict(inventory))
        if not _publish(generations, inventory_id + '.json', manifest):
            return blocked('IMMUTABLE_CONFLICT', inventory=inventory_id)
        try:
            read_manifest = _read(generations, inventory_id + '.json', len(manifest))
            values = tuple(_read(blobs, a.sha256, a.byte_length) for a in asset_values)
        except OSError:
            return blocked('ARCHIVE_READBACK_FAILED', inventory=inventory_id)
        if read_manifest != manifest or any(v is None for v in values):
            return blocked('ARCHIVE_READBACK_FAILED', inventory=inventory_id)
        read_packs = tuple((p.asset.logical_path, raw) for p, raw in zip(inventory.packs, values[1:]))
        if inventory_tactics_asset_bytes(catalog_bytes=values[0], pack_assets=read_packs) != inventory:
            return blocked('ARCHIVE_READBACK_FAILED', inventory=inventory_id)
        os.fsync(blobs)
        os.fsync(generations)
        os.fsync(root)
        return ArchiveReadbackReceipt('ARCHIVED_READBACK', inventory_id, _hash(manifest),
                                      tuple((a.sha256, a.byte_length) for a in asset_values), identity)
    except (OSError, ValueError, AttributeError):
        return blocked('ARCHIVE_IO_FAILED', inventory=inventory_id)
    finally:
        for fd in (generations, blobs, root):
            if fd is not None:
                os.close(fd)


def read_archived_blob(receipt: ArchiveReadbackReceipt, digest: str, length: int):
    root = blobs = None
    try:
        root = _open_absolute(Path(receipt.archive_root_identity.configured_absolute_path))
        s = os.fstat(root)
        if (s.st_dev, s.st_ino) != (receipt.archive_root_identity.opened_device, receipt.archive_root_identity.opened_inode):
            return None
        blobs = os.open('blobs', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root)
        data = _read(blobs, digest, length)
        return data if data is not None and _hash(data) == digest else None
    except (OSError, ValueError):
        return None
    finally:
        for fd in (blobs, root):
            if fd is not None:
                os.close(fd)
