#!/bin/bash
#SBATCH -p debug
#SBATCH -N 1
#SBATCH --ntasks-per-node=20
#SBATCH --mem=30G
#SBATCH -t 00:10:00
#SBATCH -o logs/job_%A_%a.out
#SBATCH -e logs/job_%A_%a.err
#SBATCH --exclude=bhd0056,bhg0005,bhg0006,bhp0003,bhp0004

# ===========================================================================
# UNIVERSAL EXPERIMENT SUBMISSION SCRIPT
# Usage: sbatch --array=1-N submit_experiment.sh <experiment_number>
# Example: sbatch --array=1-180 submit_experiment.sh 6
# ===========================================================================

# Check if experiment number provided
if [ -z "$1" ]; then
    echo "ERROR: Experiment number required!"
    echo "Usage: sbatch --array=1-N submit_experiment.sh <experiment_number>"
    echo "Example: sbatch --array=1-180 submit_experiment.sh 6"
    exit 1
fi

EXP_NUM=$1

# Define paths
ROOT="/scratch/tolugboj_lab/Prj_Wavenet/epic_production"
EXP_DIR="${ROOT}/experiments/experiment_${EXP_NUM}"
JOB_MAP="${EXP_DIR}/job_map_180wedges.csv"
OUTPUT_DIR="${EXP_DIR}/outputs"
TASK_ID=${SLURM_ARRAY_TASK_ID}

# Check if job map exists
if [ ! -f "${JOB_MAP}" ]; then
    echo "ERROR: Job map not found: ${JOB_MAP}"
    echo "Did you run generate_configs.sh for experiment ${EXP_NUM}?"
    exit 1
fi

# Extract config from job map
CONFIG_LINE=$(sed -n "${TASK_ID}p" ${JOB_MAP})
CONFIG_FILE=$(echo $CONFIG_LINE | cut -d',' -f2)
MODEL_FILE=$(echo $CONFIG_LINE | cut -d',' -f3)

# Parse config filename to get parameters
CONFIG_BASENAME=$(basename $CONFIG_FILE)
# Example: SIM_00001_ang_0_2_dist_50_rad_400_600.txt

# Extract parameters using regex
if [[ $CONFIG_BASENAME =~ ang_([0-9]+)_([0-9]+)_dist_([0-9]+)_rad_([0-9]+)_([0-9]+) ]]; then
    THETA_MIN=${BASH_REMATCH[1]}
    THETA_MAX=${BASH_REMATCH[2]}
    DIST_KM=${BASH_REMATCH[3]}
    R_MIN=${BASH_REMATCH[4]}
    R_MAX=${BASH_REMATCH[5]}
else
    echo "WARNING: Could not parse config filename"
    THETA_MIN="?"
    THETA_MAX="?"
    DIST_KM="?"
    R_MIN="?"
    R_MAX="?"
fi

# Print header
echo "=========================================="
echo "Experiment ${EXP_NUM}"
echo "=========================================="
echo "SLURM Job ID: ${SLURM_JOB_ID}"
echo "Array Task ID: ${SLURM_ARRAY_TASK_ID}"
echo "Node: $(hostname)"
echo "Started: $(date)"
echo ""
echo "Configuration:"
echo "  Experiment: ${EXP_NUM}"
echo "  Distance: ${DIST_KM} km"
echo "  Wedge: ${THETA_MIN}°-${THETA_MAX}°"
echo "  Source radius: ${R_MIN}-${R_MAX} km"
echo "  Config: $(basename ${CONFIG_FILE})"
echo "  Model: $(basename ${MODEL_FILE})"
echo "  Output dir: ${OUTPUT_DIR}"
echo "  MPI ranks: 20"
echo "=========================================="
echo ""

# Create output directory
mkdir -p ${OUTPUT_DIR}

# Load modules
module purge
module load openmpi/4.0.1/b2
module load CPS/3.30

# Run simulation
echo "Starting MPI simulation..."
mpirun -n 20 python ${ROOT}/worker_point_forces.py \
    ${JOB_MAP} \
    ${OUTPUT_DIR} \
    ${TASK_ID}

EXIT_CODE=$?

# Cleanup temporary files
echo ""
echo "Cleaning up temporary files..."
sleep 2
rm -rf ${ROOT}/tmp_wavenet/task_${TASK_ID}

# Print footer
echo ""
echo "=========================================="
echo "Experiment ${EXP_NUM} - Task ${TASK_ID} Complete"
echo "Distance: ${DIST_KM} km, Wedge: ${THETA_MIN}°-${THETA_MAX}°"
echo "Exit code: ${EXIT_CODE}"
echo "Ended: $(date)"
echo "=========================================="

exit ${EXIT_CODE}
