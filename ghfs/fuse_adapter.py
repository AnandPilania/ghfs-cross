"""
FUSE adapter for GHFS — works on Linux (libfuse3) and macOS (macFUSE).

Install dependencies:
  Linux:   sudo apt install fuse3 libfuse3-dev && pip install fusepy
  macOS:   brew install --cask macfuse  && pip install fusepy

We use the ``fusepy`` package (or its drop-in replacement ``refuse``).
If ``refuse`` is installed it is preferred because it also supports Windows
via WinFSP — but the FUSE-specific code in this file still targets Linux/macOS.
"""

import os
import sys
import errno
import logging
import threading

from .filesystem import GitHubVFS, FSNode

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Import the right FUSE binding
# ---------------------------------------------------------------------------

try:
    # refuse is a modernised fusepy fork (also exposes WinFSP on Windows)
    import refuse.high as fuse  # type: ignore
    _FUSE_LIB = "refuse"
except ImportError:
    try:
        import fuse  # type: ignore
        _FUSE_LIB = "fusepy"
    except ImportError:
        fuse = None  # type: ignore
        _FUSE_LIB = None


def fuse_available() -> bool:
    return fuse is not None


class _GHFSOperations(fuse.Operations if fuse else object):  # type: ignore
    """
    fusepy Operations implementation backed by GitHubVFS.

    All write operations raise EROFS (read-only filesystem).
    """

    def __init__(self, vfs: GitHubVFS):
        self._vfs = vfs
        self._fd_counter = 0
        self._fd_map: dict = {}   # fd → FSNode
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_node(self, path: str) -> FSNode:
        node = self._vfs.get_node(path)
        if node is None:
            raise fuse.FuseOSError(errno.ENOENT)
        return node

    # ------------------------------------------------------------------
    # Metadata operations
    # ------------------------------------------------------------------

    def getattr(self, path: str, fh=None):
        logger.debug("getattr %s", path)
        node = self._get_node(path)
        return node.to_stat()

    def access(self, path: str, mode: int):
        # We never allow write access
        if mode & os.W_OK:
            raise fuse.FuseOSError(errno.EROFS)

    def statfs(self, path: str):
        # Report a nominal 1 TB read-only filesystem
        block = 512
        total = (1 << 40) // block   # 1 TiB in 512-byte blocks
        return {
            "f_bsize":   block,
            "f_frsize":  block,
            "f_blocks":  total,
            "f_bfree":   0,
            "f_bavail":  0,
            "f_files":   1 << 20,
            "f_ffree":   0,
            "f_favail":  0,
            "f_flag":    0,
            "f_namemax": 255,
        }

    # ------------------------------------------------------------------
    # Directory operations
    # ------------------------------------------------------------------

    def readdir(self, path: str, fh):
        logger.debug("readdir %s", path)
        names = self._vfs.list_dir(path)
        if names is None:
            raise fuse.FuseOSError(errno.ENOTDIR)
        return [".", ".."] + names

    def opendir(self, path: str):
        self._get_node(path)  # validate existence
        return 0

    def releasedir(self, path: str, fh):
        pass

    # ------------------------------------------------------------------
    # File operations
    # ------------------------------------------------------------------

    def open(self, path: str, flags: int):
        # Reject any write flags
        write_flags = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
        if flags & write_flags:
            raise fuse.FuseOSError(errno.EROFS)

        node = self._get_node(path)
        if node.is_dir:
            raise fuse.FuseOSError(errno.EISDIR)

        with self._lock:
            self._fd_counter += 1
            fd = self._fd_counter
            self._fd_map[fd] = node
        return fd

    def release(self, path: str, fh: int):
        with self._lock:
            self._fd_map.pop(fh, None)

    def read(self, path: str, size: int, offset: int, fh: int):
        logger.debug("read %s off=%d size=%d", path, offset, size)
        with self._lock:
            node = self._fd_map.get(fh)
        if node is None:
            node = self._get_node(path)

        try:
            return self._vfs.read_file(node, offset, size)
        except OSError:
            raise
        except Exception as e:
            logger.exception("read error on %s: %s", path, e)
            raise fuse.FuseOSError(errno.EIO)

    # ------------------------------------------------------------------
    # Symlink / xattr stubs (always fail gracefully)
    # ------------------------------------------------------------------

    def readlink(self, path: str):
        node = self._get_node(path)
        if node.is_dir:
            raise fuse.FuseOSError(errno.EINVAL)
        # We don't follow symlinks from the git tree — return the raw target
        # (blob content) so callers can at least read it.
        raise fuse.FuseOSError(errno.EINVAL)

    # Write stubs — all raise EROFS
    def write(self, *a, **kw):       raise fuse.FuseOSError(errno.EROFS)
    def create(self, *a, **kw):      raise fuse.FuseOSError(errno.EROFS)
    def mkdir(self, *a, **kw):       raise fuse.FuseOSError(errno.EROFS)
    def rmdir(self, *a, **kw):       raise fuse.FuseOSError(errno.EROFS)
    def unlink(self, *a, **kw):      raise fuse.FuseOSError(errno.EROFS)
    def rename(self, *a, **kw):      raise fuse.FuseOSError(errno.EROFS)
    def symlink(self, *a, **kw):     raise fuse.FuseOSError(errno.EROFS)
    def link(self, *a, **kw):        raise fuse.FuseOSError(errno.EROFS)
    def chmod(self, *a, **kw):       raise fuse.FuseOSError(errno.EROFS)
    def chown(self, *a, **kw):       raise fuse.FuseOSError(errno.EROFS)
    def truncate(self, *a, **kw):    raise fuse.FuseOSError(errno.EROFS)
    def utimens(self, *a, **kw):     raise fuse.FuseOSError(errno.EROFS)
    def mknod(self, *a, **kw):       raise fuse.FuseOSError(errno.EROFS)


