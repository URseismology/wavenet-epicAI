#!/bin/bash
# Generate master job list for SREGN computation across all families.
#
# Output: experiments/experiment_17/vel_curves/sregn_job_list.csv
# Format: job_id,model_file,config_file,output_path

ROOT="/scratch/tolugboj_lab/Prj_Wavenet/epic_production"
PERTURBED_DIR="${ROOT}/experiments/perturbed_models"
CONFIGS_DIR="${ROOT}/experiments/experiment_17/configs"
VEL_CURVES_DIR="${ROOT}/experiments/experiment_17/vel_curves"
JOB_LIST="${VEL_CURVES_DIR}/sregn_job_list.csv"

mkdir -p "$VEL_CURVES_DIR"

echo "job_id,model_file,config_file,output_path" > "$JOB_LIST"

job_id=0

while IFS= read -r MOD_FILE; do
    STEM=$(basename "$MOD_FILE" .mod)
    FAMILY=$(echo "$STEM" | sed 's/_[0-9]\{4\}$//')

    # Find first config file for this model
    CONFIG_FILE=$(find "${CONFIGS_DIR}/${FAMILY}/${STEM}" -name "*.txt" | sort | head -1)

    if [ -z "$CONFIG_FILE" ]; then
        echo "  WARNING: No config found for $STEM - skipping"
        continue
    fi

    OUTPUT_PATH="${VEL_CURVES_DIR}/${FAMILY}/SREGN_${STEM}.ASC"
    job_id=$(( job_id + 1 ))
    echo "${job_id},${MOD_FILE},${CONFIG_FILE},${OUTPUT_PATH}" >> "$JOB_LIST"

    if (( job_id % 5000 == 0 )); then
        echo "  Processed ${job_id} models..."
    fi

done < <(find "$PERTURBED_DIR" -maxdepth 1 -name "*.mod" | sort)

echo ""
echo "Job list written: $JOB_LIST"
echo "Total jobs: $job_id"