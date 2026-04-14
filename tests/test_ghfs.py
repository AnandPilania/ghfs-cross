"""
Unit tests for GHFS.

These tests run without a real GitHub token by mocking the API client.
Integration tests that actually mount a filesystem are marked with
@pytest.mark.mount and are skipped in CI.
"""

import sys
import os
import time
import pytest
from unittest.mock import MagicMock, patch
from typing import List

# Make sure the package is importable even when not installed
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from ghfs.github_client import RepoInfo, TreeEntry, UserInfo
from ghfs.cache import MemoryCache, DiskCache
from ghfs.filesystem import GitHubVFS, FSNode


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_repo(owner: str, name: str, branch: str = "main") -> RepoInfo:
    return RepoInfo(
        owner=owner, name=name, full_name=f"{owner}/{name}",
        default_branch=branch, size=100, private=False, description=None,
    )


def _make_entry(path: str, type_: str = "blob", size: int = 42) -> TreeEntry:
    return TreeEntry(
        path=path,
        mode="100644" if type_ == "blob" else "040000",
        type=type_,
        sha=f"sha_{path.replace('/', '_')}",
        size=size if type_ == "blob" else None,
    )


@pytest.fixture
def mock_client():
    client = MagicMock()
    client.get_authenticated_user.return_value = UserInfo(
        login="alice", name="Alice", email="alice@example.com"
    )
    client.get_user_repos.return_value = [
        _make_repo("alice", "hello-world"),
        _make_repo("alice", "dotfiles"),
        _make_repo("acme-org", "backend"),   # org repo alice has access to
    ]
    client.get_user_orgs.return_value = []
    client.get_repo.side_effect = lambda owner, name: _make_repo(owner, name)
    client.get_repo_tree.return_value = [
        _make_entry("README.md"),
        _make_entry("src", type_="tree"),
        _make_entry("src/main.py"),
        _make_entry("src/utils.py"),
        _make_entry("tests", type_="tree"),
        _make_entry("tests/test_main.py"),
    ]
    client.get_blob.return_value = b"Hello, GitHub!\n"
    return client


@pytest.fixture
def cache():
    return MemoryCache(max_size=256, default_ttl=60.0)


@pytest.fixture
def vfs(mock_client, cache):
    return GitHubVFS(client=mock_client, cache=cache)


# ---------------------------------------------------------------------------
# Cache tests
# ---------------------------------------------------------------------------

class TestMemoryCache:
    def test_set_get(self, cache):
        cache.set("k", "v")
        assert cache.get("k") == "v"

    def test_ttl_expiry(self):
        c = MemoryCache(default_ttl=0.05)
        c.set("k", "v", ttl=0.05)
        assert c.get("k") == "v"
        time.sleep(0.1)
        assert c.get("k") is None

    def test_max_size_eviction(self):
        c = MemoryCache(max_size=3)
        for i in range(5):
            c.set(str(i), i, ttl=100)
        # Only 3 entries should remain
        assert len(c) == 3

    def test_invalidate_prefix(self, cache):
        cache.set("blob:a/b/sha1", b"x")
        cache.set("blob:a/b/sha2", b"y")
        cache.set("tree:a/b", [])
        removed = cache.invalidate_prefix("blob:")
        assert removed == 2
        assert cache.get("tree:a/b") is not None

    def test_delete(self, cache):
        cache.set("k", 1)
        cache.delete("k")
        assert cache.get("k") is None


class TestDiskCache:
    def test_set_get(self, tmp_path):
        dc = DiskCache(str(tmp_path))
        dc.set("hello", {"a": 1}, ttl=60)
        assert dc.get("hello") == {"a": 1}

    def test_expiry(self, tmp_path):
        dc = DiskCache(str(tmp_path), default_ttl=0.05)
        dc.set("k", "v", ttl=0.05)
        time.sleep(0.1)
        assert dc.get("k") is None

    def test_bytes_value(self, tmp_path):
        dc = DiskCache(str(tmp_path))
        dc.set("blob", b"\x00\x01\x02", ttl=60)
        assert dc.get("blob") == b"\x00\x01\x02"


# ---------------------------------------------------------------------------
# VFS tests
# ---------------------------------------------------------------------------

