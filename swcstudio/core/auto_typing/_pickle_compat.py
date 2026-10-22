"""Let the labeler's pickled models load from SWC-Studio's copy of its code.

The Stage 2 model pickles two helper classes under their module path in the
labeler repository, ``pipeline._xgb_classifiers``. SWC-Studio's copy of that
module is ``swcstudio.core.labeler._xgb_classifiers``. While a model loads,
:func:`labeler_pickle_aliases` maps the original names onto the copy, so the
shipped files load unchanged; afterwards the names are removed again, so no
``pipeline`` module stays visible to the rest of the process.
"""
from __future__ import annotations

import sys
import threading
import types
from contextlib import contextmanager
from typing import Iterator

_LOCK = threading.RLock()


@contextmanager
def labeler_pickle_aliases() -> Iterator[None]:
    """Expose ``pipeline`` and ``pipeline._xgb_classifiers`` while the block runs."""
    from swcstudio.core.labeler import _xgb_classifiers  # noqa: PLC0415

    with _LOCK:
        added: list[str] = []
        if "pipeline" not in sys.modules:
            root = types.ModuleType("pipeline")
            root.__path__ = []  # package-like, so the submodule name resolves
            root._xgb_classifiers = _xgb_classifiers  # type: ignore[attr-defined]
            sys.modules["pipeline"] = root
            added.append("pipeline")
        if "pipeline._xgb_classifiers" not in sys.modules:
            sys.modules["pipeline._xgb_classifiers"] = _xgb_classifiers
            added.append("pipeline._xgb_classifiers")
        try:
            yield
        finally:
            for name in added:
                sys.modules.pop(name, None)
