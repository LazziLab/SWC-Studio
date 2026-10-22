"""QThread-friendly workers for the auto-typing engine.

The auto-typing engine is CPU-bound (feature extraction and XGBoost
prediction) and can take seconds per file. Running it on
the GUI thread would freeze the window. These QObject workers wrap
the engine calls so the panels can run them on a ``QThread`` and stay
responsive.

Two workers are provided:

* ``_AutoLabelFileWorker`` — single-file path used by the Validation
  Auto Label panel. Wraps the temp-file write + ``run_file`` call.
* ``_AutoLabelBatchWorker`` — folder path used by the Batch Processing
  Auto Label panel. Drives a per-file progress callback so the panel's
  progress bar can update.
"""

from __future__ import annotations

import json
import os
import pickle
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Signal, Slot


class _AutoLabelFileWorker(QObject):
    """Run auto-typing on one SWC file.

    Emits ``finished(run_id, result_obj)`` on success or
    ``failed(run_id, error_message)`` on any exception.
    """

    finished = Signal(int, object)
    failed = Signal(int, str)

    def __init__(
        self,
        run_id: int,
        file_path: str,
        options: object,
        config_overrides: dict[str, Any] | None,
    ):
        super().__init__()
        self._run_id = int(run_id)
        self._file_path = str(file_path)
        self._options = options
        self._config_overrides = dict(config_overrides) if config_overrides else None

    @staticmethod
    def _subprocess_command(request_path: str, output_path: str) -> list[str]:
        if getattr(sys, "frozen", False):
            return [
                sys.executable,
                "--swcstudio-auto-label-worker",
                request_path,
                output_path,
            ]
        return [
            sys.executable,
            "-m",
            "swcstudio.gui.auto_label_process",
            request_path,
            output_path,
        ]

    def _run_isolated(self) -> object:
        with tempfile.TemporaryDirectory(prefix="swcstudio-auto-label-") as tmp:
            request_path = Path(tmp) / "request.pkl"
            output_path = Path(tmp) / "output.pkl"
            request = {
                "kind": "single",
                "file_path": self._file_path,
                "options": self._options,
                "config_overrides": self._config_overrides,
            }
            with request_path.open("wb") as stream:
                pickle.dump(request, stream, protocol=pickle.HIGHEST_PROTOCOL)
            request_path.chmod(0o600)

            env = os.environ.copy()
            env.setdefault("OMP_NUM_THREADS", "1")
            env.setdefault("OPENBLAS_NUM_THREADS", "1")
            env.setdefault("MKL_NUM_THREADS", "1")
            completed = subprocess.run(
                self._subprocess_command(str(request_path), str(output_path)),
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            if completed.returncode != 0:
                detail = (completed.stderr or completed.stdout or "").strip()
                if completed.returncode < 0:
                    detail = (
                        f"auto-label subprocess terminated by signal "
                        f"{-completed.returncode}"
                        + (f": {detail}" if detail else "")
                    )
                raise RuntimeError(detail or f"auto-label subprocess exited {completed.returncode}")
            if not output_path.is_file():
                raise RuntimeError("auto-label subprocess produced no result")
            with output_path.open("rb") as stream:
                return pickle.load(stream)  # noqa: S301 - private child output

    @Slot()
    def run(self) -> None:  # noqa: D401 - Qt slot
        try:
            result = self._run_isolated()
            self.finished.emit(self._run_id, result)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(self._run_id, str(exc))


class _AutoLabelBatchWorker(QObject):
    """Run auto-typing on every SWC in a folder.

    Emits ``progress(current_index, total, current_filename)`` once
    per file *before* that file is processed, then either
    ``finished(run_id, batch_result)`` or ``failed(run_id, message)``.
    """

    progress = Signal(int, int, str)
    finished = Signal(int, object)
    failed = Signal(int, str)

    def __init__(
        self,
        run_id: int,
        folder: str,
        options: object,
        config_overrides: dict[str, Any] | None,
    ):
        super().__init__()
        self._run_id = int(run_id)
        self._folder = str(folder)
        self._options = options
        self._config_overrides = dict(config_overrides) if config_overrides else None

    def _run_isolated(self) -> object:
        with tempfile.TemporaryDirectory(prefix="swcstudio-auto-label-batch-") as tmp:
            request_path = Path(tmp) / "request.pkl"
            output_path = Path(tmp) / "output.pkl"
            progress_path = Path(tmp) / "progress.jsonl"
            request = {
                "kind": "batch",
                "folder": self._folder,
                "options": self._options,
                "config_overrides": self._config_overrides,
                "progress_path": str(progress_path),
            }
            with request_path.open("wb") as stream:
                pickle.dump(request, stream, protocol=pickle.HIGHEST_PROTOCOL)
            request_path.chmod(0o600)

            env = os.environ.copy()
            env.setdefault("OMP_NUM_THREADS", "1")
            env.setdefault("OPENBLAS_NUM_THREADS", "1")
            env.setdefault("MKL_NUM_THREADS", "1")
            process = subprocess.Popen(
                _AutoLabelFileWorker._subprocess_command(
                    str(request_path), str(output_path)
                ),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            progress_offset = 0
            while process.poll() is None:
                progress_offset = self._emit_new_progress(progress_path, progress_offset)
                time.sleep(0.1)
            progress_offset = self._emit_new_progress(progress_path, progress_offset)
            stdout, stderr = process.communicate()

            if process.returncode != 0:
                detail = (stderr or stdout or "").strip()
                if process.returncode < 0:
                    detail = (
                        f"batch auto-label subprocess terminated by signal "
                        f"{-process.returncode}"
                        + (f": {detail}" if detail else "")
                    )
                raise RuntimeError(
                    detail or f"batch auto-label subprocess exited {process.returncode}"
                )
            if not output_path.is_file():
                raise RuntimeError("batch auto-label subprocess produced no result")
            with output_path.open("rb") as stream:
                return pickle.load(stream)  # noqa: S301 - private child output

    def _emit_new_progress(self, progress_path: Path, offset: int) -> int:
        if not progress_path.is_file():
            return offset
        with progress_path.open("r", encoding="utf-8") as stream:
            stream.seek(offset)
            for line in stream:
                try:
                    payload = json.loads(line)
                    self.progress.emit(
                        int(payload["index"]),
                        int(payload["total"]),
                        str(payload["name"]),
                    )
                except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                    continue
            return stream.tell()

    @Slot()
    def run(self) -> None:  # noqa: D401 - Qt slot
        try:
            result = self._run_isolated()
            self.finished.emit(self._run_id, result)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(self._run_id, str(exc))


class _TypeSuspicionWorker(QObject):
    """Run the auto-typing engine on a dataframe and return only the
    ``Likely wrong labels`` issues. Used by the GUI to compute type
    suspicion off the main thread so the issue panel can show its
    fast-path entries (validation, radii, simplification suggestion)
    immediately instead of blocking ~1-2 seconds while the auto-labeler
    runs.
    """

    finished = Signal(int, object)  # run_id, list[dict]
    failed = Signal(int, str)

    def __init__(self, run_id: int, df: object):
        super().__init__()
        self._run_id = int(run_id)
        self._df = df

    @staticmethod
    def _subprocess_command(input_path: str, output_path: str) -> list[str]:
        if getattr(sys, "frozen", False):
            return [
                sys.executable,
                "--swcstudio-type-suspicion-worker",
                input_path,
                output_path,
            ]
        return [
            sys.executable,
            "-m",
            "swcstudio.gui.type_suspicion_process",
            input_path,
            output_path,
        ]

    def _run_isolated(self) -> list[dict]:
        """Run inference outside Qt's native-library-loaded process."""
        with tempfile.TemporaryDirectory(prefix="swcstudio-type-suspicion-") as tmp:
            input_path = Path(tmp) / "input.pkl"
            output_path = Path(tmp) / "output.pkl"
            with input_path.open("wb") as stream:
                pickle.dump(self._df, stream, protocol=pickle.HIGHEST_PROTOCOL)
            input_path.chmod(0o600)

            env = os.environ.copy()
            # One OpenMP worker is sufficient for background issue detection
            # and avoids oversubscribing the GUI host.
            env.setdefault("OMP_NUM_THREADS", "1")
            env.setdefault("OPENBLAS_NUM_THREADS", "1")
            env.setdefault("MKL_NUM_THREADS", "1")
            completed = subprocess.run(
                self._subprocess_command(str(input_path), str(output_path)),
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            if completed.returncode != 0:
                detail = (completed.stderr or completed.stdout or "").strip()
                if completed.returncode < 0:
                    detail = (
                        f"inference subprocess terminated by signal "
                        f"{-completed.returncode}"
                        + (f": {detail}" if detail else "")
                    )
                raise RuntimeError(detail or f"inference subprocess exited {completed.returncode}")
            if not output_path.is_file():
                raise RuntimeError("inference subprocess produced no result")
            with output_path.open("rb") as stream:
                result = pickle.load(stream)  # noqa: S301 - private child output
            return list(result)

    @Slot()
    def run(self) -> None:
        try:
            issues = self._run_isolated()
            self.finished.emit(self._run_id, list(issues))
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(self._run_id, str(exc))


__all__ = [
    "_AutoLabelFileWorker",
    "_AutoLabelBatchWorker",
    "_TypeSuspicionWorker",
]
