"""
Core virtual filesystem that maps GitHub repositories to a directory tree.

Directory layout:
    /                     — root (lists all owners)
    /{owner}              — user or organisation
    /{owner}/{repo}       — repository root (default branch)
    /{owner}/{repo}/...   — files and directories inside the repo

This module is entirely OS-agnostic; platform adapters (FUSE, WinFSP, etc.)
call into this module to serve filesystem operations.
"""

import os
import stat
import time
import threading
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .github_client import GitHubClient, RepoInfo, TreeEntry, GitHubAPIError
from .cache import MemoryCache

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Node types
# ---------------------------------------------------------------------------

@dataclass
class FSNode:
    """A single node in the virtual filesystem tree."""
    name: str
    is_dir: bool
    size: int = 0
    mode: int = 0o444          # file permission bits (no execute by default)
    sha: Optional[str] = None  # blob SHA — only set for files
    owner: Optional[str] = None
    repo: Optional[str] = None
    ref: Optional[str] = None  # branch / tag / commit SHA

    # Directory children — populated lazily
    children: Dict[str, "FSNode"] = field(default_factory=dict)

    # Has this directory's children been fully loaded from the API?
    children_loaded: bool = False

    @property
    def st_mode(self) -> int:
        if self.is_dir:
            return stat.S_IFDIR | 0o555
        # Preserve executable bit from git mode (100755 → executable)
        exec_bit = 0o111 if self.mode & 0o111 else 0
        return stat.S_IFREG | 0o444 | exec_bit

    @property
    def st_size(self) -> int:
        return self.size

    def to_stat(self) -> dict:
        now = time.time()
        return {
            "st_mode":  self.st_mode,
            "st_ino":   0,
            "st_dev":   0,
            "st_nlink": 2 if self.is_dir else 1,
            "st_uid":   os.getuid() if hasattr(os, "getuid") else 0,
            "st_gid":   os.getgid() if hasattr(os, "getgid") else 0,
            "st_size":  self.st_size,
            "st_atime": now,
            "st_mtime": now,
            "st_ctime": now,
        }


# ---------------------------------------------------------------------------
# Virtual filesystem
# ---------------------------------------------------------------------------

