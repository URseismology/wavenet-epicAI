# Single NCF Test Pipeline

This sub-folder contains a modular, Pure-Python orchestrator for testing a single-pair NoisePy cross-correlation pipeline between EarthScope's GeoLab and the BlueHive cluster.

It features custom Frequency-Time Normalization (FTN) pre-processing math designed to mimic the ADAMA framework exactly.

## The Scripts

*   `download_pairs.py`: Runs on GeoLab. Authenticates with EarthScope S3, parses the GridMeta pairs, and downloads a specific pair's raw MiniSEED data locally.
*   `ftn.py`: Python translation of Shen et al. 2012 Frequency-Time Normalization (ported from ADAMA's MATLAB logic).
*   `xcorr_pairs.py`: The main NoisePy script. Runs on BlueHive. Intercepts NoisePy's loop, applies the `ftn.py` normalization, computes cross-correlations (NCFs), and automatically cleans up the raw MiniSEED files to save disk space.
*   `sync_and_submit.py`: The Pure-Python orchestrator. Runs on GeoLab. Bypasses strict `.sh` firewalls by executing SSH/SCP/Rsync via Python's `subprocess` to move data to BlueHive and dynamically generate/submit the SLURM job.

## Usage

1. **Download Data on GeoLab:**
   ```bash
   python download_pairs.py --pairs ../global_station_pairs10k.csv --start 2019-01-01 --end 2019-01-31 --outdir raw_data --max-pairs 1
   ```
2. **Sync to BlueHive and Compute:**
   ```bash
   python sync_and_submit.py <bluehive_username> /scratch/<bluehive_username>/metadatagloba
   ```

*Note: You do not need to upload any `.sh` files to GeoLab. The `sync_and_submit.py` script automatically manages SLURM submission on BlueHive!*
