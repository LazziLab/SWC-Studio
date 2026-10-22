"""Installation diagnostics for fresh and upgraded SWC-Studio environments."""
from __future__ import annotations

import importlib
import importlib.metadata
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any

from swcstudio import __version__
from swcstudio.core.model_paths import MODEL_FILES, REQUIRED_MODELS, select_model_dir


SUPPORTED_PYTHON = ">=3.10,<3.13"

RUNTIME_DEPENDENCIES: tuple[tuple[str, str], ...] = (
    ("numpy", "numpy"),
    ("pandas", "pandas"),
    ("scipy", "scipy"),
    ("morphio", "morphio"),
    ("neurom", "neurom"),
    ("scikit-learn", "sklearn"),
    ("joblib", "joblib"),
    ("xgboost", "xgboost"),
    ("PySide6", "PySide6"),
    ("pyqtgraph", "pyqtgraph"),
    ("vispy", "vispy"),
    ("zstandard", "zstandard"),
    ("pyzipper", "pyzipper"),
)

REQUIRED_CONFIGS: tuple[str, ...] = (
    "batch_processing/configs/auto_typing.json",
    "batch_processing/configs/batch_validation.json",
    "batch_processing/configs/radii_cleaning.json",
    "batch_processing/configs/split.json",
    "batch_processing/configs/swc_splitter.json",
    "morphology_editing/configs/dendrogram_editing.json",
    "morphology_editing/configs/simplification.json",
    "validation/configs/auto_fix.json",
    "validation/configs/default.json",
    "validation/configs/radii_cleaning.json",
    "validation/configs/run_checks.json",
    "visualization/configs/mesh_editing.json",
)


def _dependency_report(distribution: str, module: str) -> dict[str, Any]:
    row: dict[str, Any] = {
        "distribution": distribution,
        "module": module,
        "ok": False,
        "version": None,
        "error": None,
    }
    try:
        importlib.import_module(module)
        try:
            row["version"] = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            imported = sys.modules.get(module)
            row["version"] = str(getattr(imported, "__version__", "unknown"))
        row["ok"] = True
    except Exception as exc:  # noqa: BLE001
        row["error"] = f"{exc.__class__.__name__}: {exc}"
    return row


def _load_model(short_name: str, path: Path) -> None:
    """Deserialize one labeler model file the way the labeler reads it."""
    from swcstudio.core.auto_typing._pickle_compat import labeler_pickle_aliases

    with labeler_pickle_aliases():
        if path.suffix == ".pkl":
            import pickle

            with path.open("rb") as fh:
                pickle.load(fh)
        else:
            import joblib

            joblib.load(path)


def _check_qapplication() -> dict[str, Any]:
    # QApplication can only be constructed once per process and side-effects the
    # Qt platform plugin loader, so we exercise it in a fresh subprocess.
    # Defaults to the offscreen platform so doctor never pops a window, but
    # still catches the case where Qt's plugin loader cannot start at all —
    # e.g. PySide6 installed against a Qt that needs system libs the host
    # is missing (libxcb-cursor on Linux, etc.).
    script = (
        "import os, sys\n"
        "os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')\n"
        "from PySide6.QtWidgets import QApplication\n"
        "app = QApplication.instance() or QApplication([])\n"
        "sys.stdout.write(app.platformName())\n"
    )
    try:
        completed = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return {"ok": False, "platform": None, "error": f"{exc.__class__.__name__}: {exc}"}
    if completed.returncode != 0:
        stderr = completed.stderr.strip() or f"exit code {completed.returncode}"
        return {"ok": False, "platform": None, "error": stderr}
    return {"ok": True, "platform": completed.stdout.strip() or None, "error": None}


