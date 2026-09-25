#!/bin/bash
#SBATCH -A tolugboj_lab -p urseismo
#SBATCH --job-name=xd_pair_dryrun
#SBATCH -t 00:40:00
#SBATCH --mem=24G
#SBATCH -n 1
#SBATCH -o /scratch/tolugboj_lab/wavenet_ncf_xd_pair_test/logs/dryrun.out
#SBATCH -e /scratch/tolugboj_lab/wavenet_ncf_xd_pair_test/logs/dryrun.err
# DRY RUN on a snapshot of the still-growing per-station files -- validates merge/load/correlation
# code paths early. Separate directory; never touches the live packaged_h5/ or the final master.h5.
R=/scratch/tolugboj_lab/wavenet_ncf_xd_pair_test
DR=$R/scratch_work/dryrun
rm -rf $DR; mkdir -p $DR/packaged_h5 $DR/out
cp $R/packaged_h5/XD.MTAN.h5 $R/packaged_h5/XD.RUNG.h5 $DR/packaged_h5/
source /scratch/tolugboj_lab/softwares/anaconda/anaconda3/2021.05/etc/profile.d/conda.sh
conda activate instaseis
python3 $R/code/rover_download/build_master_h5.py --inputs-dir $DR/packaged_h5 --master $DR/master.h5
python3 $R/code/rover_download/load_master_h5.py $DR/master.h5
python3 $R/code/rover_download/load_master_h5.py $DR/master.h5 XD MTAN BHZ
python3 $R/code/rover_download/load_master_h5.py $DR/master.h5 XD RUNG BHZ
conda activate fastmspec_batch
python3 $R/code/correlation_smoke.py $DR/master.h5 $DR/out
