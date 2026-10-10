"""Write the golden files that pin SWC-Studio's labeling to the released labeler.

``tests/test_labeler_parity.py`` checks that SWC-Studio labels these cells exactly
as the SWC auto-labeler does. This script writes the test cells and the expected
results:

* ``tests/fixtures/labeler/synthetic_*.swc`` -- three cells drawn by a fixed
  generator: a 3-class cell (soma, basal dendrites, an apical trunk with a tuft,
  an axon), a 2-class cell (soma, radiating dendrites, a local axon), and an
  unlabeled copy of the first, every node typed 0.
* ``tests/fixtures/labeler/expected.json`` -- for those cells and
  ``tests/fixtures/single-soma.swc``, the labels, cell class and flag the labeler
  in ``--source`` gives, automatic and with each cell class given. The labels
  are the type column of the file the labeler's own writer
  (``write_labeled_swc``) produces, not a re-derivation of how labels map to
  node lines, so the parity test also checks SWC-Studio's mapping.

Run it with the labeler's own environment, from the SWC-Studio repository root:

    python scripts/make_labeler_golden.py --source ../Autolabeler
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures"
OUT = FIX / "labeler"


class _Tree:
    def __init__(self) -> None:
        self.rows: list[list] = []

    def add(self, t: int, xyz, r: float, parent: int) -> int:
        nid = len(self.rows) + 1
        self.rows.append([nid, t, *[float(v) for v in xyz], float(r), parent])
        return nid

    def branch(self, rng, t: int, start_id: int, start, direction, length: float, r0: float, r1: float,
               step: float = 4.0, wiggle: float = 0.25) -> tuple[int, np.ndarray]:
        """A jittered straight-ish section; returns the last node id and position."""
        d = np.asarray(direction, float)
        d /= np.linalg.norm(d)
        pos = np.asarray(start, float)
        last = start_id
        n = max(2, int(length / step))
        for i in range(1, n + 1):
            jitter = rng.normal(0, wiggle, 3)
            d = d + jitter * 0.15
            d /= np.linalg.norm(d)
            pos = pos + d * step
            r = r0 + (r1 - r0) * i / n
            last = self.add(t, pos, r, last)
        return last, pos

    def tree(self, rng, t: int, start_id: int, start, direction, length: float, r0: float, depth: int,
             spread: float = 0.6) -> None:
        end_id, end = self.branch(rng, t, start_id, start, direction, length, r0, r0 * 0.75)
        if depth == 0:
            return
        d = np.asarray(direction, float) / np.linalg.norm(direction)
        for sign in (-1, 1):
            perp = np.cross(d, rng.normal(0, 1, 3))
            perp /= np.linalg.norm(perp)
            child = d + sign * spread * perp
            self.tree(rng, t, end_id, end, child, length * 0.7, r0 * 0.7, depth - 1, spread)

    def text(self, relabel_unknown: bool = False) -> str:
        lines = ["# synthetic cell drawn by scripts/make_labeler_golden.py"]
        for nid, t, x, y, z, r, p in self.rows:
            lines.append(f"{nid} {0 if relabel_unknown else t} {x:.3f} {y:.3f} {z:.3f} {r:.3f} {p}")
        return "\n".join(lines) + "\n"


def synthetic_three_class() -> _Tree:
    rng = np.random.default_rng(7)
    c = _Tree()
    soma = c.add(1, (0, 0, 0), 9.0, -1)
    top, tpos = c.branch(rng, 4, soma, (0, 9, 0), (0, 1, 0), 320, 3.2, 1.6, wiggle=0.1)
    trunk_ids = [row[0] for row in c.rows if row[1] == 4]
    for k, idx in enumerate(trunk_ids[10:70:12]):  # obliques off the trunk
        row = c.rows[idx - 1]
        ang = k * 2.1
        c.tree(rng, 4, idx, row[2:5], (math.cos(ang), 0.3, math.sin(ang)), 60, 0.9, 1)
    for k in range(3):  # tuft
        ang = k * 2.09
        c.tree(rng, 4, top, tpos, (math.cos(ang), 1.0, math.sin(ang)), 90, 1.4, 2)
    for k in range(5):  # basal dendrites
        ang = k * 2 * math.pi / 5
        c.tree(rng, 3, soma, (8 * math.cos(ang), -2, 8 * math.sin(ang)),
               (math.cos(ang), -0.5, math.sin(ang)), 70, 1.6, 2)
    ax, apos = c.branch(rng, 2, soma, (0, -9, 0), (0.05, -1, 0), 420, 1.0, 0.5, wiggle=0.15)
    axon_ids = [row[0] for row in c.rows if row[1] == 2]
    for k, idx in enumerate(axon_ids[20:100:25]):
        row = c.rows[idx - 1]
        ang = k * 1.7
        c.tree(rng, 2, idx, row[2:5], (math.cos(ang), -0.4, math.sin(ang)), 80, 0.5, 1)
    return c


def synthetic_two_class() -> _Tree:
    rng = np.random.default_rng(11)
    c = _Tree()
    soma = c.add(1, (0, 0, 0), 6.0, -1)
    for k in range(7):
        theta = k * 2 * math.pi / 7
        phi = 0.6 * math.sin(3 * k)
        d = (math.cos(theta) * math.cos(phi), math.sin(phi), math.sin(theta) * math.cos(phi))
        c.tree(rng, 3, soma, (6 * d[0], 6 * d[1], 6 * d[2]), d, 55, 1.2, 2, spread=0.8)
    c.tree(rng, 2, soma, (0, -6, 0), (0.2, -1, 0.1), 60, 0.6, 4, spread=1.0)
    return c


def write_fixtures() -> list[Path]:
    OUT.mkdir(parents=True, exist_ok=True)
    three, two = synthetic_three_class(), synthetic_two_class()
    files = {
        "synthetic_three_class.swc": three.text(),
        "synthetic_two_class.swc": two.text(),
        "synthetic_unlabeled.swc": three.text(relabel_unknown=True),
    }
    paths = []
    for name, text in files.items():
        p = OUT / name
        p.write_text(text, encoding="utf-8", newline="\n")
        paths.append(p)
    return paths + [FIX / "single-soma.swc"]


def _written_types(path: Path) -> str:
    """The type field of each node line of a labeled file, in file order, one
    digit per node. Node lines are found with the labeler's own line filter."""
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) < 7 or parts[0].startswith("#"):
            continue
        try:
            int(float(parts[0])), int(float(parts[6]))
            [float(v) for v in parts[2:6]]
        except ValueError:
            continue
        out.append(str(int(float(parts[1]))))
    return "".join(out)


