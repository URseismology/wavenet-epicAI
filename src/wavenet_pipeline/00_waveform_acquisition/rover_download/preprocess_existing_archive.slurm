#!/bin/bash
#SBATCH -A tolugboj_lab -p urseismo
#SBATCH -t 24:00:00
#SBATCH --mem-per-cpu=8G
#SBATCH -n 1
#SBATCH -o /scratch/tolugboj_lab/wavenet_ncf_quicktest/logs/full_EFI_run.out
#SBATCH -e /scratch/tolugboj_lab/wavenet_ncf_quicktest/logs/full_EFI_run.err

source /scratch/tolugboj_lab/softwares/anaconda/anaconda3/2021.05/etc/profile.d/conda.sh
conda activate instaseis
python3 /scratch/tolugboj_lab/wavenet_ncf_quicktest/preprocess_existing_archive.py \
  --datarepo-data /scratch/tolugboj_lab/PrjXX_SAmericaNoise/2_Data/2_RoverDB/II/II-EFI/datarepo/data/II \
  --network II --station EFI \
  --out-h5 /scratch/tolugboj_lab/wavenet_ncf_quicktest/full_run_EFI.h5
