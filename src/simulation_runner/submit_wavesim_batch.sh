#!/bin/bash
#SBATCH -J waveNet
#SBATCH -A tolugboj_lab 
#SBATCH -N 1
#SBATCH -n 24 
#SBATCH --mem=30G
#SBATCH -o logs/baowei_%A_%a.out
#SBATCH -e logs/baowei_%A_%a.err

ROOT="/scratch/tolugboj_lab/Prj_Wavenet/epic_production/Baowei_test"
CONFIGS_DIR="${ROOT}/configs"
JOB_MAP="${ROOT}/job_map_pending.csv"
OUTPUT_BASE="${ROOT}/outputs_v2"

BATCH_SIZE=$1
numSubJobs=$2
BASE_OFFSET=${3:-0}

# Calculate exact row range in the CSV file
OFFSET=$(( BASE_OFFSET + (SLURM_ARRAY_TASK_ID-1) * numSubJobs + 1 ))

if (( $# < 2 )); then
  echo "submit_wavesim_batch.sh Error: two arguments needed!!"
  exit 1
fi

module purge
module load circ slurm/24.05.0.b1
module load gcc/4.9.4
module load CPS/3.30
module load openmpi
module load instaseis_env

export OMPI_MCA_coll_ml_enable=0
export UCX_TLS=rc,self,shm
export OMPI_MCA_pml=ucx
export OMPI_MCA_btl=self,vader

if [ ! -f "$JOB_MAP" ]; then
    echo "ERROR: Job map not found: $JOB_MAP"
    exit 1
fi

mkdir -p "${ROOT}/logs/status"

for ((i_subJob=0; i_subJob<numSubJobs; i_subJob++))
do
  ROW=$(( OFFSET + i_subJob))
  echo "Offset=$OFFSET, ROW=$ROW"
  
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
      DIST_KM="?" R_MIN="?" R_MAX="?" THETA_MIN="?" THETA_MAX="?"
  fi
  
  OUTPUT_DIR="${OUTPUT_BASE}/${FAMILY}/${STEM}"
  mkdir -p "$OUTPUT_DIR"
  
  # --- THE FAIL SAFE SWITCH ---
  STATUS_FILE="${ROOT}/logs/status/${STEM}_SUCCESS.done"
  if [ -f "$STATUS_FILE" ]; then
      echo "=========================================="
      echo "FAIL-SAFE TRIGGERED: ${STEM} already completed!"
      echo "File found at: $STATUS_FILE"
      echo "Skipping to next sub-job."
      echo "=========================================="
      continue
  fi
  
  echo "=========================================="
  echo "Baowei Test (WaveSim)"
  echo "=========================================="
  echo "SLURM Job ID   : ${SLURM_JOB_ID}"
  echo "Job map row    : ${ROW}"
  echo "Node           : $(hostname)"
  echo "Config         : $(basename ${CONFIG_FILE})"
  echo "=========================================="
  
  tt0=$SECONDS
  
  mpirun -n $SLURM_NTASKS python3 ${ROOT}/worker_point_forces_bl.py "$JOB_MAP" "$OUTPUT_DIR" "$ROW"
  EXIT_CODE=$?
  
  echo "mpi job complete. total running time: $(( SECONDS - tt0 )) secs"
  sleep 2
  rm -rf ${ROOT}/tmp_wavenet/task_${SLURM_JOB_ID}_${i_subJob}
  
  # --- THE PROGRESS STATUS WRITER ---
  if [ $EXIT_CODE -eq 0 ]; then
      echo "Writing success status to $STATUS_FILE"
      touch "$STATUS_FILE"
  fi
  
  echo "=========================================="
  echo "Family: ${FAMILY} | Stem: ${STEM} | Task: ${i_subJob}"
  echo "Exit code : ${EXIT_CODE}"
  echo "=========================================="
done  

exit ${EXIT_CODE}
