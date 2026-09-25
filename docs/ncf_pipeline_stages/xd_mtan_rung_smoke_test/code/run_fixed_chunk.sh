#!/bin/bash
# usage: run_fixed_chunk.sh <sub-root name> <station idx 0=MTAN 1=RUNG> <DAY_START yyyymmdd> <DAY_END yyyymmdd>
# Re-processes ALREADY-DOWNLOADED raw files (WAVENET_SKIP_DOWNLOAD=1) with the FIXED code into its own root.
R=/scratch/tolugboj_lab/wavenet_ncf_xd_pair_test
SUB=$R/$1; STA_IDX=$2
mkdir -p $SUB/scratch_work $SUB/manifest $SUB/results $SUB/packaged_h5 $SUB/logs
cp $R/manifest/xd_pair.csv $SUB/manifest/
for s in XD_MTAN XD_RUNG; do
  mkdir -p $SUB/scratch_work/$s
  [ -e $SUB/scratch_work/$s/mseed ] || ln -s $R/scratch_work/$s/mseed $SUB/scratch_work/$s/mseed
  [ -e $SUB/scratch_work/$s/stationxml ] || ln -s $R/scratch_work/$s/stationxml $SUB/scratch_work/$s/stationxml
done
source /scratch/tolugboj_lab/softwares/anaconda/anaconda3/2021.05/etc/profile.d/conda.sh
conda activate instaseis
export WAVENET_PROD_ROOT=$SUB WAVENET_STATIONS_CSV=$SUB/manifest/xd_pair.csv
export WAVENET_START=1994-05-25 WAVENET_END=1994-10-30T23:59:59 WAVENET_CHANNELS='BH?' WAVENET_CHANNEL_PRIORITIES='BH?'
export WAVENET_SKIP_DOWNLOAD=1 WAVENET_DAY_START=$3 WAVENET_DAY_END=$4
python3 $R/code_fixed/production/orchestrator.py $STA_IDX
