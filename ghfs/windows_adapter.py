"""
Windows adapter for GHFS using WinFSP (Windows File System Proxy).

Prerequisites:
  1. Install WinFSP from https://winfsp.dev/rel/  (select "Developer" in the installer)
  2. pip install winfspy

WinFSP mounts the filesystem as a Windows drive letter (e.g. G:) or as a
directory under a UNC-style path. Explorer, cmd, PowerShell, and all other
Windows applications can then browse it natively.
"""

import logging
import threading

from .filesystem import GitHubVFS, FSNode

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Conditional import — winfspy only exists on Windows
# ---------------------------------------------------------------------------

try:
    from winfspy import (                                     # type: ignore
        FileSystem,
        BaseFileSystemOperations,
        NTStatusError,
        FILE_ATTRIBUTE,
    )
    _WINFSPY_AVAILABLE = True
except ImportError:
    _WINFSPY_AVAILABLE = False
    BaseFileSystemOperations = object                         # dummy base


def winfspy_available() -> bool:
    return _WINFSPY_AVAILABLE


# ---------------------------------------------------------------------------
# WinFSP operations class
# ---------------------------------------------------------------------------

if _WINFSPY_AVAILABLE:
    import datetime
    import struct

    # Minimal valid self-relative Windows Security Descriptor with:
    #   Owner = S-1-1-0 (Everyone), no Group, no SACL, no DACL (null DACL = full access).
    # Windows requires at least a valid owner SID; a header-only SD is rejected (WinError 1338).
    #
    # SID S-1-1-0: Revision=1, SubAuthorityCount=1, Authority=1 (World), SubAuthority[0]=0
    _EVERYONE_SID = (
        struct.pack("BB", 1, 1)      # Revision, SubAuthorityCount
        + b"\x00\x00\x00\x00\x00\x01"  # IdentifierAuthority = SECURITY_WORLD_SID_AUTHORITY
        + struct.pack("<I", 0)          # SubAuthority[0] = SECURITY_WORLD_RID
    )
    # SD header: Control=SE_SELF_RELATIVE(0x8000), OffsetOwner points past the 20-byte header.
    _MINIMAL_SD = struct.pack("<BBHIIII", 1, 0, 0x8000, 20, 0, 0, 0) + _EVERYONE_SID

    # NT status codes we use
    STATUS_ACCESS_DENIED        = 0xC0000022
    STATUS_MEDIA_WRITE_PROTECTED = 0x80000013
    STATUS_NO_SUCH_FILE         = 0xC000000F
    STATUS_NOT_A_DIRECTORY      = 0xC0000103
    STATUS_FILE_IS_A_DIRECTORY  = 0xC00000BA

    _EPOCH = datetime.datetime(1601, 1, 1, tzinfo=datetime.timezone.utc)

    def _to_filetime(ts: float) -> int:
        """Convert a POSIX timestamp to a Windows FILETIME (100-ns intervals since 1601-01-01)."""
        dt = datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc)
        return int((dt - _EPOCH).total_seconds() * 10_000_000)

    def _node_to_info(node: FSNode) -> dict:
        now = _to_filetime(__import__("time").time())
        return {
            "file_attributes": (
                FILE_ATTRIBUTE.FILE_ATTRIBUTE_DIRECTORY
                if node.is_dir else
                FILE_ATTRIBUTE.FILE_ATTRIBUTE_READONLY
            ),
            "allocation_size":       ((node.size + 4095) // 4096) * 4096,
            "file_size":             node.size,
            "creation_time":         now,
            "last_access_time":      now,
            "last_write_time":       now,
            "change_time":           now,
            "index_number":          0,
        }

    class _GHFSWinOperations(BaseFileSystemOperations):
        """WinFSP operations implementation backed by GitHubVFS."""

        def __init__(self, vfs: GitHubVFS, volume_label: str = "GitHub"):
            super().__init__()
            self._vfs = vfs
            self._volume_label = volume_label
            self._lock = threading.Lock()
            self._fd_map: dict = {}
            self._fd_counter = 0

        # ------------------------------------------------------------------
        # Volume info
        # ------------------------------------------------------------------

        def get_volume_info(self):
            return {
                "total_size":     1 << 40,   # 1 TiB (virtual)
                "free_size":      0,
                "volume_label":   self._volume_label,
            }

        def set_volume_label(self, volume_label):
            # Read-only — silently ignore
            pass

        # ------------------------------------------------------------------
        # File info
        # ------------------------------------------------------------------

        def get_security_by_name(self, file_name):
            # WinFSP needs us to return (attributes, security_descriptor, sd_size)
            # We return a minimal self-relative SD that satisfies the memmove call.
            node = self._vfs.get_node(file_name)
            if node is None:
                raise NTStatusError(STATUS_NO_SUCH_FILE)
            attr = (
                FILE_ATTRIBUTE.FILE_ATTRIBUTE_DIRECTORY
                if node.is_dir else
                FILE_ATTRIBUTE.FILE_ATTRIBUTE_READONLY
            )
            return attr, _MINIMAL_SD, len(_MINIMAL_SD)

        def open(self, file_name, create_options, granted_access, file_context):
            node = self._vfs.get_node(file_name)
            if node is None:
                raise NTStatusError(STATUS_NO_SUCH_FILE)
            with self._lock:
                self._fd_counter += 1
                fd = self._fd_counter
                self._fd_map[fd] = node
            file_context["fd"] = fd
            return _node_to_info(node)

        def close(self, file_context):
            fd = file_context.get("fd")
            if fd is not None:
                with self._lock:
                    self._fd_map.pop(fd, None)

        def get_file_info(self, file_context):
            fd = file_context.get("fd")
            with self._lock:
                node = self._fd_map.get(fd)
            if node is None:
                raise NTStatusError(STATUS_NO_SUCH_FILE)
            return _node_to_info(node)

        # ------------------------------------------------------------------
        # Directory listing
        # ------------------------------------------------------------------

        def read_directory(self, file_context, marker, dirinfo_list):
            fd = file_context.get("fd")
            with self._lock:
                node = self._fd_map.get(fd)
            if node is None:
                raise NTStatusError(STATUS_NO_SUCH_FILE)

            path = file_context.get("path", "/")
            names = self._vfs.list_dir(path)
            if names is None:
                raise NTStatusError(STATUS_NOT_A_DIRECTORY)

            past_marker = (marker is None)
            for name in names:
                if not past_marker:
                    if name == marker:
                        past_marker = True
                    continue
                child_path = path.rstrip("/") + "/" + name
                child = self._vfs.get_node(child_path)
                if child is None:
                    continue
                info = _node_to_info(child)
                info["file_name"] = name
                dirinfo_list.append(info)

        # ------------------------------------------------------------------
        # File reading
        # ------------------------------------------------------------------

        def read(self, file_context, offset, length):
            fd = file_context.get("fd")
            with self._lock:
                node = self._fd_map.get(fd)
            if node is None:
                raise NTStatusError(STATUS_NO_SUCH_FILE)
            if node.is_dir:
                raise NTStatusError(STATUS_FILE_IS_A_DIRECTORY)
            try:
                return self._vfs.read_file(node, offset, length)
            except Exception as e:
                logger.exception("read error: %s", e)
                raise NTStatusError(0xC0000010)  # STATUS_INVALID_DEVICE_REQUEST

        # ------------------------------------------------------------------
        # Write stubs — all raise STATUS_MEDIA_WRITE_PROTECTED
        # ------------------------------------------------------------------

        def _write_protected(self, *a, **kw):
            raise NTStatusError(STATUS_MEDIA_WRITE_PROTECTED)

        write             = _write_protected
        create            = _write_protected
        overwrite         = _write_protected
        set_basic_info    = _write_protected
        set_file_size     = _write_protected
        can_delete        = _write_protected
        rename            = _write_protected
        set_security      = _write_protected
        create_directory  = _write_protected
        set_delete        = _write_protected


# ---------------------------------------------------------------------------
# Public mount / unmount
# ---------------------------------------------------------------------------

def mount(
    vfs: GitHubVFS,
    mountpoint: str,
    *,
    volume_label: str = "GitHub",
    debug: bool = False,
) -> None:
    """
    Mount the GitHub virtual filesystem on Windows via WinFSP.

    Args:
        vfs:          Configured GitHubVFS instance.
        mountpoint:   Drive letter (e.g. ``G:``) or empty directory path.
        volume_label: Label shown in Explorer.
        debug:        Enable WinFSP debug logging.
    """
    if not _WINFSPY_AVAILABLE:
        raise RuntimeError(
            "winfspy is not installed (or WinFSP is not installed on this system).\n"
            "  1. Download and install WinFSP from https://winfsp.dev/rel/\n"
            "     (select the 'Developer' feature during installation)\n"
            "  2. Run: pip install winfspy"
        )

    ops = _GHFSWinOperations(vfs, volume_label=volume_label)

    fs = FileSystem(
        mountpoint,
        ops,
        sector_size=512,
        sectors_per_allocation_unit=1,
        volume_creation_time=0,
        volume_serial_number=0xDEADBEEF,
        file_info_timeout=1000,
        case_sensitive_search=0,
        case_preserved_names=1,
        unicode_on_disk=1,
        persistent_acls=0,
        reparse_points=0,
        named_streams=0,
        read_only_volume=1,
        post_cleanup_when_modified_only=1,
        debug=debug,
    )

    logger.info("Mounting GitHub FS at %s via WinFSP", mountpoint)
    try:
        fs.start()
        logger.info("Mounted. Press Ctrl+C to unmount.")
        threading.Event().wait()   # Block until interrupted
    finally:
        fs.stop()
        logger.info("Unmounted.")


def unmount(mountpoint: str) -> None:
    """Unmount a WinFSP filesystem (simply sends Ctrl+C to the mount process)."""
    # For a long-running process this would typically be handled by process
    # management (e.g. killing the server process).
    logger.warning(
        "Unmounting on Windows requires stopping the ghfs process "
        "that owns the mount (%s).", mountpoint
    )
