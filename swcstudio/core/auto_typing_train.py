"""Train the auto-labeler on your own labeled SWC files.

This runs the labeler's own trainer (``swcstudio.core.labeler.train_pipeline``,
the same code that built the shipped models) on your corpus and writes a model
folder SWC-Studio can label with. Point ``swcstudio`` at the folder with
``--model-dir``, the ``SWCSTUDIO_MODEL_DIR`` environment variable, or the GUI's
model selector.

Expected input dataset layout::

    <data-dir>/
        pyramidal/      3-class cells (axon, basal, apical)
            <files>.swc
        interneuron/    2-class cells (axon, dendrite)
            <files>.swc

Each SWC's type column (soma=1, axon=2, basal=3, apical=4) is the per-node
ground truth. The trainer holds out a deterministic 20% of the files (by file
name and seed) and fits, in order:

    1. Stage 1, the cell-class classifier.
    2. Stage 2, the subtree classifier for each cell class.
    3. Stage 3, the proximal branch correction (3-class cells).
    4. Stage 4, the quality flag for each cell class, fitted on the held-out
       files, which it needs; skip it for a labeler without a flag.

The output folder holds ``cell_type_classifier.pkl``, ``branch_classifier.pkl``,
``branch_correction.joblib``, ``flag_model.joblib``, the split it used
(``eval_split.json``) and a ``train_manifest.json`` with counts and timings.
A full run on the labeler's own corpus (about 9,500 training cells) takes about
45 minutes; a corpus of a few hundred cells takes minutes.

Usage from Python::

    from swcstudio.core.auto_typing_train import train_user_models
    train_user_models(data_dir="path/to/labeled/dataset", output_dir="path/to/models")

Usage from the CLI::

    swcstudio train auto-typing --data-dir path/to/dataset --output-dir path/to/models
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from swcstudio.core.model_paths import MODEL_FILES

STAGES = ("stage1", "stage2", "correction", "flag")


@dataclass
class TrainingResult:
    output_dir: str
    seed: int
    model_paths: dict[str, str | None]
    counts: dict[str, Any]
    timings_min: dict[str, float]
    skipped: list[str]


def train_user_models(
    data_dir: str | Path,
    output_dir: str | Path,
    *,
    seed: int | None = None,
    device: str = "cpu",
    skip: Iterable[str] = (),
    test_size: float = 0.20,
) -> TrainingResult:
    """Train the labeler on ``data_dir`` and write the model folder ``output_dir``.

    ``seed`` fixes the held-out split (random when omitted; recorded either way).
    ``device`` is where Stages 1 and 2 are fitted: ``cpu``, or ``cuda`` with an
    NVIDIA GPU and a CUDA build of XGBoost. ``skip`` names stages to leave out,
    from ``stage1``, ``stage2``, ``correction`` and ``flag``.
    """
    data_dir = Path(data_dir)
    output_dir = Path(output_dir)
    for sub in ("pyramidal", "interneuron"):
        if not (data_dir / sub).is_dir():
            raise FileNotFoundError(
                f"Expected {data_dir / sub} to exist. The dataset needs a 'pyramidal' "
                "(3-class) and an 'interneuron' (2-class) subfolder of SWC files.")
    skip = [s.strip() for s in skip if s and s.strip()]
    unknown = sorted(set(skip) - set(STAGES))
    if unknown:
        raise ValueError(f"unknown stage(s) {unknown}; choose from {', '.join(STAGES)}")
    if device not in ("cpu", "cuda"):
        raise ValueError("device must be 'cpu' or 'cuda'")

    from swcstudio.core.labeler import train_pipeline  # noqa: PLC0415

    argv = ["swcstudio train auto-typing", "--data-dir", str(data_dir), "--out-dir", str(output_dir),
            "--device", device, "--test-size", str(test_size)]
    if seed is not None:
        argv += ["--seed", str(int(seed))]
    if skip:
        argv += ["--skip", ",".join(skip)]
    saved_argv = sys.argv
    sys.argv = argv
    try:
        train_pipeline.main()
    finally:
        sys.argv = saved_argv

    manifest = json.loads((output_dir / "train_manifest.json").read_text(encoding="utf-8"))
    paths = {key: (str(output_dir / fname) if (output_dir / fname).is_file() else None)
             for key, fname in MODEL_FILES.items()}
    print(f"To label with these models, pass `--model-dir {output_dir}` or set "
          f"`SWCSTUDIO_MODEL_DIR={output_dir}`.")
    return TrainingResult(
        output_dir=str(output_dir),
        seed=int(manifest["seed"]),
        model_paths=paths,
        counts=dict(manifest.get("counts", {})),
        timings_min=dict(manifest.get("timings_min", {})),
        skipped=list(manifest.get("skipped", [])),
    )


__all__ = ["train_user_models", "TrainingResult", "STAGES"]