def expected_for(paths: list[Path], source: Path) -> dict:
    sys.path.insert(0, str(source))
    from pipeline.infer_pipeline import (  # noqa: PLC0415
        parse_swc_for_labeling,
        run_pipeline_on_nodes,
        write_labeled_swc,
    )
    from pipeline.stage4.single_seed_flag import load_flag, score_flag  # noqa: PLC0415

    md = source / "pipeline" / "models" / "seed123"
    s1, s2 = md / "cell_type_classifier.pkl", md / "branch_classifier.pkl"
    flag = load_flag(md)

    def row_labels(path: Path, nodes, labels) -> str:
        """The labels as the labeler itself writes them: run its own writer
        (``write_labeled_swc``) and read back the type of each node line."""
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / path.name
            write_labeled_swc(path, nodes, labels, out)
            return _written_types(out)

    result = {"labeler_commit": None, "cells": {}}
    try:
        import subprocess  # noqa: PLC0415
        result["labeler_commit"] = subprocess.run(["git", "-C", str(source), "rev-parse", "HEAD"],
                                                  capture_output=True, text=True, check=True).stdout.strip()
    except Exception:  # noqa: BLE001
        pass
    for p in paths:
        nodes, notes = parse_swc_for_labeling(p)
        auto = run_pipeline_on_nodes(nodes, str(p), s1, s2)
        fr = score_flag(nodes, auto, flag)
        cell = {
            "notes": notes,
            "cell_type": auto.stage1.cell_type,
            "stage1_confidence": float(auto.stage1.confidence),
            "labels": row_labels(p, nodes, auto.node_labels),
            "predicted_f1": None if fr.get("predicted_f1") is None else float(fr["predicted_f1"]),
            "flagged": bool(fr.get("flagged")),
        }
        for ct in ("pyramidal", "interneuron"):
            given = run_pipeline_on_nodes(nodes, str(p), s1, s2, override_cell_type=ct)
            cell[f"labels_given_{ct}"] = row_labels(p, nodes, given.node_labels)
        result["cells"][p.name] = cell
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", type=Path, required=True, help="labeler repository checkout")
    args = ap.parse_args()
    paths = write_fixtures()
    expected = expected_for(paths, args.source.resolve())
    (OUT / "expected.json").write_text(json.dumps(expected, indent=1) + "\n", encoding="utf-8", newline="\n")
    for name, cell in expected["cells"].items():
        counts = {t: cell["labels"].count(str(t)) for t in (1, 2, 3, 4)}
        print(f"{name}: {cell['cell_type']} ({cell['stage1_confidence']:.2f}), labels {counts}, "
              f"predicted F1 {cell['predicted_f1']}, flagged {cell['flagged']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
