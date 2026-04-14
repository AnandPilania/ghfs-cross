# GHFS — Cross-Platform GitHub Virtual Filesystem

Mount your GitHub repositories as a **read-only virtual filesystem** on **Windows, Linux, and macOS** — browse every repo and file as if they were local directories, with no cloning required.

```
$ ghfs mount ~/ghfs --token ghp_xxxx
✓ Authenticated as alice

$ ls ~/ghfs/alice/
hello-world/   dotfiles/   awesome-project/

$ cat ~/ghfs/alice/dotfiles/.zshrc
# my zsh config …

$ ls ~/ghfs/torvalds/linux/kernel/
fork.c  pid.c  signal.c  sched/  …
```

---

## Features

| Feature               | Details                                                                                            |
| --------------------- | -------------------------------------------------------------------------------------------------- |
| **Zero cloning**      | Files are fetched on demand from the GitHub API; no `git clone` needed                             |
| **All platforms**     | Windows (WinFSP), Linux (libfuse3), macOS (macFUSE)                                                |
| **No external tools** | No `git`, `gh`, or other CLI tools required                                                        |
| **Auth optional**     | Works unauthenticated for public repos (60 req/hr); authenticated for private repos (5 000 req/hr) |
| **Smart caching**     | In-memory LRU cache with TTL; optional disk cache for offline use                                  |
| **Multiple owners**   | Browse your own repos + any public user / org repos side-by-side                                   |
| **GitHub Enterprise** | Point `--api-url` at your GHE instance                                                             |
| **Read-only**         | All write operations return EROFS / STATUS_MEDIA_WRITE_PROTECTED                                   |

---

## Filesystem layout

```
/                           ← root
├── alice/                  ← your GitHub login (or any --owner)
│   ├── hello-world/        ← repository (default branch)
│   │   ├── README.md
│   │   └── src/
│   │       └── main.py
│   └── dotfiles/
├── acme-org/               ← organisation you're a member of
│   └── backend/
└── torvalds/               ← any --owner you added
    └── linux/
```

---

## Installation

### Linux

```bash
# 1. Install libfuse3
sudo apt install fuse3 libfuse3-dev    # Debian / Ubuntu
sudo dnf install fuse3 fuse3-devel     # Fedora / RHEL
sudo pacman -S fuse3                   # Arch

# 2. Install Python packages
pip install refuse ghfs

# Or run the helper script:
bash scripts/install.sh
```

> **Note:** On some distros you also need to add yourself to the `fuse` group:
> `sudo usermod -aG fuse $USER` then log out and back in.
> Alternatively, set `user_allow_other` in `/etc/fuse.conf` and pass `--allow-other`.

### macOS

```bash
# 1. Install macFUSE
brew install --cask macfuse
# Then: System Settings → Privacy & Security → Allow macFUSE

# 2. Install Python packages
pip install refuse ghfs

# Or:
bash scripts/install.sh
```

### Windows

```powershell
# 1. Download and install WinFSP (select "Developer" feature)
#    https://winfsp.dev/rel/

# 2. Install Python packages
pip install winfspy ghfs

# Or run the helper script (as Administrator):
.\scripts\install.ps1
```

---

## Quick Start

### Get a GitHub token

1. Go to <https://github.com/settings/tokens>
2. **Fine-grained tokens** (recommended): grant read-only access to Contents and Metadata for the repos you want to browse.
3. **Classic tokens**: the `repo` scope is sufficient.

Set it in your environment:

```bash
export GITHUB_TOKEN=ghp_your_token_here      # Linux / macOS
$env:GITHUB_TOKEN = "ghp_your_token_here"   # Windows PowerShell
```

