#!/bin/bash
##SBATCH=/software/slurm/current/bin/sbatch
##SQUEUE=/software/slurm/current/bin/squeue
SBATCH=/sfw/rhel9-x86_64/slurm/24.05.0.b1/bin/sbatch
SQUEUE=/sfw/rhel9-x86_64/slurm/24.05.0.b1/bin/squeue
ROOT="/scratch/tolugboj_lab/Prj_Wavenet/epic_production/Baowei_test"
CONFIGS_DIR="${ROOT}/Baowei_test/configs"
SUBMIT_SCRIPT="${ROOT}/submit_BH3_combined.sh"
numSubJobs=2000
JOB_MAP="${ROOT}/job_map.csv"

TOTAL_ROWS=$(( $(wc -l < "$JOB_MAP") - 1 ))
BATCH_SIZE=$(( (TOTAL_ROWS + numSubJobs - 1) / numSubJobs )) ##JOBArraySize

if (( BATCH_SIZE>1000 ))
then
  echo "JobArray size: ${BATCH_SIZE}, maximum allowed 1000"
  exit 1
fi
rm -rf  "${ROOT}/logs"
mkdir -p "${ROOT}/logs"
echo "Rows: ${TOTAL_ROWS} | JobArray: ${BATCH_SIZE} | Batches: ${numSubJobs}"
$SBATCH --array=1-${BATCH_SIZE} "$SUBMIT_SCRIPT" "$BATCH_SIZE" "$numSubJobs"

