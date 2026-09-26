# Production migration, 2026-09-25: applying the XD smoke-test patches at 2,000-station scale

**Base:** `main` @ `06da0b9` (the merge of `add-september-ncf-pipeline`, which carries the
XD.MTAN/XD.RUNG smoke test at `docs/ncf_pipeline_stages/xd_mtan_rung_smoke_test/`).

This is the companion record to that smoke test. The smoke test found the defects on one
station pair; this documents what happened when its patches were applied to the live
2,000-station run, what that exposed that a two-station test could not, and every decision
taken in response. Written to be checked, not just read -- see **How to verify** at the end.

Style follows `xd_mtan_rung_smoke_test/PATCHES.md` deliberately, so the same evaluating
agent can navigate it.

---

## 1. What was decided, and why

| # | Decision | Rationale |
|---|---|---|
| D1 | Apply all six patches (1, 1b, 2, 3, 4, 6) unchanged | Verified before applying: `git apply --check`, 6/6 unit tests on the patched copy, tests fail to import on the unpatched source, and `neg_control.py` reproduced both original bugs against the live source (day 2 lost to a false overlap; +0.700 s placement error). Re-verified on the patched live source afterwards. |
| D2 | **Wipe and reprocess** the 151 mid-flight stations rather than let them resume | A channel's grid is anchored at its **first stored sample** and `start_time` is never rewritten, so patched days appended to a pre-patch anchor are still snapped onto the old phase. A station cannot be fixed by switching code mid-station; it must be wholly one pipeline or the other. Reprocessing is from raw SEED already on disk -- no re-download. |
| D3 | Do **not** reprocess the 674 already-completed pre-patch stations; bank their exact timing correction instead | The smoke test showed pre-patch data **plus a downstream timing correction** reaches +0.83 / 12-of-12 crossings / −0.5 % — statistically indistinguishable from the fully patched pipeline (+0.83 / 12-of-12 / −0.6 %). So banking the offsets makes them first-class at the cost of header reads, instead of ~50 CPU-days of reprocessing. |
| D4 | `patch_level` provenance written into the data itself | A pair mixing levels needs the correction applied; provenance that lives only in someone's memory silently corrupts results later. Stamped as an HDF5 group attribute (travels with the shard), in the result JSON, and in the station index. A shard holding data but no `patch_level` is marked `patch_level_mixed` rather than silently claimed as patched. |
| D5 | **Retire the continuously-maintained master HDF5**; build it on demand | It was 34 GB from only 74 stations (~1–3 TB projected), duplicated the shards (~2× storage), forced a serial single-writer bottleneck, and caused the reader/writer lock contention that killed the inspector. NCF is inherently *pairwise* — you open two stations — so per-station shards suit the real access pattern better. `build_master_h5.py` remains as an on-demand builder for whole-network or study-specific subsets. |
| D6 | Inspector rearchitected: shard-direct, global index, `--once` + SLURM chaining | See §4. |
| D7 | **Decouple station response metadata from waveform download** | See §3. This was the most serious defect found. |

---

## 2. Timing: what the full-scale replay showed that two stations could not

`timing_replay_production.py` replayed `append_channel_data`'s placement arithmetic from
raw miniSEED **headers only** across every completed pre-patch station, recovering per-day
placement error *and* the days silently dropped. 600 stations banked; **73 unbankable
because the inspector had already purged their raw SEED** (this is why purging was stopped
— see §5).

**Silent data loss is far worse than the smoke test could see.** The XD pair lost 14 of 954
channel-days (1.5 %). Across 600 production stations:

- **47,363 channel-days silently dropped — 5.56 % of the entire archive**
- **463 of 600 stations (77 %) lost at least one day**
- worst cases: `BL.STMB` 50 %, `BL.CRJB` 48 %, `RS.RSNT` 190 days, `ZB.TIKU` 33 %

**The "LH channels should be spared" expectation is wrong.** The smoke test flagged it
honestly as reasoned-but-untested ("expected from the mechanism, not tested here") and
suggested documenting BH-only stations as the exposed set. Measured over 803,887
channel-days:

| | median \|e\| | p90 | max | >0.1 s | >0.5 s |
|---|---|---|---|---|---|
| LH (native 1 Hz) | 0.001 s | 0.432 s | 1.000 s | **36.6 %** | 5.6 % |
| BH (decimated) | 0.346 s | 0.750 s | 1.000 s | 72.3 % | 21.8 % |