class TestGitHubVFS:
    def test_root_lists_owners(self, vfs):
        names = vfs.list_dir("/")
        assert "alice" in names
        assert "acme-org" in names

    def test_owner_lists_repos(self, vfs):
        names = vfs.list_dir("/alice")
        assert "hello-world" in names
        assert "dotfiles" in names

    def test_repo_node_is_directory(self, vfs):
        node = vfs.get_node("/alice/hello-world")
        assert node is not None
        assert node.is_dir

    def test_file_node_attributes(self, vfs):
        node = vfs.get_node("/alice/hello-world/README.md")
        assert node is not None
        assert not node.is_dir
        assert node.sha is not None

    def test_nested_directory(self, vfs):
        node = vfs.get_node("/alice/hello-world/src")
        assert node is not None
        assert node.is_dir

    def test_nested_file(self, vfs):
        node = vfs.get_node("/alice/hello-world/src/main.py")
        assert node is not None
        assert not node.is_dir

    def test_nonexistent_path_returns_none(self, vfs):
        assert vfs.get_node("/alice/nonexistent-repo") is None
        assert vfs.get_node("/alice/hello-world/no-such-file.txt") is None

    def test_list_dir_nonexistent(self, vfs):
        assert vfs.list_dir("/nobody") is None
        assert vfs.list_dir("/alice/hello-world/README.md") is None  # file not dir

    def test_read_file(self, vfs):
        node = vfs.get_node("/alice/hello-world/README.md")
        content = vfs.read_file(node, 0, 1024)
        assert content == b"Hello, GitHub!\n"

    def test_read_file_offset(self, vfs):
        node = vfs.get_node("/alice/hello-world/README.md")
        content = vfs.read_file(node, 7, 6)
        assert content == b"GitHub"

    def test_read_caches_blob(self, vfs, mock_client):
        node = vfs.get_node("/alice/hello-world/README.md")
        vfs.read_file(node, 0, 1024)
        vfs.read_file(node, 0, 1024)
        # Should only call the API once
        mock_client.get_blob.assert_called_once()

    def test_tree_loaded_once_per_repo(self, vfs, mock_client):
        vfs.get_node("/alice/hello-world/README.md")
        vfs.get_node("/alice/hello-world/src/main.py")
        # Tree should only be fetched once for the same repo
        assert mock_client.get_repo_tree.call_count == 1

    def test_stat_directory(self, vfs):
        node = vfs.get_node("/alice/hello-world")
        st = node.to_stat()
        import stat
        assert stat.S_ISDIR(st["st_mode"])

    def test_stat_file(self, vfs):
        node = vfs.get_node("/alice/hello-world/README.md")
        st = node.to_stat()
        import stat
        assert stat.S_ISREG(st["st_mode"])

    def test_extra_owners(self, cache):
        client = MagicMock()
        client.get_authenticated_user.return_value = UserInfo("alice", None, None)
        client.get_user_repos.return_value = []
        client.get_public_user_repos.return_value = [_make_repo("linus", "linux")]
        client.get_repo.return_value = _make_repo("linus", "linux")
        client.get_repo_tree.return_value = [_make_entry("Makefile")]
        client.get_blob.return_value = b""

        vfs = GitHubVFS(client=client, cache=cache, extra_owners=["linus"])
        names = vfs.list_dir("/")
        assert "linus" in names

    def test_refresh_repo_invalidates(self, vfs, mock_client):
        # Load once
        vfs.get_node("/alice/hello-world/README.md")
        call_count = mock_client.get_repo_tree.call_count

        # Refresh
        mock_client.get_repo_tree.return_value = [_make_entry("NEWFILE.md")]
        vfs.refresh_repo("alice", "hello-world")
        vfs.get_node("/alice/hello-world/NEWFILE.md")

        assert mock_client.get_repo_tree.call_count == call_count + 1


# ---------------------------------------------------------------------------
# CLI tests (no actual mounting)
# ---------------------------------------------------------------------------

class TestCLI:
    def test_info_command(self, mock_client, cache):
        from ghfs.cli import cmd_info
        import argparse

        args = argparse.Namespace(token="mock_token", log_level="WARNING", unauthenticated=False)
        with patch("ghfs.cli.GitHubClient", return_value=mock_client):
            rc = cmd_info(args)
        assert rc == 0

    def test_info_command_api_error(self):
        from ghfs.cli import cmd_info
        from ghfs.github_client import GitHubAPIError
        import argparse

        client = MagicMock()
        client.get_authenticated_user.side_effect = GitHubAPIError("Unauthorized", 401)

        args = argparse.Namespace(token="bad_token", log_level="WARNING")
        with patch("ghfs.cli.GitHubClient", return_value=client):
            rc = cmd_info(args)
        assert rc == 1
