"""Sphinx configuration for SWC-Studio documentation."""

from __future__ import annotations

import os
import re
import sys
from datetime import datetime
from importlib.metadata import PackageNotFoundError, version as pkg_version

# Allow autodoc to import project modules.
DOCS_DIR = os.path.abspath(os.path.dirname(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(DOCS_DIR, ".."))
sys.path.insert(0, PROJECT_ROOT)

project = "SWC-Studio"
author = "Xinran Shi"
copyright = f"{datetime.now():%Y}, {author}"

try:
    release = pkg_version("swcstudio")
except PackageNotFoundError:
    # Not installed: read the version from pyproject.toml, its one source.
    with open(os.path.join(PROJECT_ROOT, "pyproject.toml"), encoding="utf-8") as fh:
        _match = re.search(r'^version\s*=\s*"([^"]+)"', fh.read(), re.MULTILINE)
    release = _match.group(1) if _match else "0+unknown"

# Sphinx expects both version (short) and release (full).
version = release

extensions = [
    "myst_parser",
    "sphinx.ext.autodoc",
    "sphinx.ext.autosummary",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
]

autosummary_generate = True
autodoc_member_order = "bysource"
autodoc_typehints = "description"
autodoc_class_signature = "mixed"
autodoc_default_options = {
    "members": True,
    "show-inheritance": True,
    "inherited-members": False,
}

# Keep docs build lightweight in environments without full GUI/runtime deps.
autodoc_mock_imports = [
    "PySide6",
    "pyqtgraph",
    "vispy",
    "morphio",
    "neurom",
]

source_suffix = {
    ".rst": "restructuredtext",
    ".md": "markdown",
}

master_doc = "index"
language = "en"
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store", "README.md", "MACOS_PACKAGING.md"]

# Keep docs folder tidy: only include optional paths when present.
templates_path = ["_templates"] if os.path.isdir(os.path.join(DOCS_DIR, "_templates")) else []
html_static_path = ["_static"] if os.path.isdir(os.path.join(DOCS_DIR, "_static")) else []

html_theme = "pydata_sphinx_theme"
html_title = f"SWC-Studio v{release}"
html_copy_source = False
html_show_sourcelink = False
html_theme_options = {
    "header_links_before_dropdown": 10,
    "navbar_align": "content",
    "navbar_start": ["navbar-logo"],
    "navbar_center": ["navbar-nav"],
    "navbar_end": ["search-button", "theme-switcher"],
    "navigation_with_keys": True,
    "show_nav_level": 2,
    "navigation_depth": 4,
    "collapse_navigation": True,
    "sidebar_includehidden": True,
    "show_toc_level": 2,
    "secondary_sidebar_items": ["page-toc"],
    "logo": {
        "text": f"SWC-Studio v{release}",
        "image_light": "../packaging/icon.png",
        "image_dark": "../packaging/icon.png",
        "alt_text": f"SWC-Studio v{release}",
    },
}
html_css_files = ["custom.css"]

myst_enable_extensions = [
    "colon_fence",
    "deflist",
    "fieldlist",
]

# Generate implicit anchors for headings up to depth 3 so cross-document
# fragment links like `auto-typing-backends.md#using-your-custom-models`
# resolve correctly.
myst_heading_anchors = 3
