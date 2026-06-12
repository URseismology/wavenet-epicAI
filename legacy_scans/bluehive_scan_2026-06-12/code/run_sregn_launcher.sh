#!/bin/bash
# Launcher for SREGN computation - experiment 17.
# Loops through all families automatically, submits batches of 1000.
#
# Usage: nohup ./run_sregn_launcher.sh > sregn_launcher.log 2>&1 &

SBATCH=/software/slurm/current/bin/sbatch
SQUEUE=/software/slurm/current/bin/squeue

ROOT="/scratch/tolugboj_lab/Prj_Wavenet/epic_production"
SUBMIT_SCRIPT="${ROOT}/submit_sregn_exp17.sh"
JOB_LIST="${ROOT}/experiments/experiment_17/vel_curves/sregn_job_list.csv"
BATCH_SIZE=1000
DELAY=5
MAX_QUEUE=1000

if [ ! -f "$JOB_LIST" ]; then
    echo "ERROR: Job list not found: $JOB_LIST"
    echo "Run generate_sregn_joblist.sh first"
    exit 1
fi

TOTAL_ROWS=$(( $(wc -l < "$JOB_LIST") - 1 ))
TOTAL_BATCHES=$(( (TOTAL_ROWS + BATCH_SIZE - 1) / BATCH_SIZE ))

mkdir -p "${ROOT}/logs"

echo ""
echo "========================================================================"
echo "SREGN LAUNCHER - EXPERIMENT 17"
echo "========================================================================"
echo ""
echo "  Job list     : $JOB_LIST"
echo "  Total models : $TOTAL_ROWS"
echo "  Batch size   : $BATCH_SIZE"
echo "  Total batches: $TOTAL_BATCHES"
echo "  Max queued   : $MAX_QUEUE"
echo "  Delay        : ${DELAY}s between submissions"
echo ""
echo "========================================================================"
echo ""

for (( batch=0; batch<TOTAL_BATCHES; batch++ )); do
    OFFSET=$(( batch * BATCH_SIZE + 1 ))

    REMAINING=$(( TOTAL_ROWS - batch * BATCH_SIZE ))
    if [ "$REMAINING" -lt "$BATCH_SIZE" ]; then
        ARRAY_END=$REMAINING
    else
        ARRAY_END=$BATCH_SIZE
    fi

    # Wait until queue has room for a full batch
    while true; do
        QUEUED=$($SQUEUE --array -u "$USER" -h -p debug 2>/dev/null | wc -l)
        if [ "$QUEUED" -lt "$MAX_QUEUE" ]; then
            break
        fi
        echo "  Queue full (${QUEUED}) - waiting 60s..."
        sleep 60
    done

    JOB_ID=$($SBATCH --array=1-${ARRAY_END} "$SUBMIT_SCRIPT" "$OFFSET" | awk '{print $NF}')
    echo "  Batch $((batch+1))/${TOTAL_BATCHES} | rows ${OFFSET}-$(( OFFSET + ARRAY_END - 1 )) | job ${JOB_ID}"

    sleep $DELAY
done

echo ""
echo "========================================================================"
echo "All SREGN batches submitted"
echo "========================================================================"
echo ""