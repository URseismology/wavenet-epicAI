#!/bin/bash

EXP_DIR="./experiments/experiment_18"
CONFIG_DIR="${EXP_DIR}/configs"
JOB_MAP="${EXP_DIR}/job_map.csv"
MODEL_FILE="./experiments/model_suite/tak135sph.mod"

DIST_KM=200
HALF_DIST=100
XR1=-100.0
XR2=100.0
YR1=0.0
YR2=0.0
R_MIN=200
R_MAX=400
ELEVATION=100.0

mkdir -p "$CONFIG_DIR"
echo "job_id,config_file,model_file,status" > "$JOB_MAP"

declare -A SOURCE_COUNTS=([100k]=100000 [500k]=500000 [1mil]=1000000)
job_id=0

for label in 100k 500k 1mil; do
    N_SOURCES=${SOURCE_COUNTS[$label]}
    CONFIG_NAME="SIM_00001_ang_0_360_dist_${DIST_KM}_rad_${R_MIN}_${R_MAX}_${label}.txt"
    CONFIG_FILE="$CONFIG_DIR/$CONFIG_NAME"

    cat > "$CONFIG_FILE" << CFGEOF
# Experiment 18 - Source count test: ${label}
# Azimuth: 0-360 deg (full ring)
# Distance: ${DIST_KM} km
# Source Radius: ${R_MIN}-${R_MAX} km
# Model: tak135sph
THETA_MIN_DEG 0
THETA_MAX_DEG 360
R_MIN_KM      ${R_MIN}
R_MAX_KM      ${R_MAX}
XR1_KM        ${XR1}
YR1_KM        ${YR1}
XR2_KM        ${XR2}
YR2_KM        ${YR2}
ELEVATION_M   ${ELEVATION}
N_SOURCES     ${N_SOURCES}
CFGEOF

    job_id=$(( job_id + 1 ))
    echo "${job_id},${CONFIG_FILE},${MODEL_FILE},NO" >> "$JOB_MAP"
    echo "  Created: $CONFIG_NAME"
done

echo ""
echo "Done: 3 configs in $CONFIG_DIR"
echo "Job map: $JOB_MAP"
