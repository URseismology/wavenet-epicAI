# WaveNet-EpicAI: Agent Handoff Document

> **Intended Audience:** A new AI agent (Claude Code) with humans in the loop, picking up this project fresh.
> **Last Updated:** September 2, 2026 (13:19 EDT)
> **Status of Primary Simulation:** ✅ COMPLETE — 10,000 models at `sep_km=127.0 km`.
> **Next Priority:** Run more training sets on `terravibranium` (nightly). Then expand to multi-separation scan on Bluehive.

---

## 0. Project Timeline

> All timestamps are verified from git commit history, file modification times on `terravibranium`, and the simulation execution log. Times are Eastern (EDT, UTC-4).

### Phase 0 — Repository Initialized
| Date | Event | Source |
|---|---|---|
| **2026-02-23** | First commit: `FTAN for ML model` — initial FTAN ML code uploaded | `git log` |
| **2026-05-28** | Repository activity resumes with Chris's scripts for NoisePy/S3 data download | `git log` |
| **2026-05-29 – Jun 1** | Chris's `chrisScripts/` pipeline development: EarthScope S3 download, FDSN coverage, Parquet indexing | `git log` |

### Phase 1 — New CPS-Based Physics Engine (wvsim Development)
| Date | Event | Source |
|---|---|---|
| **2026-06-12 17:01** | Major restructure: `src/` hierarchy created; `h5_wavenet_tools.py`, `build_ml_dataset.py`, `U_NET_array.py` placed in organized structure; ML docs updated | `git log` (commit `ebe8c8d`) |
| **2026-06-12 17:18 – 17:30** | HDF5 dataset architecture documented; NAS download link embedded; `Collaborative_Roadmap.md` added for Chris | `git log` |
| **2026-06-15 12:33** | `verify_hdf5.py` written and committed — 6-panel verification suite for the old Instaseis HDF5 | `git log` (commit `a3417f3`) |
| **2026-06-15 12:38** | `verify_hdf5.py` helper notes updated (last touch before sim work began) | `git log` (commit `21a507f`) |
| **2026-06-16 ~16:56 – 17:47** | CPS simulator development on `terravibranium`: `wavenet_simulator.py` → `v2` → `v3` written and iterated | File mtimes on `terravibranium` |
| **2026-06-16 ~20:47** | `wavenet_simulator_v3.1.py` finalized — last of the iterative debug versions; key physics bugs being resolved | File mtime on `terravibranium` |

### Phase 2 — Physics Bug Fixes & Final Simulator (wvsim_terra_allmodsv2.py)
| Date | Event | Source |
|---|---|---|
| **2026-06-17 ~09:09** | `wvsim_terra_allmodsv2.py` written on `terravibranium` — the production-ready simulator incorporating all 7 bug fixes (CPS pipeline order, LUT resolution, halfspace, wedge noise, dynamic NPT, SREGN.ASC columns) | File mtime on `terravibranium` |
| **2026-06-17 10:27** | **🚀 10,000-model production run launched** on `terravibranium` via `nohup` with `--cores 44 --sep_km 127.0` | Conversation log + git history |
| **2026-06-17 ~10:00 – 10:17** | Verification of earlier test run: `verify_terra_v2.py` used to generate 3 rounds of animated verification frames (`verify_frames_100`, `_v2`, `_v3`) | File mtimes on `terravibranium` |
| **2026-06-17 10:57** | Repository cleanup: simulation scripts renamed to `wvsim_main.py` / `verify_main.py`; legacy v1–v4 archived | `git log` (commit `87cedf8`) |
| **2026-06-17 11:06 – 11:15** | Root-level debug scripts, old datasets, and SLURM scripts consolidated and archived; all pushed to GitHub | `git log` (commits `b527763`, `4c1454f`) |
| **2026-06-18 03:14:19** | **✅ 10,000-model run COMPLETED** — 16.65 hours, 10,000/10,000 models, 0 errors. HDF5 and log written simultaneously. | `stat` on terravibranium output files |

