"""Shared PyInstaller inputs for SWC-Studio desktop bundles."""

from __future__ import annotations

from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_dynamic_libs,
    collect_submodules,
    copy_metadata,
)


def _exclude_xgboost_tests(name: str) -> bool:
    return not name.startswith("xgboost.testing")


def _exclude_test_modules(name: str) -> bool:
    parts = set(name.split("."))
    return not parts.intersection({"tests", "testing", "conftest"})


def bundle_inputs() -> tuple[list, list, list[str]]:
    """Return ``datas``, ``binaries``, and ``hiddenimports``.

    Both platform specs use the dependency-only modular runtime. The
    SWC-Studio source and model layers are staged after PyInstaller runs.
    """
    datas: list = []
    datas += collect_data_files("vispy")
    datas += collect_data_files("xgboost", includes=["VERSION"])
    for distribution in (
        "neurom",
        "morphio",
        "swcstudio",
        "vispy",
        "scikit-learn",
        "xgboost",
        "zstandard",
        "pyzipper",
    ):
        datas += copy_metadata(distribution)

    binaries = collect_dynamic_libs("xgboost")
    hiddenimports = (
        collect_submodules("vispy.app.backends")
        + collect_submodules("neurom", filter=_exclude_test_modules)
        + collect_submodules("morphio", filter=_exclude_test_modules)
        + collect_submodules("sklearn", filter=_exclude_test_modules)
        + collect_submodules("scipy", filter=_exclude_test_modules)
        + collect_submodules("xgboost", filter=_exclude_xgboost_tests)
        + [
            "PySide6",
            "PySide6.QtCore",
            "PySide6.QtGui",
            "PySide6.QtNetwork",
            "PySide6.QtOpenGLWidgets",
            "PySide6.QtSvg",
            "PySide6.QtWidgets",
            "pyqtgraph",
            "vispy",
            "vispy.app.backends._qt",
            "vispy.app.backends._pyside6",
            "xgboost",
            "joblib",
            "numpy",
            "pandas",
            "scipy",
            "sklearn",
            "morphio",
            "neurom",
            "zstandard",
            "pyzipper",
            "pyzipper.zipfile",
            "pyzipper.zipfile_aes",
        ]
    )
    return datas, binaries, hiddenimports


MODULE_COLLECTION_MODE: dict[str, str] = {}

EXCLUDES = [
    "swcstudio",
    # Auto-labeling needs no deep-learning framework; keep one installed in the
    # build environment out of the bundle.
    "torch",
    "torch_geometric",
    "xgboost.testing",
    "hypothesis",
    "pytest",
    "sphinx",
    "myst_parser",
    "pydata_sphinx_theme",
    "build",
]
