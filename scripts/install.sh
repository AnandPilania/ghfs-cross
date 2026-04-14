#!/usr/bin/env bash
# Set up GHFS on Linux or macOS
set -euo pipefail

OS="$(uname -s)"

echo "=== GHFS installer ==="
echo "Platform: $OS"

# ---- macOS ----
if [[ "$OS" == "Darwin" ]]; then
    echo
    echo "Installing macFUSE (requires Homebrew)..."
    if ! command -v brew &>/dev/null; then
        echo "ERROR: Homebrew not found. Install it from https://brew.sh then re-run."
        exit 1
    fi
    if brew list --cask macfuse &>/dev/null; then
        echo "  macFUSE already installed."
    else
        brew install --cask macfuse
        echo
        echo "  ⚠️  macFUSE requires a system extension to be allowed."
        echo "  Open System Settings → Privacy & Security → scroll down → Allow"
        echo "  Then re-run this script."
        echo
    fi
fi

# ---- Linux ----
if [[ "$OS" == "Linux" ]]; then
    echo
    echo "Installing libfuse3..."
    if command -v apt-get &>/dev/null; then
        sudo apt-get install -y fuse3 libfuse3-dev
    elif command -v dnf &>/dev/null; then
        sudo dnf install -y fuse3 fuse3-devel
    elif command -v pacman &>/dev/null; then
        sudo pacman -S --noconfirm fuse3
    else
        echo "  Unknown package manager — please install fuse3 manually."
    fi

    # Ensure user is in the fuse group (required on some distros)
    if getent group fuse &>/dev/null; then
        if ! groups | grep -q fuse; then
            echo "  Adding $USER to 'fuse' group..."
            sudo usermod -aG fuse "$USER"
            echo "  ⚠️  Log out and back in for the group change to take effect."
        fi
    fi
fi

# ---- Python package ----
echo
echo "Installing Python package and FUSE bindings..."
pip install --upgrade "refuse>=0.1.0"
pip install -e "$(dirname "$0")/.." || pip install ghfs

echo
echo "=== Done! ==="
echo
echo "Quick start:"
echo "  export GITHUB_TOKEN=ghp_your_token_here"
echo "  mkdir ~/ghfs"
echo "  ghfs mount ~/ghfs"
echo "  ls ~/ghfs"
echo "  ghfs unmount ~/ghfs"
