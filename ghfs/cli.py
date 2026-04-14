"""
Command-line interface for GHFS.

Usage examples
--------------
Mount (interactive, reads token from env var GITHUB_TOKEN):
    ghfs mount ~/ghfs

Mount with an explicit token:
    ghfs mount ~/ghfs --token ghp_xxxx

Mount extra public GitHub profiles alongside your own:
    ghfs mount ~/ghfs --owner torvalds --owner gvanrossum

Mount a Windows drive letter:
    ghfs mount G: --token ghp_xxxx

Unmount:
    ghfs unmount ~/ghfs

Show account info and rate limit (authenticated):
    ghfs info --token ghp_xxxx

Show rate limit only (unauthenticated):
    ghfs info --unauthenticated
"""

import argparse
import logging
import os
import sys
import signal
import threading
from typing import List, Optional

from .github_client import GitHubClient, GitHubAPIError
from .cache import MemoryCache, DiskCache
from .filesystem import GitHubVFS


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

def _setup_logging(level: str) -> None:
    numeric = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        level=numeric,
        datefmt="%H:%M:%S",
    )


# ---------------------------------------------------------------------------
# Token resolution
# ---------------------------------------------------------------------------

def _resolve_token(token_arg: Optional[str]) -> Optional[str]:
    """Return token from --token flag, GITHUB_TOKEN env var, or gh CLI config."""
    if token_arg:
        return token_arg

    env = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if env:
        return env

    # Try reading from gh CLI config (~/.config/gh/hosts.yml)
    try:
        import yaml  # optional dependency  # noqa
        cfg_path = os.path.expanduser("~/.config/gh/hosts.yml")
        if os.path.exists(cfg_path):
            with open(cfg_path) as f:
                cfg = yaml.safe_load(f)
            gh_com = cfg.get("github.com", {})
            return gh_com.get("oauth_token")
    except Exception:
        pass

    return None


# ---------------------------------------------------------------------------
# Sub-commands
# ---------------------------------------------------------------------------

def cmd_mount(args: argparse.Namespace) -> int:
    _setup_logging(args.log_level)

    token = _resolve_token(args.token)
    if not token and not args.unauthenticated:
        print(
            "⚠️  No GitHub token found.\n"
            "   Set GITHUB_TOKEN, pass --token, or use --unauthenticated "
            "for public repos only (rate limit: 60 req/hr).",
            file=sys.stderr,
        )
        if args.require_token:
            return 1

    client = GitHubClient(token=token, base_url=args.api_url)

    # Verify connectivity / credentials
    if token:
        try:
            me = client.get_authenticated_user()
            print(f"✓ Authenticated as {me.login}")
        except GitHubAPIError as e:
            print(f"✗ GitHub authentication failed: {e}", file=sys.stderr)
            return 1
    else:
        print("ℹ️  Running unauthenticated — only public repos of specified --owner values.")

    # Cache
    if args.cache_dir:
        cache = DiskCache(
            os.path.expanduser(args.cache_dir),
            default_ttl=args.cache_ttl,
        )
        print(f"  Cache: {args.cache_dir}")
    else:
        cache = MemoryCache(default_ttl=args.cache_ttl)
        print("  Cache: in-memory")

    # Build VFS
    vfs = GitHubVFS(
        client=client,
        cache=cache,
        extra_owners=args.owner or [],
    )

    mountpoint = os.path.expanduser(args.mountpoint)

    # Choose adapter
    platform = sys.platform
    if platform == "win32" or args.backend == "winfsp":
        from .windows_adapter import mount, winfspy_available
        if not winfspy_available():
            print(
                "✗ WinFSP / winfspy not available. See README for installation.",
                file=sys.stderr,
            )
            return 1
        print(f"  Backend: WinFSP  →  {mountpoint}")
        try:
            mount(vfs, mountpoint, debug=args.debug)
        except KeyboardInterrupt:
            pass
    else:
        from .fuse_adapter import mount, unmount as fuse_unmount, fuse_available
        if not fuse_available():
            print(
                "✗ FUSE library not available.\n"
                "  Linux:  sudo apt install fuse3 libfuse3-dev && pip install refuse\n"
                "  macOS:  brew install --cask macfuse && pip install refuse",
                file=sys.stderr,
            )
            return 1
        if not os.path.isdir(mountpoint):
            os.makedirs(mountpoint, exist_ok=True)
        print(f"  Backend: FUSE    →  {mountpoint}")
        print("  Press Ctrl+C to unmount.\n")

        # Handle SIGTERM gracefully
        def _sigterm(signum, frame):
            fuse_unmount(mountpoint)
            sys.exit(0)
        signal.signal(signal.SIGTERM, _sigterm)

        try:
            mount(
                vfs,
                mountpoint,
                foreground=True,
                allow_other=args.allow_other,
                debug=args.debug,
            )
        except KeyboardInterrupt:
            pass
        finally:
            fuse_unmount(mountpoint)

    return 0


