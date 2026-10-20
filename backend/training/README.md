# Phase 7A training data foundation

This directory contains schema and manifest tooling only. It does not train or
load a model. Label files must be independently reviewed, explicitly approved
for training, and kept out of Git when they contain real screenshots, phone
numbers, messages, or OCR exports.

Build a deterministic manifest locally with:

```bash
backend/.venv/bin/python -m backend.training.scripts.build_manifest \
  backend/training/datasets/synthetic \
  /tmp/gharpayy-phase7a-manifest.json
```

The builder groups by `capture_group_id`, so overlapping scroll captures cannot
cross train/validation/test boundaries. It prints counts only.