### Phase 3 — Post-Simulation Backup & Documentation
| Date | Event | Source |
|---|---|---|
| **2026-06-20 08:20** | `wavenetv2_dataset_10k_full.h5` (11 GB) **backed up to repovibranium** at `/volume1/ADAMA-Shared/traindatawavenet/` | `ls -lh` on repovibranium |
| **2026-07-08** | Chris adds AWS Docker pipeline docs and orchestrator scripts; documentation expanded | `git log` |
| **2026-07-09 – 14** | Additional pipeline development pushed (exact details in `chrisScripts/` commits) | `git log` |
| **2026-07-16** | TA reporting script for JupyterHub added; unrelated to wavenet simulation | `git log` |
| **2026-09-02 13:11** | **This handoff document first written** by AI agent — project timeline documented, stale docs flagged, next steps defined | `git log` (commit `5515740`) |
| **2026-09-02 13:18** | Three critical data processing files restored to `src/data_processing/` with usage documentation | `git log` (commit `b27bea6`) |
| **2026-09-02 13:19** | Handoff updated with repovibranium Synology Drive sync paths (this version) | `git log` (commit `1182374`) |

### What Has Not Happened Yet (Future Milestones)
| Milestone | Status |
|---|---|
| Additional terravibranium overnight runs (other `sep_km` values) | ⏳ Not started |
| `submit_wvsim_bluehive.sh` created and tested | ⏳ Not started |
| Bluehive 100-separation array job run | ⏳ Not started |
| `build_ml_dataset.py` adapted for new CPS HDF5 schema | ⏳ Not started |
| PyTorch U-Net training on new CPS dataset | ⏳ Not started |

---


## ⚠️ Critical Documentation Warning (Read This First)

Several documents in this repository are **stale** and describe the **old, abandoned pipeline** using Instaseis + MPI + `.sac` files. **Do not trust them for current workflows.**

| Document | Status | What's Wrong |
|---|---|---|
| `README.md` | 🟡 Partially Stale | Describes both old and new pipelines ambiguously |
| `docs/Master_Project_Overview.md` | 🔴 Stale | Describes Instaseis/MPI architecture. Fully superseded. |
| `docs/README_WaveSimArchitecture.md` | 🔴 Stale | Describes Bluehive MPI jobs. New engine runs on `terravibranium` with Python multiprocessing. |
| `docs/Collaborative_Roadmap.md` | 🟡 Partially Stale | Phases 1–2 reference old pipeline. Phases 3–4 (HDF5/PyTorch) remain valid goals. |

**The Ground Truth Code** is in:
- `src/wavenet_pipeline/02_simulation/wvsim_main.py` — The production simulator (local copy).
- `terravibranium:/home/tolugboj/wavenet-epicAI/src/wavenet_pipeline/02_simulation/wvsim_terra_allmodsv2.py` — The version that was actually run (see naming note in Section 3.1).

---

## 1. Project Overview

WaveNet-EpicAI generates synthetic seismic ambient noise cross-correlation functions (CCFs) for training deep neural networks to perform seismic dispersion analysis (FTAN). The pipeline has two main engines:

1. **Simulation Engine** — Generates synthetic CCFs from randomized 1D Earth models using the Computer Programs in Seismology (CPS) package. Each simulation produces waveforms for a given station pair geometry (defined by `sep_km`) and saves results to HDF5.
2. **ML Engine (Future)** — A PyTorch U-Net that consumes the HDF5 training data to learn FTAN dispersion curves from synthetic CCFs.

### Architecture Pivot
The original architecture used **Instaseis + MPI + Bluehive** to generate `.sac` files. This was **abandoned** due to complexity, fragility, and queue limitations. The current system uses a **self-contained CPS-based Python simulator** (`wvsim_main.py`) running with Python's `multiprocessing.Pool` directly on `terravibranium` (48-core standalone workstation).

---

## 2. What Succeeded ✅

### 2.1 Physics Engine Rewrite (`wvsim_main.py`)
Seven identified bugs were fixed over the development history:

