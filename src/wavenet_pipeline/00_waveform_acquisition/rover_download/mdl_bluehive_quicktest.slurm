#!/bin/bash
#SBATCH -A tolugboj_lab -p urseismo
#SBATCH --array=0-19
#SBATCH -t 00:30:00
#SBATCH --mem-per-cpu=2G
#SBATCH -n 1
#SBATCH -o /scratch/tolugboj_lab/wavenet_ncf_quicktest/logs/slurm_%a.out
#SBATCH -e /scratch/tolugboj_lab/wavenet_ncf_quicktest/logs/slurm_%a.err

source /scratch/tolugboj_lab/softwares/anaconda/anaconda3/2021.05/etc/profile.d/conda.sh
conda activate instaseis
python3 /scratch/tolugboj_lab/wavenet_ncf_quicktest/mdl_bluehive_quicktest.py $SLURM_ARRAY_TASK_ID