LH is near-zero in the *median*, so the no-decimation-phase argument holds for continuous
data — but **36.6 % of LH channel-days still exceed 0.1 s**, because after a real gap the
day's start phase no longer matches the anchor and `round()` misplaces it regardless of
native rate. Patch 1 applying alignment unconditionally to every trace is therefore correct,
and "BH-only stations are the exposed set" should not be documented as true.

---

## 3. The response-metadata defect (most serious finding)

**Symptom.** A survey of the first 667 packaged stations found **36.9 % of channels
(787/2130) left in raw counts**, **319 stations with no channel in metres at all**, and
**283 stations with no StationXML stored whatsoever**. Raw counts are not amplitude-
comparable across stations, so roughly a third of the archive was silently unusable for any
calibrated work.

**Root cause, established by elimination.** The first hypothesis — that reusing one
`MassDownloader` instance across per-year calls suppressed the StationXML step — was tested
and **falsified**: a shared instance and a fresh-instance-per-year both retrieved it
(`mseed=393, stationxml=1` either way).

The actual mechanism is ObsPy's own already-documented availability-endpoint bug
(`PROGRESS.md`, "ObsPy itself breaks on very wide date-range requests"):

```
TypeError: sequence item 0: expected str instance, tuple found
```

**233 of the 234 no-XML stations (100 %) recorded exactly this in `year_errors`.** The
per-year chunking reduced its frequency but did not eliminate it. When it fires, it aborts
that `download()` call partway — *after* waveforms have been written incrementally, but
*before* MassDownloader writes StationXML at the end of the call. Hence the signature:
mseed present, XML absent, error recorded. Stations that kept their XML are those where at
least one year's call completed (40 % of them still hit the error in *some* year).

This makes the structural point sharper rather than weaker: StationXML was obtained only as
a *side effect* of a code path that is **known to be unreliable**, so any failure there cost
us the response and `orchestrator.py` fell through to `units="counts"` without complaint.
Supporting evidence:

- affected stations have **hundreds of miniSEED files and zero StationXML** on disk
  (`1P.EIDA` 173 mseed / 0 xml, `1P.OHRS` 378/0, `2O.BTL04` 159/0)
- a direct `get_stations(level="response")` returns **complete** responses for exactly those
  stations — `2H.BTIE` 3/3 channels, `1P.GUMN` 6/6, `1P.EIDA` 6/6
- MassDownloader itself returns StationXML fine when pointed at a window that actually has
  data (`2H.BTIE` 2009-10-12: mseed=3, stationxml=1)

The metadata was reachable the entire time.

**Fix (PI: "the best design pattern is to decouple station metadata from channel data").**
New `fetch_station_metadata.py` retrieves each station's response **once**, independently of
waveforms, through a provider chain, over the station's full deployment span so every
response *epoch* is captured (a decades-long station changes instruments and the response is
looked up per trace by time). It is ~0.1 MB/station, re-runnable, reusable across
reprocessing, and — the point — **verifiable up front** via `--report`, so missing responses
are known *before* the preprocessing budget is spent.

`orchestrator.py` now reads from `station_metadata/`, stores that same inventory as
`_stationxml_raw` so each shard is self-describing, and records `inventory_source`,
`n_channels_response_ok`/`n_channels_total`, `response_ok` and per-channel
`response_failures`. A channel keeping raw counts is now an explicit, recorded decision.

**The gate is not "every station has a response."** Many stations in the fixed network have
no `BH?`/`LH?` channels at all, so they have neither waveforms nor a relevant response, and
excluding them is correct. `X3.OBS01` is the worked example: it carries only `EDH`, `EL1`,
`EL2`, `ELZ`, and produced no waveform data either.

- `EL1/EL2/ELZ` — 200 Hz, input units **M/S**, but a **4.5 Hz corner** (a standard OBS
  short-period geophone). The 0.028–0.196 Hz surface-wave band sits 23–160× below that
  corner, where response falls as f², so the band was never meaningfully sensed.
  Resampling to 1 Hz cannot recover it.
- `EDH` — 200 Hz, input units **PA** (differential pressure gauge), corner 0.02 Hz. Sensitive
  across our band, but it measures **pressure, not ground motion**. PI decision 2026-09-25:
  **not usable for these science goals**; not pursued.

So the real gate is **"does every station that HAS waveform data also have a response?"** —
implemented in `report_coverage()`, which fails loudly on that set and treats no-data
stations as benign.

