"""In the 3D view, dragging rotates without selecting; a click selects without moving the camera; a
double-click zooms to the node."""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

SCRIPT = textwrap.dedent(
    """
    import os
    from types import SimpleNamespace

    os.environ['QT_QPA_PLATFORM'] = 'offscreen'

    import pandas as pd
    from PySide6.QtWidgets import QApplication
    from swcstudio.gui.neuron_3d_widget import Neuron3DWidget

    app = QApplication([])
    w = Neuron3DWidget()
    w.load_swc(pd.DataFrame({
        "id": [1, 2, 3], "type": [1, 3, 3], "x": [0.0, 10.0, 20.0], "y": [0.0, 0.0, 5.0],
        "z": [0.0, 0.0, 0.0], "radius": [3.0, 1.0, 1.0], "parent": [-1, 1, 2],
    }))
    w._nearest_node_id = lambda pos: 2  # picking itself depends on the window size; fix it here
    clicked = []
    w.node_clicked.connect(clicked.append)
    cam = w._view.camera

    def ev(x, y):
        return SimpleNamespace(pos=(x, y), button=1)

    def view():
        return float(cam.distance), tuple(float(v) for v in cam.center)

    before = view()
    w._on_mouse_press(ev(100, 100)); w._on_mouse_release(ev(180, 140))   # drag
    assert clicked == [], clicked
    assert view() == before, (view(), before)

    w._on_mouse_press(ev(100, 100)); w._on_mouse_release(ev(102, 101))   # click
    assert clicked == [2], clicked
    assert view() == before, (view(), before)

    w._on_mouse_double_click(ev(102, 101))                                # double-click
    assert clicked == [2, 2], clicked
    assert view()[0] < before[0], (view(), before)
    print("ok")
    """
)


class ThreeDViewMouseTests(unittest.TestCase):
    def test_drag_rotates_click_selects_double_click_zooms(self) -> None:
        env = os.environ.copy()
        env["QT_QPA_PLATFORM"] = "offscreen"
        result = subprocess.run([sys.executable, "-c", SCRIPT], cwd=REPO_ROOT, env=env,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, msg=f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}")
        self.assertIn("ok", result.stdout)


if __name__ == "__main__":
    unittest.main()
