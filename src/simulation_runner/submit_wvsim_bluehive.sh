#!/bin/bash
# submit_wvsim_bluehive.sh
# WaveNet-EpicAI — Multi-separation SLURM array for Bluehive
#
# Usage:
#   sbatch --array=1-100 src/simulation_runner/submit_wvsim_bluehive.sh
#
# This runs one sep_km value per array task:
#   Task 1  → sep_km = 50.0 km
#   Task 100 → sep_km = 297.5 km  (step: 2.5 km)
#
# Before first submission, verify with PI:
#   1. pycwt is available: `module load instaseis_env && python3 -c "import pycwt"`
#   2. Allocation name is correct (tolugboj_lab)
#   3. Output directory exists on scratch
#   4. wvsim_main.py CPS_BIN fix is committed (see HANDOFF.md §6.4)
#
# Monitor: squeue -u tolugboj
# Cancel:  scancel <JOBID>

#SBATCH -J waveNet_sim
#SBATCH -A tolugboj_lab
#SBATCH -p urseismo
#SBATCH -N 1
#SBATCH -n 24
#SBATCH --mem=32G
#SBATCH -t 24:00:00
#SBATCH -o logs/wvsim_%A_%a.out
#SBATCH -e logs/wvsim_%A_%a.err

set -euo pipefail

# ── Compute sep_km from array task ID ──────────────────────────────────────
# Task 1 = 50.0 km, step = 2.5 km, Task 100 = 297.5 km
SEP_KM=$(python3 -c "print(f'{50.0 + ($SLURM_ARRAY_TASK_ID - 1) * 2.5:.1f}')")
echo "[$(date)] Task ${SLURM_ARRAY_TASK_ID}: sep_km = ${SEP_KM} km"

# ── Load environment ───────────────────────────────────────────────────────
module purge
module load circ slurm
module load gcc/4.9.4
module load CPS/3.30          # puts /software/CPS/3.30/bin on PATH — enables shutil.which() fix
module load instaseis_env     # python with numpy, h5py, pandas, pycwt

# ── Paths ──────────────────────────────────────────────────────────────────
ROOT="/scratch/tolugboj_lab/Prj_Wavenet/epic_production"
REPO="${ROOT}/wavenet-epicAI"
OUTPUT_DIR="${ROOT}/wavenet_outputs_multisep"
LOG_DIR="${REPO}/logs"

mkdir -p "${OUTPUT_DIR}" "${LOG_DIR}"

OUTPUT_FILE="${OUTPUT_DIR}/wavenet_dataset_10k_sep_${SEP_KM}km.h5"
MANIFEST="${REPO}/src/wavenet_pipeline/01_parametrization/model_manifest.parquet"
SIMULATOR="${REPO}/src/wavenet_pipeline/02_simulation/wvsim_main.py"

# ── Guard: skip if output already exists and is non-empty ─────────────────
if [ -f "${OUTPUT_FILE}" ] && [ $(stat -c%s "${OUTPUT_FILE}") -gt 1000000 ]; then
    echo "[$(date)] Output exists and is non-trivial ($(du -h ${OUTPUT_FILE} | cut -f1)). Skipping."
    exit 0
fi

# ── Sanity check: confirm simulator is reachable ───────────────────────────
if [ ! -f "${SIMULATOR}" ]; then
    echo "[ERROR] Simulator not found at ${SIMULATOR}" >&2
    exit 1
fi

# ── Run ───────────────────────────────────────────────────────────────────
echo "[$(date)] Starting simulation: sep_km=${SEP_KM}, output=${OUTPUT_FILE}"

python3 -u "${SIMULATOR}" \
    --models   "${MANIFEST}" \
    --output   "${OUTPUT_FILE}" \
    --sep_km   "${SEP_KM}" \
    --cores    24

echo "[$(date)] Simulation complete: ${OUTPUT_FILE}"
echo "[$(date)] Output size: $(du -h ${OUTPUT_FILE} | cut -f1)"

# ── Quick HDF5 integrity check ─────────────────────────────────────────────
python3 -c "
import h5py, sys
try:
    f = h5py.File('${OUTPUT_FILE}', 'r')
    n = len(list(f.get('simulations', {}).keys()))
    f.close()
    print(f'[$(date)] HDF5 check: {n} models in output')
    if n < 9900:
        print(f'[WARNING] Expected ~10000 models, got {n}', file=sys.stderr)
except Exception as e:
    print(f'[ERROR] HDF5 check failed: {e}', file=sys.stderr)
    sys.exit(1)
"

echo "[$(date)] Task ${SLURM_ARRAY_TASK_ID} done."
