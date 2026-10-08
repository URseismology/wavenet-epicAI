#!/bin/bash
# Stage D LR sweep on Bluehive3: one array task per LR, 1 H100 each, short fixed-epoch budget.
# Purpose is comparing convergence across LRs, not producing a final model, so the
# plateau scheduler and early stopping are effectively disabled (patience > epochs):
# each run holds its LR for the whole budget.
#
# Submit from the code copy root (the directory containing src/):
#   sbatch src/wavenet_pipeline/03_machine_learning/slurm/lr_sweep_bluehive3.sbatch
#
# Per-LR outputs:  $RUNS/lr_sweep_<array job id>/lr_<LR>/{train_log.txt,train_log.jsonl,config.json,best.pt,last.pt}
# SLURM stdout:    $RUNS/logs/lrsweep_<array job id>_<task>.out  (one file per task)
#SBATCH --job-name=ftan_lrsweep
#SBATCH --account=nkoziol_lab
#SBATCH --partition=coe   # coe,h100 is rejected: the two partitions carry different QoS
#SBATCH --gres=gpu:1
#SBATCH --cpus-per-task=8
#SBATCH --mem=64G
#SBATCH --time=02:00:00
#SBATCH --array=0-2
#SBATCH --requeue
#SBATCH --output=/scratch/tolugboj_lab/Prj_Wavenet/epic_production/FTAN_ML_RUNS/logs/lrsweep_%A_%a.out

set -euo pipefail

LRS=(1e-4 1e-3 3e-3)
LR=${LRS[$SLURM_ARRAY_TASK_ID]}
EPOCHS=15

DATA=/scratch/tolugboj_lab/Prj_Wavenet/epic_production/FTAN_ML_CACHE
RUNS=/scratch/tolugboj_lab/Prj_Wavenet/epic_production/FTAN_ML_RUNS
VENV=/scratch/scratch/tbalamur/envs/wavenet-ml
PKG=src/wavenet_pipeline/03_machine_learning
OUT=$RUNS/lr_sweep_${SLURM_ARRAY_JOB_ID}/lr_${LR}

module load pytorch/2.4
export PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD/src

echo "job ${SLURM_ARRAY_JOB_ID}_${SLURM_ARRAY_TASK_ID} lr=$LR epochs=$EPOCHS host=$(hostname) partition=$SLURM_JOB_PARTITION restart=${SLURM_RESTART_COUNT:-0}"
nvidia-smi --query-gpu=name,memory.total --format=csv,noheader

# Fail fast unless we got an H100 and are running the current dataset.py / Stage C code.
rm -rf $PKG/__pycache__
sha256sum $PKG/{dataset.py,stage_c_input_batching.py,train.py}
$VENV/bin/python -B - <<'EOF'
import importlib, inspect, torch
name = torch.cuda.get_device_name(0)
assert "H100" in name, f"expected H100, got {name}"
ds = importlib.import_module("wavenet_pipeline.03_machine_learning.dataset")
sc = importlib.import_module("wavenet_pipeline.03_machine_learning.stage_c_input_batching")
assert "_h5_pid" in inspect.getsource(ds.CachedFTANDataset), "dataset.py lacks the per-worker HDF5 handle fix"
assert "persistent_workers=num_workers > 0" in inspect.getsource(sc.get_dataloaders), "Stage C lacks persistent_workers"
print(f"preflight ok: {name}, torch {torch.__version__}, dataset={ds.__file__}, stage_c={sc.__file__}")
EOF

mkdir -p "$OUT"
$VENV/bin/python -B -m wavenet_pipeline.03_machine_learning.train \
    $DATA/full_production_cache.h5 $DATA/splits_seed42_full.json \
    --out-dir "$OUT" --lr "$LR" --epochs $EPOCHS \
    --num-workers $((SLURM_CPUS_PER_TASK - 1)) \
    --plateau-patience 1000 --early-stop-patience 1000 \
    --device cuda --resume