---

## 3b. The largest defect found: ~86 % of the waveform archive was missing

Investigating the metadata loss led somewhere much worse. Measuring days actually obtained
against what the key index says exists (`station_summary.csv` `total_days`), for completed
stations:

| | days obtained / expected (median) | mean | got <50 % |
|---|---|---|---|
| stations **with** a `year_errors` entry | **0.14** | 0.25 | **84 %** |
| stations **without** | **1.00** | 0.90 | 12 % |

Stations that never hit the error land on **exactly** the predicted day count — which
validates the comparison and means the 0.14 median is real loss, not a bad expectation.
**380 of 602 completed stations (63 %) hit it.** Examples: `ZG.CP12` obtained 2 days of
~631; `N4.G65A` 2 of ~598; `YW.MAIO` 6 of ~690.

**It is transient, not deterministic.** Isolated re-runs of two stations that failed in
production both succeeded cleanly — `YP.NE83` 2009 gave 642 mseed files, `EI.IMAY` 2023
gave 1083, both with StationXML. The suspected trigger is provider flakiness under the
120-way concurrency the array ran at; GFZ timeouts appear even in a single isolated run.

**Fix:** each year's download now retries up to `WAVENET_DOWNLOAD_ATTEMPTS` (default 4)
with backoff and a fresh `MassDownloader` per attempt. Retrying is cheap because
MassDownloader skips files already on disk, so an attempt *resumes* rather than
re-downloads. `n_download_retries` is recorded per station so the fix is measurable rather
than assumed.

**Verified end to end.** Three of the worst-affected stations were re-run under the patched
code in an isolated root:

| station | production obtained | patched re-run | key index expects |
|---|---|---|---|
| `ZG.CP12` | 2 days | **631 days** (3,786 files) | ~631 |
| `YW.MAIO` | 6 days | **690 days** (4,137 files) | ~690 |
| `N4.G65A` | 2 days | **7,266 files** | ~598 |

All three recovered to exactly the expected day count, with StationXML, and -- the
informative part -- **0 retries were needed**. The error simply did not occur at 3-way
concurrency, where it had hit 63 % of stations at the production array's 120-way. So the
retry is the safety net; **concurrency is the actual driver**.

**Restart decisions (PI: "restart with new fixes to aim for completeness"):**

- **464 completed stations selected for a full patched redo** -- those with a recorded
  `year_errors` entry or under half their expected days. They collectively held ~20 % of
  the days they should. Their pre-patch artefacts (shard, daystate, qc, result JSON) were
  **moved, not copied**, to `wavenet_ncf_prepatch_incomplete/` -- a rename within one GPFS
  filesystem is a metadata operation, so preserving them for comparison cost nothing and
  stays reversible. Raw SEED was deliberately left in place so re-download *resumes*.
- **211 completed stations kept** -- they hit no error and are complete, so they are
  timing-shifted only and their exact offsets are already banked (D3 still applies).
- **Concurrency cut from 120 to ~50 total.** A gotcha worth recording: `--array-limit` is
  **per array**, and the deployment submits five chunks, so `--array-limit 40` would have
  meant up to 200 concurrent -- *more* than the setting that caused the damage. The correct
  value for ~50 total across five chunks is `--array-limit 10`.
- **Inspector stays off.** Raw SEED is now load-bearing for resuming the redo, so purging
  would be actively counterproductive until the redo settles.

Completeness is now a measured gate rather than an assumption: `n_download_retries` is
recorded per station, and days-obtained-vs-key-index is checked directly on the first
batch of patched completions.

## 3c. The smoke test's trim threshold was too tight for production

The first batch of patched completions came back at median 0.67 of expected days -- much
better than 0.14, but not the >0.9 the fix predicted. Three genuinely different causes,
worth separating rather than averaging:

1. **`max_trim_samples=2` was refusing real data.** `NL.HGN` downloaded 572 days and
   processed **82**, reporting `package_ok=True` with `n_days_refused=490`. Across the
   patched stations, **18 % of all attempted days were refused**, and the overlap
   distribution is strikingly tight: **every single one was 10-19 samples** (median 15,
   max 19) -- day files starting a few seconds before midnight while the previous day ran
   a few seconds past it. Patch 1b's threshold of 2 came from the XD pair, which only
   showed the single-extra-sample case. Raised to **60** (1 min at 1 Hz): clears every
   observed case, covers the smoke test's own 42-second straddling record, and stays
   1/1440th of a day so a genuinely duplicated day is still refused.
