#!/bin/bash
#SBATCH -A tolugboj_lab -p urseismo
#SBATCH --job-name=xd_pair_dl
#SBATCH --array=0-1
#SBATCH -t 04:00:00
#SBATCH --mem=8G
#SBATCH -n 1
#SBATCH -o /scratch/tolugboj_lab/wavenet_ncf_xd_pair_test/logs/orchestrator_%a.out
#SBATCH -e /scratch/tolugboj_lab/wavenet_ncf_xd_pair_test/logs/orchestrator_%a.err

source /scratch/tolugboj_lab/softwares/anaconda/anaconda3/2021.05/etc/profile.d/conda.sh
conda activate instaseis
export WAVENET_PROD_ROOT=/scratch/tolugboj_lab/wavenet_ncf_xd_pair_test
export WAVENET_STATIONS_CSV=/scratch/tolugboj_lab/wavenet_ncf_xd_pair_test/manifest/xd_pair.csv
python3 /scratch/tolugboj_lab/wavenet_ncf_xd_pair_test/code/production/orchestrator_xd_test.py $SLURM_ARRAY_TASK_ID
