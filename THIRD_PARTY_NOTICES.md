# Third-party notices

SWC-Studio's own source is released under the MIT license (see `LICENSE`).
Installed with `pip`, it uses the packages below as separate installations.
The stand-alone macOS and Windows applications bundle Python and these
packages; each keeps its own license, listed here.

## Qt for Python (PySide6, Shiboken6) and Qt 6

The applications include Qt for Python and the Qt 6 libraries, used under the
GNU Lesser General Public License, version 3 (LGPL-3.0;
<https://www.gnu.org/licenses/lgpl-3.0.html>). They are included as separate
shared libraries in the application folder and can be replaced with other
builds of the same version. Their source code is available from
<https://code.qt.io> and <https://download.qt.io/official_releases/QtForPython/>.

## The SWC auto-labeler

`swcstudio/core/labeler/` and the models in `swcstudio/data/models/` are copied
from the SWC auto-labeler (<https://github.com/LazziLab/swc-autolabeler>),
released under the MIT license, Copyright (c) 2026 Xinran Shi.

## Other bundled packages

| Package | License |
| --- | --- |
| Python | Python Software Foundation License |
| NumPy | BSD-3-Clause |
| SciPy | BSD-3-Clause |
| pandas | BSD-3-Clause |
| scikit-learn | BSD-3-Clause |
| joblib | BSD-3-Clause |
| XGBoost | Apache-2.0 |
| NeuroM | BSD-3-Clause, Copyright (c) 2015-2022 Blue Brain Project/EPFL |
| MorphIO | Apache-2.0 |
| VisPy | BSD-3-Clause |
| pyqtgraph | MIT |
| zstandard | BSD-3-Clause |
| pyzipper | MIT |

The license text of each package ships with the package itself, in its
`*.dist-info` folder.