2. **The `TypeError` can survive 4 retries.** `XA.SA81` used all 4 attempts on 1997 and
   still failed, losing that year. Retry mitigates but does not eliminate; concurrency
   remains the real lever.
3. ~~The key index is not a perfect oracle for FDSN availability.~~ **Retracted.** This was
   inferred from `XF.GOAT` downloading 45 days cleanly against an expectation of 505, with
   no errors or retries. On the rebuild it obtained **455 days (0.90)** -- so that was the
   transient failure too, simply not recorded as an error. The lesson is about the
   diagnosis, not the pipeline: "no error recorded" is not evidence that nothing failed,
   which is precisely why completeness is now measured directly rather than inferred from
   error counts.

**Verified after the fix.** Across the first 17 rebuilt stations:

| metric | original | after retry alone | after trim fix |
|---|---|---|---|
| days obtained / expected (median) | 0.14 | 0.67 | **1.00** |
| days refused | 18 % | 18 % | **0.0 %** |
| years still failing | many | 1 | **0** |

4 retries fired and recovered, confirming the safety net works without being the primary
mechanism. `NL.HGN` remains an outlier (572 of 8553 expected days): the trim fix raised it
from 82 processed to all 572 downloaded, so its remaining shortfall is in the DOWNLOAD, not
the packaging -- tracked as an open item.

**Forward-only append has a consequence worth recording.** `append_channel_data` only ever
appends, so a day refused under the old threshold cannot be back-filled by rerunning -- it
would arrive out of order and be refused again. Shards written under the old threshold must
therefore be *rebuilt*, not topped up. 251 were moved aside (112 GB, fully recoverable) and
raw SEED was retained so downloads resume and only preprocessing repeats.

**An error in that selection, and the decision taken because of it.** The wipe was meant to
spare the 211 complete pre-patch stations (D3). It did not: pre-patch results carry **no
`patch_level` field at all** (it was only added today), so a `patch_level == 1` test never
matched and they were swept in. Because artefacts are *moved* rather than deleted, nothing
was lost and restoring them was a one-line operation. The wipe was nonetheless **allowed to
stand, deliberately**: those stations are pre-patch, so they carry both the timing error
*and* the days dropped by the original no-trim-at-all logic (5.56 % of channel-days
archive-wide). Rebuilding them costs preprocessing only, and yields a uniformly
`patch_level=2` archive with no per-station offset correction needed downstream -- which
serves "aim for completeness" better than preserving them would. **This supersedes D3.**

## 4. Services: why they kept dying, and the fix

**What happened.** Logger was OOM-killed at 2026-09-24T18:43:54 (MaxRSS 4.03 GB against a
4 GB cgroup limit) and inspector hit its 8-hour walltime at 02:33. Neither was noticed for
13–21 hours: `master_log.csv` sat frozen at 74 merged stations while the orchestrator kept
reporting hundreds more stations with data.

**Two distinct root causes, not one.**

1. *Every recovery mechanism lived inside the thing that dies.* The in-job
   `for attempt in 1 2 3 4` wrapper shares the job's cgroup, so an OOM kill takes the
   wrapper with it. Evidence: `logger.err` carries the OOM message and **not one**
   "attempt N failed" line — attempts 2–4 never ran.
2. *A hidden coupling.* Inspector waited on logger's ledger, so a dead logger made a
   healthy inspector useless. Two outages that looked like one.

**Fix.** A supervisor must not share a failure domain with what it supervises. On this
cluster `crontab` is PAM-blocked for the account, and axon-1's SSH is 2FA-seeded with
`ControlPersist 10m`, so neither can host one. The **SLURM controller** is the only
always-alive component outside every job's cgroup — so it holds the recovery record:
`inspector.slurm` submits its own successor as its **first statement**, while healthy, with
`--dependency=afterany` (fires on every terminal state, including SIGKILL where the dying
job executes nothing) and `--begin=now+20minutes` (sets cadence, caps a runaway chain at
~3/hour). Guards: honour `state/STOP`, and refuse to chain if the previous handoff was under
5 minutes ago.

Continuity comes from the chain; **progress** comes from the resumable state file. Two
separate mechanisms, deliberately.

