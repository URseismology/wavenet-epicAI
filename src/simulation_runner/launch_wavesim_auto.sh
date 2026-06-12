#!/bin/bash
#SBATCH -J wavesim_auto
#SBATCH -A tolugboj_lab
#SBATCH -p standard
#SBATCH --time=01:00:00
#SBATCH -o logs/orchestrator_%j.out
#SBATCH -e logs/orchestrator_%j.err

ROOT="/scratch/tolugboj_lab/Prj_Wavenet/epic_production/Baowei_test"
cd $ROOT

echo "=========================================="
echo "WaveSim Orchestrator Started"
echo "=========================================="

echo "[1/3] Generating JobDiff Pending Map..."
python3 generate_jobmap_diff.py

PENDING_COUNT=$(wc -l < job_map_pending.csv)
if [ "$PENDING_COUNT" -eq 0 ]; then
    echo "No pending jobs found. All simulations complete!"
    exit 0
fi

# 15 configs * ~2 hours = ~30 hours (fits perfectly in 48 hr preempt limit)
SUBJOBS=15

# Bluehive limits Array Task IDs to a maximum of 1000.
# We will natively chunk the submissions to respect this limit.
CHUNK_SIZE=999

echo "[2/3] Total Pending Simulations: $PENDING_COUNT"

# Dispatch Logic
PROCESSED=0

while [ "$PROCESSED" -lt "$PENDING_COUNT" ]; do
    REMAINING=$(( PENDING_COUNT - PROCESSED ))
    TASKS=$(( REMAINING / SUBJOBS ))
    if [ "$(( REMAINING % SUBJOBS ))" -ne 0 ]; then
        TASKS=$(( TASKS + 1 ))
    fi
    
    if [ "$TASKS" -gt "$CHUNK_SIZE" ]; then
        TASKS=$CHUNK_SIZE
    fi
    
    # Routing everything to preempt because standard nodes crash MPI
    echo "Submitting 1-${TASKS} to preempt with BASE_OFFSET=${PROCESSED}..."
    sbatch -p preempt --time=1-12:00:00 --array=1-${TASKS} submit_wavesim_batch.sh 1 $SUBJOBS $PROCESSED
    
    PROCESSED=$(( PROCESSED + TASKS * SUBJOBS ))
done

echo "=========================================="
echo "WaveSim Orchestrator Complete"
echo "=========================================="
