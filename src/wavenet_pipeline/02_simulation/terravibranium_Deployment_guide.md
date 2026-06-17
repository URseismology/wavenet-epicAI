# Cluster Deployment Guide: `terravibranium`

This document provides the exact configuration details and instructions for an AI agent to successfully deploy and run the `wvsim_terra_allmodsv2.py` simulation script natively on the `terravibranium` cluster.

---

## 1. SSH & Connection Authorization
- **SSH Target**: `tolugboj@terravibranium.earth.rochester.edu`
- **Authentication**: Key-based (already configured and verified to work without a password via `~/.ssh/config` or default SSH keys).
- **Network Requirements**: Direct SSH access works instantly.

## 2. Remote Directory Structure
- **Project Workspace Path**: `/home/tolugboj/wavenet-epicAI`
- **Input Parquet File Path**: `/home/tolugboj/wavenet-epicAI/src/wavenet_pipeline/01_parametrization/model_manifest.parquet`
- **Output Destination Path**: `/home/tolugboj/wavenet-epicAI/wavenet_dataset_100k.h5`

## 3. Remote Software Environment
- **Python / Conda Environment**: `/home/tolugboj/miniconda/bin/python3`
- **CPS Binaries Path (`CPS_BIN`)**: `/home/tolugboj/PROGRAMS.330/bin`
  - *Note*: The server relies on these exact paths to find the Seismology binaries (`spulse96`, `sdisp96`, etc.).

## 4. Hardware Configuration & Execution Strategy
**Crucial Finding**: `terravibranium` is a single powerful standalone workstation, **NOT** a SLURM cluster. There is no `sbatch` or `sinfo` command available. 
- **CPU Cores Available**: 48 Cores
- **RAM Available**: 251 GB

Therefore, the simulation MUST be run natively using Python's `multiprocessing` pool, which the script is already designed to use.
- **Execution Command**: `nohup /home/tolugboj/miniconda/bin/python3 /home/tolugboj/wavenet-epicAI/src/wavenet_pipeline/02_simulation/wvsim_terra_allmodsv2.py --models /home/tolugboj/wavenet-epicAI/src/wavenet_pipeline/01_parametrization/model_manifest.parquet --output /home/tolugboj/wavenet-epicAI/wavenet_dataset_100k.h5 --cores 48 &`

## 5. Syncing Protocol
- Use `rsync -avz` or `scp` to push the latest `wvsim_terra_allmodsv2.py` script from the local machine to `/home/tolugboj/wavenet-epicAI/src/wavenet_pipeline/02_simulation/` on `terravibranium` before executing.

---

## Example Action Plan for the Agent
1. **Sync**: `rsync -avz src/wavenet_pipeline/02_simulation/wvsim_terra_allmodsv2.py tolugboj@terravibranium.earth.rochester.edu:/home/tolugboj/wavenet-epicAI/src/wavenet_pipeline/02_simulation/`
2. **Execute**: SSH into `terravibranium.earth.rochester.edu` and trigger the execution using `nohup` in the background with `--cores 48`.
3. **Monitor**: Check the execution status by running `tail -f nohup.out` or checking the active processes using `top -u tolugboj`.
