# ghfs.spec
# PyInstaller build spec for GHFS standalone binary.
#
# Usage:
#   pyinstaller ghfs.spec
#
# Output:
#   dist/ghfs          (Linux / macOS — single binary)
#   dist/ghfs.exe      (Windows — single executable)
#
# The resulting binary bundles the Python interpreter + all dependencies.
# End users need ZERO Python installation.

import sys
import os
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

block_cipher = None

# ── Hidden imports ────────────────────────────────────────────────────────────
# PyInstaller's static analyser misses some dynamic imports; list them here.

hidden = [
    # stdlib modules used indirectly
    "urllib.request",
    "urllib.error",
    "urllib.parse",
    "urllib.response",
    "http.client",
    "email.mime.multipart",
    "email.mime.text",
    "threading",
    "pickle",
    "hashlib",
    "base64",
    "json",
    "stat",
    "collections",
    "collections.abc",
    # optional YAML (gh CLI token discovery) — include if present
]

# Platform-specific FUSE / WinFSP bindings
if sys.platform == "win32":
    hidden += collect_submodules("winfspy")
else:
    # Try 'refuse' first (preferred), fall back to 'fuse'
    try:
        import refuse  # noqa: F401
        hidden += collect_submodules("refuse")
    except ImportError:
        try:
            import fuse  # noqa: F401
            hidden += collect_submodules("fuse")
        except ImportError:
            pass  # will warn at runtime

# Optional PyYAML (gh CLI config)
try:
    import yaml  # noqa: F401
    hidden += collect_submodules("yaml")
except ImportError:
    pass

# ── Data files ───────────────────────────────────────────────────────────────
datas = []

# Include the package itself so __version__ etc. are accessible
datas += [("ghfs", "ghfs")]

# ── Analysis ─────────────────────────────────────────────────────────────────
a = Analysis(
    ["ghfs/__main__.py"],
    pathex=[os.path.abspath(".")],
    binaries=[],
    datas=datas,
    hiddenimports=hidden,
    hookspath=["packaging/pyinstaller_hooks"],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Heavy stdlib modules we never use
        "tkinter",
        "unittest",
        "xml",
        "xmlrpc",
        "distutils",
        "email",
        "multiprocessing",
        "asyncio",
        "concurrent",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# ── Single-file executable ───────────────────────────────────────────────────
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="ghfs",
    debug=False,
    bootloader_ignore_signals=False,
    strip=True,          # strip symbols → smaller binary
    upx=True,            # compress with UPX if available
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,        # CLI app — always console mode
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,    # use host arch; override with --target-arch for cross-compilation
    codesign_identity=None,   # set for macOS notarisation
    entitlements_file=None,
    # Windows-specific metadata
    version="packaging/win_version_info.txt" if sys.platform == "win32" else None,
    icon="packaging/icon.ico" if (sys.platform == "win32" and os.path.exists("packaging/icon.ico")) else None,
)
