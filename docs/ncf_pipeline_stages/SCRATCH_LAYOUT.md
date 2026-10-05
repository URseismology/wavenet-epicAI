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
    /scratch/tolugboj_lab/wavenet_ncf/archive/            -> superseded roots + dead dirs
    /scratch/tolugboj_lab/wavenet_ncf/analysis/           -> was delivered_network_analysis (20 GB)
    /scratch/tolugboj_lab/wavenet_ncf/debug/              -> was claude_debug

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

### Code trees — CONSOLIDATED 2026-10-05

Both now live inside the project. Neither sits at the lab root any more.

| Path | Status |
|---|---|
| `wavenet_ncf/code/CURRENT` → `code/current_tree` | **The only tree to deploy to or run from.** Has the daily-window fix (`WINDOW_DAYS`) and provider pinning (`discovery_lookup`). Was `wavenet_ncf_framework_recovery`. |
| `wavenet_ncf/archive/retired_roots/wavenet_ncf_framework` | **RETIRED** (Sep 24). Has **neither** fix. This is the tree the 1,999-station campaign actually ran. |

**`CURRENT` is a symlink and that is the point.** Generated roots bake *this* path, never the
physical directory, so swapping the code tree repoints one symlink and every root follows.
`master.py` emits it via `CODE_ROOT` (falls back to `HERE` off-cluster; `WAVENET_CODE_ROOT`
overrides). Before this, `master.py` baked wherever it happened to live at `init` time, which
is the whole cause of the incident above.

Jobs `2041206`/`2041269` were executing the retired tree and were cancelled 2026-10-05 after
~3 days — they were producing data from the known-defective path. Their raw SEED is retained
(239 GB across `MN.PDG` and `MN.BNI`), so a relaunch resumes rather than re-downloads.

### Data roots — production
| Path | Shards | Status |
|---|---|---|
| `wavenet_ncf_production_v2` | 1,442 | **LIVE.** 917 GB. The campaign. |
| `wavenet_ncf_production` | 1,090 | v1, complete, 768 GB. Reference baseline. |

### Data roots — tests and canaries: ARCHIVED 2026-10-05
All twelve moved to `wavenet_ncf/archive/test_roots/`: `wavenet_launchverify`,
`wavenet_preflight`, `wavenet_retryfix`, `wavenet_ncf_canary`, `wavenet_ncf_canary2`,
`wavenet_ncf_canary_clean`, `wavenet_ncf_canary_full`, `wavenet_ncf_comptest`,
`wavenet_ncf_96test`, `wavenet_ncf_quicktest`, `wavenet_ncf_xd_pair_test`,
`wavenet_ncf_yeartest`.

No compatibility symlinks were left for these. They are superseded verification roots that
nothing live references, and an old script that reaches for one should fail loudly and point
here rather than silently resolve through an indirection. Nothing was deleted.

**Lab-root `wavenet*` entries went from 23 to 3**: `wavenet_ncf/`, `wavenet_ncf_production/` (v1, complete), `wavenet_ncf_production_v2/` (live). The last two move inside at the next quiet moment.

### Archived 2026-10-05 → `ncf_archive/`
Four empty marker directories, 1 KB each: `wavenet_ncf_hgntest.MOVED`,
`wavenet_ncf_oldtrim_wiped.MOVED`, `wavenet_ncf_prepatch_incomplete.MOVED`,
`wavenet_ncf_v2test.MOVED`.

### Retired roots — ARCHIVED 2026-10-05 → `wavenet_ncf/archive/retired_roots/`

All three were named as if dead and all three held real data, which is why they sat
untouched until the PI approved the move. Nothing was deleted. Contents recorded here so the
names stop being the only description:

| Directory | Size | What is actually inside |
|---|---|---|
| `wavenet_ncf_v1_retired` | **71 GB** | 276 entries: 197 v1 result JSONs plus **79 packaged `.h5` shards**. Not empty despite "retired". Last written 2026-09-26. |
| `wavenet_ncf_ratejitter_wiped` | **17 GB** | 24 entries: result JSONs plus **6 `.h5` shards** with their `.daystate.json` checkpoints (e.g. `II.NIL`). Not wiped despite the name. Last written 2026-09-27. |
| `wavenet_ncf_migration` | **9.8 GB** | 25 entries, **no shards** — tooling and logs from the old-cluster→BH3 migration: `audit_excluded_stations.py`, `backup_partials.slurm`, `backup_prepatch_partials.py`, `completed_prepatch_stations.csv`, `fetch_meta{,_batch}.slurm`, `logs/`, `repro_EI_IMAY_2023`. Last written 2026-09-28. |

**Lesson, recorded because it nearly caused a deletion:** a directory name is not a statement
about its contents. Two of these three said "retired"/"wiped" while holding 88 GB and 85
shards between them. Check before trusting a name here.

### Archive size

`wavenet_ncf/archive/` is **1.2 TB**, dominated by retained raw SEED inside the archived test
roots rather than by packaged output. Prunable later; retained for now because raw SEED lets a
station be re-packaged without re-downloading.

---

## Pending consolidation (when the campaign drains)

Three jobs are still running and hold absolute paths into both code trees and the production
root, so the physical work waits for quiescence:

1. Move the two remaining production roots inside `wavenet_ncf/run/`, so the lab root holds
   ONE entry for this project. Done for everything else.
2. ~~Remove the hard-wire.~~ **DONE 2026-10-05** — `orchestrator.slurm` should resolve the code tree through
   `ncf/code/CURRENT` rather than baking an absolute path at init time, so a root cannot
   drift away from the code it is meant to run.
3. Prune `wavenet_ncf/archive/` (currently **1.2 TB**, mostly retained raw SEED in archived
   test roots).
4. Have the orchestrator record its own resolved path and md5 into each result JSON and as
   an HDF5 attribute, so "which code produced this shard" is answerable **from the data**.
   This is a backstop, not the fix — the structure should make the mistake impossible first.