Inspector itself is now shard-direct (no logger dependency, no master lock contention),
runs `--once` per tick, and additionally gates the irreversible purge on: `n_days_refused
== 0`, absence of `patch_level_mixed`, and — for pre-patch stations — the timing offsets
having been banked. It maintains the **global station index** (`station_index/*.json` plus a
regenerated `station_index.csv`), which is the input to an on-demand master build and lets a
consumer refuse to silently mix patch levels.

`master.py progress` stopped reporting "merged" from the retired ledger — a stale number
that looks like progress is worse than none — and now reports the patch-level split,
response coverage, days refused, and an explicit service-health line asserting the chain
invariant *exactly one inspector link queued or running*.

---

## 5. Irreversibility: what was protected and what was lost

Inspector is the only component that deletes anything. It was stopped as soon as the timing
issue was understood, because purging a station's raw SEED destroys the only source of its
exact correction (the packaged array alone yields the anchor phase but not the per-gap
re-randomisation, since gap-fills are zero-runs indistinguishable from quiet data).

- **Protected:** 600 stations banked before any further purge.
- **Lost:** the **73 stations purged before the pause** can no longer be corrected exactly.
  They are listed in `station_metadata/_coverage.json`-adjacent outputs and in the banked
  set as `raw_missing: true`. They are concentrated in the smallest stations (the fast tier
  processes smallest-first), so the lost precision is in the least valuable data.
- **Backed up:** the 151 wiped mid-flight shards were copied first (213.9 GB, 0 missing,
  0 size mismatches) to `/scratch/tolugboj_lab/wavenet_ncf_prepatch_backup/`, giving a
  controlled pre/post pair on real production stations — same raw input, two pipelines.

---

## 6. How to verify this (for an evaluating agent)

Read-only checks, no cluster job required beyond a login shell:

```bash
source /scratch/tolugboj_lab/softwares/anaconda/anaconda3/2021.05/etc/profile.d/conda.sh
conda activate instaseis
ROOT=/scratch/tolugboj_lab/wavenet_ncf_production
FW=/scratch/tolugboj_lab/wavenet_ncf_framework/production
```

1. **Patches are really applied.**
   `git -C <repo> log --oneline -- src/wavenet_pipeline/00_waveform_acquisition/` shows the
   apply commit; `grep -c PIPELINE_PATCH_LEVEL $FW/orchestrator.py` and
   `grep -c align_to_integer_second .../rover_download/build_master_h5.py` are both > 0.
   The smoke test's own suite still passes against the live source:
   `BUILD_MASTER_DIR=<dir> pytest docs/.../xd_mtan_rung_smoke_test/tests/test_patches.py`
   → 6 passed.

2. **Patched output is actually aligned.** For any station whose result JSON has
   `patch_level == 2`, every channel's `start_time` must fall exactly on an integer second,
   and the group must carry `patch_level=2` with no `patch_level_mixed`. Verified example:
   `HL.ARG`, `start_time=2008-02-16T19:47:54.000000Z`.

3. **Response coverage gate.**
   `python3 $FW/fetch_station_metadata.py --root $ROOT --report`
   The number that must be **zero** is *"HAS DATA but NO response (blocking)"*. Stations
   counted as benign must have `package_ok=False` in their result JSON.

4. **Timing offsets are banked and self-consistent.** Each
   `wavenet_ncf_migration/timing_offsets/NET.STA.json` replays from raw headers only. The
   replay's validation criterion is the smoke test's: predicted offsets matched directly
   measured pre-vs-post shifts to a median of 0.0 ms over 310 station-days.
   `raw_missing: true` marks the 73 unrecoverable stations.

5. **Service-health invariant.** `python3 $FW/master.py progress --root $ROOT` must show
   `inspector chain: OK (1 link)` once inspector is switched on. Zero means the chain is
   broken, and that is now reported rather than looking like normal quiet.

6. **Negative controls still reproduce the original bugs** against an unpatched copy:
   `BM=<unpatched dir> python3 docs/.../code/neg_control.py` → day 2 refused, +0.700 s error.

---

## 6b. Schema v2: the absolute-time grid (IMPLEMENTED and verified 2026-09-26)

PI made this a requirement, not an option: *"archive should grow forward or backward easily
without raw with order 1 insert. Time lookup and load on pair wise correlation run should
also be efficient. This is what data is designed for. It's a must have."*

**The design.** Every channel of every station is indexed against one shared epoch:

```
index(t) = round((t - EPOCH) * sampling_rate)      EPOCH = 1970-01-01T00:00:00Z
```

A day's position depends only on its own timestamp, never on what is already stored.