| Bug # | Description | Status |
|---|---|---|
| Bug 1 | CPS model header: `FLAT EARTH` → `SPHERICAL EARTH` + `DATA` | ✅ Fixed |
| Bug 2 | CPS pipeline order: `sregn96` must run **before** `sdpegn96` | ✅ Fixed |
| Bug 3 | LUT grid resolution: 5.0 km grid → **0.5 km grid** (10× finer) | ✅ Fixed |
| Bug 4 | Halfspace layer: last layer H must be `0.0` | ✅ Fixed |
| Bug 5 | Noise seeding: per-source seeding + TMAX time shifts for wedge noise | ✅ Fixed |
| Bug 6 | NPT computation: dynamic NPT from model `Vmin` (not fixed value) | ✅ Fixed |
| Bug 7 | SREGN.ASC columns: phase column index was off by 1 | ✅ Fixed |

### 2.2 10,000-Model Production Run
- **Completed:** June 17–18, 2026 (overnight)
- **Duration:** 16.65 hours (59,937 seconds)
- **Result:** 10,000/10,000 models — **0 errors**
- **Station separation:** `sep_km = 127.0 km`
- **Model IDs:** `M01_0000` → `M10_0999` (10 families × 1,000 models each)
- **Geometry:** Annulus with `r_min = 200.0 km`, `r_max = 300.0 km`

> **NOTE:** A non-fatal `RuntimeWarning: divide by zero` appears in the log at line 369 of `wvsim_terra_allmodsv2.py` in the velocity calculation `velocity_kms = np.where(travel_times > 0, sep_km / travel_times, 0.0)`. It is safely handled by `np.where` and does not affect output integrity.

### 2.3 Repository Cleanup (June 17, 2026)
- Debug scripts moved to `.gitignore`'d `_root_debug_archive/` and `_simulation_outputs/` directories.
- SLURM scripts consolidated in `src/simulation_runner/`.
- Main scripts renamed: `wvsim_main.py` (simulator) and `verify_main.py` (verifier).
- All changes committed and pushed to GitHub.

---

## 3. What Failed / Incomplete ⚠️

### 3.1 Script Name Mismatch (Important!)
The local repo and remote server have **different script names**. They are functionally equivalent but the remote may be slightly behind.

| Location | Script Name |
|---|---|
| Local (`src/wavenet_pipeline/02_simulation/`) | `wvsim_main.py` |
| `terravibranium` (same relative path on remote) | `wvsim_terra_allmodsv2.py` |

**Action required:** Before new runs, rsync `wvsim_main.py` to `terravibranium` and run a `--limit 2` test. See Section 5.1–5.2.

### 3.2 Bluehive Multi-Separation Scan (Planned, Never Implemented)
A complete plan was drafted for running `wvsim_main.py` on Bluehive as a Slurm Array scanning 100 station separations (50.0 → 297.5 km in 2.5 km steps). The script `src/simulation_runner/submit_wvsim_bluehive.sh` **does not exist yet**. See Section 6.

### 3.3 HDF5 → PyTorch ML Training (Not Started)
Phases 3–4 of `docs/Collaborative_Roadmap.md` (HDF5 verification, Dataloader, U-Net training) have not been started on the new CPS-based dataset.

---

## 4. Where Everything Lives

### 4.1 Local Mac (`EES-C02X20PPHX8F`)
Root: `/Users/olugboji/SynologyDrive/1.UofR_Seismology/1_Admin/Admin8_LabAI/wavenet-epicAI/`

| Relative Path | Description |
|---|---|
| `src/wavenet_pipeline/02_simulation/wvsim_main.py` | **Primary simulator** — production version |
| `src/wavenet_pipeline/02_simulation/verify_main.py` | Verification script & **Reference HDF5 Reader** — Study this script to understand the new CPS HDF5 schema (`wavenetv2_dataset_10k_full.h5`) |
| `src/wavenet_pipeline/01_parametrization/model_manifest.parquet` | **10,000 unique Earth models (1.7 MB) — master input** |
| `src/wavenet_pipeline/01_parametrization/wavenet_training_data.h5` | 1.7 GB **legacy** HDF5 (old Instaseis pipeline — NOT CPS) |
| `src/simulation_runner/submit_wavesim_batch.sh` | Legacy Bluehive MPI batch script (old pipeline — reference only) |
| `src/simulation_runner/launch_wavesim_auto.sh` | Legacy Bluehive orchestrator (old pipeline — reference only) |
| `docs/HANDOFF.md` | This document |
| `_root_debug_archive/` | `.gitignore`'d — legacy debug scripts, not tracked by git |

