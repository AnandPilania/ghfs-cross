"""
GitHub REST API client for GHFS.
Handles authentication, repo listing, tree fetching, and blob retrieval.
"""

import base64
import time
import logging
from typing import Optional, List, Dict, Any
from dataclasses import dataclass
import urllib.request
import urllib.error
import json

logger = logging.getLogger(__name__)


@dataclass
class UserInfo:
    login: str
    name: Optional[str]
    email: Optional[str]


@dataclass
class RepoInfo:
    owner: str
    name: str
    full_name: str
    default_branch: str
    size: int  # in KB
    private: bool
    description: Optional[str]


@dataclass
class TreeEntry:
    path: str
    mode: str
    type: str   # 'blob' or 'tree'
    sha: str
    size: Optional[int]  # only present for blobs


@dataclass
class OrgInfo:
    login: str
    description: Optional[str]


class GitHubAPIError(Exception):
    def __init__(self, message: str, status_code: int = 0):
        super().__init__(message)
        self.status_code = status_code


class RateLimitError(GitHubAPIError):
    def __init__(self, reset_at: int):
        super().__init__(f"Rate limit exceeded. Resets at {reset_at}", 429)
        self.reset_at = reset_at


class GitHubClient:
    """
    Thin wrapper around the GitHub REST API v3.

    Args:
        token: Personal access token (or fine-grained PAT). If None,
               unauthenticated requests are used (60 req/hr limit).
        base_url: Override for GitHub Enterprise deployments,
                  e.g. "https://github.mycompany.com/api/v3"
    """

    BASE_URL = "https://api.github.com"

    def __init__(self, token: Optional[str] = None, base_url: Optional[str] = None):
        self._token = token
        self._base_url = (base_url or self.BASE_URL).rstrip("/")
        self._headers: Dict[str, str] = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "GHFS-CrossPlatform/1.0",
        }
        if token:
            self._headers["Authorization"] = f"Bearer {token}"

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _request(self, path: str, params: Optional[Dict[str, str]] = None) -> Any:
        url = f"{self._base_url}{path}"
        if params:
            query = "&".join(f"{k}={v}" for k, v in params.items())
            url = f"{url}?{query}"

        req = urllib.request.Request(url, headers=self._headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = {}
            try:
                body = json.loads(e.read().decode("utf-8"))
            except Exception:
                pass

            if e.code == 403 and "rate limit" in body.get("message", "").lower():
                reset = int(e.headers.get("X-RateLimit-Reset", time.time() + 60))
                raise RateLimitError(reset) from e
            if e.code == 404:
                raise GitHubAPIError(f"Not found: {url}", 404) from e
            raise GitHubAPIError(
                body.get("message", str(e)), e.code
            ) from e
        except urllib.error.URLError as e:
            raise GitHubAPIError(f"Network error: {e.reason}") from e

    def _paginate(self, path: str, params: Optional[Dict[str, str]] = None) -> List[Any]:
        """Collect all pages from a paginated endpoint."""
        results = []
        page = 1
        per_page = 100
        base_params = dict(params or {})
        base_params["per_page"] = str(per_page)

        while True:
            base_params["page"] = str(page)
            data = self._request(path, base_params)
            if not data:
                break
            results.extend(data)
            if len(data) < per_page:
                break
            page += 1

        return results

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_authenticated_user(self) -> UserInfo:
        data = self._request("/user")
        return UserInfo(
            login=data["login"],
            name=data.get("name"),
            email=data.get("email"),
        )

    def get_user_repos(self) -> List[RepoInfo]:
        """All repos accessible to the authenticated user (own + org member)."""
        items = self._paginate("/user/repos", {"type": "all", "sort": "full_name"})
        return [self._repo_from_dict(r) for r in items]

    def get_user_orgs(self) -> List[OrgInfo]:
        items = self._paginate("/user/orgs")
        return [OrgInfo(login=o["login"], description=o.get("description")) for o in items]

    def get_org_repos(self, org: str) -> List[RepoInfo]:
        items = self._paginate(f"/orgs/{org}/repos", {"type": "all", "sort": "full_name"})
        return [self._repo_from_dict(r) for r in items]

    def get_public_user_repos(self, username: str) -> List[RepoInfo]:
        items = self._paginate(f"/users/{username}/repos", {"sort": "full_name"})
        return [self._repo_from_dict(r) for r in items]

    def get_repo(self, owner: str, repo: str) -> RepoInfo:
        data = self._request(f"/repos/{owner}/{repo}")
        return self._repo_from_dict(data)

    def get_repo_tree(self, owner: str, repo: str, sha: str) -> List[TreeEntry]:
        """
        Returns the full recursive tree for a ref (branch, tag, or commit SHA).
        For very large repos the API may truncate; we fall back to paginated
        directory-level fetches when truncated=True.
        """
        data = self._request(
            f"/repos/{owner}/{repo}/git/trees/{sha}",
            {"recursive": "1"},
        )
        entries = [self._tree_entry_from_dict(e) for e in data.get("tree", [])]
        if data.get("truncated"):
            logger.warning(
                "Tree for %s/%s is truncated by GitHub API (repo too large). "
                "Some files may be missing until lazy directory loading is triggered.",
                owner, repo,
            )
        return entries

    def get_directory_contents(self, owner: str, repo: str, path: str, ref: str) -> List[TreeEntry]:
        """Fetch a single directory level via the Contents API (fallback for large repos)."""
        url_path = f"/repos/{owner}/{repo}/contents/{path.lstrip('/')}"
        items = self._request(url_path, {"ref": ref})
        if not isinstance(items, list):
            raise GitHubAPIError(f"Expected directory listing at {path}")
        return [
            TreeEntry(
                path=item["path"],
                mode="040000" if item["type"] == "dir" else "100644",
                type="tree" if item["type"] == "dir" else "blob",
                sha=item["sha"],
                size=item.get("size"),
            )
            for item in items
        ]

    def get_blob(self, owner: str, repo: str, sha: str) -> bytes:
        """Fetch raw file content by blob SHA."""
        data = self._request(f"/repos/{owner}/{repo}/git/blobs/{sha}")
        if data.get("encoding") == "base64":
            # GitHub may include line breaks in the base64 string
            return base64.b64decode(data["content"].replace("\n", ""))
        return data["content"].encode("utf-8")

    def get_file_by_path(self, owner: str, repo: str, path: str, ref: str) -> bytes:
        """Fetch raw file content via the Contents API (alternative to blob SHA)."""
        data = self._request(
            f"/repos/{owner}/{repo}/contents/{path.lstrip('/')}",
            {"ref": ref},
        )
        if isinstance(data, list):
            raise GitHubAPIError(f"{path} is a directory")
        return base64.b64decode(data["content"].replace("\n", ""))

    def get_rate_limit(self) -> Dict[str, Any]:
        return self._request("/rate_limit")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _repo_from_dict(d: Dict[str, Any]) -> RepoInfo:
        return RepoInfo(
            owner=d["owner"]["login"],
            name=d["name"],
            full_name=d["full_name"],
            default_branch=d.get("default_branch", "main"),
            size=d.get("size", 0),
            private=d.get("private", False),
            description=d.get("description"),
        )

    @staticmethod
    def _tree_entry_from_dict(d: Dict[str, Any]) -> TreeEntry:
        return TreeEntry(
            path=d["path"],
            mode=d.get("mode", "100644"),
            type=d["type"],
            sha=d["sha"],
            size=d.get("size"),
        )
