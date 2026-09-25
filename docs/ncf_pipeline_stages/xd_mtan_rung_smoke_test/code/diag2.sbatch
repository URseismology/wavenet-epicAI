#!/bin/bash
#SBATCH -A tolugboj_lab -p urseismo
#SBATCH --job-name=xd_pair_diag2
#SBATCH -t 00:30:00
#SBATCH --mem=16G
#SBATCH -n 1
#SBATCH -o /scratch/tolugboj_lab/wavenet_ncf_xd_pair_test/logs/diag2.out
#SBATCH -e /scratch/tolugboj_lab/wavenet_ncf_xd_pair_test/logs/diag2.err
R=/scratch/tolugboj_lab/wavenet_ncf_xd_pair_test
source /scratch/tolugboj_lab/softwares/anaconda/anaconda3/2021.05/etc/profile.d/conda.sh
conda activate instaseis
python3 $R/code/diag_alignment.py
