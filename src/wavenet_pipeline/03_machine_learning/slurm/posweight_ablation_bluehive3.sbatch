#!/bin/bash
# Stage D ablation: does a lower WeightedBCELoss pos_weight shrink the ~10% mask width bias
# (pred/tgt 1.06-1.13 in final_lr3e-3_2107428), and what does the per-row-peak velocity
# decoder (metrics.peak_row_velocity) do to vRMSE? VAL ONLY: no task in this job reads the
# test split.
#
#   tasks 0-2  A: pos_weight 15 = the final run. No retraining: final_lr3e-3_2107428's
#              seed_{42,43,44}/best.pt are re-scored on val with the new decoder. That
#              directory is only read; the result goes under this job's own directory.
#   tasks 3-5  B: pos_weight 5, seeds 42/43/44
#   tasks 6-8  C: pos_weight 1, seeds 42/43/44
#
# B and C use the final run's recipe unchanged: LR 3e-3, up to 100 epochs, plateau
# patience 5, early-stop patience 25, grad clip 1.0, best.pt on the 5-epoch trailing mean
# of val IoU. Only --pos-weight differs.
#
# The Bluehive3 code copy is an rsync, so the commit it was synced from must be passed in;
# it is written to each run's config.json (git_commit) and echoed in the job log:
#   sbatch --export=ALL,WAVENET_GIT_COMMIT=<hash> src/wavenet_pipeline/03_machine_learning/slurm/posweight_ablation_bluehive3.sbatch
# (submit from the code copy root, the directory containing src/)
#
# Outputs: $RUNS/ablate_pw_<array job id>/pw<PW>/seed_<S>/{config.json,train_log.*,best.pt,last.pt,val_eval.json}
#          (pw15/seed_<S>/ holds val_eval.json only)
# SLURM stdout: $RUNS/logs/ablate_pw_<array job id>_<task>.out
#SBATCH --job-name=ftan_ablate_pw
#SBATCH --account=nkoziol_lab
#SBATCH --partition=coe   # coe,h100 is rejected: the two partitions carry different QoS
#SBATCH --gres=gpu:1
#SBATCH --cpus-per-task=8
#SBATCH --mem=64G
#SBATCH --time=01:00:00   # final run took ~22 min for 100 epochs + eval
#SBATCH --array=0-8
#SBATCH --output=/scratch/tolugboj_lab/Prj_Wavenet/epic_production/FTAN_ML_RUNS/logs/ablate_pw_%A_%a.out

set -euo pipefail

: "${WAVENET_GIT_COMMIT:?submit with --export=ALL,WAVENET_GIT_COMMIT=<hash of the synced commit>}"

SEEDS=(42 43 44)
POS_WEIGHTS=(15 5 1)
SEED=${SEEDS[$((SLURM_ARRAY_TASK_ID % 3))]}
PW=${POS_WEIGHTS[$((SLURM_ARRAY_TASK_ID / 3))]}
LR=3e-3
EPOCHS=100
PLATEAU_PATIENCE=5
EARLY_STOP_PATIENCE=25
GRAD_CLIP=1.0
SMOOTH_WINDOW=5

DATA=/scratch/tolugboj_lab/Prj_Wavenet/epic_production/FTAN_ML_CACHE
RUNS=/scratch/tolugboj_lab/Prj_Wavenet/epic_production/FTAN_ML_RUNS
BASELINE=$RUNS/final_lr3e-3_2107428
VENV=/scratch/scratch/tbalamur/envs/wavenet-ml
PKG=src/wavenet_pipeline/03_machine_learning
OUT=$RUNS/ablate_pw_${SLURM_ARRAY_JOB_ID}/pw${PW}/seed_${SEED}

module load pytorch/2.4
export PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD/src

echo "job ${SLURM_ARRAY_JOB_ID}_${SLURM_ARRAY_TASK_ID} commit=$WAVENET_GIT_COMMIT pos_weight=$PW seed=$SEED lr=$LR epochs=$EPOCHS plateau=$PLATEAU_PATIENCE early_stop=$EARLY_STOP_PATIENCE grad_clip=$GRAD_CLIP smooth=$SMOOTH_WINDOW host=$(hostname)"
nvidia-smi --query-gpu=name,memory.total --format=csv,noheader

rm -rf $PKG/__pycache__
sha256sum $PKG/{dataset.py,stage_c_input_batching.py,losses.py,metrics.py,train.py,eval_by_family.py}
$VENV/bin/python -B - <<'PY'
import importlib, inspect, torch
name = torch.cuda.get_device_name(0)
assert "H100" in name, f"expected H100, got {name}"
tr = importlib.import_module("wavenet_pipeline.03_machine_learning.train")
ls = importlib.import_module("wavenet_pipeline.03_machine_learning.losses")
mt = importlib.import_module("wavenet_pipeline.03_machine_learning.metrics")
importlib.import_module("wavenet_pipeline.03_machine_learning.eval_by_family")
assert "pos_weight=args.pos_weight" in inspect.getsource(tr.main), "train.py lacks --pos-weight"
assert "pos_weight=pos_weight" in inspect.getsource(ls.CombinedLoss.__init__), "CombinedLoss ignores pos_weight"
assert mt.PEAK_HALF_WINDOW == 3 and hasattr(mt, "peak_row_velocity"), "metrics.py lacks the per-row-peak decoder"
assert "smooth_window" in inspect.getsource(tr.main) and "clip_grad_norm_" in inspect.getsource(tr.run_epoch)
print(f"preflight ok: {name}, torch {torch.__version__}, train={tr.__file__}")
PY

if [ -e "$OUT" ]; then echo "refusing to reuse existing $OUT"; exit 1; fi
mkdir -p "$OUT"

if [ "$PW" = 15 ]; then
    CKPT=$BASELINE/seed_${SEED}/best.pt
    echo "=== A: re-scoring $CKPT on val (no training)"
else
    $VENV/bin/python -B -m wavenet_pipeline.03_machine_learning.train \
        $DATA/full_production_cache.h5 $DATA/splits_seed42_full.json \
        --out-dir "$OUT" --lr $LR --epochs $EPOCHS --seed $SEED --pos-weight $PW \
        --num-workers $((SLURM_CPUS_PER_TASK - 1)) \
        --plateau-patience $PLATEAU_PATIENCE --early-stop-patience $EARLY_STOP_PATIENCE \
        --grad-clip $GRAD_CLIP --smooth-window $SMOOTH_WINDOW \
        --device cuda
    CKPT=$OUT/best.pt
    echo "=== selected checkpoint on val"
fi
$VENV/bin/python -B -m wavenet_pipeline.03_machine_learning.eval_by_family \
    $DATA/full_production_cache.h5 $DATA/splits_seed42_full.json "$CKPT" \
    --split val --num-workers $((SLURM_CPUS_PER_TASK - 1)) --json "$OUT/val_eval.json"
