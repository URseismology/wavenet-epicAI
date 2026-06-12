#!/bin/bash


# Fixed parameters
DIST_KM=200
HALF_DIST=100
XR1=-100.0
XR2=100.0
YR1=0.0
YR2=0.0
R_MIN=200      # FAR sources
R_MAX=400      # FAR sources
ELEVATION=100.0
N_SOURCES=10000

# Wedge parameters
NUM_WEDGES=1
WEDGE_SIZE=360  # 360/180 = 2° wedges

# Directories
EXP_DIR="./experiments/experiment_18"
CONFIG_DIR="${EXP_DIR}/configs"
JOB_MAP="${EXP_DIR}/job_map_180wedges.csv"
MODEL_FILE="./experiments/model_suite/Central_US_Continental.mod"

# Create directories
mkdir -p "$CONFIG_DIR"
mkdir -p "$EXP_DIR"

# Clear job map
> "$JOB_MAP"
echo ""
echo "Configuration:"
echo "  Wedges: $NUM_WEDGES × ${WEDGE_SIZE}°"
echo "  Distance: ${DIST_KM} km (XR1=${XR1}, XR2=${XR2})"
echo "  Source radius: ${R_MIN}-${R_MAX} km"
echo "  Sources per wedge: ${N_SOURCES}"
echo "  Model: $MODEL_FILE"
echo ""
echo "Output:"
echo "  Configs: $CONFIG_DIR/"
echo "  Job map: $JOB_MAP"
echo ""

# Generate configs
for i in $(seq 0 $((NUM_WEDGES - 1))); do
    THETA_MIN=$((i * WEDGE_SIZE))
    THETA_MAX=$(((i + 1) * WEDGE_SIZE))
    
    SIM_ID=$(printf "%05d" $((i + 1)))
    CONFIG_NAME="SIM_${SIM_ID}_ang_${THETA_MIN}_${THETA_MAX}_dist_${DIST_KM}_rad_${R_MIN}_${R_MAX}.txt"
    CONFIG_FILE="$CONFIG_DIR/$CONFIG_NAME"
    
    cat > "$CONFIG_FILE" << CFGEOF
# Experiment 6 - Simulation ID: $SIM_ID
# Wedge: [${THETA_MIN}°, ${THETA_MAX}°]
# Distance: ${DIST_KM} km
# Source Radius: [${R_MIN}, ${R_MAX}] km (far-field sources)

THETA_MIN_DEG $THETA_MIN
THETA_MAX_DEG $THETA_MAX
R_MIN_KM $R_MIN
R_MAX_KM $R_MAX
XR1_KM $XR1
YR1_KM $YR1
XR2_KM $XR2
YR2_KM $YR2
ELEVATION_M $ELEVATION
N_SOURCES $N_SOURCES
CFGEOF
    
    # Add to job map
    echo "$((i + 1)),$CONFIG_FILE,$MODEL_FILE,NO" >> "$JOB_MAP"
    
    # Progress indicator
    if [ $((i % 30)) -eq 29 ]; then
        echo "  ✓ Created configs $((i - 28))-$((i + 1))"
    fi
done

