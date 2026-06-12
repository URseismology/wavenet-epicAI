#!/bin/bash
#   Baowei_test/configs/<Family>/<Stem>_dist_<D>_rad_<R1>-<R2>_ang_0_360.txt
#   Baowei_test/job_map.csv

PERTURBED_DIR="experiments/perturbed_models"
OUTPUT_DIR="Baowei_test"
CONFIGS_DIR="${OUTPUT_DIR}/configs"
JOB_MAP="${OUTPUT_DIR}/job_map.csv"

N_SOURCES=100000
ELEVATION=100.0
YR1=0.0
YR2=0.0
THETA_MIN=0
THETA_MAX=360

DISTANCES=(50 100 150 200)
RADII_RANGES=("100-200" "200-300" "300-400" "400-500" "500-600")

N_DIST=${#DISTANCES[@]}
N_RADII=${#RADII_RANGES[@]}

if [ ! -d "$PERTURBED_DIR" ]; then
    echo "ERROR: Perturbed models directory not found: $PERTURBED_DIR"
    exit 1
fi

TOTAL_MODS=$(find "$PERTURBED_DIR" -maxdepth 1 -name "*.mod" | wc -l)
if [ "$TOTAL_MODS" -eq 0 ]; then
    echo "ERROR: No .mod files found in $PERTURBED_DIR"
    exit 1
fi

mkdir -p "$OUTPUT_DIR"
echo "job_id,config_file,model_file,status" > "$JOB_MAP"

echo ""
echo "========================================================================"
echo "CONFIG GENERATOR - BAOWEI TEST"
echo "========================================================================"
echo ""
echo "  Perturbed models dir : $PERTURBED_DIR"
echo "  Total .mod files     : $TOTAL_MODS"
echo "  Distances            : ${DISTANCES[*]} km"
echo "  Radii ranges         : ${RADII_RANGES[*]} km"
echo "  Azimuth              : 0-360 deg (full)"
echo "  Sources per config   : $N_SOURCES"
echo "  Output dir           : $OUTPUT_DIR"
echo ""
echo "========================================================================"
echo ""

job_id=0
files_written=0
files_skipped=0

while IFS= read -r MOD_FILE; do
    STEM=$(basename "$MOD_FILE" .mod)
    FAMILY=$(echo "$STEM" | sed 's/_[0-9]\{4\}$//')

    FAMILY_DIR="${CONFIGS_DIR}/${FAMILY}"
    mkdir -p "$FAMILY_DIR"

    # Randomly select geometry
    DIST_IDX=$(( RANDOM % N_DIST ))
    RADII_IDX=$(( RANDOM % N_RADII ))

    DIST_KM=${DISTANCES[$DIST_IDX]}
    RADII=${RADII_RANGES[$RADII_IDX]}

    R_MIN=$(echo "$RADII" | cut -d'-' -f1)
    R_MAX=$(echo "$RADII" | cut -d'-' -f2)

    HALF_DIST=$(echo "scale=1; $DIST_KM / 2" | bc)
    XR1=$(echo "scale=1; -$HALF_DIST" | bc)
    XR2=$(echo "scale=1;  $HALF_DIST" | bc)

    CONFIG_FILE="${FAMILY_DIR}/${STEM}_dist_${DIST_KM}_rad_${R_MIN}-${R_MAX}_ang_${THETA_MIN}_${THETA_MAX}.txt"

    if [ -f "$CONFIG_FILE" ]; then
        files_skipped=$(( files_skipped + 1 ))
    else
        cat > "$CONFIG_FILE" << CFGEOF
# Baowei Test - Model: ${STEM}
# Family: ${FAMILY}
# Model file: ${MOD_FILE}
# Receiver distance: ${DIST_KM} km  (+-${HALF_DIST} km)
# Annular source region: ${R_MIN}-${R_MAX} km
# Azimuth: full 0-360 deg
THETA_MIN_DEG ${THETA_MIN}
THETA_MAX_DEG ${THETA_MAX}
R_MIN_KM      ${R_MIN}
R_MAX_KM      ${R_MAX}
XR1_KM        ${XR1}
YR1_KM        ${YR1}
XR2_KM        ${XR2}
YR2_KM        ${YR2}
ELEVATION_M   ${ELEVATION}
N_SOURCES     ${N_SOURCES}
CFGEOF
        files_written=$(( files_written + 1 ))
    fi

    job_id=$(( job_id + 1 ))
    echo "${job_id},${CONFIG_FILE},${MOD_FILE},NO" >> "$JOB_MAP"

    if (( job_id % 5000 == 0 )); then
        echo "  Processed ${job_id}/${TOTAL_MODS} models..."
    fi

done < <(find "$PERTURBED_DIR" -maxdepth 1 -name "*.mod" | sort)

echo ""
echo "========================================================================"
echo "DONE"
echo "========================================================================"
echo ""
echo "  Config files written : ${files_written}"
echo "  Config files skipped : ${files_skipped} (already existed)"
echo "  Job map entries      : ${job_id}"
echo "  Job map              : ${JOB_MAP}"
echo ""