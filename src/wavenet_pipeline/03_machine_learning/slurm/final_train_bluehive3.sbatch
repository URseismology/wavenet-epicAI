#!/bin/bash
# Stage D final training: LR 3e-3 (picked by the round-2 sweep, job 2091906), three seeds,
# one array task per seed, 1 H100 each. The data split is fixed (splits_seed42_full.json);
# --seed only changes weight init and shuffle order, so the spread across seeds measures
# training noise, not split noise.
#
#   plateau patience 5, early-stop patience 25, both on the tracked score below
#   gradient clipping at global L2 norm 1.0 (a probe showed mean norms of 0.14-0.41 at
#     3e-3, with clipping firing on <5% of steps, and only in epoch 0, so it guards
#     spikes without acting as a hidden LR cut)
#   best.pt is selected on the 5-epoch trailing mean of val IoU, not raw val IoU
#
# After training, the selected best.pt is scored on val, then on TEST (family M09)
# exactly once. Test is never touched during training. A rerun into the same directory
# is refused, so test can't be scored twice by accident.
#
# Submit from the code copy root (the directory containing src/):
#   sbatch src/wavenet_pipeline/03_machine_learning/slurm/final_train_bluehive3.sbatch
#
# Per-seed outputs: $RUNS/final_lr3e-3_<array job id>/seed_<S>/{train_log.*,config.json,best.pt,last.pt,val_eval.json,test_eval.json}
# SLURM stdout:     $RUNS/logs/final_<array job id>_<task>.out
#SBATCH --job-name=ftan_final
#SBATCH --account=nkoziol_lab
#SBATCH --partition=coe   # coe,h100 is rejected: the two partitions carry different QoS
#SBATCH --gres=gpu:1
#SBATCH --cpus-per-task=8
#SBATCH --mem=64G
#SBATCH --time=01:30:00   # 100 epochs x ~15s ~= 25 min, plus eval
#SBATCH --array=0-2
#SBATCH --output=/scratch/tolugboj_lab/Prj_Wavenet/epic_production/FTAN_ML_RUNS/logs/final_%A_%a.out

set -euo pipefail

SEEDS=(42 43 44)
SEED=${SEEDS[$SLURM_ARRAY_TASK_ID]}
LR=3e-3
EPOCHS=100
PLATEAU_PATIENCE=5
EARLY_STOP_PATIENCE=25
GRAD_CLIP=1.0
SMOOTH_WINDOW=5

DATA=/scratch/tolugboj_lab/Prj_Wavenet/epic_production/FTAN_ML_CACHE
RUNS=/scratch/tolugboj_lab/Prj_Wavenet/epic_production/FTAN_ML_RUNS
VENV=/scratch/scratch/tbalamur/envs/wavenet-ml
PKG=src/wavenet_pipeline/03_machine_learning
OUT=$RUNS/final_lr${LR}_${SLURM_ARRAY_JOB_ID}/seed_${SEED}

module load pytorch/2.4
export PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD/src

echo "job ${SLURM_ARRAY_JOB_ID}_${SLURM_ARRAY_TASK_ID} seed=$SEED lr=$LR epochs=$EPOCHS plateau=$PLATEAU_PATIENCE early_stop=$EARLY_STOP_PATIENCE grad_clip=$GRAD_CLIP smooth=$SMOOTH_WINDOW host=$(hostname)"
nvidia-smi --query-gpu=name,memory.total --format=csv,noheader

rm -rf $PKG/__pycache__
sha256sum $PKG/{dataset.py,stage_c_input_batching.py,train.py,eval_by_family.py}
$VENV/bin/python -B - <<'PY'
import importlib, inspect, torch
name = torch.cuda.get_device_name(0)
assert "H100" in name, f"expected H100, got {name}"
ds = importlib.import_module("wavenet_pipeline.03_machine_learning.dataset")
sc = importlib.import_module("wavenet_pipeline.03_machine_learning.stage_c_input_batching")
tr = importlib.import_module("wavenet_pipeline.03_machine_learning.train")
importlib.import_module("wavenet_pipeline.03_machine_learning.eval_by_family")
assert "_h5_pid" in inspect.getsource(ds.CachedFTANDataset), "dataset.py lacks the per-worker HDF5 handle fix"
assert "persistent_workers=num_workers > 0" in inspect.getsource(sc.get_dataloaders), "Stage C lacks persistent_workers"
assert "clip_grad_norm_" in inspect.getsource(tr.run_epoch), "train.py lacks --grad-clip"
assert "smooth_window" in inspect.getsource(tr.main), "train.py lacks --smooth-window"
print(f"preflight ok: {name}, torch {torch.__version__}, train={tr.__file__}")
PY

if [ -e "$OUT" ]; then echo "refusing to reuse existing $OUT (fresh run, and test must be scored only once)"; exit 1; fi
mkdir -p "$OUT"
$VENV/bin/python -B -m wavenet_pipeline.03_machine_learning.train \
    $DATA/full_production_cache.h5 $DATA/splits_seed42_full.json \
    --out-dir "$OUT" --lr $LR --epochs $EPOCHS --seed $SEED \
    --num-workers $((SLURM_CPUS_PER_TASK - 1)) \
    --plateau-patience $PLATEAU_PATIENCE --early-stop-patience $EARLY_STOP_PATIENCE \
    --grad-clip $GRAD_CLIP --smooth-window $SMOOTH_WINDOW \
    --device cuda

echo "=== selected checkpoint on val"
$VENV/bin/python -B -m wavenet_pipeline.03_machine_learning.eval_by_family \
    $DATA/full_production_cache.h5 $DATA/splits_seed42_full.json "$OUT/best.pt" \
    --split val --num-workers $((SLURM_CPUS_PER_TASK - 1)) --json "$OUT/val_eval.json"
echo "=== selected checkpoint on TEST (M09), scored once"
$VENV/bin/python -B -m wavenet_pipeline.03_machine_learning.eval_by_family \
    $DATA/full_production_cache.h5 $DATA/splits_seed42_full.json "$OUT/best.pt" \
    --split test --num-workers $((SLURM_CPUS_PER_TASK - 1)) --json "$OUT/test_eval.json"
