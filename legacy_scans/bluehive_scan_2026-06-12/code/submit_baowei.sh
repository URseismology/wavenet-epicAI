#!/bin/bash
#SBATCH -J waveNet
#SBATCH -p reserved
#SBATCH --reservation=bliu17_03272026
#SBATCH -A tolugboj_lab
#SBATCH -N 1
#SBATCH --ntasks-per-node=96
#SBATCH --mem=150G
#SBATCH -t 04:00:00
#SBATCH -o logs/baowei_res_%A_%a.out
#SBATCH -e logs/baowei_res_%A_%a.err

ROOT="/scratch/tolugboj_lab/Prj_Wavenet/epic_production"
JOB_MAP="${ROOT}/Baowei_test/job_map_roundrobin.csv"
OUTPUT_BASE="${ROOT}/Baowei_test/outputs"

TOTAL_ROWS=$1

if [ -z "$TOTAL_ROWS" ]; then
    echo "ERROR: Usage: sbatch --array=1-N submit_baowei_reservation.sh <total_rows>"
    exit 1
fi

if [ ! -f "$JOB_MAP" ]; then
    echo "ERROR: Job map not found: $JOB_MAP"
    exit 1
fi

# Read from the BACK — reverse the index
ROW=$(( TOTAL_ROWS - SLURM_ARRAY_TASK_ID + 1 ))

CONFIG_LINE=$(sed -n "${ROW}p" "$JOB_MAP")
if [ -z "$CONFIG_LINE" ]; then
    echo "INFO: No entry at row $ROW - nothing to do"
    exit 0
fi

CONFIG_FILE=$(echo "$CONFIG_LINE" | cut -d',' -f2)
MODEL_FILE=$(echo  "$CONFIG_LINE" | cut -d',' -f3)

CONFIG_BASENAME=$(basename "$CONFIG_FILE" .txt)
STEM=$(echo "$CONFIG_BASENAME" | sed 's/_dist_.*$//')
FAMILY=$(echo "$STEM" | sed 's/_[0-9]*$//')

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
mkdir -p "${ROOT}/Baowei_test/logs"

echo "=========================================="
echo "Baowei Test - Reservation"
echo "=========================================="
echo "SLURM Job ID   : ${SLURM_JOB_ID}"
echo "Array Task ID  : ${SLURM_ARRAY_TASK_ID}"
echo "Job map row    : ${ROW} (from back of ${TOTAL_ROWS})"
echo "Node           : $(hostname)"
echo "Started        : $(date)"
echo ""
echo "Configuration:"
echo "  Family     : ${FAMILY}"
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
module load circ slurm/24.05.0.b1
module load CPS/3.30
module load bluehive/2.5
module load instaseis_env

export OMPI_MCA_coll_ml_enable=0
export UCX_TLS=rc,self,shm
export OMPI_MCA_pml=ucx
export OMPI_MCA_btl=self,vader

tt0=$SECONDS
echo "Starting MPI simulation..."

mpirun -n $SLURM_NTASKS python3 ${ROOT}/worker_point_forces_bl.py \
    "$JOB_MAP" \
    "$OUTPUT_DIR" \
    "$ROW"

EXIT_CODE=$?

echo "mpi job complete. total running time: $(( SECONDS - tt0 )) secs"

echo ""
echo "Cleaning up..."
sleep 2
rm -rf ${ROOT}/tmp_wavenet/task_${SLURM_JOB_ID}_${SLURM_ARRAY_TASK_ID}

echo ""
echo "=========================================="
echo "Family: ${FAMILY} | Stem: ${STEM} | Task: ${SLURM_ARRAY_TASK_ID}"
echo "Exit code : ${EXIT_CODE}"
echo "Ended     : $(date)"
echo "=========================================="

exit ${EXIT_CODE}