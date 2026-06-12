#!/bin/bash
# Generate one job_map.csv per model family from existing config files.
#
# Output:
#   experiments/experiment_17/configs/<Family>/job_map.csv
#
# Format:
#   job_id,config_file,model_file,status
#
# Job ID resets to 1 for each family.
# Status is always NO (not yet run).

EXP_NUM=17
CONFIGS_DIR="experiments/experiment_${EXP_NUM}/configs"
PERTURBED_DIR="experiments/perturbed_models"

if [ ! -d "$CONFIGS_DIR" ]; then
    echo "ERROR: Configs directory not found: $CONFIGS_DIR"
    exit 1
fi

echo ""
echo "========================================================================"
echo "JOB MAP GENERATOR - EXPERIMENT ${EXP_NUM}"
echo "========================================================================"
echo ""
echo "  Configs dir : $CONFIGS_DIR"
echo ""

for FAMILY_DIR in "$CONFIGS_DIR"/*/; do
    FAMILY=$(basename "$FAMILY_DIR")
    JOB_MAP="${FAMILY_DIR}/job_map.csv"

    echo "  Processing family: $FAMILY"

    echo "job_id,config_file,model_file,status" > "$JOB_MAP"

    job_id=0

    while IFS= read -r CONFIG_FILE; do
        # Extract stem from config filename to find the .mod file
        # e.g. WUS_0042_dist_400_rad_300-400_ang_0_2.txt -> WUS_0042
        BASENAME=$(basename "$CONFIG_FILE" .txt)
        STEM=$(echo "$BASENAME" | sed 's/_dist_.*$//')
        MOD_FILE="${PERTURBED_DIR}/${STEM}.mod"

        job_id=$(( job_id + 1 ))
        echo "${job_id},${CONFIG_FILE},${MOD_FILE},NO" >> "$JOB_MAP"

    done < <(find "$FAMILY_DIR" -maxdepth 2 -name "*.txt" ! -name "job_map.csv" | sort)

    echo "    Written: $JOB_MAP  ($job_id entries)"
    echo ""
done

echo "========================================================================"
echo "DONE"
echo "========================================================================"
echo ""