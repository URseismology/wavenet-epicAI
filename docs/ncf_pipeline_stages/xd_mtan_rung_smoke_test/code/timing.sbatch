#!/bin/bash
#SBATCH -A tolugboj_lab -p urseismo
#SBATCH --job-name=xd_pair_timing
#SBATCH -t 00:20:00
#SBATCH --mem=8G
#SBATCH -n 1
#SBATCH -o /scratch/tolugboj_lab/wavenet_ncf_xd_pair_test/logs/timing.out
#SBATCH -e /scratch/tolugboj_lab/wavenet_ncf_xd_pair_test/logs/timing.err
R=/scratch/tolugboj_lab/wavenet_ncf_xd_pair_test
source /scratch/tolugboj_lab/softwares/anaconda/anaconda3/2021.05/etc/profile.d/conda.sh
conda activate instaseis
python3 $R/code/timing_replay.py $R/results/placement_errors.json