GHFS also reads tokens from the [gh CLI](https://cli.github.com) config automatically.

### Mount

```bash
# Linux / macOS — mount at ~/ghfs
mkdir ~/ghfs
ghfs mount ~/ghfs

# Windows — mount as drive letter G:
ghfs mount G:

# Include extra public profiles / orgs
ghfs mount ~/ghfs --owner torvalds --owner gvanrossum

# GitHub Enterprise
ghfs mount ~/ghfs --api-url https://github.mycompany.com/api/v3

# Enable persistent disk cache (survives restarts)
ghfs mount ~/ghfs --cache-dir ~/.cache/ghfs --cache-ttl 3600

# Verbose debugging
ghfs mount ~/ghfs --debug --log-level DEBUG
```

### Unmount

```bash
ghfs unmount ~/ghfs      # Linux / macOS
# Windows: press Ctrl+C in the terminal running ghfs
```

### Account info

```bash
ghfs info                # shows repos, rate limit, etc.
```

---

## Command reference

```
ghfs mount <mountpoint> [options]
  --token, -t TOKEN       GitHub personal access token
  --owner LOGIN           Extra GitHub user/org to include (repeatable)
  --api-url URL           GitHub API base URL (for GHE)
  --unauthenticated       Proceed without token (public repos only)
  --allow-other           Allow other OS users to access the mount
  --cache-dir DIR         Enable disk cache in DIR
  --cache-ttl SECONDS     Cache TTL (default: 300)
  --backend fuse|winfsp   Force a specific backend
  --debug                 Verbose FUSE/WinFSP logging
  --log-level LEVEL       DEBUG | INFO | WARNING | ERROR

ghfs unmount <mountpoint>

ghfs info [--token TOKEN]
```

---

### How lazy loading works

1. On first `ls /`, GHFS calls `GET /user/repos` to build the owner/repo list.
2. When you open a repo directory (`ls ~/ghfs/alice/hello-world`), GHFS calls
   `GET /repos/alice/hello-world/git/trees/main?recursive=1` to fetch the full tree.
3. When you read a file, GHFS calls `GET /repos/alice/hello-world/git/blobs/{sha}`
   and caches the result for 24 hours.

The git tree (step 2) is cached for 1 hour by default. Blob content (step 3) is
cached for 24 hours. Both TTLs are configurable.

### Rate limits

| Auth state                                       | Limit                 |
| ------------------------------------------------ | --------------------- |
| Unauthenticated                                  | 60 requests / hour    |
| Authenticated (classic token / fine-grained PAT) | 5 000 requests / hour |

Each repo tree fetch + file read = 1–2 API calls. With a token, 5 000 req/hr is
plenty for browsing dozens of repos.

---

## Distribution & Publishing

GHFS supports two parallel distribution tracks — one for users **with Python**, one for users **without**.

```
                    ┌─────────────────────────────────────────┐
                    │           git tag v1.2.3                │
                    │        git push --tags                  │
                    └──────────────────┬──────────────────────┘
                                       │  GitHub Actions (release.yml)
                 ┌─────────────────────┼──────────────────────┐
                 ▼                     ▼                       ▼
        PyInstaller binary       PyInstaller binary     sdist + wheel
        (ubuntu-latest)          (windows-latest)       (build)
               │                       │                      │
               ▼                       ▼                      ▼
    ghfs-linux-x86_64      ghfs-windows-x86_64.exe     ghfs-1.x.x.tar.gz
    ghfs-macos-x86_64                                   ghfs-1.x.x-py3-none-any.whl
               │                       │                      │
               └───────────────────────┴──────────────────────┘
                                       │
                              GitHub Release assets
                              + SHA256SUMS.txt
                                       │
                              ┌────────┴────────┐
                              ▼                  ▼
                           PyPI            Package managers
                      pip install ghfs    (Homebrew/Scoop/winget/choco/deb)
```

### Track 1 — Standalone binary (no Python needed)

Built with **PyInstaller** — bundles the Python interpreter, all deps, and the FUSE/WinFSP bindings into one file.

```bash
# Build for the current platform
make binary              # → dist/ghfs  (Linux/macOS)
#                           dist/ghfs.exe (Windows)

# Users just download and run — no pip, no Python
chmod +x ghfs-linux-x86_64
./ghfs-linux-x86_64 mount ~/ghfs
```

Size is typically 12–20 MB after UPX compression.

### Track 2 — PyPI package (Python ≥ 3.9)

```bash
pip install ghfs                    # core (no FUSE binding)
pip install "ghfs[fuse]"            # + refuse (Linux/macOS)
pip install "ghfs[windows]"         # + winfspy (Windows)
pip install "ghfs[all]"             # everything
```

### Package managers

| Manager        | Platform      | Command                                                                                            |
| -------------- | ------------- | -------------------------------------------------------------------------------------------------- |
| **Homebrew**   | macOS         | `brew tap anandpilania/tap && brew install ghfs`                                                   |
| **Scoop**      | Windows       | `scoop bucket add anandpilania https://github.com/anandpilania/scoop-bucket && scoop install ghfs` |
| **winget**     | Windows       | `winget install anandpilania.ghfs`                                                                 |
| **Chocolatey** | Windows       | `choco install ghfs`                                                                               |
| **apt (.deb)** | Debian/Ubuntu | `dpkg -i ghfs_0.0.1_amd64.deb`                                                                     |

### Release process (maintainers)

```bash
# 1. Bump version in ghfs/__init__.py and pyproject.toml
# 2. Update CHANGELOG.md
# 3. Tag and push — CI does everything else:
make tag                 # creates v1.x.x tag and pushes it

# CI will:
#  - Run tests on all 3 platforms
#  - Build binaries with PyInstaller
#  - Build PyPI sdist + wheel
#  - Create GitHub Release with all assets + SHA256SUMS.txt
#  - Publish to PyPI (requires 'pypi' environment approval in repo settings)
```

### Updating package manifests after a release

After a new release, update the SHA256 checksums in:

```
Formula/ghfs.rb                    ← Homebrew
packaging/scoop/ghfs.json          ← Scoop
packaging/winget/ghfs.yaml         ← winget
packaging/choco/tools/chocolateyInstall.ps1  ← Chocolatey
```

SHA256 hashes are printed in the GitHub Release as `SHA256SUMS.txt`.

---

## Development

```bash
git clone https://github.com/anandpilania/ghfs-cross
cd ghfs-cross
pip install -e ".[fuse]"    # Linux / macOS
pip install -e ".[windows]" # Windows

# Run unit tests (no token needed — uses mocks)
pytest tests/ -v
```

### Running integration tests (real GitHub API)

```bash
export GITHUB_TOKEN=ghp_xxxx
pytest tests/ -v -m mount    # requires FUSE / WinFSP installed
```

---

## License

MIT — see [LICENSE](LICENSE).
