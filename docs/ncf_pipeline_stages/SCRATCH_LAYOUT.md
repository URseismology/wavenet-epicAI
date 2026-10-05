# wavenet_ncf on BlueHive scratch — what is production, what is retired

**Lives at `/scratch/tolugboj_lab/wavenet_ncf/README.md`. Read it before launching,
deploying, or deleting anything in this project.**

`/scratch/tolugboj_lab` is a SHARED lab root — 101 directories, ~80 belonging to other
people and other projects (`global-tomography`, `planetary_seismo`, `Prj10_*`, several
`*_WS` workspaces). Nothing here describes those. This project should occupy exactly one
entry in that root, and today it occupies twenty-one.

Written 2026-10-05 after a 1,999-station campaign ran to completion against the wrong code
tree. The fixes were real, deployed, and verified — into a directory production never used.
Nothing in the layout made that visible, and both a human and an AI agent missed it
repeatedly over four days.

---

## The two kinds of thing here, and why they get confused

`/scratch/tolugboj_lab/wavenet*` holds **24 sibling directories of two completely different
kinds**, with no naming that separates them:

| Kind | What it holds | How many |
|---|---|---|
| **Code tree** | `production/*.py` — orchestrator, master, fetch_station_metadata | **2** |
| **Data root** | `manifest/`, `results/`, `packaged_h5/`, `scratch_work/`, `logs/`, and a generated `orchestrator.slurm` | **14 with shards** |

`wavenet_ncf_framework` and `wavenet_ncf_production_v2` look like siblings. One is code, one
is data. The only thing separating the live code from the fixed code is the suffix
`_recovery`.

## Use these names. They are the only ones guaranteed current.

    /scratch/tolugboj_lab/wavenet_ncf/code/CURRENT        -> deploy to and run from here
    /scratch/tolugboj_lab/wavenet_ncf/code/RETIRED_sep24  -> superseded, do not deploy here
    /scratch/tolugboj_lab/wavenet_ncf/run/PRODUCTION      -> the live campaign data root
    /scratch/tolugboj_lab/wavenet_ncf/run/PRODUCTION_v1_complete
    /scratch/tolugboj_lab/wavenet_ncf/archive/            -> dead directories

> **This is a transitional state and it has a real cost.** These are symlinks; the actual
> directories are still `wavenet_*` siblings in the shared lab root. So two namespaces
> describe the same thing, and a reader must understand the indirection before they
> understand the layout. That is worse than one namespace, and it is the price of not
> disturbing three jobs holding ~6 days of accumulated download.
>
> The end state is ONE directory: everything physically inside `wavenet_ncf/`, the lab root
> gaining one entry and losing twenty-one. These names are chosen so they do not change when
> that happens — only their targets do.

---

## The defect this exists to prevent

A data root's `orchestrator.slurm` is generated once by `master.py init`, with the code path
**hardcoded at that moment** (`{here}` = wherever `master.py` lived when init ran).

`wavenet_ncf_production_v2/orchestrator.slurm` was generated **2026-09-30 13:57**, when that
was `wavenet_ncf_framework`. Every subsequent fix was deployed to
`wavenet_ncf_framework_recovery`. Nothing re-checks the binding; deploying code to a tree
updates no root. So the campaign silently ran code that had:

- **0 occurrences** of `WINDOW_DAYS` — no daily-window fix
- **0 occurrences** of `discovery_lookup` — no provider pinning at all

Consequences, all measured: 0 of 2,042 job logs carried a `[discovery] pinned` line; the
contradiction guard (`if disco and download_bytes == 0`) could never fire, so **536 stations
returned zero bytes with no error and no warning**; and a claimed 2x depth improvement
attributed to the daily-window fix was a selection effect, because that code never ran.

**The check that failed to catch it.** `md5(code_tree/orchestrator.py) == md5(repo)` was
verified and passed. It proves the tree matches the repo. It does **not** prove production
*runs* that tree. Verifying the wrong edge of the graph is the whole failure.

### Before any launch, verify the binding, not just the code

    grep 'orchestrator.py \$SLURM' <root>/orchestrator.slurm     # which code tree?
    grep WAVENET_DISCOVERY_MANIFEST <root>/orchestrator.slurm    # pinning wired in?

and after launch, confirm it actually took effect **in the data**:

    grep -l 'discovery.*pinned' <root>/logs/orchestrator_<jobid>_*.out | wc -l   # must be > 0

---

## Directory inventory, 2026-10-05

### Code trees
| Path | Status |
|---|---|
| `wavenet_ncf_framework_recovery` | **CURRENT.** Has the daily-window fix and pinning. Deploy here. |
| `wavenet_ncf_framework` | **RETIRED** (Sep 24). Has neither fix. Still executed by jobs `2041206`/`2041269`, submitted before the repoint — do not move until they finish. |

### Data roots — production
| Path | Shards | Status |
|---|---|---|
| `wavenet_ncf_production_v2` | 1,442 | **LIVE.** 917 GB. The campaign. |
| `wavenet_ncf_production` | 1,090 | v1, complete, 768 GB. Reference baseline. |

### Data roots — tests and canaries (11, all superseded)
`wavenet_launchverify` (1) · `wavenet_preflight` (1) · `wavenet_retryfix` (1) ·
`wavenet_ncf_canary` (33) · `wavenet_ncf_canary2` (1) · `wavenet_ncf_canary_clean` (0) ·
`wavenet_ncf_canary_full` (7) · `wavenet_ncf_comptest` (3) · `wavenet_ncf_96test` (18) ·
`wavenet_ncf_quicktest` (6) · `wavenet_ncf_xd_pair_test` (2) · `wavenet_ncf_yeartest` (0)

Shard counts are small; these are throwaway verification roots, not data to preserve.
Archive them at the next consolidation.

### Archived 2026-10-05 → `ncf_archive/`
Four empty marker directories, 1 KB each: `wavenet_ncf_hgntest.MOVED`,
`wavenet_ncf_oldtrim_wiped.MOVED`, `wavenet_ncf_prepatch_incomplete.MOVED`,
`wavenet_ncf_v2test.MOVED`.

### Retired in name but holding real data — PI decision needed
| Path | Size | Note |
|---|---|---|
| `wavenet_ncf_v1_retired` | **71 GB** | Named retired, not empty. Not moved. |
| `wavenet_ncf_ratejitter_wiped` | **17 GB** | Named wiped, not empty. Not moved. |
| `wavenet_ncf_migration` | — | 35 slurm references. Purpose unclear; not moved. |
| `wavenet_ncf_prepatch_backup.MOVED` | — | 1 slurm reference, so not treated as dead. |

Nothing above was deleted. Per project rule, primary data is never moved or deleted without
PI approval.

---

## Pending consolidation (when the campaign drains)

Three jobs are still running and hold absolute paths into both code trees and the production
root, so the physical work waits for quiescence:

1. Adopt a prefix convention that makes the kind obvious: `code_*` vs `run_*`.
2. Archive the 11 superseded test roots.
3. **Remove the hard-wire.** `orchestrator.slurm` should resolve the code tree through
   `ncf/code/CURRENT` rather than baking an absolute path at init time, so a root cannot
   drift away from the code it is meant to run.
4. Have the orchestrator record its own resolved path and md5 into each result JSON and as
   an HDF5 attribute, so "which code produced this shard" is answerable **from the data**.
   This is a backstop, not the fix — the structure should make the mistake impossible first.
