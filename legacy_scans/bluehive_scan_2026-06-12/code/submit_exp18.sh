#!/bin/bash
#SBATCH -p preempt
#SBATCH -N 1
#SBATCH --ntasks-per-node=25
#SBATCH --mem=30G
#SBATCH -t 48:00:00
#SBATCH -o logs/exp18_%A_%a.out
#SBATCH -e logs/exp18_%A_%a.err
#SBATCH --exclude=bhd0056,bhg0005,bhg0006,bhp0003,bhp0004

ROOT="/scratch/tolugboj_lab/Prj_Wavenet/epic_production"
EXP_DIR="${ROOT}/experiments/experiment_18"
JOB_MAP="${EXP_DIR}/job_map.csv"
OUTPUT_DIR="${EXP_DIR}/outputs"
TASK_ID=${SLURM_ARRAY_TASK_ID}

if [ ! -f "${JOB_MAP}" ]; then
    echo "ERROR: Job map not found: ${JOB_MAP}"
    exit 1
fi

CONFIG_LINE=$(sed -n "${TASK_ID}p" ${JOB_MAP})
if [ -z "$CONFIG_LINE" ]; then
    echo "INFO: No entry at row $TASK_ID - nothing to do"
    exit 0
fi

CONFIG_FILE=$(echo $CONFIG_LINE | cut -d',' -f2)
MODEL_FILE=$(echo $CONFIG_LINE | cut -d',' -f3)
CONFIG_BASENAME=$(basename $CONFIG_FILE)

if [[ $CONFIG_BASENAME =~ ang_([0-9]+)_([0-9]+)_dist_([0-9]+)_rad_([0-9]+)_([0-9]+) ]]; then
    THETA_MIN=${BASH_REMATCH[1]}
    THETA_MAX=${BASH_REMATCH[2]}
    DIST_KM=${BASH_REMATCH[3]}
    R_MIN=${BASH_REMATCH[4]}
    R_MAX=${BASH_REMATCH[5]}
fi

LABEL=$(echo $CONFIG_BASENAME | grep -oE '(100k|500k|1mil)')

mkdir -p ${OUTPUT_DIR}
mkdir -p ${ROOT}/logs

echo "=========================================="
echo "Experiment 18 - Source Count Test"
echo "=========================================="
echo "SLURM Job ID  : ${SLURM_JOB_ID}"
echo "Array Task ID : ${SLURM_ARRAY_TASK_ID}"
echo "Node          : $(hostname)"
echo "Started       : $(date)"
echo ""
echo "Configuration:"
echo "  Source count : ${LABEL}"
echo "  Distance     : ${DIST_KM} km"
echo "  Radius       : ${R_MIN}-${R_MAX} km"
echo "  Wedge        : ${THETA_MIN}-${THETA_MAX} deg"
echo "  Config       : $(basename ${CONFIG_FILE})"
echo "  Model        : $(basename ${MODEL_FILE})"
echo "  Output dir   : ${OUTPUT_DIR}"
echo "=========================================="
echo ""

module purge
module load openmpi/4.0.1/b2
module load CPS/3.30

tt0=$SECONDS
echo "Starting MPI simulation..."

mpirun -n $SLURM_NTASKS python3 ${ROOT}/worker_point_forces_exp18.py \
    "${JOB_MAP}" \
    "${OUTPUT_DIR}" \
    "${TASK_ID}"

EXIT_CODE=$?

echo "mpi job complete. total running time: $(( SECONDS - tt0 )) secs"
echo ""
echo "Cleaning up..."
sleep 2
rm -rf ${ROOT}/tmp_wavenet/task_${TASK_ID}_${SLURM_JOB_ID}

echo ""
echo "=========================================="
echo "Source count: ${LABEL} | Task: ${TASK_ID}"
echo "Exit code : ${EXIT_CODE}"
echo "Ended     : $(date)"
echo "=========================================="

exit ${EXIT_CODE}
