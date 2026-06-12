#!/bin/bash
#SBATCH --job-name=wavenet_hdf5_builder
#SBATCH --partition=standard
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --time=48:00:00
#SBATCH --output=logs/hdf5_builder_%j.out
#SBATCH --error=logs/hdf5_builder_%j.err

echo "Starting HDF5 dataset builder job: $SLURM_JOB_ID"
# Load the noisepy module which contains all dependencies (numpy, h5py, pycwt)
module load noisepy/0.9.91




OUTPUT_H5="/scratch/tolugboj_lab/Prj_Wavenet/epic_production/Baowei_test/wavenet_training_data.h5"

echo "Output Database: $OUTPUT_H5"

echo "----------------------------------------"
echo "Processing Legacy Outputs..."
echo "----------------------------------------"
INPUT_1="/scratch/tolugboj_lab/Prj_Wavenet/epic_production/Baowei_test/outputs"
python3 build_ml_dataset.py --input_dir "$INPUT_1" --output_h5 "$OUTPUT_H5"

echo "----------------------------------------"
echo "Processing New Outputs V2..."
echo "----------------------------------------"
INPUT_2="/scratch/tolugboj_lab/Prj_Wavenet/epic_production/Baowei_test/outputs_v2"
python3 build_ml_dataset.py --input_dir "$INPUT_2" --output_h5 "$OUTPUT_H5"

echo "Finished HDF5 build job"
echo "Date: $(date)"
