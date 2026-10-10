"""swcstudio package entrypoint.

Layers:
- swcstudio.core: shared algorithm/data logic
- swcstudio.tools: tool + feature modules
- swcstudio.api: public Python API
- swcstudio.cli: terminal interface
- swcstudio.gui: desktop GUI interface
"""

import os as _os
import sys as _sys

# Several macOS wheels, xgboost among them, ship their own libomp.dylib; with
# two copies loaded into one process, concurrent OpenMP regions can segfault on
# arm64. KMP_DUPLICATE_LIB_OK silences the duplicate-load fast-fail; pinning
# OpenMP to a single thread keeps auto-label inference alive. setdefault lets
# power users override either knob.
if _sys.platform == "darwin":
    _os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
    _os.environ.setdefault("OMP_NUM_THREADS", "1")

from importlib.metadata import PackageNotFoundError, version as _pkg_version

try:
    __version__ = _pkg_version("swcstudio")
except PackageNotFoundError:
    # Local source checkout before installation.
    __version__ = "0.1.0"

__all__ = ["api", "core", "tools", "plugins", "cli", "gui", "__version__"]
