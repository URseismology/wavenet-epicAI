#!/bin/bash
# Generate config files for experiment 17.
#
# Structure:
#   experiments/experiment_17/<Family>/<Stem>/<Stem>_dist_<D>_rad_<R1>-<R2>_ang_<T1>_<T2>.txt
#
# Per perturbed model:
#   - Randomly select one distance   : 200, 300, 400, 500, 600 km
#   - Randomly select one radii range: 100-200, 200-300, 300-400, 400-500, 500-600 km
#   - Write 180 individual wedge config files (2 deg each, 10000 sources each)

EXP_NUM=17
EXP_DIR="experiments/experiment_${EXP_NUM}/configs"
PERTURBED_DIR="experiments/perturbed_models"

NUM_WEDGES=180
WEDGE_SIZE=2
N_SOURCES=10000
ELEVATION=100.0
YR1=0.0
YR2=0.0

DISTANCES=(200 300 400 500 600)
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

echo ""
echo "========================================================================"
echo "CONFIG GENERATOR - EXPERIMENT ${EXP_NUM}"
echo "========================================================================"
echo ""
echo "  Perturbed models dir : $PERTURBED_DIR"
echo "  Total .mod files     : $TOTAL_MODS"
echo "  Distances            : ${DISTANCES[*]} km"
echo "  Radii ranges         : ${RADII_RANGES[*]} km"
echo "  Wedges per model     : $NUM_WEDGES  (${WEDGE_SIZE} deg each, ${N_SOURCES} sources)"
echo "  Output dir           : $EXP_DIR"
echo ""
echo "========================================================================"
echo ""

mkdir -p "$EXP_DIR"

models_processed=0
files_written=0
files_skipped=0

while IFS= read -r MOD_FILE; do
    STEM=$(basename "$MOD_FILE" .mod)
    FAMILY=$(echo "$STEM" | sed 's/_[0-9]\{4\}$//')

    MODEL_DIR="${EXP_DIR}/${FAMILY}/${STEM}"
    mkdir -p "$MODEL_DIR"

    # Randomly select geometry once per model file
    DIST_IDX=$(( RANDOM % N_DIST ))
    RADII_IDX=$(( RANDOM % N_RADII ))

    DIST_KM=${DISTANCES[$DIST_IDX]}
    RADII=${RADII_RANGES[$RADII_IDX]}

    R_MIN=$(echo "$RADII" | cut -d'-' -f1)
    R_MAX=$(echo "$RADII" | cut -d'-' -f2)

    HALF_DIST=$(echo "scale=1; $DIST_KM / 2" | bc)
    XR1=$(echo "scale=1; -$HALF_DIST" | bc)
    XR2=$(echo "scale=1;  $HALF_DIST" | bc)

    # Write one config file per wedge
    for j in $(seq 0 $((NUM_WEDGES - 1))); do
        THETA_MIN=$(( j * WEDGE_SIZE ))
        THETA_MAX=$(( (j + 1) * WEDGE_SIZE ))

        CONFIG_FILE="${MODEL_DIR}/${STEM}_dist_${DIST_KM}_rad_${R_MIN}-${R_MAX}_ang_${THETA_MIN}_${THETA_MAX}.txt"

        if [ -f "$CONFIG_FILE" ]; then
            files_skipped=$(( files_skipped + 1 ))
            continue
        fi

        cat > "$CONFIG_FILE" << CFGEOF
# Experiment ${EXP_NUM} - Model: ${STEM}
# Family: ${FAMILY}
# Model file: ${MOD_FILE}
# Receiver distance: ${DIST_KM} km  (+-${HALF_DIST} km)
# Annular source region: ${R_MIN}-${R_MAX} km
# Wedge: [${THETA_MIN} deg, ${THETA_MAX} deg]
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
    done

    models_processed=$(( models_processed + 1 ))

    if (( models_processed % 500 == 0 )); then
        echo "  Models processed: ${models_processed}/${TOTAL_MODS}  |  Files written: ${files_written}"
    fi

done < <(find "$PERTURBED_DIR" -maxdepth 1 -name "*.mod" | sort)

echo ""
echo "========================================================================"
echo "DONE"
echo "========================================================================"
echo ""
echo "  Models processed : ${models_processed}"
echo "  Files written    : ${files_written}"
echo "  Files skipped    : ${files_skipped} (already existed)"
echo ""
echo "  Files per model family:"
for FAMILY_DIR in "$EXP_DIR"/*/; do
    FAMILY=$(basename "$FAMILY_DIR")
    COUNT=$(find "$FAMILY_DIR" -name "*.txt" | wc -l)
    printf "    %-30s %d\n" "$FAMILY" "$COUNT"
done
echo ""
echo "  Output: $EXP_DIR"
echo ""