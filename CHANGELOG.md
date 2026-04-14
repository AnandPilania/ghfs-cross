# Changelog

All notable changes to GHFS are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/0.0.1/).
Versioning follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.0.1] — 2026-04-14

### Added
- Initial cross-platform release (Windows, Linux, macOS)
- FUSE adapter for Linux and macOS via `refuse` / `fusepy`
- WinFSP adapter for Windows via `winfspy`
- GitHub REST API client (pure stdlib — zero mandatory dependencies)
- Thread-safe LRU + TTL in-memory cache with optional disk persistence
- Lazy loading: repo trees fetched on first open; blobs fetched on first read
- CLI: `ghfs mount`, `ghfs unmount`, `ghfs info`
- Support for extra owners / organisations (`--owner`)
- GitHub Enterprise support (`--api-url`)
- Standalone binary distribution via PyInstaller
- Homebrew formula, Scoop manifest, Chocolatey package, winget manifest
- 19 unit tests (all passing, no token or FUSE required)

[Unreleased]: https://github.com/anandpilania/ghfs-cross/compare/v0.0.1...HEAD
[0.0.1]: https://github.com/anandpilania/ghfs-cross/releases/tag/v0.0.1