def cmd_unmount(args: argparse.Namespace) -> int:
    _setup_logging(args.log_level)
    mountpoint = os.path.expanduser(args.mountpoint)

    if sys.platform == "win32":
        from .windows_adapter import unmount
    else:
        from .fuse_adapter import unmount  # type: ignore

    unmount(mountpoint)
    print(f"Unmounted {mountpoint}")
    return 0


def cmd_info(args: argparse.Namespace) -> int:
    _setup_logging(args.log_level)
    token = _resolve_token(args.token)
    client = GitHubClient(token=token)

    try:
        if token:
            me = client.get_authenticated_user()
            print(f"Authenticated as: {me.login}")
            repos = client.get_user_repos()
            print(f"Repositories visible: {len(repos)}")
            for r in repos[:20]:
                priv = " [private]" if r.private else ""
                print(f"  {r.full_name}{priv}  ({r.default_branch})")
            if len(repos) > 20:
                print(f"  … and {len(repos) - 20} more")
        else:
            print("ℹ️  Running unauthenticated — repo listing unavailable.")
            print("   Set GITHUB_TOKEN or pass --token to see your repositories.")

        rl = client.get_rate_limit()
        core = rl.get("rate", rl.get("resources", {}).get("core", {}))
        print(
            f"\nRate limit: {core.get('remaining', '?')} / {core.get('limit', '?')} remaining"
        )
    except GitHubAPIError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    return 0


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ghfs",
        description="Mount GitHub repositories as a virtual read-only filesystem.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--log-level", default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
                        help="Logging verbosity (default: INFO)")

    sub = parser.add_subparsers(dest="command", required=True)

    # ---- mount ----
    p_mount = sub.add_parser("mount", help="Mount the filesystem")
    p_mount.add_argument("mountpoint",
                         help="Local directory or Windows drive letter (e.g. G:)")
    p_mount.add_argument("--token", "-t",
                         help="GitHub personal access token "
                              "(falls back to GITHUB_TOKEN env var)")
    p_mount.add_argument("--owner", action="append", metavar="LOGIN",
                         help="Additional GitHub user/org to include "
                              "(can be repeated, e.g. --owner torvalds)")
    p_mount.add_argument("--api-url", default="https://api.github.com",
                         help="GitHub API base URL (for GitHub Enterprise)")
    p_mount.add_argument("--unauthenticated", action="store_true",
                         help="Proceed without a token (public repos only)")
    p_mount.add_argument("--require-token", action="store_true",
                         help="Exit with error if no token is found")
    p_mount.add_argument("--allow-other", action="store_true",
                         help="Allow other users to access the mount (Linux/macOS)")
    p_mount.add_argument("--cache-dir", metavar="DIR",
                         help="Persist API responses to disk cache in DIR")
    p_mount.add_argument("--cache-ttl", type=float, default=300.0, metavar="SECONDS",
                         help="Default cache TTL in seconds (default: 300)")
    p_mount.add_argument("--backend", choices=["fuse", "winfsp"],
                         help="Force a specific backend (auto-detected by default)")
    p_mount.add_argument("--debug", action="store_true",
                         help="Enable verbose FUSE/WinFSP debug output")
    p_mount.set_defaults(func=cmd_mount)

    # ---- unmount ----
    p_umount = sub.add_parser("unmount", aliases=["umount"],
                               help="Unmount the filesystem")
    p_umount.add_argument("mountpoint")
    p_umount.set_defaults(func=cmd_unmount)

    # ---- info ----
    p_info = sub.add_parser("info", help="Show GitHub account info and rate limit")
    p_info.add_argument("--token", "-t")
    p_info.add_argument("--unauthenticated", action="store_true",
                        help="Proceed without a token (shows rate limit only)")
    p_info.set_defaults(func=cmd_info)

    return parser


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return args.func(args)
