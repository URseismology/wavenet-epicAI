# Handoff — 2026-09-30, written for conversation pruning

This document exists because the working conversation this work happened in is about to
be cleared. Everything a fresh session needs to pick this up correctly — decisions made,
why, what's running, what's still open — is here. Read this before touching any of the
systems below.

---

## 0. READ THIS FIRST — every conflation this session actually made, now resolved

This section exists because this exact session repeatedly confused the things below —
not hypothetically, these are real errors that happened and were caught mid-conversation
(several by the PI, not self-caught). A fresh session must not re-make any of them.

**0.1 — Three unrelated things can all be true of "terravibranium" at once. They are
NOT the same project:**

| | CPS synthetic simulation | SAmericaNoise NCF packaging | (not on terravibranium) |
|---|---|---|---|
| What | Generates synthetic CCFs from random Earth models | Packages a pre-existing REAL ROVER waveform archive | BH3's download campaign |
| Code | `src/wavenet_pipeline/02_simulation/wvsim_main.py` | `/tmp/sam_package_one.py` (not in git repo) | `production/orchestrator.py` |
| Feeds | `03_machine_learning/` U-Net (wavenet-proper) | Nothing yet — raw material only | Nothing yet — raw material only |
| Touched this session? | No | **Yes — §3** | **Yes — §4** |
| Has a scheduler? | No (plain background jobs) | No (plain background jobs) | Yes (SLURM) |

`grep`-ing "terravibranium" in isolation does not tell you which of these three you're
looking at. Always check which script/path is involved.

**0.2 — terravibranium has NO job scheduler.** Corrected mid-session after referring to
a "queue" there and being told directly: *"What do you mean by queue? The machine
doesn't have a slurm scheduler."* Work there is a plain Python driver
(`sam_package_driver2.py`) spawning OS subprocesses via `nohup`/`disown` — there is no
`sbatch`, no `squeue`, no partitions, no QoS. BlueHive/BH3 have all of those; conflating
the two machines' job-management model is a real, repeated error this session.

**0.3 — BlueHive3 (BH3) vs legacy BlueHive (BH): separate clusters, separate machines,
a deliberate PI preference, NOT an upgrade-and-retire.**
- **BH3 is production** for long compute (`ssh bluehive3`): migrated here 2026-09-29,
  measured 2-3x faster on identical work, far better scheduling throughput (260+
  concurrent tasks vs ~46 on old BH). This is where the v2 NCF campaign (§4) runs.
