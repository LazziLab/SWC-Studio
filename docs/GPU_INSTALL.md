---
orphan: true
---

# GPU Requirements

SWC-Studio does not need a GPU.

Auto-labeling runs on the CPU in every install: the desktop app,
`pip install swcstudio`, and a source install. No GPU drivers or extra
packages are needed.

Training your own models can use an NVIDIA GPU, but it does not have
to. `swcstudio train auto-typing` fits Stages 1 and 2 on the CPU by
default. With `--device cuda` it fits them on an NVIDIA GPU, which needs
a CUDA build of XGBoost in your pip or source environment. See
[Training Custom Models](documentation/auto-typing-backends.md#training-custom-models).

For installation, see [Getting Started](GETTING_STARTED.md).
