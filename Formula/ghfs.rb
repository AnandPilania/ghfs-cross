# Homebrew formula for GHFS — installs the pre-built macOS binary.
#
# To create a tap and use this formula:
#   1. Create a GitHub repo named homebrew-tap (or homebrew-ghfs)
#   2. Copy this file into it as Formula/ghfs.rb
#   3. Users install with:
#        brew tap anandpilania/tap
#        brew install ghfs
#
# Or submit to homebrew-core once the project is established.

class Ghfs < Formula
  desc     "Mount GitHub repositories as a read-only virtual filesystem"
  homepage "https://github.com/anandpilania/ghfs-cross"
  version  "0.0.1"
  license  "MIT"

  # ── Pre-built binary (preferred — no Python required on the user's machine) ──
  on_macos do
    if Hardware::CPU.arm?
      url "https://github.com/anandpilania/ghfs-cross/releases/download/v#{version}/ghfs-macos-arm64"
      sha256 "REPLACE_WITH_ACTUAL_SHA256_FOR_ARM64_BINARY"
    else
      url "https://github.com/anandpilania/ghfs-cross/releases/download/v#{version}/ghfs-macos-x86_64"
      sha256 "REPLACE_WITH_ACTUAL_SHA256_FOR_X86_64_BINARY"
    end
  end

  # macFUSE is required at runtime (not bundled — it's a kernel extension)
  depends_on cask: "macfuse"

  def install
    # The downloaded file IS the binary
    bin.install Dir["ghfs-macos-*"].first => "ghfs"
  end

  def caveats
    <<~EOS
      GHFS requires macFUSE to be allowed in System Settings.
      After installation (or after an OS update), go to:
        System Settings → Privacy & Security → Security
      and click "Allow" next to the macFUSE kernel extension.

      Set your GitHub token before mounting:
        export GITHUB_TOKEN=ghp_your_token_here
        ghfs mount ~/ghfs
    EOS
  end

  test do
    # Basic smoke test — print help
    assert_match "usage", shell_output("#{bin}/ghfs --help 2>&1", 0)
  end
end