### 4.2 `terravibranium` (Primary Compute Node)
**SSH:** `ssh tolugboj@terravibranium.earth.rochester.edu` (passwordless key auth)
**Hardware:** 48-core Intel Xeon Gold 6136 @ 3.00 GHz, 251 GB RAM, RHEL 7

| Remote Path | Description |
|---|---|
| `/home/tolugboj/wavenet-epicAI/` | Full project clone (may be behind local repo) |
| `/home/tolugboj/wavenet-epicAI/src/wavenet_pipeline/02_simulation/wvsim_terra_allmodsv2.py` | **Script that ran the 10K job** |
| `/home/tolugboj/wavenet-epicAI/src/wavenet_pipeline/01_parametrization/model_manifest.parquet` | Remote copy of the model manifest |
| `/RAID6/wavenet_output/wavenetv2_dataset_10k_full.h5` | **PRIMARY OUTPUT — 11 GB, 10,000 models, sep=127 km** |
| `/RAID6/wavenet_output/wavenetv2_dataset_10k.h5` | Earlier partial test run (122 MB — ignore) |
| `/RAID6/wavenet_output/terra_v2_run_full.log` | Full execution log of the 10K run |
| `/home/tolugboj/miniconda/envs/wavenet/` | Active conda environment with all Python dependencies |
| `/home/tolugboj/PROGRAMS.330/bin/` | CPS binaries (`sprep96`, `sdisp96`, `sregn96`, etc.) |

### 4.3 `repovibranium` (NAS Backup)
**SSH:** `ssh administrator@repovibranium.earth.rochester.edu`
**Key:** `~/.ssh/id_rsa_nas`

**Two distinct storage areas exist on repovibranium:**

**A) Manually backed-up HDF5 training data** (`/volume1/ADAMA-Shared/traindatawavenet/`):

| Remote Path | Description |
|---|---|
| `/volume1/ADAMA-Shared/traindatawavenet/wavenetv2_dataset_10k_full.h5` | **11 GB — 10K CPS models, sep=127 km, backed up June 20, 2026 ✅** |
| `/volume1/ADAMA-Shared/traindatawavenet/wavenet_training_data.h5` | 1.7 GB legacy dataset (old Instaseis pipeline — NOT CPS) |
| `/volume1/ADAMA-Shared/GodModeData/CodeBaseFull/` | AI-mapped code snapshots (from `roverBckUp.py`) |
| `/volume1/ADAMA-Shared/GodModeData/Wikis/` | Auto-generated AI wikis |

**B) Synology Drive auto-sync of local Mac** (`/volume1/homes/Administrator/Drive/`):

The entire local `wavenet-epicAI` directory is **automatically mirrored** to repovibranium via Synology Drive:
```
/volume1/homes/Administrator/Drive/1.UofR_Seismology/1_Admin/Admin8_LabAI/wavenet-epicAI/
```
This means every file you edit locally also appears here automatically. Note that `wavenet_training_data.h5` appears in **two locations** within this mirror (under `src/wavenet_pipeline/01_parametrization/` and `src/data_processing/`) — both are the same legacy 1.7 GB file. The 11 GB new CPS output is NOT in the Drive sync; it is only in `traindatawavenet/` above.

**Legacy simulator archive on NAS** (for historical reference):
```
/volume1/.../wavenet-epicAI/src/wavenet_pipeline/02_simulation/_simulation_outputs/archive_legacy/
  wavenet_simulator.py, v2, v3, v3.1, v4
```

### 4.4 GitHub
- **Repo:** `https://github.com/URseismology/wavenet-epicAI`
- **Branch:** `main`
- **SSH Remote:** `git@github.com:URseismology/wavenet-epicAI.git`
- **Push key:** `~/.ssh/id_ed25519` (may need explicit: `GIT_SSH_COMMAND="ssh -i ~/.ssh/id_ed25519" git push`)

---

## 5. Immediate Priority: More Training Sets on `terravibranium`

Run additional overnight sessions at different station separations to expand the training dataset **before** setting up Bluehive.