class GitHubVFS:
    """
    Presents GitHub repositories as a read-only virtual filesystem.

    The tree is built lazily:
      - The list of owners / organisations is fetched once on first access.
      - Each repo's tree is fetched the first time that repo directory is
        opened (getattr or readdir).
      - Blob content is fetched on demand and cached.

    Thread safety: all public methods acquire ``_lock`` where necessary.
    The lock is a simple threading.Lock — long I/O operations (API calls)
    release it to avoid blocking other filesystem operations.
    """

    # How long (seconds) to cache the user's repo list before re-fetching
    REPO_LIST_TTL: float = 120.0
    # How long to cache a repo's git tree
    TREE_TTL: float = 3600.0
    # How long to cache blob content
    BLOB_TTL: float = 86400.0

    def __init__(
        self,
        client: GitHubClient,
        cache: MemoryCache,
        *,
        extra_owners: Optional[List[str]] = None,
    ):
        self._client = client
        self._cache = cache
        self._extra_owners = extra_owners or []
        self._lock = threading.Lock()

        # Root children: owner-login → FSNode(is_dir=True)
        self._owners: Dict[str, FSNode] = {}
        self._owners_loaded = False
        self._owners_loaded_at: float = 0.0

        # Set of (owner, repo) pairs whose tree has been loaded
        self._trees_loaded: set = set()

        # Current authenticated user login
        self._me: Optional[str] = None

    # ------------------------------------------------------------------
    # Path resolution
    # ------------------------------------------------------------------

    def _split_path(self, path: str) -> List[str]:
        """'/a/b/c' → ['a', 'b', 'c'], '/' → []"""
        return [p for p in path.replace("\\", "/").split("/") if p]

    def get_node(self, path: str) -> Optional[FSNode]:
        """
        Return the FSNode for *path*, triggering lazy loads as needed.
        Returns None if the path does not exist.
        """
        parts = self._split_path(path)

        if not parts:
            # Root
            self._ensure_owners_loaded()
            root = FSNode(name="/", is_dir=True, children=self._owners, children_loaded=True)
            return root

        # Ensure owner list is ready
        self._ensure_owners_loaded()

        owner = parts[0]
        if owner not in self._owners:
            return None
        if len(parts) == 1:
            return self._owners[owner]

        repo_name = parts[1]
        owner_node = self._owners[owner]
        if repo_name not in owner_node.children:
            return None

        # Ensure this repo's tree is loaded before we descend further
        self._ensure_tree_loaded(owner, repo_name)

        current = owner_node.children[repo_name]
        for part in parts[2:]:
            if not current.is_dir:
                return None
            if part not in current.children:
                return None
            current = current.children[part]

        return current

    def list_dir(self, path: str) -> Optional[List[str]]:
        """Return child names for a directory, or None if not a directory / not found."""
        parts = self._split_path(path)

        if not parts:
            self._ensure_owners_loaded()
            return list(self._owners.keys())

        self._ensure_owners_loaded()
        owner = parts[0]
        if owner not in self._owners:
            return None

        if len(parts) == 1:
            return list(self._owners[owner].children.keys())

        repo_name = parts[1]
        if repo_name not in self._owners[owner].children:
            return None

        self._ensure_tree_loaded(owner, repo_name)
        current = self._owners[owner].children[repo_name]
        for part in parts[2:]:
            if part not in current.children:
                return None
            current = current.children[part]

        if not current.is_dir:
            return None
        return list(current.children.keys())

    # ------------------------------------------------------------------
    # File reading
    # ------------------------------------------------------------------

    def read_file(self, node: FSNode, offset: int, length: int) -> bytes:
        """
        Read *length* bytes from *offset* in the file represented by *node*.
        Content is fetched from the GitHub API and cached by SHA.
        """
        if node.is_dir:
            raise IsADirectoryError(node.name)
        if node.sha is None:
            return b""

        cache_key = f"blob:{node.owner}/{node.repo}/{node.sha}"
        content = self._cache.get(cache_key)
        if content is None:
            logger.debug("Fetching blob %s for %s/%s", node.sha, node.owner, node.repo)
            try:
                content = self._client.get_blob(node.owner, node.repo, node.sha)
            except GitHubAPIError as e:
                logger.error("Failed to fetch blob %s: %s", node.sha, e)
                raise OSError(str(e)) from e
            self._cache.set(cache_key, content, ttl=self.BLOB_TTL)

        return content[offset: offset + length]

    # ------------------------------------------------------------------
    # Lazy loaders
    # ------------------------------------------------------------------

    def _ensure_owners_loaded(self) -> None:
        now = time.monotonic()
        with self._lock:
            if self._owners_loaded and (now - self._owners_loaded_at) < self.REPO_LIST_TTL:
                return

        # Fetch outside the lock to avoid blocking other FS ops
        try:
            owners = self._fetch_owners()
        except GitHubAPIError as e:
            logger.error("Failed to load owner/repo list: %s", e)
            if not self._owners_loaded:
                # First load failed — show an empty root
                owners = {}
            else:
                return  # Keep stale data

        with self._lock:
            # Merge new data, preserving already-loaded tree nodes
            for login, owner_node in owners.items():
                if login not in self._owners:
                    self._owners[login] = owner_node
                else:
                    existing = self._owners[login]
                    for repo_name, repo_node in owner_node.children.items():
                        if repo_name not in existing.children:
                            existing.children[repo_name] = repo_node
            self._owners_loaded = True
            self._owners_loaded_at = time.monotonic()

    def _fetch_owners(self) -> Dict[str, FSNode]:
        """Call GitHub API and build owner/repo structure."""
        owners: Dict[str, FSNode] = {}

        def _add_repo(repo: RepoInfo) -> None:
            if repo.owner not in owners:
                owners[repo.owner] = FSNode(
                    name=repo.owner,
                    is_dir=True,
                    children={},
                    children_loaded=True,
                    owner=repo.owner,
                )
            node = owners[repo.owner]
            if repo.name not in node.children:
                node.children[repo.name] = FSNode(
                    name=repo.name,
                    is_dir=True,
                    size=repo.size * 1024,  # convert KB → bytes (approx)
                    owner=repo.owner,
                    repo=repo.name,
                    children={},
                    children_loaded=False,
                )

        # Authenticated user's repos
        try:
            me = self._client.get_authenticated_user()
            self._me = me.login
            for repo in self._client.get_user_repos():
                _add_repo(repo)
        except GitHubAPIError:
            # Unauthenticated — no user repos
            pass

        # Extra owners (public profiles or orgs)
        for login in self._extra_owners:
            try:
                for repo in self._client.get_public_user_repos(login):
                    _add_repo(repo)
            except GitHubAPIError as e:
                logger.warning("Could not load repos for %s: %s", login, e)

        return owners

    def _ensure_tree_loaded(self, owner: str, repo_name: str) -> None:
        key = (owner, repo_name)
        with self._lock:
            if key in self._trees_loaded:
                return

        try:
            self._load_tree(owner, repo_name)
        except GitHubAPIError as e:
            logger.error("Failed to load tree for %s/%s: %s", owner, repo_name, e)

        with self._lock:
            self._trees_loaded.add(key)

    def _load_tree(self, owner: str, repo_name: str) -> None:
        """Fetch the full git tree and wire it into the in-memory structure."""
        cache_key = f"tree:{owner}/{repo_name}"
        cached: Optional[Tuple[str, List[TreeEntry]]] = self._cache.get(cache_key)

        if cached is not None:
            ref, entries = cached
        else:
            logger.debug("Loading tree for %s/%s from API", owner, repo_name)
            repo_info = self._client.get_repo(owner, repo_name)
            ref = repo_info.default_branch
            entries = self._client.get_repo_tree(owner, repo_name, ref)
            self._cache.set(cache_key, (ref, entries), ttl=self.TREE_TTL)

        with self._lock:
            repo_node = self._owners.get(owner, FSNode("", True)).children.get(repo_name)
            if repo_node is None:
                return
            repo_node.ref = ref
            self._build_subtree(repo_node, entries, owner, repo_name, ref)
            repo_node.children_loaded = True

    @staticmethod
    def _build_subtree(
        root: FSNode,
        entries: List[TreeEntry],
        owner: str,
        repo: str,
        ref: str,
    ) -> None:
        """Insert a flat list of TreeEntry objects into the subtree rooted at *root*."""
        # Pre-sort so parent directories are always created before their children
        for entry in sorted(entries, key=lambda e: e.path):
            parts = entry.path.split("/")
            current = root
            for i, part in enumerate(parts):
                is_leaf = i == len(parts) - 1
                if part not in current.children:
                    if is_leaf:
                        mode_int = int(entry.mode, 8)
                        current.children[part] = FSNode(
                            name=part,
                            is_dir=(entry.type == "tree"),
                            size=entry.size or 0,
                            mode=mode_int,
                            sha=entry.sha if entry.type == "blob" else None,
                            owner=owner,
                            repo=repo,
                            ref=ref,
                            children={},
                            children_loaded=(entry.type == "blob"),
                        )
                    else:
                        # Intermediate directory implied by path (no explicit tree entry)
                        current.children[part] = FSNode(
                            name=part,
                            is_dir=True,
                            owner=owner,
                            repo=repo,
                            ref=ref,
                            children={},
                            children_loaded=False,
                        )
                current = current.children[part]

    # ------------------------------------------------------------------
    # Misc helpers for adapters
    # ------------------------------------------------------------------

    def refresh_repo(self, owner: str, repo_name: str) -> None:
        """Force-refresh a single repo's tree (invalidates cache)."""
        self._cache.delete(f"tree:{owner}/{repo_name}")
        key = (owner, repo_name)
        with self._lock:
            self._trees_loaded.discard(key)
            owner_node = self._owners.get(owner)
            if owner_node:
                repo_node = owner_node.children.get(repo_name)
                if repo_node:
                    repo_node.children.clear()
                    repo_node.children_loaded = False
        self._ensure_tree_loaded(owner, repo_name)

    def stats(self) -> Dict:
        return {
            "owners":       list(self._owners.keys()),
            "trees_loaded": len(self._trees_loaded),
            "cache_size":   len(self._cache),
        }