- **Legacy BH is deliberately KEPT, not retired** (`ssh bluehive`): more short-job
  capacity right now (91 standard/21 interactive/**12 debug** nodes vs BH3's
  20/1/**none**) — BH3 has no debug partition at all, which is why BH stays in rotation
  for tests/debugging/short jobs specifically.
- **Both require Duo 2FA on every fresh connection** — use an SSH ControlMaster
  (`ssh -fN <host>`, check with `ssh -O check <host>`, close with `ssh -O exit <host>`)
  rather than reconnecting repeatedly.
- **BH3's login-node banner forbids running AI coding assistants on it directly** — get
  a compute allocation first (`smux -c 4 --mem=8G -t 08:00:00`, or `salloc`) and work
  from the allocated node for anything interactive. Submitting jobs (`sbatch`/`squeue`)
  FROM the login node is fine and is how this session did all its work — Claude Code
  itself ran on axon-1 throughout, issuing individual `ssh bluehive3 "..."` commands; it
  was never run as an interactive session ON BH3's login node, which is the actual thing
  the banner prohibits.
- **BH3 still self-flags "in development, not production"** in its own login banner —
  this is why legacy BH is kept as a deadline-critical fallback, not phased out.
- (Unrelated aside, in case it's ever confusing from the other side: on axon-2 — a
  **completely different, unrelated project**, global tomography — the name `bluehive`
  is deliberately *aliased* to bluehive3 so that project can't reach legacy BH by
  muscle memory. This has no bearing on wavenet-epicAI/axon-1's use of both names as
  distinct hosts.)

**0.4 — v1 vs v2 are two DIFFERENT DIRECTORIES on the SAME machine (BH3), not two
machines, not two clusters.** `wavenet_ncf_production` (v1, superseded, being wound
down) and `wavenet_ncf_production_v2` (v2, current, live) both exist simultaneously
under `/scratch/tolugboj_lab/` on BH3 right now. A real consequence of this that
confused a status report mid-session: `master.py progress --root <either one>` lists
SLURM jobs via `squeue -u tolugboj`, which is **user-wide, not root-scoped** — querying
v1's progress can show v2's job IDs in its queue listing. This is a display quirk in
the tool, not evidence of cross-contamination between the two campaigns' actual data.
Always check which root's `results/`, `packaged_h5/`, and `state/` you're reading, by
full path, not by which job IDs happen to be in a squeue listing.

**0.5 — "recoverable by config change" (metadata inference) vs "recovered" (measured
fact) are not the same claim, and treating them as the same produced a real false
signal this session.** A synthetic `MassDownloader` recovery test was run ON
TERRAVIBRANIUM — wrong machine, because terravibranium does no downloading — and
because that machine was saturated with packaging workers, `MassDownloader` could not
even construct itself (`RuntimeError: can't start new thread`). A silently-swallowed
exception turned this into a false "0/20 recovered" result that nearly got reported as
a real finding. The PI caught the root confusion directly: *"are you confused again
between terravibranium and bh3? one has no download."* The test was re-run correctly ON
BH3 (job-submitted via `sbatch`, not run on the login node directly) and returned real,
trustworthy signal (25-33% measured recovery, §4.1). **Rule going forward: any test that
exercises FDSN/MassDownloader must run on BH3, never terravibranium — and a config
value being theoretically more permissive is not evidence it works until measured.**

**0.6 — A mirror script's own progress counter is NOT the campaign's completion count.**
Directly asked and clarified mid-session: *"your mirror says 236 files. does that mean
only 236 stations are complete...?"* — no. Each mirror script snapshots its file list
ONCE at start and does not update it; the TRUE complete-station count must be queried
independently (§4.6, §5.3). These two numbers will diverge more the longer a mirror
script has been running against a still-completing campaign.

**0.7 — `archive_move.py` (existing tool) and the new `mirror_to_{repo,atos,terra}.sh`
scripts (written this session) solve DIFFERENT problems and must not be conflated:**
- `archive_move.py` = **move and DELETE** stale/superseded data, to free BH3 scratch
  quota. One-shot. Built for data nobody needs in place anymore.
- `mirror_to_*.sh` = **copy, keep source, never delete**, for live/current/valuable
  campaign output that must survive in three places. Recurring, snapshot-based (§0.6,
  §5.3). Built because `archive_move.py`'s move-and-delete shape is wrong for
  continuously-growing, still-in-use data.
Do not run `archive_move.py` against `packaged_h5/` — it would delete the source after
copying, which is the opposite of what the backup plan requires.

**0.8 — "atos" is two unrelated things that happen to share a name.** CLAUDE.md
documents an AWS IAM identity called `atos-orchestrator` used by **mothership** for
EarthScope/AWS work (a separate workstream entirely, §"Real waveform acquisition (NCF)
pipeline" in CLAUDE.md, ROVER/AWS cost comparison — not touched this session). The `atos`
referenced in §5 of THIS document is an SSH alias (`10.17.7.230`, hostname
`dro-mittal`) for a physical Synology NAS used as one of the three backup mirror
destinations. Same word, unrelated systems — do not assume a reference to "atos"
elsewhere in the project docs means this NAS, or vice versa.

**0.9 — This whole document is ONE of TWO independent project workstreams** (see §1.5
below) — don't let anything here imply something about "wavenet proper" (the
FTAN/U-Net ML pipeline), which this session never touched.

---

## 1. Where things stand, in one paragraph

Two independent problems on two independent machines got fixed today: an unbounded memory
leak in terravibranium's SAmericaNoise packaging, and a config/memory failure mode in
BlueHive3's global NCF download campaign that had silently lost data for ~954 of 2,000
stations. Both fixes are deployed and running. A 208-station replacement search for
stations with no seismometer found 108 real, data-confirmed substitutes. A v2 campaign
root on BH3 is live, re-running everything the old config/memory bugs broke, with real
measured recovery (not just inferred). Packaged NCF output is now being mirrored to all
three required destinations (repovibranium, atos, terravibranium) for the first time.
A live inspector self-chaining bug on BH3 was found and fixed today (see §5.5) — it had
gone silent after one link, which matters because it's the process that frees scratch
space, and scratch is at 80% of its hard limit.

**Nothing has been committed to git yet.** See §8 for exactly what's staged and why it
wasn't committed mid-session.

---

## 1.5. Project-level framing: this is ONE of TWO independent workstreams

**Everything in this document is the NCF data-acquisition workstream only** (BlueHive3 +
terravibranium, described below). It has no bearing on and shares no code with the other
workstream:

- **"wavenet proper"** — the classical-baseline ML pipeline (FTAN dispersion analysis,
  U-Net training, `src/wavenet_pipeline/03_machine_learning/`), worked on by the
  `wavenet-senior`/`wavenet-junior` team. Not touched this session.
- **NCF data acquisition** (this document) — downloading and packaging real global
  ambient-noise waveforms, upstream of and separate from the ML pipeline above (it's
  numbered `00_` in the repo for exactly this reason — it feeds nothing built yet, it's
  raw material for future work).

Both are real, both have "important issues deployed, resolved, and ongoing" (PI's
framing) at any given time — don't assume a status update on one says anything about the
other, and don't merge their state when reporting.

## 2. Two separate pipelines — do not confuse them (this happened repeatedly today)

| | Terravibranium (SAmericaNoise) | BlueHive3 (global NCF campaign) |
|---|---|---|
| What it does | **Packaging only** — an already-downloaded ROVER archive | **Download + packaging** — 2,000-station global network |
| Code | `/tmp/sam_package_one.py`, `/tmp/sam_package_driver2.py` on terravibranium (not in the git repo) | `src/wavenet_pipeline/00_waveform_acquisition/production/` (in the repo) |
| Campaign state | `/RAID6/lab_archive/` on terravibranium | `/scratch/tolugboj_lab/wavenet_ncf_production_v2/` on BH3 |

They share no code. The channel-audit and replacement-search *analysis* in this handoff
is entirely about **BH3's** campaign, even though it was *run* on terravibranium (a
free shell, doing FDSN metadata lookups only — no relation to terravibranium's own
packaging work).

---

## 3. Terravibranium — SAmericaNoise packaging

### 3.1 The bug: unbounded evalresp cache

`_RESPONSE_CACHE` (an evalresp memoization cache added 2026-09-28 for a real ~25x
speedup) was keyed partly on `nfft`, derived per-file from each day's sample count. Real
archive days aren't identical length (partial days, gaps), so near-duplicate lengths
(`3456000` vs `3456024` vs `3452112`, <0.15% apart) each minted a separate ~40-80 MB
cache entry that was **never evicted**. Measured: 0.81 GB of cache after only 45 of
G.HDC's 9,486 files — 80% of process RSS, still climbing. With the driver's
smallest-station-first ordering, all 40 workers ended up on giants simultaneously by the
end of the run: 234/251 GB used, swap exhausted, **76 stations SIGKILLed** by the kernel
OOM killer.

**A reporting error worth remembering**: after fixing an earlier timeout bug on this same
pipeline, "zero TIMEOUT lines" was reported as "all healthy" — but the same 76 stations
were dying a different way (`rc=-9`, no "out of memory" text in the log). Checking that a
fixed failure mode has stopped is not the same as checking the work is succeeding.

### 3.2 The fix: byte-capped LRU cache

`WAVENET_RESP_CACHE_MB` (default 512MB), evicting least-recently-used. Safe by
construction — pure memoization, a miss costs a recompute and nothing else. **Verified
bit-exact**: forced to a 64MB cap (aggressive eviction, 4 entries vs 24 unbounded) against
the pre-patch worker over 30 files of G.HDC — 12/12 datasets identical, max abs diff 0.
Raising the cap 512MB -> 3072MB changed nothing (58.3% hit rate both ways on an HH-only
station) — the cap is not the limiting factor; the misses are intrinsic distinct
`(channel, epoch, nfft)` combinations.

### 3.3 Second, independent fix: lowest-rate-band selection

**PI rule**: "always choose the channels with lowest sample rate... If no LH, then BH, if
no BH then HH" — this was already encoded on the *download* side
(`CHANNEL_PRIORITIES = "LH?,BH?"` in BH3's orchestrator.py) but never made it to the
*packaging* side, which just processed every trace in every file.

Cost of the gap, measured like-for-like on one file of `IU.SAML`:

| Band | s/trace | vs LH |
|---|---:|---:|
| HH | 20.25 | 43x |
| BH | 7.16 | 15x |
| LH | 0.48 | 1x |

**A naive "lowest sample rate" rule is unsafe** — 794 sub-1Hz channels exist in the
pending stations' metadata, and most are not seismometers: `VM0-VM6` (mass position, 0.1
Hz, 7 "components" — would outrank LH on a naive rule), `LOG`/`ACE` (0.0 Hz, datalogger
text/clock), `Q*` (0.05 Hz, electric potential). The actual, now-deployed selector in
`select_lowest_rate_band()` (patched into `/tmp/sam_package_one.py` on terravibranium)
requires:
1. 2nd SEED char == `H` (high-gain seismometer) — excludes `VM`/`LOG`/`ACE`/`Q*`,
   accelerometers (`?N?`), and low-gain seismometers (`?L?`)
2. sample rate >= 1 Hz — Nyquist >= 0.5 Hz, the floor for the pipeline's 0.4 Hz lowpass.
   `VH?` is a genuine seismometer at 0.1 Hz and is excluded for exactly this reason.

Verified with 6 synthetic guard cases (all pass) plus bit-exact real-data checks: `G.HDC`
8.2x speedup, `IU.SAML` 43.8x, retained channels bit-identical to the full run both times.

**Deploy decision (explicit PI call)**: deployed WITHOUT resetting in-flight stations'
`done_files.json` checkpoints, because BH and LH both decimate to the same 1 Hz product —
carrying both is redundancy, not a correctness issue. In-flight stations will end up part
all-band/part single-band; **a purge of the redundant extra band from completed HDF5s is
a deferred task for campaign end** (not yet scheduled or scripted).

### 3.4 Measured throughput after both fixes

779 files/hr -> ~3,740 files/hr (~4.8x) measured directly via checkpoint-file counts
after deployment. 30-minute memory watch post-deploy: oscillating 64-77 GB of 251 GB,
**zero new OOM kills** (flat at 77 throughout).

### 3.5 Option B (canonical nfft) — tested, NOT deployed, recommend against

Tested whether pinning `nfft` to one canonical per-channel value (full-day length)
instead of per-file would collapse cache fragmentation further (PI's idea: nfft values
differing by <0.15% are "the same grid" to any meaningful frequency resolution).
Implemented, verified bit-safe (max abs diff ~1e-13, floating-point noise, correlation
1.0), but measured **no benefit** on the one real test that should have shown it
(`WI.MAGL`, HH-only, 8 files): baseline and Option B both landed on only 1 distinct nfft
already (band selection had already solved the fragmentation for this file window),
and Option B was actually 6% *slower* (extra FFT padding, no cache-miss reduction to
offset it). **Recommendation: do not deploy.** The backup files
(`/tmp/sam_package_one_optB.py` and `.bak_preoptB` on terravibranium) can be deleted or
kept as reference; nothing references them in production.

### 3.6 Current terravibranium state (as of this handoff)

- 40 workers running, band selection + bounded cache both live in
  `/tmp/sam_package_one.py`
- Swap was fully drained by the user (`swapoff -a && swapon -a`) — now 0/4095 MB used,
  full headroom restored
- Backup of the pre-patch worker: `/tmp/sam_package_one.py.bak_precache`,
  `.bak_preband`, `.bak_preband2` (chronological patch history, in case of regression)
- **To check current status**: `ssh tolugboj@terravibranium.earth.rochester.edu "ps -eo args | grep -c '[s]am_package_one'; grep -c 'rc=-9' /RAID6/lab_archive/sam_packaging_run.log; grep -c ' ok (' /RAID6/lab_archive/sam_packaging_run.log"`

---

## 4. BlueHive3 — global NCF campaign

### 4.1 The channel audit: what happened to the 954 no-data stations

Of the locked 2,000-station manifest, 954 stations (47.7%) returned zero bytes from the
original (v1) campaign. Audited all 954 against live FDSN metadata:

| Count | Category | Cause |
|---:|---|---|
| 433 | `has_BH_LH_should_have_worked` | BH/LH registered, config excluded them anyway |
| 312 | `missed_other_seismometer_band` | Real seismometer present, just not `BH?,LH?` |
| 208 | `no_seismometer_on_station` | No seismometer at the site at all |
| 1 | `manifest_defect` | `nan.SABA` — empty network code |
| 0 | `no_fdsn_match_iris` | — |
| 0 | `exists_no_channels` | — |

Every station matched FDSN and had channels registered — nothing lost to bad identifiers.

**Root causes identified and fixed in code** (both in `production/orchestrator.py`):
1. `location_priorities` was hardcoded to `["", "00", "10"]`. In a 25-station sample, 10
   (40%) had **all** their location codes outside that set (`01`, `02`, `30`, `31`, `32`
   observed) — usually multiple sensors at different depths at one site, not junk (PI's
   read, confirmed). **Fixed**: now `"*"` by default, env-configurable via
   `WAVENET_LOCATION_PRIORITIES`.
2. `CHANNELS` defaulted to `"BH?,LH?"` only. **Fixed**: now
   `"LH?,MH?,BH?,SH?,HH?,EH?"` — every high-gain seismometer band >=1 Hz, cheapest
   first, same floor-at-LH reasoning as §3.3.
3. `--mem-per-cpu=2G` in the orchestrator SLURM template (386 tasks OOM-killed before
   writing any result — `MaxRSS` pinned at the 2048M ceiling on killed tasks). **Fixed**:
   `--mem-per-cpu=7G` (the nodes' real per-core share: urseismo 24 cores/182GB = 7.6GB/core;
   standard 48-56 cores/375-505GB = 7.8-9.0GB/core). Deliberately NOT `--mem=0`
   ("all memory on the node") — that would cut concurrency from 260+ tasks to 5 per
   partition.

**Both config changes were VERIFIED to actually retrieve data**, not just inferred from
metadata (a real methodological trap: metadata registration != data availability, which
is exactly what created category 1 in the first place). Real `MassDownloader` test, run
**on BH3** (must run there — on a machine saturated with other workers, `MassDownloader`
can't even construct itself: `RuntimeError: can't start new thread`, which silently
produces a false "nothing works" result if not checked):

```
has_BH_LH_should_have_worked   : tested 12, current got 8, FIXED recovered 3 more (25%)
missed_other_seismometer_band  : tested 12, current got 0, FIXED recovered 4 (33%)
errors: 0, no regressions (every "current" success also succeeded under "fixed")
```

### 4.2 Station replacement search (the 208 no-seismometer stations)

**Full accounting — the 108 replacements are NOT the whole recovery story:**

| Count | What | Fix |
|---:|---|---|
| 837 | zero-byte in v1 | location/channel config (§4.1) |
| 386 | OOM-killed, no result at all | `--mem-per-cpu=7G` |
| 108 | new replacement stations | never attempted before |
| 8 | "complete" but data was lost on disk | re-run (see §4.4) |
| **1,339** | **total being re-run in v2** | |

**The 386 OOM stations were never channel-audited** — the audit only ever looked at
stations that had completed (zero result JSON was written for OOM deaths before any
channel info could be captured). This is an open blind spot: giving them memory fixes the
crash, not necessarily the channel/location problem too.

**Replacement selection rule** (PI, 2026-09-30, iterated twice):
1. Candidate must be a real, high-gain seismometer (2nd SEED char `H`), sampling >=1 Hz
   — same guards as §3.3, verified against the same synthetic test cases.
2. **Distance is primary** ("always choose the next closest triad") — nearest station
   wins; lowest sampling rate only decides *which band* to use *at* that station (MH
   over SH if both present).
3. Data availability is a **hard gate** — verified via actual `get_waveforms`, not
   metadata. First-pass probe had a **39% false-negative rate** (5 fixed windows inside
   only the original station's operating years, missing contemporaneous data at a
   different era) — corrected with an epoch-aware re-probe that intersects the
   candidate's real channel epochs with the original's window. Recovered 6 more stations
   this way.
4. Acceptance threshold: **0.5 degrees** (~55.6 km at the equator) between original and
   replacement.
5. Low-gain seismometers (`?L?`) excluded even if otherwise qualifying — measure
   velocity but built for strong motion, poor for ambient noise.
6. `XB.ELYH0` (rank 693) **deleted, not replaced** — it is the InSight lander on Mars
   (4.502N 135.623E). Its "replacement" under every other rule would be `XB.ELYSE` (the
   actual InSight seismometer) — correct by the rules, useless for terrestrial work.

**Final count: 108 accepted, 1 deleted (Mars), 99 genuinely outstanding** (65 beyond
0.5 deg, 17 nothing within 5 deg, 8 empty, 6 data-but-different-era science calls, 1 was
low-gain only — recheck the CSVs in §6 for the current exact split, this was measured
mid-analysis and may have shifted slightly across the two merge passes).

### 4.3 `fps_stations_v2.csv` — the revised manifest

`src/wavenet_pipeline/00_waveform_acquisition/metadata3/fps_stations_v2.csv` — 1,999
rows (was 2,000). **The original `fps_stations.csv` is untouched** — this is a new file,
not an edit, because station selection is a PI-locked decision per CLAUDE.md.

- 108 rows: network/station/lat/lon swapped for the accepted replacement.
  `days` cleared on swapped rows (unmeasured for the new station — not fabricated).
- 1 row deleted (`XB.ELYH0`).
- 99 rows: unchanged (kept under original code so the gap stays visible, not silently
  dropped).
- `rank` preserved throughout — farthest-point-sampling order is unchanged.
- **Verified**: 0 duplicate station codes introduced.
- Every change itemized in
  `metadata3/channel_audit_2026_09_30/manifest_v2_changes.csv`.

### 4.4 The v2 campaign — live, running, and what had to be fixed to make it correct

**Init**: `master.py init --manifest fps_stations_v2.csv --carry-forward-from
<v1 root>` — copies result JSONs for any `package_ok=True` station so it's skipped, not
re-downloaded. **Verified safe before running**: all 947 zero-byte v1 stations are
`package_ok=False`; all 668 successes are `package_ok=True` — carry-forward only ever
skips genuine successes.

**Bug found and fixed during init**: `--carry-forward-from` copies *result JSONs* but
not the underlying `.h5` data — master.py's own comment assumes this is safe because
`packaged_h5/` is "keyed by network.station, not idx", true only when *reusing one
root*. v2 is a new root, so all 668 carried-forward stations were marked complete in a
directory holding **zero** of their actual data (verified: v1 `packaged_h5/` = 1,089
files, v2 = 0 before the fix). **Fixed**: hard-linked 1,980 files (660 stations) from
v1's `packaged_h5/` into v2's — both roots share one GPFS filesystem, so this cost zero
extra disk. **Caveat for whoever touches this next**: hard-linked files share one inode;
an in-place rewrite in either root would affect both. These are completed, read-only
products, so this shouldn't arise, but don't point a reprocessing run at them.

**A second, worse thing this surfaced**: 8 of the 668 carried-forward stations
(`3H.G08`, `A7.LUBAN`, `NR.NE75`, `XJ.BNG`, `XL.HD72`, `XO.WD60`, `YT.MRTP`, `YY.SHER`)
report `package_ok=True` and real `download_bytes` (184MB to **12.9 GB** for `NR.NE75`,
~22.6 GB total) but have **no `.h5` anywhere in either root** — data genuinely lost,
most likely during one of the earlier scratch `*.MOVED` migrations. Carried forward, v2
would have skipped them permanently and silently. **Fixed**: removed their
carried-forward result JSONs so v2 re-runs them (they sit at manifest idx 550-1596, all
in the `standard` partition arrays, confirmed not yet reached when this was done).

**Submitted**: `--array-limit 96` across 5 partition chunks (standard x2, preempt,
interactive, urseismo) — went from **8 running tasks to 76-84 concurrent**. The low task
count before this was NOT a capacity problem (urseismo had 3/5 nodes idle, standard 6/20,
preempt 8/65, no restrictive QoS limit) — it was simply that v1's arrays had finished and
the 511 OOM-killed tasks were never resubmitted.

**v1 cleanup**: v1's leftover inspector (job `2011883`, been running since before the
migration) was found still purging the SAME shared scratch tree as v2's inspector — a
real race risk, two inspectors purging concurrently. **Cancelled** (PI approved).

### 4.5 Inspector self-chain bug — found and fixed today, after the handoff was started

v2's own inspector (job `2019792`) ran one pass (19 minutes, processed 660 stations,
purged normally) then **did not resubmit its successor**, silently. `progress` reported
"inspector chain: NOT RUNNING -- chain broken or intentionally off" and scratch was
climbing (80.0% of hard limit) with nothing purging.

**Root cause**: the self-chaining `sbatch` call in `INSPECTOR_SLURM` (in `master.py`)
deliberately runs as the very first statement of the job (crash-safety: an OOM/timeout
kill destroys any retry logic that runs later in the script, so the successor must be
queued before real work starts). But on a bare compute-node shell, `sbatch` is not on
`PATH` until the slurm module loads — which normally happens via interactive-shell
profile sourcing, not in a batch job's minimal environment. The call failed with
`sbatch: command not found`, written only to `inspector_chain.log` (not the job's own
stderr, so the job still reported clean `COMPLETED` with no visible error anywhere
obvious).

**Fixed** in `master.py`'s `INSPECTOR_SLURM` template: added
`module load slurm/24.05.0.b1 >/dev/null 2>&1` immediately before the chain-stamp check
(a near-instant env-var export, not a real crash-risk window, so it doesn't meaningfully
weaken the original crash-safety design), plus a `|| echo "... FAILED to queue
successor" >&2` fallback so a future occurrence surfaces in the job's own `.err` file
instead of silently vanishing into a side log.

**Deployed**: patched directly into the already-generated
`/scratch/tolugboj_lab/wavenet_ncf_production_v2/inspector.slurm` (regenerating via a
fresh `init` was not warranted for a one-line fix) and manually resubmitted. **Verified**:
new job (`2022779`) ran and successfully queued a successor (`2022780`, confirmed
`PENDING` in queue) — the exact thing that failed silently before.

**This fix is in the master.py template now**, so it will be correct automatically for
any future `init`. The manual patch to v2's already-generated file is a one-time
workaround for the currently-running campaign.

### 4.6 Current BH3 state (as of this handoff)

- v2 campaign: ~96% of 1,999 stations reported, **1,014 stations verified-complete with
  data on disk** (this is the authoritative "how many are really done" number — do not
  use the mirror scripts' file counts for this, see §5)
- Scratch quota: ~80% of hard limit (104,448 GB), climbing — inspector chain restarted,
  should relieve pressure once it catches up
- Group `/home` quota: 98.4% of 25GB hard limit — **already cannot write**, unrelated to
  this campaign, a standing lab-wide issue
- ETA at last check: ~1.5 days for v2 to finish its pass (this will change; always
  re-check with the command below rather than trusting this number)
- **To check current status**:
  ```
  ssh bluehive3 "source /scratch/tolugboj_lab/softwares/anaconda/anaconda3/2021.05/etc/profile.d/conda.sh && conda activate instaseis && python3 /scratch/tolugboj_lab/wavenet_ncf_framework/production/master.py progress --root /scratch/tolugboj_lab/wavenet_ncf_production_v2"
  ```
  (Note: this command's job-queue listing is `squeue -u tolugboj`-wide, not scoped to
  the root you pass — don't be alarmed if it shows job IDs you don't recognize as
  belonging to this root; check the job name/partition, not just presence.)

---

## 5. Backup / mirroring of packaged NCF data — IN PROGRESS, not yet complete

**PI's explicit guidance (2026-09-30)**:
1. All shards mirrored on repo (repovibranium), atos, and terra (terravibranium) — no
   distinction between which campaign (v1/v2/v3) produced them.
2. A full merged master constructed on terra **when the campaign completes**, backed up
   to repo and atos too. **Not started — correctly deferred, this is an end-of-campaign
   task.**
3. Documentation on provenance stating clearly: this is **not** the NCF product, it is
   preprocessed waveform data (instrument response removed, decimated to 1 Hz) ready for
   NCF computation.

### 5.1 What's been done

- **Provenance README** written and deployed to all three destinations, stating point 3
  explicitly. Source: `/private/tmp/.../scratchpad/PROVENANCE_README.md` (session
  scratchpad — **not yet copied into the git repo**, should be, see §8).
- **repovibranium**: destination `/volume1/ADAMA-Shared/wavenet_archive/packaged_h5_ncf/`.
  31TB free, no capacity concern. Direct rsync confirmed working from BH3 itself (not
  just axon-1) — this had never been tested from BH3 specifically before today.
- **atos**: destination `/volume1/NetBackup/wavenet_archive/packaged_h5_ncf/`. 42TB free
  on `/volume1` (do NOT use the USB-attached volumes there — all 97-100% full).
  **BH3 had no authorized key on atos before today** — added via axon-1's existing trust
  (axon-1 -> atos already worked; the pattern from the repovibranium fail2ban incident
  was followed: authorize via an already-trusted host, never attempt a fresh direct
  connection first).
- **terravibranium**: destination `/RAID6/lab_archive/wavenet_ncf_packaged_h5/`. 79TB
  free. **BH3 -> terravibranium direct auth actually already worked** — an earlier audit
  claimed "not authenticated in either direction" and this was simply stale information,
  same pattern as the NAS reachability claim in `archive_move.py`'s own docstring (also
  stale — BH3 reaching repovibranium directly was proven false-negative today too).

### 5.2 The mirror scripts

Purpose-built (not `archive_move.py` — see why below), run **from BH3 itself**:
- `mirror_to_repo.sh`, `mirror_to_atos.sh`, `mirror_to_terra.sh` — **now committed** at
  `src/wavenet_pipeline/00_waveform_acquisition/archive_tools/`, alongside
  `archive_move.py` and a directory `README.md` explaining the distinction between the
  two tool families. The copies actively running on BH3
  (`/scratch/tolugboj_lab/mirror_to_*.sh`) are from before this commit — functionally
  identical, but re-deploy from the repo on next use so they stay in sync.
- **Why not `archive_move.py`**: that tool is a one-shot **move** workflow (copy ->
  verify -> record -> delete), built for archiving stale/superseded data off scratch.
  This is a **recurring, non-destructive mirror** of a live, partially-complete,
  continuously-growing directory — a different operation. It borrows the same
  discipline (verify before trusting, keep a durable record) but not the literal code.
- **Safety design**: only mirrors stations whose result JSON already says
  `package_ok=True` — the live campaign writes to `packaged_h5/` continuously, and a
  naive whole-directory rsync could catch a station's `.h5` mid-write and mirror a
  corrupt file.
- **Verification**: rsync's own checksum (trustworthy for content) plus an independent
  byte-size spot-check on 8 random files per run (catches "silently skipped", which
  rsync's own exit code would not).
- **Recording**: append-only JSON log per destination
  (`state/{repo,atos,terra}_mirror_log.jsonl` under the v2 campaign root).

### 5.3 CRITICAL CAVEAT — these are ONE-TIME SNAPSHOTS, not continuous sync

**Each script builds its file list ONCE when it starts, then stops.** It does NOT pick
up stations that complete afterward, and will NOT run again on its own. Concretely: the
repo-leg script's file list was built when v2 had ~700 stations complete; v2 now has
1,014. The extra ~300+ stations are **not yet queued for any mirror leg**.

**This must be re-run periodically** (a cron-like approach, or simply re-invoked by hand
every so often) to actually keep all three destinations current. Re-running is cheap —
rsync will skip already-transferred files near-instantly and only fetch the delta.
**This is not yet automated. It needs either a cron entry on BH3 or a standing reminder
to re-run manually.**

**Do not use a mirror script's own progress counter (files copied so far) as a proxy for
"how many stations are complete in the campaign."** Those are different numbers. Use
`master.py progress` (see §4.6) for the true count.

### 5.4 Status at handoff time (re-check before trusting — these move continuously)

| Destination | Progress at last check | Notes |
|---|---:|---|
| repovibranium | ~74 GB / 245 files | |
| atos | ~37 GB / 167 files | |
| terravibranium | ~12 GB / 58 files | started last, furthest behind |

**To check current progress** (repovibranium example, same pattern for the others):
```
ssh -i ~/.ssh/id_rsa_nas administrator@repovibranium.earth.rochester.edu \
  "du -sh /volume1/ADAMA-Shared/wavenet_archive/packaged_h5_ncf/packaged_h5; \
   find /volume1/ADAMA-Shared/wavenet_archive/packaged_h5_ncf/packaged_h5 -name '*.h5' | wc -l"
```

**To re-run a mirror leg** (e.g. to pick up newly completed stations):
```
ssh bluehive3 "bash /scratch/tolugboj_lab/mirror_to_repo.sh"
```
(same pattern for `mirror_to_atos.sh`, `mirror_to_terra.sh` — all three currently live at
`/scratch/tolugboj_lab/` on BH3.)

### 5.5 Still open for the backup plan

- Automate re-running the three mirror scripts (cron, or a wrapper that loops until the
  campaign's STOP condition). This is the main remaining gap — see §0.6 and §5.3.
- The merged-master-on-terra step (PI point 2) is intentionally not started.

---

## 6. File locations reference

### In the git repo (wavenet-epicAI, axon-1)

| Path | What |
|---|---|
| `src/wavenet_pipeline/00_waveform_acquisition/production/orchestrator.py` | Channel/location config fixes (§4.1) |
| `src/wavenet_pipeline/00_waveform_acquisition/production/master.py` | Memory fix + inspector self-chain fix (§4.1, §4.5) |
| `src/wavenet_pipeline/00_waveform_acquisition/metadata3/fps_stations_v2.csv` | Revised 1,999-station manifest |
| `src/wavenet_pipeline/00_waveform_acquisition/metadata3/channel_audit_2026_09_30/` | Full audit: all CSVs, all analysis scripts, its own README |
| `src/wavenet_pipeline/00_waveform_acquisition/archive_tools/mirror_to_{repo,atos,terra}.sh` | Backup mirror scripts (§5), with a directory README distinguishing them from `archive_move.py` |
| `src/wavenet_pipeline/00_waveform_acquisition/archive_tools/PACKAGED_H5_PROVENANCE.md` | Provenance doc deployed to all three mirror destinations |
| `docs/ncf_pipeline_stages/2026-09-30_packaging_response_cache_oom.md` | Terravibranium cache-leak writeup (§3.1-3.2) |
| `docs/ncf_pipeline_stages/2026-09-29_bluehive3_migration_and_hang_misdiagnosis.md` | Earlier BH3 migration record (pre-existing this session) |
| `docs/ncf_pipeline_stages/PROGRESS.md` | Live operational snapshot, updated throughout |
| `CLAUDE.md` | BH3/legacy-BH infrastructure entries updated |

### On terravibranium (not in git — operational scripts)

| Path | What |
|---|---|
| `/tmp/sam_package_one.py` | Live packaging worker (cache + band selection fixes) |
| `/tmp/sam_package_one.py.bak_precache`, `.bak_preband`, `.bak_preband2` | Patch history backups |
| `/tmp/sam_package_driver2.py` | Driver (unchanged this session) |
| `/RAID6/lab_archive/sam_packaging_run.log` | Campaign log |
| `/RAID6/lab_archive/packaging_results/` | Per-station results + per-file checkpoints |
| `/RAID6/lab_archive/wavenet_ncf_packaged_h5/` | NEW — terra mirror destination (§5) |

### On BlueHive3 (not in git — deployed campaign)

| Path | What |
|---|---|
| `/scratch/tolugboj_lab/wavenet_ncf_framework/production/` | Deployed code (matches repo after this handoff's changes are pushed) |
| `/scratch/tolugboj_lab/wavenet_ncf_production/` | v1 root (old campaign, 668 successes, being superseded) |
| `/scratch/tolugboj_lab/wavenet_ncf_production_v2/` | **Current live campaign root** |
| `/scratch/tolugboj_lab/mirror_to_{repo,atos,terra}.sh` | Mirror scripts (§5) — now also committed at `archive_tools/` |
| `/scratch/tolugboj_lab/channel_audit_results.csv`, etc. | Staged copies of the audit CSVs, used for the live recovery test |

### On repovibranium / atos (mirror destinations)

| Path | What |
|---|---|
| `repovibranium:/volume1/ADAMA-Shared/wavenet_archive/packaged_h5_ncf/` | NCF mirror destination + README |
| `repovibranium:/volume1/ADAMA-Shared/wavenet_archive/wavenet_ncf_prepatch_backup/` | Pre-existing, unrelated to this session — a 213.9 GB pre-patch snapshot from 2026-09-26 |
| `atos:/volume1/NetBackup/wavenet_archive/packaged_h5_ncf/` | NCF mirror destination + README |
| `atos:/volume1/NetBackup/wavenet_archive/wavenet_ncf_v2test/` | Pre-existing, unrelated — an earlier `archive_move.py` round-trip test artifact |

---

## 7. New durable knowledge (memory files, `~/.claude/projects/.../memory/`)

- `feedback_no_compound_bash_commands.md` — one Bash command per call; chaining with
  `&&` defeats the permission allow-list (raised repeatedly this session, now fixed with
  27 new allow-list entries plus this standing note)
- `feedback_measure_against_ground_truth.md` — updated with the "a fixed failure mode can
  be replaced by a new one, silently" lesson from §3.1
- (Pre-existing, still relevant): `feedback_fail2ban_sensitive_hosts.md`,
  `feedback_no_timeouts_diagnose_instead.md`, `reference_hpc_cluster_split.md`

---

## 8. Uncommitted changes — exact state, and why

```
 M CLAUDE.md
 M docs/ncf_pipeline_stages/PROGRESS.md
M  src/wavenet_pipeline/00_waveform_acquisition/production/fetch_station_metadata.py
 M src/wavenet_pipeline/00_waveform_acquisition/production/master.py
 M src/wavenet_pipeline/00_waveform_acquisition/production/orchestrator.py
?? docs/ncf_pipeline_stages/2026-09-29_bluehive3_migration_and_hang_misdiagnosis.md
?? docs/ncf_pipeline_stages/2026-09-30_packaging_response_cache_oom.md
?? docs/ncf_pipeline_stages/2026-09-30_HANDOFF_pre_pruning.md   (this file)
?? src/wavenet_pipeline/00_waveform_acquisition/metadata3/channel_audit_2026_09_30/
?? src/wavenet_pipeline/00_waveform_acquisition/metadata3/fps_stations_v2.csv
```

`fetch_station_metadata.py`'s staged-modified state predates this session (was already
staged at conversation start) — not something done here, left as-is.

**Everything above was validated and has now been committed** (PI instruction,
2026-09-30): the location/channel/memory config fixes have a real recovery test showing
they work with no regressions (§4.1); the terravibranium cache and band-selection fixes
are bit-exact verified (§3.2-3.3); the inspector self-chain fix is confirmed working
end-to-end (§4.5). The mirror scripts and provenance README were also moved from the
session scratchpad into `archive_tools/` and committed in the same pass — see §6 for
their final repo location.

---

## 9. Immediate next actions for whoever picks this up

1. **Re-run the three mirror scripts** (§5.3) — they are one-time snapshots and more
   stations have completed on BH3 since the commit above.
2. **Check `master.py progress --root wavenet_ncf_production_v2`** for current campaign
   state — the ETA and completion numbers in this document are a snapshot, not live.
3. **Decide on the 386 never-audited OOM stations** (§4.2) — memory fix lets them run,
   but nobody has checked whether they also need the channel/location fix.
4. **Decide on the 99 genuinely outstanding no-seismometer stations** (§4.2) — no
   further automated recovery path exists for these without relaxing the 0.5deg
   threshold or accepting a different-era data compromise.
5. **The redundant-band purge on terravibranium** (§3.3) is still unscheduled —
   in-flight stations during the band-selection deploy have mixed BH+LH content that
   should eventually be trimmed to LH-only.

---

## 10. Addendum — 2026-10-01, everything since §1-9 were written

### 10.1 Twice-daily diagnostic monitor — built, tested, live

`src/wavenet_pipeline/00_waveform_acquisition/production/campaign_monitor.py` +
`monitor_chain.slurm` (committed `ace726c`, `57d90e0`, `e6dbe36`). Self-chained on BH3's
`preempt` partition (same pattern as inspector, with the `module load`-before-`sbatch`
fix applied from the start this time), reports every 12 hours to all three team emails
(`tolugboj@ur.rochester.edu`, `cpenagon@u.rochester.edu`, `tbalamur@ur.rochester.edu`).
Ends automatically via the same `state/STOP` file inspector/logger already honor.

**Delivery mechanism required real investigation** — two no-credential paths were tried
and both failed, confirmed by direct test, not assumed:
- BH3's own `sendmail` exists but doesn't deliver (`postdrop: unable to look up
  public/pickup` — no running local mail queue).
- SLURM's own `--mail-type=FAIL` **works for a single recipient** but **silently drops
  ALL recipients when given a comma-separated list** on this site (confirmed by
  isolating the two variables separately: job with 1 address arrived, same content with
  3 comma-separated addresses never arrived to anyone). Its notification format is also
  fixed — `--comment` does not get surfaced in the body either, tested directly.
- **Working path found**: terravibranium's Postfix is genuinely active (it already sends
  real RAID-monitoring alerts, confirmed via `systemctl status postfix`, uptime >1 year).
  The monitor composes the full report, then relays it through
  `ssh tolugboj@terravibranium.earth.rochester.edu "mail -s ... <addr>"`, **one
  invocation per recipient** (never a comma list, learning directly from the SLURM
  failure above).

**Report contents** (full digest every run, success context alongside any issues, not
alert-only): stations reported/with-data, days checkpointed vs total, days lost to
refusal, downloaded/packaged GB, rate/ETA, scratch/home quota, orchestrator task count,
inspector chain health, **provisioning** (jobs running/pending broken down by partition
— added after PI feedback; "provisioning" initially misread as storage quota share,
corrected directly by the PI to mean compute jobs/partitions), and **shard count**
(direct `find`/`du` against `packaged_h5/` on disk as a ground-truth cross-check against
the campaign's own self-reported packaged GB — measured 512.62 GB vs self-reported
512.30 GB, 0.1% difference, reassuring).

`--root` is a parameter, verified working against both v1 and v2 with correct,
independent numbers pulled from each (real backward-compatibility test, not assumed).

**Two real bugs found by testing the monitor itself, not assumed fixed:**
- `squeue` wasn't reachable in the nested shell this script runs under (the module
  system's own init breaks on repeated sourcing in that context) — fixed by setting
  `PATH` directly to the known SLURM bin path rather than depending on `module load`.
- This cluster's benign startup warning (`export: _module_raw: not a function`, seen on
  literally every single command all session) was being misread as a real stderr error
  — filtered out specifically so it can never manufacture a false "issue."

**Also fixed in the same pass**: `master.py`'s own inspector-chain health check had a
false-positive baked in. Its "exactly one link" invariant didn't match the self-chain
design it was checking, which deliberately keeps one running + one already-queued
successor in flight the whole time a link is healthy (submit-before-work, for crash
safety). This only surfaced once the §4.5 inspector fix made the chain actually run
continuously — before that fix, the chain died too fast for "2" to ever show up.
Corrected to treat 1-2 as the healthy range, `>=3` as the real anomaly. This fix is now
in `master.py` itself, so it's correct for any future `init`, not just this run.

### 10.2 BH3 partition/capacity investigation — capacity was never the constraint

Checked directly (not assumed) whether BH3 had unused partitions that could speed up
the campaign, or whether work should move to legacy BH. Findings:
- Our account (`tolugboj_lab`) has access to 4 partitions beyond the 4 already used
  (`standard`, `preempt`, `interactive`, `urseismo`): `fastx`, `gpu`, `h100`, `reserved`.
  **None are useful for this workload** — `fastx`/`reserved` don't even appear in
  `sinfo`'s node list (not general batch-compute partitions); `gpu`/`h100` are
  GPU-gated (`gres=gpu:A100:4` etc.), and this workload is 1-CPU, no GPU use.
- Capacity was never the bottleneck: at the time of checking, only ~69 jobs were running
  total, against 105 of our own 120 `urseismo` cores idle and thousands idle on
  `preempt` cluster-wide.
- The real reason job count was low: **96.6-96.7% of stations were already reported**,
  leaving only ~67 stations remaining — and ~68 tasks were already running, essentially
  1:1. There was no backlog of unscheduled work waiting for a slot. Moving work to
  legacy BH would not have helped, for the same reason — you can't parallelize a queue
  that isn't actually queued.

### 10.3 Stale v1 jobs — one cancelled after verification, four left alone

Five v1-era jobs were found still running well past the v2 migration (1+ day runtime
each): `2011879_129` (MN.RTC), `2011879_114` (MN.VTS), `2011881_15` (IC.QIZ),
`2011881_16` (MN.BNI), `2011881_19` (G.AIS).

**Verified before touching anything** (PI's explicit instruction) rather than assumed
redundant:
- Confirmed via `scontrol` + the actual running process's own `/proc/<pid>/environ`
  (`WAVENET_IDX_OFFSET`) which manifest row each corresponds to, resolved to real
  station codes -- not guessed from job array index alone.
- Confirmed these write to **v1's own separate directories**
  (`wavenet_ncf_production/{results,packaged_h5}/`), never v2's — zero path collision.
- Confirmed **none had a v2 result yet** — contrary to an initial assumption, they were
  NOT duplicating any currently-running v2 task. Cancelling them would only force v2 to
  redo work that was progressing fine, not stop a race.
- Confirmed none of the 5 appeared in the 954-station bug-affected list, so finishing
  under the old config wasn't expected to produce worse data for these specific
  stations.
- Checked actual memory use directly (`ps` on the compute node, not inferred): four were
  healthy (0.2-0.35 GB RSS, comfortably under the old 2G cap). **`IC.QIZ` (job
  `2011881_15`) was found at 6.7 GB RSS — 3.3x over its nominal 2G cap, not yet
  OOM-killed** — a real, live anomaly (cgroup limit not actually being enforced, or
  about to be killed uncontrolled). This one was cancelled (PI approved after seeing the
  specific evidence); the other four were deliberately left running.
- Verified the cancellation: confirmed gone from `squeue`, confirmed no v2 result exists
  for `IC.QIZ`, so v2 will process it fresh under the 7G cap when its array reaches that
  row — no resume-state corruption risk.
- **Verified the cancellation had no broader effect** (asked and checked directly,
  PENDING job counts in `preempt`/`standard` were identical before and after) — this is
  expected, not a sign anything is wrong: partitions are resource-isolated, so freeing a
  slot on `urseismo` cannot unblock a job queued in a different partition.

### 10.4 Live state as of 2026-10-01 (re-check before trusting — both move continuously)

**BH3 v2 campaign**: 1933/1999 stations reported (96.7%), 1023 with real data, days
checkpointed 607,543/2,739,232 (22.2%), packaged ~664 GB. **Scratch quota now at 86.3%**
of hard limit (was 80.8% at the time §4.6 was written — climbing, worth watching, though
the inspector chain is confirmed `OK (1-2 links)` and purging is active). Home quota
unchanged at 98.4% (chronic, group-wide, not specific to this campaign). Monitor chain
confirmed healthy and self-sustaining — already advanced to a new job (`2029664`,
scheduled `2026-10-01T18:30:04`) without intervention.

**Terravibranium SAmericaNoise packaging**: 40 workers, memory healthy (76/251 GB used),
**zero new OOM kills** (flat at 77, unchanged since the band-selection deploy), 734
stations fully complete (was 730). Real progress confirmed via file-level checkpoint
activity, not just the slow-moving completed-station counter — remaining work is
dominated by large, naturally slow stations (multi-hour each), not a stall.

### 10.5 Immediate next actions, updated

Items 1-5 in §9 above are still open and unchanged. Add:
6. **Watch BH3 scratch quota** — climbed from 80.8% to 86.3% between §4.6 and this
   addendum. Not yet an emergency (inspector is actively purging), but closer to the
   hard limit than before; the twice-daily monitor will flag it automatically if it
   crosses 90%.
7. **Re-run the three backup mirror scripts** (§5.3) if not already done since this
   addendum — they are one-time snapshots and significant additional packaging has
   landed (664 GB now vs ~510 GB when last mirrored).