# ---------------------------------------------------------------------------
# Public mount function
# ---------------------------------------------------------------------------

def mount(
    vfs: GitHubVFS,
    mountpoint: str,
    *,
    foreground: bool = True,
    allow_other: bool = False,
    allow_root: bool = False,
    nonempty: bool = False,
    debug: bool = False,
) -> None:
    """
    Mount the GitHub virtual filesystem at *mountpoint* using FUSE.

    Args:
        vfs:         Configured GitHubVFS instance.
        mountpoint:  Local directory to mount onto.
        foreground:  If True, block until unmounted (Ctrl+C). If False,
                     daemonise (not yet implemented — always True for now).
        allow_other: Allow other users to access the mount (requires
                     ``user_allow_other`` in /etc/fuse.conf on Linux).
        allow_root:  Allow root to access the mount.
        nonempty:    Mount even if the mountpoint directory is not empty.
        debug:       Enable verbose FUSE debug logging.
    """
    if fuse is None:
        raise RuntimeError(
            "No FUSE library found. "
            "Install 'refuse' (pip install refuse) or 'fusepy' (pip install fusepy), "
            "and ensure libfuse3 / macFUSE is installed on your system."
        )

    if not os.path.isdir(mountpoint):
        os.makedirs(mountpoint, exist_ok=True)

    ops = _GHFSOperations(vfs)

    fuse_options: dict = {
        "foreground": foreground,
        "nothreads":  False,
        "debug":      debug,
    }
    if allow_other:
        fuse_options["allow_other"] = True
    if allow_root:
        fuse_options["allow_root"] = True
    if nonempty:
        fuse_options["nonempty"] = True

    # macOS-specific options
    if sys.platform == "darwin":
        fuse_options.setdefault("volname", "GitHub")
        fuse_options.setdefault("local", True)

    logger.info("Mounting GitHub FS at %s using %s", mountpoint, _FUSE_LIB)
    fuse.FUSE(ops, mountpoint, **fuse_options)


def unmount(mountpoint: str) -> None:
    """Unmount a previously mounted FUSE filesystem."""
    if sys.platform == "darwin":
        os.system(f"diskutil unmount '{mountpoint}'")
    else:
        os.system(f"fusermount3 -u '{mountpoint}' || fusermount -u '{mountpoint}'")
