#!/bin/bash
# Stage D LR sweep, round 2 (long): one array task per LR, 1 H100 each, 100-epoch budget
# with the plateau scheduler and early stopping ACTIVE, so each curve can actually
# plateau. Round 1 (lr_sweep_bluehive3.sbatch, job 2019070) ran 15 fixed-LR epochs and
# every LR was still improving at the end.
#
#   plateau patience 5   -> LR halves after 5 epochs without a val-IoU improvement
#   early-stop patience 15 -> stop after 15 epochs without one (~3 halvings' worth)
#
# Every task starts from scratch: no --resume, a fresh per-job output directory, and
# no --requeue (a requeued task without --resume would restart at epoch 0 and append a
# second run to the same train_log.jsonl). The output directory is refused if it already exists.
#
# Submit from the code copy root (the directory containing src/):
#   sbatch src/wavenet_pipeline/03_machine_learning/slurm/lr_sweep_long_bluehive3.sbatch
#
# Per-LR outputs:  $RUNS/lr_sweep_long_<array job id>/lr_<LR>/{train_log.txt,train_log.jsonl,config.json,best.pt,last.pt}
# SLURM stdout:    $RUNS/logs/lrsweeplong_<array job id>_<task>.out
#SBATCH --job-name=ftan_lrsweep_long
#SBATCH --account=nkoziol_lab
#SBATCH --partition=coe   # coe,h100 is rejected: the two partitions carry different QoS
#SBATCH --gres=gpu:1
#SBATCH --cpus-per-task=3   # small so all 3 tasks fit on coe's free CPUs at once; 1 dataloader worker per CPU
#SBATCH --mem=64G
#SBATCH --time=01:00:00   # 100 epochs x ~12s ~= 20 min
#SBATCH --array=0-2
#SBATCH --output=/scratch/tolugboj_lab/Prj_Wavenet/epic_production/FTAN_ML_RUNS/logs/lrsweeplong_%A_%a.out

set -euo pipefail

LRS=(1e-4 3e-4 3e-3)
LR=${LRS[$SLURM_ARRAY_TASK_ID]}
EPOCHS=100
PLATEAU_PATIENCE=5
EARLY_STOP_PATIENCE=15

DATA=/scratch/tolugboj_lab/Prj_Wavenet/epic_production/FTAN_ML_CACHE
RUNS=/scratch/tolugboj_lab/Prj_Wavenet/epic_production/FTAN_ML_RUNS
VENV=/scratch/scratch/tbalamur/envs/wavenet-ml
PKG=src/wavenet_pipeline/03_machine_learning
OUT=$RUNS/lr_sweep_long_${SLURM_ARRAY_JOB_ID}/lr_${LR}

module load pytorch/2.4
export PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD/src

echo "job ${SLURM_ARRAY_JOB_ID}_${SLURM_ARRAY_TASK_ID} lr=$LR epochs=$EPOCHS plateau=$PLATEAU_PATIENCE early_stop=$EARLY_STOP_PATIENCE host=$(hostname) partition=$SLURM_JOB_PARTITION"
nvidia-smi --query-gpu=name,memory.total --format=csv,noheader

# Fail fast unless we got an H100 and are running the current dataset.py / Stage C / train.py code.
rm -rf $PKG/__pycache__
sha256sum $PKG/{dataset.py,stage_c_input_batching.py,train.py}
$VENV/bin/python -B - <<'PY'
import importlib, inspect, torch
name = torch.cuda.get_device_name(0)
assert "H100" in name, f"expected H100, got {name}"
ds = importlib.import_module("wavenet_pipeline.03_machine_learning.dataset")
sc = importlib.import_module("wavenet_pipeline.03_machine_learning.stage_c_input_batching")
tr = importlib.import_module("wavenet_pipeline.03_machine_learning.train")
assert "_h5_pid" in inspect.getsource(ds.CachedFTANDataset), "dataset.py lacks the per-worker HDF5 handle fix"
assert "persistent_workers=num_workers > 0" in inspect.getsource(sc.get_dataloaders), "Stage C lacks persistent_workers"
assert "by_family" in inspect.getsource(tr.run_epoch), "train.py lacks the per-family val breakdown"
print(f"preflight ok: {name}, torch {torch.__version__}, train={tr.__file__}")
PY

if [ -e "$OUT" ]; then echo "refusing to reuse existing $OUT (this sweep must start from scratch)"; exit 1; fi
mkdir -p "$OUT"
$VENV/bin/python -B -m wavenet_pipeline.03_machine_learning.train \
    $DATA/full_production_cache.h5 $DATA/splits_seed42_full.json \
    --out-dir "$OUT" --lr "$LR" --epochs $EPOCHS --seed 42 \
    --num-workers $SLURM_CPUS_PER_TASK \
    --plateau-patience $PLATEAU_PATIENCE --early-stop-patience $EARLY_STOP_PATIENCE \
    --device cuda
