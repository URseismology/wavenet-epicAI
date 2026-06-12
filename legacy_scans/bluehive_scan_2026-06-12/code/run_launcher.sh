#!/bin/bash
SBATCH=/software/slurm/current/bin/sbatch
SQUEUE=/software/slurm/current/bin/squeue
ROOT="/scratch/tolugboj_lab/Prj_Wavenet/epic_production"
CONFIGS_DIR="${ROOT}/experiments/experiment_17/configs"
SUBMIT_SCRIPT="${ROOT}/submit_exp17.sh"
BATCH_SIZE=1000
DELAY=5
MAX_QUEUE=1000
FAMILY=$1
JOB_MAP="${CONFIGS_DIR}/${FAMILY}/job_map.csv"
TOTAL_ROWS=$(( $(wc -l < "$JOB_MAP") - 1 ))
TOTAL_BATCHES=$(( (TOTAL_ROWS + BATCH_SIZE - 1) / BATCH_SIZE ))
mkdir -p "${ROOT}/logs"
echo "Family: ${FAMILY} | Rows: ${TOTAL_ROWS} | Batches: ${TOTAL_BATCHES}"
for (( batch=0; batch<TOTAL_BATCHES; batch++ )); do
    OFFSET=$(( batch * BATCH_SIZE + 1 ))
    REMAINING=$(( TOTAL_ROWS - batch * BATCH_SIZE ))
    if [ "$REMAINING" -lt "$BATCH_SIZE" ]; then ARRAY_END=$REMAINING; else ARRAY_END=$BATCH_SIZE; fi
    while true; do
        QUEUED=$($SQUEUE --array -u "$USER" -h 2>/dev/null | wc -l)
        if [ "$QUEUED" -lt "$MAX_QUEUE" ]; then break; fi
        echo "  Queue full (${QUEUED}) - waiting 60s..."
        sleep 60
    done
    JOB_ID=$($SBATCH --array=1-${ARRAY_END} "$SUBMIT_SCRIPT" "$FAMILY" "$OFFSET" | awk '{print $NF}')
    echo "  Batch $((batch+1))/${TOTAL_BATCHES} | rows ${OFFSET}-$(( OFFSET + ARRAY_END - 1 )) | job ${JOB_ID}"
    sleep $DELAY
done
echo "Done: ${FAMILY}"
