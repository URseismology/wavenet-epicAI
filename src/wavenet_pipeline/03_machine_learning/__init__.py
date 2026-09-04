"""
03_machine_learning — U-Net pipeline for FTAN group-velocity dispersion curve extraction.

Consumes the CPS HDF5 datasets produced by 02_simulation (wvsim_main.py / verify_main.py
schema). Consolidates and supersedes three previously scattered, partial implementations:
src/machine_learning/U_NET_array.py, src/data_processing/{h5_wavenet_tools,build_ml_dataset}.py,
and chrisScripts/julyncf_pipeline/ML_pipeline/. See docs/HANDOFF.md sec 4.4/9 and
docs/ml_pipeline_stages/ for the staged build plan and per-stage status.

Stage A (data):  ftan_grid.py, dataset.py, splits.py, build_ml_cache.py, validate_ftan.py
Stage B (model): model.py, losses.py, metrics.py
Stage C (batching), D (training), E (evaluation): train.py, evaluate.py (later)
"""
