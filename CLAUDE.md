# CLAUDE.md — WaveNet-EpicAI
# Claude Code reads this file automatically at the start of every session.
# Keep this file current. It is the single source of truth for all AI agents.
# Last updated: 2026-09-04

---

## What this project does

WaveNet-EpicAI generates synthetic seismic ambient noise cross-correlation
functions (CCFs) from randomized 1D Earth models using the Computer Programs
in Seismology (CPS) package. These CCFs train a PyTorch U-Net to perform
FTAN dispersion analysis.

Full project state: docs/HANDOFF.md (always check this first)

---

## Single-machine rule

ALL work originates from axon-1 (10.17.6.243, macOS, urseismoadmin).
- Other machines are accessed via SSH from axon-1 only.
- All code changes are committed from axon-1 and pushed to GitHub.
- Do NOT commit directly from terravibranium, Bluehive, or repovibranium.
- code-server runs on axon-1 at http://10.17.6.243:8080 (lab network only).
- Each team member logs into axon-1 with their own account (wavenet-senior,
  wavenet-junior) and maintains their own clone at ~/wavenet-epicAI/.
- Always `git pull` at the start of a session.
- Always `git push` when your work is complete.
- Coordinate overnight terravibranium jobs in the lab Slack before launching.

## axon-1 accounts

| Account | Role |
|---|---|
| urseismoadmin | Admin, service account, PI access |
| wavenet-senior | Senior researcher — sims, Bluehive, ML |
| wavenet-junior | Junior researcher — monitoring, docs, verification |

---

## Ground-truth files (everything else may be stale)

| File | Role |
|---|---|
| `src/wavenet_pipeline/02_simulation/wvsim_main.py` | **Production simulator — the only simulator** |
| `src/wavenet_pipeline/02_simulation/verify_main.py` | HDF5 verifier + reference schema reader |
| `src/wavenet_pipeline/01_parametrization/model_manifest.parquet` | 10,000 Earth models (master input) |
| `src/wavenet_pipeline/03_machine_learning/` | **ML pipeline (added 2026-09-04) — FTAN group-velocity U-Net.** Staged build in progress; see `docs/ml_pipeline_stages/PROGRESS.md` for status. |
| `docs/HANDOFF.md` | Full project state, timelines, infrastructure |
| `CLAUDE.md` | This file — rules for all AI agents |

---

## Stale documents — do not use for current workflows

| Document | Problem |
|---|---|
| `README.md` | Describes old + new pipelines ambiguously |
| `docs/Master_Project_Overview.md` | Describes abandoned Instaseis/MPI architecture |
| `docs/README_WaveSimArchitecture.md` | Describes Bluehive MPI. Superseded. |
| `docs/Collaborative_Roadmap.md` | Phases 1–2 are stale. Phases 3–4 (HDF5/PyTorch) superseded by `src/wavenet_pipeline/03_machine_learning/` (2026-09-04). |
| `src/wavenet_pipeline/01_parametrization/wavenet_training_data.h5` | Legacy Instaseis HDF5 — NOT the CPS dataset |
| `src/machine_learning/U_NET_array.py` | Superseded 2026-09-04 — wrong grid (80,400)/0.5-4.5km/s, old schema reader. See `src/wavenet_pipeline/03_machine_learning/`. |
| `src/data_processing/{h5_wavenet_tools,build_ml_dataset}.py` | Superseded 2026-09-04 — old flat HDF5 schema, incompatible with the real CPS dataset layout. |
| `chrisScripts/julyncf_pipeline/ML_pipeline/` | Superseded 2026-09-04 — the prototype this design came from; kept as reference only, see its README. |

---

## Infrastructure

### Hub (axon-1 — all work starts here)
Address: 10.17.6.243
OS: macOS 15
code-server: http://10.17.6.243:8080

### terravibranium (primary CPU compute)
SSH: `ssh tolugboj@terravibranium.earth.rochester.edu`
Hardware: 48-core Intel Xeon Gold 6136, 251 GB RAM, RHEL 7
Conda env: `/home/tolugboj/miniconda/envs/wavenet/bin/python3`
CPS bins: `/home/tolugboj/PROGRAMS.330/bin/`
Primary output: `/RAID6/wavenet_output/`
**Run intensive jobs overnight only. Always check `uptime` first.**

### terravibranium-gpu (ML training)
SSH: `ssh terravibranium-gpu`
Hardware: RTX 3090, 24 GB VRAM

