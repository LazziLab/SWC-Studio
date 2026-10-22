"""Crash-isolated process entry point for GUI type-suspicion inference.

On macOS, scikit-learn and XGBoost wheels can each load a
different OpenMP runtime. Loading the XGBoost model after the full Qt GUI
stack is resident has caused native ``libomp`` segmentation faults. A
separate process keeps that native failure outside the GUI and gives the
child a clean library-loading order.
"""

from __future__ import annotations

import pickle
import sys
from pathlib import Path


def run_files(input_path: str, output_path: str) -> None:
    """Compute type-suspicion issues from a trusted local pickle."""
    with Path(input_path).open("rb") as stream:
        dataframe = pickle.load(stream)  # noqa: S301 - private 0600 temp file

    from swcstudio.core.issues import compute_type_suspicion_issues

    issues = list(compute_type_suspicion_issues(dataframe))
    with Path(output_path).open("wb") as stream:
        pickle.dump(issues, stream, protocol=pickle.HIGHEST_PROTOCOL)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2:
        print(
            "usage: python -m swcstudio.gui.type_suspicion_process INPUT OUTPUT",
            file=sys.stderr,
        )
        return 2
    run_files(args[0], args[1])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