### 5.1 Sync Latest Script to terravibranium
```bash
rsync -avz \
  src/wavenet_pipeline/02_simulation/wvsim_main.py \
  tolugboj@terravibranium.earth.rochester.edu:/home/tolugboj/wavenet-epicAI/src/wavenet_pipeline/02_simulation/
```

### 5.2 Test the Synced Script (Quick 2-Model Sanity Check)
```bash
ssh tolugboj@terravibranium.earth.rochester.edu \
  "/home/tolugboj/miniconda/envs/wavenet/bin/python3 -u \
  /home/tolugboj/wavenet-epicAI/src/wavenet_pipeline/02_simulation/wvsim_main.py \
  --models /home/tolugboj/wavenet-epicAI/src/wavenet_pipeline/01_parametrization/model_manifest.parquet \
  --output /RAID6/wavenet_output/test_quickrun.h5 \
  --cores 4 --limit 2 --sep_km 100.0"
```

### 5.3 Launch an Overnight Production Run
```bash
ssh tolugboj@terravibranium.earth.rochester.edu \
  "nohup /home/tolugboj/miniconda/envs/wavenet/bin/python3 -u \
  /home/tolugboj/wavenet-epicAI/src/wavenet_pipeline/02_simulation/wvsim_main.py \
  --models /home/tolugboj/wavenet-epicAI/src/wavenet_pipeline/01_parametrization/model_manifest.parquet \
  --output /RAID6/wavenet_output/wavenetv2_dataset_10k_sep100km.h5 \
  --cores 44 --sep_km 100.0 \
  > /RAID6/wavenet_output/run_sep100km.log 2>&1 &"
```

> **TIP:** Change `--sep_km` and the output filename for each new run. Safe range: 50.0 → 480.0 km.
> **IMPORTANT:** `terravibranium` is a physical lab machine. Run intensive jobs **overnight only**. Check load first: `ssh tolugboj@terravibranium.earth.rochester.edu "uptime"`

### 5.4 Monitor a Running Job
```bash
# Check if running
ssh tolugboj@terravibranium.earth.rochester.edu "pgrep -a python3 | grep wvsim"

# Tail live log
ssh tolugboj@terravibranium.earth.rochester.edu "tail -f /RAID6/wavenet_output/run_sep100km.log"
```

### 5.5 Back Up to repovibranium After Completion
```bash
ssh tolugboj@terravibranium.earth.rochester.edu \
  "rsync -avz --progress -e 'ssh -o ServerAliveInterval=60 -i ~/.ssh/id_rsa_nas' \
  /RAID6/wavenet_output/wavenetv2_dataset_10k_sep100km.h5 \
  administrator@repovibranium.earth.rochester.edu:/volume1/ADAMA-Shared/traindatawavenet/"
```

---

## 6. Next Major Task: Bluehive Multi-Separation Array

### 6.1 Geometry and Safe Range
The simulation geometry (from `wvsim_main.py` line ~398):
```python
r_min = max(200.0, sep_km)
r_max = r_min + 100.0
```
Safe separation range: **50.0 → 480.0 km** (mathematically verified — LUT max distance always exceeds max source-to-station distance).

### 6.2 Proposed 100-Separation Scan
- **Range:** 50.0 km → 297.5 km, step 2.5 km (100 values)
- **Per node:** One separation × all 10,000 models
- **Formula:** `SEP_KM = 50.0 + (SLURM_ARRAY_TASK_ID - 1) * 2.5`
- **Submit:** `sbatch --array=1-100 src/simulation_runner/submit_wvsim_bluehive.sh`

### 6.3 File to Create: `src/simulation_runner/submit_wvsim_bluehive.sh`

This file **does not yet exist**. Create it with:

```bash
#!/bin/bash
#SBATCH -J waveNet_sim
#SBATCH -A tolugboj_lab
#SBATCH -p urseismo
#SBATCH -N 1
#SBATCH -n 24
#SBATCH --mem=32G
#SBATCH -t 24:00:00
#SBATCH -o logs/wvsim_%A_%a.out
#SBATCH -e logs/wvsim_%A_%a.err

# Task 1 = 50.0 km, Task 100 = 297.5 km
SEP_KM=$(echo "50.0 + ($SLURM_ARRAY_TASK_ID - 1) * 2.5" | bc)

module purge
module load circ slurm
module load gcc/4.9.4
module load CPS/3.30       # puts /software/CPS/3.30/bin on PATH
module load instaseis_env  # python env with numpy, h5py, pandas, pycwt

ROOT="/scratch/tolugboj_lab/Prj_Wavenet/epic_production"
OUTPUT_DIR="${ROOT}/wavenet_outputs_multisep"
mkdir -p "${OUTPUT_DIR}" logs/

python3 ${ROOT}/wavenet-epicAI/src/wavenet_pipeline/02_simulation/wvsim_main.py \
    --models ${ROOT}/wavenet-epicAI/src/wavenet_pipeline/01_parametrization/model_manifest.parquet \
    --output ${OUTPUT_DIR}/wavenet_dataset_10k_sep_${SEP_KM}km.h5 \
    --sep_km ${SEP_KM} \
    --cores 24
```

### 6.4 Required Code Change in `wvsim_main.py` (Lines 47–50)
Update the CPS_BIN detection to use `shutil.which()` as a first-pass:

```python
# Current (hardcoded):
if platform.system() == 'Linux':
    CPS_BIN = '/home/tolugboj/PROGRAMS.330/bin'

# Replace with (dynamic):
import shutil
_cps_which = shutil.which('sprep96')
if _cps_which:
    CPS_BIN = os.path.dirname(_cps_which)   # works on Bluehive after module load CPS/3.30
elif platform.system() == 'Linux':
    CPS_BIN = '/home/tolugboj/PROGRAMS.330/bin'  # fallback for terravibranium
else:
    CPS_BIN = '/Users/olugboji/SynologyDrive/1.UofR_Seismology/1_Admin/Admin8_LabAI/wavenet-epicAI/scratch/cps/PROGRAMS.330/bin'
```

### 6.5 Bluehive Environment Facts (Verified)
- `module load CPS/3.30` puts `sprep96` at `/software/CPS/3.30/bin/sprep96` ✅
- Python/numpy/h5py/pandas available via `module load instaseis_env` ✅
- `pycwt` status on Bluehive: **unknown — needs verification** (`import pycwt` in a Bluehive interactive session)
- Bluehive 2FA: use SSH ControlMaster for scripted access (already configured in `~/.ssh/config`)

---

## 7. Machine Learning (Phase 3–4, Future)

Once multiple HDF5 files exist, the ML pipeline can begin:
- **Reference HDF5 Reader:** `src/wavenet_pipeline/02_simulation/verify_main.py` — Study this script to understand how to read the new CPS HDF5 schema (`wavenetv2_dataset_10k_full.h5`). It is the only fully compatible reader for the new output format.
- `src/wavenet_pipeline/01_parametrization/h5_wavenet_tools.py` — Legacy HDF5 reader/dataloader utilities (needs adapting to the new CPS schema based on `verify_main.py`)
- `src/machine_learning/U_NET_array.py` — Legacy U-Net (needs updating from `.npy` to HDF5 streaming using the updated dataloader)
- **Target GPU hardware:** `terravibranium-gpu` (RTX 3090, 24 GB VRAM, `ssh terravibranium-gpu`) or Empire AI (`ssh empireai` — H100/H200 GPUs)


---

## 8. Quick Reference: Key Commands

```bash
# SSH to compute nodes
ssh tolugboj@terravibranium.earth.rochester.edu
ssh administrator@repovibranium.earth.rochester.edu  # NAS
ssh tolugboj@bluehive.circ.rochester.edu             # HPC (2FA required)
ssh terravibranium-gpu                               # GPU node (RTX 3090)

# Verify HDF5 output on terravibranium
ssh tolugboj@terravibranium.earth.rochester.edu \
  "/home/tolugboj/miniconda/envs/wavenet/bin/python3 -c \
  \"import h5py; f=h5py.File('/RAID6/wavenet_output/wavenetv2_dataset_10k_full.h5','r'); \
  print(len(list(f.get('simulations',{}).keys())), 'models'); f.close()\""
# Expected output: 10000 models

# Git push (if key not in ssh-agent)
GIT_SSH_COMMAND="ssh -i ~/.ssh/id_ed25519" git push
```