### Bluehive (HPC — multi-sep array)
SSH: `ssh tolugboj@bluehive.circ.rochester.edu` (2FA — use SSH ControlMaster)
Partition: `urseismo`, Account: `tolugboj_lab`
**SLURM script does not yet exist. See docs/HANDOFF.md §6.**

### repovibranium (NAS backup — do not compute here)
SSH: `ssh administrator@repovibranium.earth.rochester.edu`
Key: `~/.ssh/id_rsa_nas`
CPS HDF5 backup: `/volume1/ADAMA-Shared/traindatawavenet/`
Note: The 11 GB CPS HDF5 dataset is NOT in any auto-sync path.
It lives only in `/volume1/ADAMA-Shared/traindatawavenet/` on repovibranium
and must be manually rsync'd after each run (see HANDOFF.md §5.5).

### cerebrum (added 2026-09-04 — role not yet defined)
SSH: `ssh cerebrum` (alias in ~/.ssh/config on axon-1)
Address: 10.17.6.17
User: tolulopeolugboji
OS: macOS
Passwordless SSH key access confirmed working from axon-1.

### Empire AI (added 2026-09-04, target GPU hardware for ML training)
SSH: `ssh empireai` (alias in ~/.ssh/config; matches `empireai`, `alpha1.empireai.edu`,
`alpha.empire-ai.org`, `alpha1.empire-ai.org` — all resolve to the same host, 67.99.173.2)
Canonical hostname: `alpha1.empireai.edu` | User: `tolugboji`
Project account: **`ro_tolugboji_planetary`** (project 580) — required for job submission.
**Requires password + 2FA (authenticator code) — cannot be automated.** Access from Claude
Code works via SSH `ControlMaster`/`ControlPersist`: a human logs in once interactively
(`ssh empireai`), which becomes the master connection; Claude Code then reuses that socket
directly with no further prompts for up to 12h of inactivity. `ControlPath` is scoped to
each axon-1 user's own home directory, so **each team account needs its own config entry
and its own manual login** — see `docs/memos/2026-09-04-empireai-persistent-access.md`.
Occasionally refuses the TCP connection on first attempt (load balancer flakiness observed
2026-09-04, not a lockout) — retry once before assuming it's actually down. Support:
support@empireai.edu | https://empireai.freshdesk.com/support/home

**Two clusters — do not confuse them:**
- **Alpha** (what we connect to via `ssh empireai`) — Grace ARM CPU nodes + newly added
  NVIDIA RTX Pro 6000 GPUs (for single-GPU jobs). **Starting 2026-09-18, institutional
  partitions retire** — all Alpha job submissions must use `--account ro_tolugboji_planetary`
  instead. Confirmed live in Alpha's own login banner (partition retirement notice).
- **Beta** — separate, newer cluster: NVIDIA GB200 NVL72 SuperPOD (Blackwell B200 GPUs,
  4-rack unified NVLink fabric). **Minimum 4 GPUs per job** — not for single-GPU work.
  SSH: `ssh empireai-beta` (canonical hostname `beta.empireai.edu`), same ControlMaster
  pattern as Alpha, same `ro_tolugboji_planetary` project account. Connection confirmed
  2026-09-04. Nodes are DGX-class, `Gres=gpu:b200:4` each — one node = the 4-GPU minimum.

**Job submission — plain Slurm works on both; Beta expects containers going forward.** A
normal `sbatch`/`srun` script with `#SBATCH -A ro_tolugboji_planetary` runs fine on both
clusters (proven directly). But Empire AI's own KB says Beta's nodes have "minimal software
installation" and "all workloads should run within containers" via Pyxis/Enroot — confirms
what sysadmin said informally: Beta is moving away from admin-installed software toward
containers-first. No equivalent language for Alpha. Container mechanism is **Pyxis, not
Docker** — no daemon, just a `--container-image=<registry>/<image>:<tag>` flag on
`srun`/`sbatch`; enroot pulls (any Docker-registry-v2 host, NGC/Docker Hub/private all work
the same way), converts to squashfs, runs ephemeral per-job. Private registry auth via
`~/.config/enroot/.credentials`. Gotcha: a job with no `--qos` can sit `PD (Priority)` with no
ETA — pass `--qos=test` for a quick sanity check instead.

