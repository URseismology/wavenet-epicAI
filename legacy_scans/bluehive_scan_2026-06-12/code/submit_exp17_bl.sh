#!/bin/bash
#SBATCH -p debug
#SBATCH -N 1
#SBATCH --ntasks-per-node=20
#SBATCH --mem=30G
#SBATCH -t 00:10:00
#SBATCH -o logs/exp17_%A_%a.out
#SBATCH -e logs/exp17_%A_%a.err
#SBATCH --exclude=bhd0056,bhg0005,bhg0006,bhp0003,bhp0004

# ===========================================================================
# EXPERIMENT 17 ARRAY JOB
# Called by launcher_exp17.sh - do not submit directly
# ===========================================================================

ROOT="/scratch/tolugboj_lab/Prj_Wavenet/epic_production"
CONFIGS_DIR="${ROOT}/experiments/experiment_17/configs"
OUTPUT_BASE="${ROOT}/experiments/experiment_17/outputs"

FAMILY=$1
OFFSET=$2      # Row offset into job map (batch_index * 1000)

if [ -z "$FAMILY" ] || [ -z "$OFFSET" ]; then
    echo "ERROR: Usage: sbatch --array=1-1000 submit_exp17.sh <Family> <Offset>"
    exit 1
fi

JOB_MAP="${CONFIGS_DIR}/${FAMILY}/job_map.csv"

if [ ! -f "$JOB_MAP" ]; then
    echo "ERROR: Job map not found: $JOB_MAP"
    exit 1
fi

# Compute actual row in job map (skip header row)
ROW=$(( OFFSET + SLURM_ARRAY_TASK_ID ))

CONFIG_LINE=$(sed -n "${ROW}p" "$JOB_MAP")
if [ -z "$CONFIG_LINE" ]; then
    echo "INFO: No entry at row $ROW - nothing to do"
    exit 0
fi

CONFIG_FILE=$(echo "$CONFIG_LINE" | cut -d',' -f2)
MODEL_FILE=$(echo  "$CONFIG_LINE" | cut -d',' -f3)

# Parse geometry from config filename
CONFIG_BASENAME=$(basename "$CONFIG_FILE" .txt)
STEM=$(echo "$CONFIG_BASENAME" | sed 's/_dist_.*$//')

if [[ $CONFIG_BASENAME =~ dist_([0-9]+)_rad_([0-9]+)-([0-9]+)_ang_([0-9]+)_([0-9]+) ]]; then
    DIST_KM=${BASH_REMATCH[1]}
    R_MIN=${BASH_REMATCH[2]}
    R_MAX=${BASH_REMATCH[3]}
    THETA_MIN=${BASH_REMATCH[4]}
    THETA_MAX=${BASH_REMATCH[5]}
else
    echo "WARNING: Could not parse config filename: $CONFIG_BASENAME"
    DIST_KM="?" R_MIN="?" R_MAX="?" THETA_MIN="?" THETA_MAX="?"
fi

OUTPUT_DIR="${OUTPUT_BASE}/${FAMILY}/${STEM}"
mkdir -p "$OUTPUT_DIR"
mkdir -p "${ROOT}/logs"

echo "=========================================="
echo "Experiment 17 - Family: ${FAMILY}"
echo "=========================================="
echo "SLURM Job ID   : ${SLURM_JOB_ID}"
echo "Array Task ID  : ${SLURM_ARRAY_TASK_ID}"
echo "Job map row    : ${ROW}"
echo "Node           : $(hostname)"
echo "Started        : $(date)"
echo ""
echo "Configuration:"
echo "  Model stem : ${STEM}"
echo "  Model file : $(basename ${MODEL_FILE})"
echo "  Distance   : ${DIST_KM} km"
echo "  Radius     : ${R_MIN}-${R_MAX} km"
echo "  Wedge      : ${THETA_MIN}-${THETA_MAX} deg"
echo "  Config     : $(basename ${CONFIG_FILE})"
echo "  Output dir : ${OUTPUT_DIR}"
echo "=========================================="
echo ""

module purge

#module load CPS/3.30_2_2
module load CPS/3.30_2

echo "Starting MPI simulation..."
which python
which python3

tt0=$SECONDS

##============= Options to try: ==========
##
##=== 1. =====
#module load openmpi/4.0.1/b2
#mpirun -n $SLURM_NTASKS python3 ${ROOT}/worker_point_forces.py  "$JOB_MAP"  "$OUTPUT_DIR"  "$ROW"
##==============

##=== 2. ========
module load openmpi/4.0.1/b2
mpirun --bind-to core --map-by core -n $SLURM_NTASKS python3 ${ROOT}/worker_point_forces.py  "$JOB_MAP"  "$OUTPUT_DIR"  "$ROW"
##=================

##=== 3. ========
#module load impi/2020.4
#mpiexec.hydra -n $SLURM_NTASKS -genv I_MPI_PIN_DOMAIN=core python3 ${ROOT}/worker_point_forces.py  "$JOB_MAP"  "$OUTPUT_DIR"  "$ROW"
##=================

echo "mpi job complete. total running time: $(( SECONDS - tt0 )) secs"

EXIT_CODE=$?

echo ""
echo "Cleaning up..."
sleep 2
rm -rf ${ROOT}/tmp_wavenet/task_${SLURM_ARRAY_TASK_ID}

echo ""
echo "=========================================="
echo "Family: ${FAMILY} | Stem: ${STEM} | Task: ${SLURM_ARRAY_TASK_ID}"
echo "Exit code : ${EXIT_CODE}"
echo "Ended     : $(date)"
echo "=========================================="

exit ${EXIT_CODE}