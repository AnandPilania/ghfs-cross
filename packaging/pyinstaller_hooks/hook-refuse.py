# Ensures the refuse (or fusepy) shared library is bundled correctly.

from PyInstaller.utils.hooks import collect_dynamic_libs, collect_submodules
import sys

hiddenimports = collect_submodules("refuse")

# On Linux/macOS, refuse loads libfuse at runtime via ctypes.
# We do NOT bundle libfuse itself — it must come from the OS.
# We only need to make sure the Python wrapper is included.
if sys.platform != "win32":
    # Collect any compiled .so extension modules inside the refuse package
    binaries = collect_dynamic_libs("refuse")
else:
    binaries = []
