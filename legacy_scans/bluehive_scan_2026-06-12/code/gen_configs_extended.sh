#!/bin/bash

# Usage check
if [ "$#" -lt 2 ]; then
    cat << EOF
Usage: ./generate_configs.sh <experiment_number> <model_file> [options]

Arguments:
  experiment_number : Experiment number (e.g., 13)
  model_file       : Model file path (e.g., experiments/model_suite/Siberian_Craton.mod)

Options:
  --distances  <d1 d2 ...>     : Receiver distances in km (default: 50)
  --radii      <r1-r2 r3-r4>   : Source radii ranges in km (default: 100-200)

Examples:
  # Single distance, single radius (180 configs)
  ./generate_configs.sh 13 experiments/model_suite/Siberian_Craton.mod

  # Single distance, 2 radii (360 configs)
  ./generate_configs.sh 13 experiments/model_suite/Siberian_Craton.mod --radii 100-200 400-600

  # 2 distances × 2 radii (720 configs)
  ./generate_configs.sh 13 experiments/model_suite/Siberian_Craton.mod --distances 50 100 --radii 100-200 400-600

All configs will be in experiments/experiment_<number>/configs/
EOF
    exit 1
fi

# Parse required arguments
EXP_NUM=$1
MODEL_FILE=$2
shift 2

# Default values
DISTANCES=(50)
RADII_RANGES=("100-200")

# Parse command-line arguments
while [ "$#" -gt 0 ]; do
    case "$1" in
        --distances)
            shift
            DISTANCES=()
            while [ "$#" -gt 0 ] && [[ ! "$1" =~ ^-- ]]; do
                DISTANCES+=("$1")
                shift
            done
            ;;
        --radii)
            shift
            RADII_RANGES=()
            while [ "$#" -gt 0 ] && [[ ! "$1" =~ ^-- ]]; do
                RADII_RANGES+=("$1")
                shift
            done
            ;;
        *)
            echo "ERROR: Unknown option: $1"
            exit 1
            ;;
    esac
done

# Fixed parameters
ELEVATION=100.0
N_SOURCES=10000
YR1=0.0
YR2=0.0

# Wedge parameters
NUM_WEDGES=180
WEDGE_SIZE=2  # 360/180 = 2° wedges

# Check if model file exists
if [ ! -f "$MODEL_FILE" ]; then
    echo "ERROR: Model file not found: $MODEL_FILE"
    echo "Available models:"
    ls -1 ./experiments/model_suite/*.mod 2>/dev/null || echo "  No models found"
    exit 1
fi

# Extract model name from filename
MODEL_NAME=$(basename "$MODEL_FILE" .mod)

# Directories
EXP_DIR="./experiments/experiment_${EXP_NUM}"
CONFIG_DIR="${EXP_DIR}/configs"
JOB_MAP="${EXP_DIR}/job_map.csv"

# Create directories
mkdir -p "$CONFIG_DIR"
mkdir -p "$EXP_DIR"

echo ""
echo "========================================================================"
echo "CONFIGURATION GENERATOR - SINGLE EXPERIMENT, MULTIPLE PARAMETERS"
echo "========================================================================"
echo ""
echo "Experiment:"
echo "  Number: $EXP_NUM"
echo "  Model: $MODEL_NAME"
echo ""
echo "Parameters:"
echo "  Distances: ${DISTANCES[@]} km"
echo "  Radii ranges: ${RADII_RANGES[@]} km"
echo "  Wedges per param set: $NUM_WEDGES"
echo "  Sources per wedge: $N_SOURCES"
echo ""
echo "Total configurations:"
TOTAL_CONFIGS=$((${#DISTANCES[@]} * ${#RADII_RANGES[@]} * NUM_WEDGES))
echo "  ${#DISTANCES[@]} distance(s) × ${#RADII_RANGES[@]} radii × $NUM_WEDGES wedges = $TOTAL_CONFIGS configs"
echo ""
echo "Output:"
echo "  Directory: $EXP_DIR"
echo "  Job map: $JOB_MAP"
echo ""
echo "========================================================================"
echo ""

# Initialize job map
> "$JOB_MAP"

# Global simulation counter (continuous across all parameter combinations)
SIM_COUNTER=1

# Loop through parameter combinations
for DIST_KM in "${DISTANCES[@]}"; do
    for RADII in "${RADII_RANGES[@]}"; do
        # Parse radius range
        R_MIN=$(echo $RADII | cut -d'-' -f1)
        R_MAX=$(echo $RADII | cut -d'-' -f2)
        
        # Validate radius range
        if [ -z "$R_MIN" ] || [ -z "$R_MAX" ]; then
            echo "ERROR: Invalid radius format: $RADII"
            echo "Expected format: MIN-MAX (e.g., 100-200)"
            exit 1
        fi
        
        # Calculate receiver positions
        HALF_DIST=$(echo "scale=1; $DIST_KM / 2" | bc)
        XR1=$(echo "scale=1; -$HALF_DIST" | bc)
        XR2=$(echo "scale=1; $HALF_DIST" | bc)
        
        echo "Parameter set: Distance=${DIST_KM} km, Radius=${R_MIN}-${R_MAX} km"
        echo "  Generating configs $SIM_COUNTER-$((SIM_COUNTER + NUM_WEDGES - 1))..."
        
        # Generate configs for this parameter combination
        for j in $(seq 0 $((NUM_WEDGES - 1))); do
            THETA_MIN=$((j * WEDGE_SIZE))
            THETA_MAX=$(((j + 1) * WEDGE_SIZE))
            SIM_ID=$(printf "%05d" $SIM_COUNTER)
            
            CONFIG_NAME="SIM_${SIM_ID}_ang_${THETA_MIN}_${THETA_MAX}_dist_${DIST_KM}_rad_${R_MIN}_${R_MAX}_${MODEL_NAME}.txt"
            CONFIG_FILE="$CONFIG_DIR/$CONFIG_NAME"
            
            cat > "$CONFIG_FILE" << CFGEOF
# Experiment ${EXP_NUM} - Simulation ID: $SIM_ID
# Model: $MODEL_NAME
# Receiver distance: ${DIST_KM} km
# Wedge: [${THETA_MIN}°, ${THETA_MAX}°]
# Source Radius: [${R_MIN}, ${R_MAX}] km
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
            echo "$SIM_COUNTER,$CONFIG_FILE,$MODEL_FILE,NO" >> "$JOB_MAP"
            
            # Increment global counter
            ((SIM_COUNTER++))
        done
        
        echo "  ✓ Completed"
        echo ""
    done
done

# Final summary
echo "========================================================================"
echo "✓ CONFIGURATION GENERATION COMPLETE"
echo "========================================================================"
echo ""
echo "Experiment ${EXP_NUM}: $MODEL_NAME"
echo "  Total configs: $((SIM_COUNTER - 1))"
echo "  Config files: $CONFIG_DIR/"
echo "  Job map: $JOB_MAP"
echo ""
echo "Parameter combinations:"
printf "%-12s %-20s %-15s\n" "Distance" "Radius Range" "Config IDs"
printf "%-12s %-20s %-15s\n" "--------" "------------" "----------"

SIM_START=1
for DIST in "${DISTANCES[@]}"; do
    for RADII in "${RADII_RANGES[@]}"; do
        SIM_END=$((SIM_START + NUM_WEDGES - 1))
        printf "%-12s %-20s %-15s\n" "${DIST} km" "${RADII} km" "$SIM_START-$SIM_END"
        SIM_START=$((SIM_END + 1))
    done
done

