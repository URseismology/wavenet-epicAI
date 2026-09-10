# WaveNet-EpicAI

Generates synthetic seismic ambient-noise cross-correlation functions (CCFs) from
10,000 randomized 1D Earth models using the Computer Programs in Seismology (CPS)
package, then trains a PyTorch U-Net to extract group-velocity dispersion curves from
the resulting FTAN images.

This repo is the **classical-baseline** component of a broader research program
(Empire AI project 580, "Planetary Imaging with AI") that also includes two
physics-informed sibling repos — **[AkiNet_V1](https://github.com/URseismology/AkiNet_V1)**
(PINN inverting phase velocity directly from CCFs) and
**[iRADNet](https://github.com/URseismology/iRADNet)** (algorithm-unrolling network for
the inverse Radon transform). The plan: benchmark this repo's classical U-Net against
those frameworks using the synthetic dataset generated here, then extend the
physics-informed approach further.

## Start here

- **[CLAUDE.md](CLAUDE.md)** — single source of truth for infrastructure, ground-truth
  files, and rules (read automatically by Claude Code every session; anyone working on
  this repo should read it too).
- **[docs/HANDOFF.md](docs/HANDOFF.md)** — full project state, timeline, and
  infrastructure detail. Check this first for "what's actually going on right now."

## Architecture — three-stage pipeline

```
src/wavenet_pipeline/
  01_parametrization/     10,000 randomized 1D Earth models
                          (model_manifest.parquet — lives on terravibranium only,
                           ~1.7MB, not checked into git)
        │
        ▼
  02_simulation/          CPS-based simulator, runs on terravibranium
    wvsim_main.py           The only simulator — full CPS eigenfunction pipeline,
                             1M point-force noise sources → CCF + empirical FTAN +
                             theoretical dispersion, written directly to HDF5
    verify_main.py           HDF5 verifier + reference schema reader
        │
        ▼
  03_machine_learning/    Staged U-Net pipeline (in progress — see PROGRESS.md)
    Stage A  Data Definition        FTAN regrid (1-20s x 2-5km/s), mask construction,
    Stage B  Model Definition       family-aware train/val/test split — LOCAL TIER
    Stage C  Input Batching         DONE, verified against real data (2026-09-04)
    Stage D  Training Process
    Stage E  Prediction/Evaluation  Next: terravibranium-gpu -> Alpha -> Beta
```

Two production datasets exist, both complete and backed up to `repovibranium`:
`wavenetv2_dataset_10k_full.h5` (sep_km=127.0) and
`wavenetv2_dataset_10k_sep100km.h5` (sep_km=100.0), 10,000 models each.

## Infrastructure (see CLAUDE.md for full detail)

| Machine | Role |
|---|---|
| **axon-1** | Hub — all work originates here (single-machine rule) |
| **terravibranium** | Primary CPU compute — runs the CPS simulator |
| **terravibranium-gpu** | RTX 3090 — ML training target |
| **Empire AI Alpha / Beta** | H100/H200 + Blackwell B200 GPU clusters — ML training targets. See `docs/empireai_alpha_slurm_faq.md` / `_tutorial.md` |
| **repovibranium** | NAS — dataset backup only, no compute |

## Key documentation

| Doc | What it's for |
|---|---|
| [CLAUDE.md](CLAUDE.md) | AI-agent rules, ground-truth files, infrastructure, escalation policy |
| [docs/HANDOFF.md](docs/HANDOFF.md) | Full project state/timeline — read this first |
| [docs/ml_pipeline_stages/PROGRESS.md](docs/ml_pipeline_stages/PROGRESS.md) | ML pipeline: stage x hardware-tier status |
| [docs/empireai_alpha_slurm_faq.md](docs/empireai_alpha_slurm_faq.md) | Empire AI Alpha Slurm/Apptainer reference |
| [docs/empireai_alpha_slurm_tutorial.md](docs/empireai_alpha_slurm_tutorial.md) | Hands-on Alpha walkthrough with our real account |
| [docs/containers_docker_vs_apptainer.md](docs/containers_docker_vs_apptainer.md) | Docker vs. Apptainer vs. our registry vs. Beta's Pyxis — what to use where |
| [docs/empireai_allocation_award.md](docs/empireai_allocation_award.md) | Empire AI Beta SU award, Coldfront record, project goals (benchmark vs. AkiNet/iRADNet) |
| [docs/memos/](docs/memos/) | Team memos — infra setup, review requests, workshop notes |

**Stale docs — do not use for current workflows** (full list with reasons in
CLAUDE.md): `docs/Master_Project_Overview.md`, `docs/README_WaveSimArchitecture.md`,
`docs/README_MachineLearning.md`, and `docs/Collaborative_Roadmap.md`'s Phases 1-2 all
describe the abandoned Instaseis/MPI-era pipeline. `src/machine_learning/
U_NET_array.py` and `src/data_processing/{h5_wavenet_tools,build_ml_dataset}.py` are
superseded by `src/wavenet_pipeline/03_machine_learning/` (2026-09-04).

## Repo layout

- **`src/wavenet_pipeline/`** — the current, documented pipeline (parametrization →
  simulation → ML), described above.
- **`src/simulation_runner/`** — Bluehive Slurm array job scripts for the planned
  multi-separation scan (`submit_wvsim_bluehive.sh`); not yet run for the first time.
- **`src/machine_learning/`, `src/data_processing/`** — superseded (see above), kept as
  historical reference with pointer comments to the current location.
- **`chrisScripts/`** — data-acquisition tooling (EarthScope/S3 downloads, metadata
  indexing) plus `julyncf_pipeline/ML_pipeline/`, the schema-correct U-Net prototype
  that `src/wavenet_pipeline/03_machine_learning/` was promoted from (superseded, kept
  as reference — see its own README).
- **`legacy_scans/`** — timestamped archives of earlier Bluehive/Instaseis workflows,
  historical reference only.
- Root-level scripts (`compute_ccf.py`, `worker_point_forces.py`, `coverage_analysis.py`,
  `FTAN_ML.py`, `FTAN_Noisepy.py`, `generate_models.py`, `plot_models.py`) — supporting
  physics/analysis utilities used during the CPS pipeline's development; the settled
  FTAN/CWT recipe they establish is documented in `src/wavenet_pipeline/
  03_machine_learning/ftan_grid.py`.