def check_installation(*, load_models: bool = True) -> dict[str, Any]:
    """Return a JSON-serializable runtime, data-file, and model report."""
    dependencies = [
        _dependency_report(distribution, module)
        for distribution, module in RUNTIME_DEPENDENCIES
    ]

    tools_dir = Path(__file__).resolve().parents[1] / "tools"
    configs = []
    for relative in REQUIRED_CONFIGS:
        path = tools_dir / relative
        configs.append(
            {
                "name": relative,
                "ok": path.is_file(),
                "path": str(path),
            }
        )

    # The folder auto-labeling would use (never downloaded here). Its three
    # required files must be present; the quality flag is optional, and a set
    # without it labels cells but scores no flag.
    selection = select_model_dir(auto_download=False)
    model_folder = selection.folder if selection is not None else None
    models = []
    for short_name, filename in MODEL_FILES.items():
        required = short_name in REQUIRED_MODELS
        candidate = model_folder / filename if model_folder is not None else None
        path = candidate if candidate is not None and candidate.is_file() else None
        row: dict[str, Any] = {
            "name": short_name,
            "filename": filename,
            "required": required,
            "ok": path is not None or (not required and model_folder is not None),
            "path": str(path) if path is not None else None,
            "loaded": None,
            "error": None,
            "note": None,
        }
        if path is None and not required and model_folder is not None:
            row["note"] = "optional, not in this model folder: cells are labeled but no quality flag is scored"
        if path is not None and load_models:
            try:
                _load_model(short_name, path)
                row["loaded"] = True
            except Exception as exc:  # noqa: BLE001
                row["ok"] = False
                row["loaded"] = False
                row["error"] = f"{exc.__class__.__name__}: {exc}"
        models.append(row)

    gui: dict[str, Any] = {
        "ok": False,
        "error": None,
        "qapplication_ok": None,
        "qt_platform": None,
        "qt_error": None,
    }
    try:
        importlib.import_module("swcstudio.gui.main")
        gui["ok"] = True
    except Exception as exc:  # noqa: BLE001
        gui["error"] = f"{exc.__class__.__name__}: {exc}"

    if gui["ok"]:
        qt_report = _check_qapplication()
        gui["qapplication_ok"] = qt_report["ok"]
        gui["qt_platform"] = qt_report["platform"]
        gui["qt_error"] = qt_report["error"]
        if not qt_report["ok"]:
            gui["ok"] = False

    python_ok = (3, 10) <= sys.version_info[:2] < (3, 13)
    ok = (
        python_ok
        and all(row["ok"] for row in dependencies)
        and all(row["ok"] for row in configs)
        and all(row["ok"] for row in models)
        and bool(gui["ok"])
    )
    return {
        "ok": ok,
        "swcstudio_version": __version__,
        "python": platform.python_version(),
        "supported_python": SUPPORTED_PYTHON,
        "python_ok": python_ok,
        "platform": platform.platform(),
        "executable": sys.executable,
        "dependencies": dependencies,
        "configs": configs,
        "model_dir": str(model_folder) if model_folder is not None else None,
        "model_source": selection.source if selection is not None else None,
        "models": models,
        "gui": gui,
        "model_loading_checked": load_models,
    }


def format_installation_report(report: dict[str, Any]) -> str:
    """Format :func:`check_installation` for terminal users."""
    marker = "PASS" if report["ok"] else "FAIL"
    lines = [
        f"SWC-Studio installation check: {marker}",
        f"  Version: {report['swcstudio_version']}",
        (
            f"  Python: {report['python']} "
            f"({report['supported_python']}) "
            f"[{'OK' if report['python_ok'] else 'UNSUPPORTED'}]"
        ),
        f"  Platform: {report['platform']}",
        f"  Executable: {report['executable']}",
        "",
        "Dependencies:",
    ]
    for row in report["dependencies"]:
        detail = row["version"] if row["ok"] else row["error"]
        lines.append(
            f"  [{'OK' if row['ok'] else 'MISSING'}] "
            f"{row['distribution']}: {detail}"
        )

    lines.extend(["", "Configuration files:"])
    for row in report["configs"]:
        lines.append(f"  [{'OK' if row['ok'] else 'MISSING'}] {row['name']}")

    lines.extend(["", "Production models:"])
    if report.get("model_dir"):
        lines.append(f"  Model folder: {report['model_dir']} ({report.get('model_source')})")
    for row in report["models"]:
        if row["ok"] and row.get("path") is None and row.get("note"):
            lines.append(f"  [NOTE] {row['filename']}: {row['note']}")
            continue
        state = "OK" if row["ok"] else "FAILED"
        detail = row["path"] or "not found"
        if row["error"]:
            detail = f"{detail} ({row['error']})"
        lines.append(f"  [{state}] {row['filename']}: {detail}")

    gui = report["gui"]
    import_state = "OK" if not gui.get("error") else "FAILED"
    lines.extend(
        [
            "",
            (
                f"GUI import: {import_state}"
                + (f" ({gui['error']})" if gui.get("error") else "")
            ),
        ]
    )
    qapp_ok = gui.get("qapplication_ok")
    if qapp_ok is not None:
        qt_platform = gui.get("qt_platform") or "unknown"
        qt_error = gui.get("qt_error")
        if qapp_ok:
            lines.append(f"Qt platform plugin: OK ({qt_platform})")
        else:
            detail = qt_error or "unknown error"
            lines.append(f"Qt platform plugin: FAILED ({detail})")
            if sys.platform.startswith("linux"):
                lines.append(
                    "  Linux hint: install system Qt deps, e.g."
                    " 'sudo apt-get install libegl1 libxkbcommon-x11-0"
                    " libxcb-cursor0 libxcb-icccm4 libxcb-image0"
                    " libxcb-keysyms1 libxcb-randr0 libxcb-render-util0"
                    " libxcb-shape0 libxcb-xinerama0 libxcb-xkb1 libxkbcommon0'"
                )
    if not report["ok"]:
        lines.extend(
            [
                "",
                "Repair this environment with:",
                (
                    f'  "{report["executable"]}" -m pip install '
                    "--upgrade --force-reinstall swcstudio"
                ),
            ]
        )
    return "\n".join(lines)


__all__ = [
    "REQUIRED_CONFIGS",
    "RUNTIME_DEPENDENCIES",
    "SUPPORTED_PYTHON",
    "check_installation",
    "format_installation_report",
]