**What v1 got wrong, and why it cascaded.** Schema v1 anchored each channel at whichever
day happened to arrive first. That one decision produced: an arbitrary sub-second anchor
phase (the ~1 s timing error), forward-only insertion, overlaps that had to be treated as
errors (a trim threshold tuned twice and wrong both times), 5.56 % of channel-days silently
dropped archive-wide, and an entire apparatus of banked offsets, replay tooling and
per-station corrections to compensate. **When one decision generates a sprawling apparatus
of compensating machinery, the decision is wrong -- the machinery is not insufficient.**

**Verified, each by test:**

| requirement | result |
|---|---|
| grow **backward**, no raw, O(1) | writing a 1993 day into a shard already holding 2001 -- impossible under v1 |
| order-independent | the same days written in any order give an identical result |
| efficient pairwise load | two stations share one index space: `a[i0:i1]`, `b[i0:i1]`, **identical** `i0,i1` |
| exact time | every sample is `EPOCH + i/sr` by construction -- no phase to drift |
| overlap | the same instant is the same slot, so a duplicate day is an idempotent overwrite |

**Storage got BETTER, which is the counter-intuitive part.** Real measurement on `XF.GOAT`:
**340 MB on v2 against 356 MB on v1**, despite a logical span of 1.31e9 samples (5.2 GB
uncompressed). HDF5 chunked datasets are sparse -- a chunk never written is never allocated
-- whereas v1 wrote real zeros into interior gaps. At 1 Hz with 86400-sample chunks, one
chunk is exactly one calendar day, so a day write touches exactly one chunk with no
read-modify-write. The instinct that "anchoring a 2009 station at 1970 wastes space" is
simply wrong given chunked storage semantics.

**The cost of the new power, paid explicitly.** Zeros cannot express "no data" versus "quiet
data", and on a sparse grid most of the array reads as zero. Coverage is therefore a
first-class per-channel uint8 bitmap over days-since-epoch under `_coverage` (kilobytes for
decades). `load_window()` returns it as a mask alongside the data, because handing a
consumer unwritten zeros that look like quiet ground motion would bias an NCF silently.
`inspector` is schema-aware and verifies against coverage -- its v1 checks would have
flagged **every healthy v2 station**, since `ds[:10000]` now reads near the 1970 epoch.

**Backwards compatible.** Sample 0 *is* the epoch, so `start_time` stays literally correct
and any existing reader doing `round((t - start_time) * sr)` works unchanged.
`append_channel_data` is retained for reading v1 shards.

**End-to-end on real data:** `XF.GOAT` rebuilt to schema 2 -- 455 days, **0 refused**, all
channels epoch-anchored, sampled data non-zero on covered days. 9/9 new schema tests pass,
and the smoke test's original 6/6 still pass.

**Timing of the switch.** Adopted at 1.7 % of days checkpointed, while nearly everything was
being rebuilt anyway -- so the migration was close to free. Deferring it would have meant
rebuilding every station a second time. 79 v1 shards were moved aside (recoverable) and raw
SEED was untouched, so only packaging repeated.

## 7. Open items / not done

0. **The ObsPy `TypeError` is still live, and may be costing waveform data, not just
   metadata.** 380 of 602 completed stations (**63 %**) recorded it in at least one year.
   Each occurrence aborts that year's `download()` call partway, so that year's waveforms
   may be incomplete — the metadata loss is simply the most *visible* symptom because
   StationXML is written last. This was not quantified tonight and should be: compare
   downloaded day counts against `station_summary.csv` expectations for stations with and
   without `year_errors`. Decoupling the metadata (§3) removes the response exposure but
   does **not** fix the underlying waveform risk.
1. **Edge taper** (smoke test §4.5) still unpatched — needs overlap-padded per-day
   processing; `WAVENET_TAPER_PCT` only makes the current behaviour adjustable.
2. **Window-level quality masking** rather than whole-day rejection (smoke test §4.3).
   The QC sidecar from patch 3 flags days; nothing consumes those flags yet.
3. **The 73 unbankable stations** — decide whether to re-download them or accept them as
   permanently approximate (anchor-phase correction only).
4. **`build_master_h5.py` chunked merges.** Now that the master is built on demand, the
   whole-channel `src_ds[()]` read is the remaining OOM risk for a large build and should be
   streamed before a full-network master is attempted.
5. **`logger.py` is retired** but not deleted; it and `master_log.csv` are frozen history.
