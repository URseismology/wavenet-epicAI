#!/bin/bash
SBATCH=/software/slurm/current/bin/sbatch
SQUEUE=/software/slurm/current/bin/squeue
ROOT="/scratch/tolugboj_lab/Prj_Wavenet/epic_production"
SUBMIT_SCRIPT="${ROOT}/submit_exp17_debug.sh"
BATCH_SIZE=1000
DELAY=5
MAX_QUEUE=1000
MAX_RETRIES=3

# Missing batch offsets from launcher_CIA.log
MISSING_OFFSETS=(12001 13001 14001 15001 16001 17001 18001 19001 20001 21001 22001 23001 25001 28001)

for OFFSET in "${MISSING_OFFSETS[@]}"; do
    while true; do
        QUEUED=$($SQUEUE --array -u "$USER" -p debug -h 2>/dev/null | wc -l)
        if [ "$QUEUED" -lt "$MAX_QUEUE" ]; then break; fi
        echo "  Queue full (${QUEUED}) - waiting 60s..."
        sleep 60
    done

    for attempt in $(seq 1 $MAX_RETRIES); do
        JOB_ID=$($SBATCH --array=1-${BATCH_SIZE} "$SUBMIT_SCRIPT" "CIA" "$OFFSET" | awk '{print $NF}')
        if [ -n "$JOB_ID" ] && [ "$JOB_ID" -gt 0 ] 2>/dev/null; then
            echo "  Offset ${OFFSET} | job ${JOB_ID}"
            break
        else
            echo "  Offset ${OFFSET} | attempt ${attempt} failed, retrying in 30s..."
            sleep 30
        fi
    done
    sleep $DELAY
done
echo "Done"
