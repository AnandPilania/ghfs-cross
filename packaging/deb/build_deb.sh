#!/usr/bin/env bash
# Builds a .deb package from the pre-built Linux binary.
# Must be run after PyInstaller has produced dist/ghfs-linux-x86_64.
#
# Usage:
#   bash packaging/deb/build_deb.sh [version]

set -euo pipefail

VERSION="${1:-0.0.1}"
PKG="ghfs_${VERSION}_amd64"
BINARY="dist/ghfs-linux-x86_64"

if [[ ! -f "$BINARY" ]]; then
  echo "ERROR: $BINARY not found. Run PyInstaller first."
  exit 1
fi

echo "Building $PKG.deb …"

# ── Build directory layout ───────────────────────────────────────────────────
rm -rf "/tmp/$PKG"
mkdir -p "/tmp/$PKG/DEBIAN"
mkdir -p "/tmp/$PKG/usr/bin"
mkdir -p "/tmp/$PKG/usr/share/doc/ghfs"
mkdir -p "/tmp/$PKG/usr/share/man/man1"

# ── Copy files ───────────────────────────────────────────────────────────────
cp "$BINARY"                        "/tmp/$PKG/usr/bin/ghfs"
chmod 755                           "/tmp/$PKG/usr/bin/ghfs"

cp packaging/deb/DEBIAN/control     "/tmp/$PKG/DEBIAN/control"
sed -i "s/^Version: .*/Version: $VERSION/" "/tmp/$PKG/DEBIAN/control"

cp README.md                        "/tmp/$PKG/usr/share/doc/ghfs/README.md"
cp LICENSE                          "/tmp/$PKG/usr/share/doc/ghfs/copyright"

# ── Man page (optional) ──────────────────────────────────────────────────────
if command -v gzip &>/dev/null && [[ -f "packaging/deb/ghfs.1" ]]; then
  gzip -9c "packaging/deb/ghfs.1" > "/tmp/$PKG/usr/share/man/man1/ghfs.1.gz"
fi

# ── post-install script ──────────────────────────────────────────────────────
cat > "/tmp/$PKG/DEBIAN/postinst" <<'POSTINST'
#!/bin/sh
set -e
echo ""
echo "GHFS installed. Quick start:"
echo "  export GITHUB_TOKEN=ghp_your_token_here"
echo "  mkdir ~/ghfs && ghfs mount ~/ghfs"
echo ""
# Ensure fuse group exists and remind user
if getent group fuse > /dev/null 2>&1; then
  echo "Tip: add yourself to the fuse group for non-root mounting:"
  echo "  sudo usermod -aG fuse \$USER   (then log out and back in)"
fi
POSTINST
chmod 755 "/tmp/$PKG/DEBIAN/postinst"

# ── Build ────────────────────────────────────────────────────────────────────
dpkg-deb --build "/tmp/$PKG" "dist/${PKG}.deb"
echo "Done: dist/${PKG}.deb ($(du -sh "dist/${PKG}.deb" | cut -f1))"