**Self-hosted registry (`urseismogate.earth.rochester.edu`) — confirmed reachable 2026-09-04**
from an actual allocated Beta compute node (DNS + TCP 443 + general internet egress all
succeeded) — unlike Bluehive/terravibranium, **Beta compute nodes have full outbound
internet**, no login-node-relay workaround needed. Note repo path is `spec2vec`, not
`registry/spec2vec` — "registry" is the service name, not a URL path segment.
**Pyxis pull of `spec2vec:latest` failed with `MANIFEST_UNKNOWN` (OCI index)** — the image was
pushed via `docker buildx build --push` with default provenance/SBOM attestations, producing
an OCI multi-platform index enroot can't negotiate. Not an Empire AI or network issue — fix is
re-pushing with `--provenance=false --sbom=false`. Full diagnosis: `docs/HANDOFF.md` §4.4.

Billing (SU charging) for both clusters deferred to **2026-10-01**. Any publication using
these resources must include: *"We gratefully acknowledge use of the research computing
resources of the Empire AI Consortium, Inc, with support from Empire State Development of
the State of New York, the Simons Foundation, and the Secunda Family Foundation."*

### GitHub
Repo: `https://github.com/URseismology/wavenet-epicAI`
Branch: `main`
Push: `GIT_SSH_COMMAND="ssh -i ~/.ssh/id_ed25519" git push`

---

## Rules — read before every task

### Never do these
1. Do not suggest or use Instaseis, MPI, `.sac` files, or any old-pipeline approach.
2. Do not use `h5_wavenet_tools.py` to read new CPS HDF5 output. Use `verify_main.py` schema only.
3. Do not run intensive jobs on terravibranium during daytime hours.
4. Do not commit from terravibranium, Bluehive, or repovibranium.
   All commits originate from axon-1 (10.17.6.243) only.
5. Do not delete or move files in `/RAID6/wavenet_output/` or `repovibranium:/volume1/ADAMA-Shared/traindatawavenet/` without PI approval.

### Always do these
1. Check terravibranium load before any compute task: `ssh tolugboj@terravibranium.earth.rochester.edu "uptime"`
2. `rsync wvsim_main.py` to terravibranium before every new run (HANDOFF.md §5.1).
3. Run a 2-model sanity check (`--limit 2 --cores 4`) before any overnight job.
4. Back up completed HDF5 files to repovibranium after each run (HANDOFF.md §5.5).
5. Update `docs/HANDOFF.md` after any significant milestone and commit to GitHub.

### Escalate to PI before proceeding
- Any change to physics logic in `wvsim_main.py` (model headers, CPS call order, LUT resolution, halfspace layer, NPT formula)
- Choosing `sep_km` values outside the verified 50–480 km safe range
- Submitting Bluehive array jobs for the first time
- ML architecture decisions (U-Net depth, loss function, training regime)
- Anything that modifies or deletes primary HDF5 datasets

---

## Current dataset status

| File | sep_km | Models | Location | Backed up |
|---|---|---|---|---|
| `wavenetv2_dataset_10k_full.h5` | 127.0 km | 10,000 ✅ | `/RAID6/wavenet_output/` | repovibranium ✅ |
| `wavenetv2_dataset_10k_sep100km.h5` | 100.0 km | 10,000 ✅ | `/RAID6/wavenet_output/` | repovibranium ✅ |

**Next runs needed:** Other sep_km values across 50–297.5 km range (see HANDOFF.md §5)

---

## Next priorities (in order)

1. Additional terravibranium overnight runs at other `sep_km` values
2. Create `src/simulation_runner/submit_wvsim_bluehive.sh` (HANDOFF.md §6.3)
3. Fix CPS_BIN detection in `wvsim_main.py` for Bluehive (HANDOFF.md §6.4)
4. Verify `pycwt` available on Bluehive (interactive session test)
5. Continue staged ML pipeline build (`src/wavenet_pipeline/03_machine_learning/`) —
   Stage A & B verified locally 2026-09-04; next: terravibranium-gpu tier, then Alpha,
   then Beta. See `docs/ml_pipeline_stages/PROGRESS.md`.
6. Update stale `README.md`

---

## Known non-fatal warning (do not fix)

`RuntimeWarning: divide by zero` at line 369 of the simulator in the velocity
calculation `velocity_kms = np.where(travel_times > 0, sep_km / travel_times, 0.0)`.
This is safely handled by `np.where` and does not affect output. Do not touch this line.
