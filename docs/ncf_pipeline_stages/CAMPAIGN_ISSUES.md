# NCF campaign (BlueHive3) — issue log and repair plan

**Authoritative copy: this file (versioned in the repo). Mirrored to
`/scratch/tolugboj_lab/wavenet_ncf/ISSUES.md` so it is readable at the point of work.**

**Scope: the BlueHive3 global FPS campaign only** — `wavenet_ncf_production` (v1) and
`wavenet_ncf_production_v2`, past and current. The South America noise packaging on
terravibranium is a different pipeline with a different codebase, different storage and a
different station set; its issues live in `TERRAVIBRANIUM_ISSUES.md` and must not be mixed in
here. Two logs, because a repair plan that spans two pipelines is a repair plan nobody can
execute.

Every issue that affects **packaged data** and therefore the eventual re-download /
re-packaging campaign. Each entry states when it was identified, what is wrong, how to
identify exactly which stations are affected, the code fix, and the repair action —
specifically whether that repair needs a **re-download** or only a **re-package**.

That last distinction is the whole point of this document: re-downloading is expensive and
re-packaging is nearly free, because raw SEED is retained. Getting the order wrong means
doing the expensive step twice.

---

## REPAIR ORDER — do this once, in this sequence

Several issues share a repair step. Running them in the wrong order means re-packaging a
station twice, or re-downloading it and *then* discovering its response was incomplete.

```
STEP 1   Re-fetch ALL station metadata with the fixed fetcher          fixes R-3, R-4
         No waveform data moves. Cheap. Must be FIRST, because steps 3
         and 4 both bake the response into the shard.
         -> gate on coverage_complete, NOT on "a file exists" (see R-3)

STEP 2   Re-verify coverage. Any station still missing channels is a
         genuine metadata gap -> R-5 decision, not a repair.

STEP 3   RE-DOWNLOAD + package the stations that are missing DATA       fixes R-1, R-2
         (truncated history, or never downloaded at all).
         One pass fixes both: daily windows AND pinning are now in the
         same code path.

STEP 4   RE-PACKAGE ONLY (WAVENET_SKIP_DOWNLOAD=1) the stations whose   fixes R-3, R-6, R-7
         raw SEED is already complete but whose SHARD is wrong
         (raw-counts channels, dead channels, rate drift).
         No network traffic. Reads retained raw SEED from scratch_work/.
```

**Do not merge steps 3 and 4 into "re-run everything".** Step 3 is bandwidth-bound and
days long; step 4 is CPU-bound and hours. Step 4 applied to a station already in step 3 is
pure waste, because step 3 re-packages as it goes.

---

## R-1 · Year-wide download windows silently truncate history

**Identified** 2026-10-02 · **Status** FIXED in code (`9844584`), data repair OUTSTANDING
· **Severity** high — affects almost every shard packaged before 2026-10-05

**Symptom.** Shards hold far fewer days than the station recorded. No error anywhere; every
result JSON reports success.

**Root cause.** The orchestrator requested a whole year per `MassDownloader.download()` call.
EarthScope returns **HTTP 502/504** on large requests (`Error: 502 : while resolving
source_ids`, service 1.1.80); MassDownloader logs these at ERROR level and continues, so the
affected intervals are simply absent. `chunklength_in_sec=86400` does not help — it chunks the
download, but the query still spans the whole window.

**Evidence** (JM.YHJB, independently proven to hold data; only window length varied):

| window | expected files | delivered | fraction |
|---|---|---|---|
| 1 day | 3 | 3 | 100% |
| 1 week | 21 | 3 | 14% |
| 1 month | 93 | 24 | 26% |
| 3 months | 276 | 60 | 22% |
| 6 months | 549 | 59 | 11% |
| 1 year | 1095 | 474 | 43% |

Measured across all shards: median depth **203 days** pre-fix. The same defect existed in v1,
worse — one call spanning decades. `2b74f3a` narrowed it to a year and was credited as a fix;
the loss merely got smaller.

**Fix.** `WINDOW_DAYS=1`, bounded to discovery's real channel epochs (`9844584`).

**Affected set.** Any shard whose station finished before 2026-10-05 ~12:11 UTC-4.
```
# stations whose result JSON predates the fix
find $ROOT/results -name '*.json' ! -newermt '2026-10-05 12:11'
```

**Repair.** STEP 3 — **RE-DOWNLOAD required.** The missing days were never fetched, so no
amount of re-packaging recovers them.

---

## R-2 · The campaign ran with no provider pinning

**Identified** 2026-10-05 · **Status** FIXED (`b615f4a` + slurm repoint), repair IN PROGRESS
(jobs `2062581`/`2062582`) · **Severity** high

**Symptom.** 557 of 1,999 stations produced no shard at all. 536 of them returned **zero bytes
with no error and no warning**.

**Root cause.** The root's `orchestrator.slurm` was generated 2026-09-30 with the code path
baked in, pointing at `wavenet_ncf_framework` — a tree containing **0 occurrences** of
`discovery_lookup` and **0** of `WINDOW_DAYS`. It also never exported
`WAVENET_DISCOVERY_MANIFEST`. `orchestrator.py` defaults that to `""` and `discovery_lookup()`
then returns `None`, so no station was ever pinned. The contradiction guard is
`if disco and download_bytes == 0`, so with `disco` always `None` it **could not fire** —
which is why the failures were silent.

**Evidence.** 0 of 2,042 job logs contained a `[discovery] pinned` line. After the fix: 296
stations pinned and 144 contradictions flagged within the first hour.

**Fix.** `master.py` bakes `CODE_ROOT` (the stable `wavenet_ncf/code/CURRENT` symlink) instead
of wherever `master.py` happened to sit at `init` time; slurm repointed and the manifest
exported.

**Affected set.** The 557 with no shard. Of those, **410 are ROUTED** (recoverable) and 147 are
not (see R-5).

**Repair.** STEP 3 — **RE-DOWNLOAD required.** Verified first: a 40-station randomised test
recovered **51%** of previously-zero-byte stations, with 40/40 logging pinning.

---

## R-3 · Incomplete responses → channels packaged in raw counts

**Identified** 2026-10-05 · **Status** FIXED in code; RESPONSES NOW IN PLACE; data repair OUTSTANDING (STEP 4 only)
· **Severity** high — silently mixes displacement and raw counts

**Symptom.** A shard contains some channels with `units='m'` and others with `units='counts'`.
Amplitudes differ by orders of magnitude. The result JSON records it honestly
(`response_ok: False`, per-channel `response_failures`), so it is detectable — but a consumer
that does not filter on `units == 'm'` will mix them.

**Root cause — two independent defects, both "two places compute the same thing and nothing
checks they agree":**

1. `fetch_station_metadata.py` **`break`'d at the first provider** that returned any channel
   with a response, and never checked that the inventory covered the channels the download
   would obtain. The download is pinned to the provider holding the waveforms and takes every
   band; the metadata fetch took whatever the first reachable provider had.
2. The metadata **time window came from a different source** than the download window —
   `metadata3/key_index_summary/station_summary.csv` rather than the discovery manifest.

And the Stage 1.5 gate counted **response files present**, not **channels covered**, so it
reported 82% healthy while 19% of packaged stations held uncorrectable channels.

**Evidence.**

| Station | StationXML had | Shard had | Failed |
|---|---|---|---|
| `TM.PANO` | BHE/BHN/BHZ | BH + HH | HHE, HHN, HHZ |
| `XL.HD25` | LHE/LHN/LHZ | HHE | HHE |
| `BL.NUPA` | BHZ only, one 7-week epoch | SHE/SHN/SHZ | all three |

**240 of 1,264 packaged stations, 410 channels.**

**Fix.** Merge inventories across **all** providers in the chain, stopping early only when the
merged set provably covers discovery's own channel list; take the window from the discovery
manifest; record `channels_wanted` / `channels_missing` / `coverage_complete` so the gate can
check coverage. Atomic XML write, since a re-fetch may run while a campaign reads the file.

**Verified** on the three stations above: all now `coverage_complete=True`, `missing=[]`.
`BL.NUPA` required merging **two** providers — direct proof the `break` was the defect.

**Affected set.**
```
# stations with at least one channel lacking a response
python3 -c "import glob,json;
print([ (json.load(open(f))['network']+'.'+json.load(open(f))['station'])
        for f in glob.glob('$ROOT/results/*.json')
        if (json.load(open(f)).get('response_failures') or {}) ])"
```

**STEP 1 IS DONE (2026-10-05).** A forced re-fetch of all 1,999 stations completed on legacy
BlueHive's `debug` partition:

| | |
|---|---|
| Re-fetched with the fixed fetcher | **1,612** (81%) |
| Got a response | 1,612 |
| **With COMPLETE channel coverage** | **1,609 — 100% of those with a response** |

Responses live in `<root>/station_metadata/` as `<NET>.<STA>.xml` plus a `<NET>.<STA>.json`
status record carrying `channels_wanted` / `channels_missing` / `coverage_complete`.

The `--force` flag had to be added first: the fetcher's resumability guard skipped any station
that already had a response, so the first re-fetch did nothing for exactly the 1,641 stations
holding the narrow XMLs it was launched to replace (790 of 1,001 tasks logged "already had a
response"). A second defect compounded it — `WAVENET_IDX_OFFSET` was applied in BOTH the slurm
wrapper and the Python, so one whole chunk asked for stations 2002-2999, found none, and
exited reporting "0 fetched, 0 already had a response". Same defect class as everything else
here: one value computed in two places with nothing checking they agree.

**Remaining repair.** STEP 4 ONLY — **NO re-download.** Raw SEED is retained and the responses
are now correct, so re-packaging alone fixes these stations.

**Live campaign:** `orchestrator.py:681` reads `station_metadata/` at the START of packaging,
so any task reaching that point after the re-fetch picks the new responses up automatically.
Stations packaged BEFORE it have the old response baked into the shard and still need STEP 4.

---

## R-4 · 358 stations have no StationXML at all

**Identified** 2026-10-05 · **Status** OPEN · **Severity** high

**Symptom.** `station_metadata/<KEY>.json` exists (a failure record) but `<KEY>.xml` does not.
Those stations can only package in raw counts, and the raw-counts guard refuses them outright
— so they produce **no shard**, or a shard entirely in counts if the guard was bypassed.

**Root cause.** Every provider in the chain failed, compounded by the same first-provider logic
and narrow window as R-3. Not yet established how many are genuine (the provider truly has no
response) versus artefacts of R-3's defects.

**Affected set.** `station_metadata/*.json` with no matching `*.xml` — 358 stations.

**Repair.** STEP 1 re-fetch will recover an unknown fraction. Whatever remains after that is a
genuine metadata gap and becomes an R-5-style decision: keep as raw counts (clearly labelled)
or exclude. **Re-count after STEP 1 before deciding.**

---

## R-5 · 147 stations are not routable at all

**Identified** 2026-10-05 · **Status** OPEN, needs a decision not a fix · **Severity** medium

Of the 557 stations with no shard, only **410** appear as `ROUTED` in the complete
1,999-station discovery manifest. The other 147 are not routed by the federator to any
provider, so no amount of pinning reaches them. Best-case recovery is 410, not 557.

**Options:** query providers directly outside the federator; accept as unavailable and record
in the delivered-network footprint; or revisit if they matter for coverage. **Not a repair.**

---

## R-6 · Dead / railed channels packaged before detection existed

**Identified** 2026-10-01 · **Status** FIXED in code, data repair OUTSTANDING · **Severity**
medium

Channels that are flat (`ptp == 0`) or pinned at a digitiser rail (2²¹/2²²/2³/2³¹−1) carry no
signal but occupy a full channel slot and will corrupt any NCF stacked from them. Detection was
added to `_raw_qc` on 2026-10-01; shards packaged before that may contain them.

**Repair.** STEP 4 — **NO re-download.** Re-packaging applies the current QC.

---

## R-7 · Off-nominal sampling rates / resample guard

**Identified** 2026-10-01 · **Status** FIXED in code, verification OUTSTANDING · **Severity**
low

Stations delivering off-nominal rates are resampled to 1 Hz; the call needed
`window='hann'` (ObsPy 1.2.2's default `'hanning'` was removed in scipy ≥1.10). Current
measurement shows **all** channels at exactly 1.0 Hz across both v1 and v2, so this may already
be fully clean.

**Repair.** Verify during STEP 4; no separate action expected.

---

## R-8 · Shards do not record which code produced them

**Identified** 2026-10-05 · **Status** OPEN · **Severity** medium — this is why several of the
issues above took days to attribute

BH3 shards carry `patch_level`, which is partial provenance. Terravibranium shards carry **empty
group attributes** — no `patch_level`, no StationXML, no coordinates — so "which script version
produced this shard" cannot be answered from the data at all, only inferred from mtimes against
script mtimes, and not at all for a shard whose packaging spanned a code edit.

**Fix (proposed).** The orchestrator should record its own resolved path and md5 into every
result JSON and as an HDF5 attribute. This is a **backstop**, not the primary control — the
directory structure should make the wrong-code mistake impossible first (see `SCRATCH_LAYOUT.md`).

---

## R-14 · Daily windows are rate-limited at scale — R-1 BLOCKED

**Identified** 2026-10-06 · **Status** DIAGNOSED 2026-10-06, compliant config verified ·
**Severity** was high

> **RESOLVED — and it was never the window.** EarthScope publishes a limit of **5 concurrent
> connections** (<https://ds.iris.edu/ds/nodes/dmc/services/usage/>). obspy's
> `MassDownloader` opens **3 per task** by default, so `--array=...%23` was **69 connections**,
> fourteen times over. The window was fine throughout.
>
> | Configuration | Connections | Outcome |
> |---|---|---|
> | `%23` x 3 threads | 69 | 20x 503, 19x 429, delivered 0.70x |
> | `%1` x 3 threads | 3 | 97% of span, 3 errors total |
> | **`%5` x 1 thread** | **5** | **zero throttling**, ~26 days/min per station |
>
> R-1 is therefore VIABLE at ~30 hours for 452 stations, not blocked. Full limits, the
> `threads_per_client` trap and how to recognise throttling in the data:
> **`PROVIDER_LIMITS.md`**.
>
> Nearly misread as "truncated days are not recoverable", which would have written off ~1.3M
> station-days on the strength of a self-inflicted configuration error.

**The R-1 validation test FAILED its own pre-committed threshold.** 20 of 20 stations
finished, 20 of 20 logged `[discovery] pinned`, so the test is valid. Median
`test_days / production_days` = **0.70x**, against a rule fixed before any result existed:
`>= 2.0 run the full repair, < 1.2 DO NOT`.

Re-downloading truncated stations with daily windows obtained LESS data than the year-window
campaign already holds.

**Root cause.** Daily windows make ~1,300 requests per station instead of ~20, and providers
throttle. `ZT.WTBG` swept 1,344 days and obtained 20; its log carries **20x HTTP 503 and 19x
HTTP 429 (Too Many Requests)** alongside 1,390 "No data available". MassDownloader logs each
rejection and continues — the same silent-loss mechanism as the original year-window defect,
reached from the opposite direction.

**So the window fix is correct in isolation and wrong at scale.** The 2026-10-02 measurement
(1 day delivers 3/3 files, 1 year 474/1095) was right about a SINGLE window and said nothing
about request RATE. Both facts are true; neither alone is actionable.

**What this invalidates.** The 1,316,822-day R-1 projection assumed truncated days are
recoverable by re-download. On this evidence they are not — at least not at daily granularity.
Do not quote that figure until a window/rate strategy is validated.

**Next step — find the window that is both complete and unthrottled.** The two measurements
bracket it: 1 day is complete but throttled; 1 year is unthrottled but 43% complete. A sweep
of intermediate windows (1, 3, 7, 14, 30 days) measuring BOTH delivered-fraction and HTTP
429/503 rate, with inter-request pacing as a second variable, is the bounded test that would
settle it. Until then R-1 stays blocked.

---

## R-16 · Some stations re-package far slower than others

**Identified** 2026-10-06 · **Status** OPEN, not investigated · **Severity** low — a slow
station is not a failed station

During the controlled STEP 4 batch, `DK.DBG` packaged ~0.5 days/min (12 of 3,700 days in 25
minutes) while the R-11 probe packaged `G.PAF` at ~53 days/min. Roughly 100x apart.

**Two candidate explanations, NEITHER verified:**

1. **Per-day cost.** `DK.DBG` carries 15 channels against `G.PAF`'s 9, so each day is simply
   more work.
2. **Response-cache thrashing.** The 512 MB LRU cap documents its own tradeoff — "the hot
   full-day nfft stays resident; only the long tail of one-off partial days is recomputed". A
   15-channel station with many distinct `nfft` values could be evicting and recomputing
   constantly. `WAVENET_RESP_CACHE_MB` is tunable if so.

**These are distinguishable by measurement:** thrashing gives a steady slow rate independent
of station size; per-day cost gives a rate proportional to channel count and sampling rate.

**Deliberately NOT fixed now** (PI, 2026-10-06). Making a production change to chase a slow
outlier mid-campaign is the behaviour principle 16 exists to prevent — there is an observed
symptom but no verified diagnosis, and the correct response to that is to log and measure
later, not to tune. Investigate with a bounded isolated test: two stations of differing
channel count at several `WAVENET_RESP_CACHE_MB` values, measuring days/min.

**It does not block STEP 4.** Correctness is unaffected: completed stations show zero
raw-counts channels and preserved day counts, and `YT.WAIS` went from 54 to 125 days because
days previously REFUSED for lacking a response now package.

---

## R-15 · Fragmented days merge non-deterministically

**Identified** 2026-10-06 · **Status** OPEN · **Severity** low — a few days per station

Two runs of IDENTICAL code on IDENTICAL raw data can produce different samples for a day
assembled from many overlapping fragments. Found while verifying the R-11 cache fix was
behaviour-preserving: 21 of 24 channels were bit-identical, and `II.ALE`'s three BH channels
differed on exactly one day index (7696 = 1991-01-21) by 0.2% relative — four orders above
float32 rounding, so real.

**Cause.** `mseed_files` comes from `os.listdir()`, which is unsorted; `files_by_day[day].append()`
preserves that arbitrary order; the day loop merges in that order. Day 7696 has **12 overlapping
fragments**, and ObsPy's overlap resolution depends on the order segments are added.

Eliminated first: partial final-day write (7696 is not last in either run), changed raw input
(zero files added in 12 h), and float32 rounding.

**Scope is small** — only days assembled from multiple overlapping fragments, a few per station.

**Fix.** Sort `mseed_files` before grouping. One line, deterministic by construction. It changes
which samples win on overlapping fragments, so it is a processing change and wants its own
isolated test, not a drive-by edit.

---

## R-13 · Stations silently skipped by the scratch-quota guard

**Identified** 2026-10-05 · **Status** OPEN, stations captured, need re-running · **Severity**
high — the failure leaves NO record

**Symptom.** `[orchestrator] REFUSING to start <KEY>: scratch at 95.2% of hard limit`. The task
exits in ~45 s having done nothing, and **writes no result JSON**. All 14 confirmed cases have
zero result records, so the station is not a "failed station" anyone can find later — it is
invisible, traceable only by grepping a log file. Same shape as R-2: produces nothing, leaves
no evidence it was attempted.

**Blocked stations** (captured to `<root>/quota_blocked_stations.txt` before logs roll):

```
AI.BELA  AK.DCPH  AT.SMY   CA.CARA  CI.CIA   CI.ISA   CI.PDM
DK.NEEM  G.CCD    G.SSB    TT.TAMR  TT.THTN  X5.CTSN  X5.NOTN
```

**Root cause — a stale calibration, not a wrong idea.** The guard refuses rather than risk a
half-written shard, which is correct. But the threshold is a PERCENTAGE of quota, and the code
comment still reads `/scratch here is quota-limited (10 TB soft / 11 TB hard)`. At 11 TB, 5%
was ~550 GB and refusing was prudent. **The real quota is 102/104 TB, so the same 5% is 4.9
TB** — far more than any station needs. A sensible guard became obstructive purely because the
filesystem grew 10x underneath it.

**Two defects, worth separating:**

1. *Calibration* — fix by configuration: `WAVENET_QUOTA_STOP_PCT` (default 95). PI, 2026-10-05:
   there is ample headroom now, and **future campaigns should be configured so this is
   non-blocking**. Raised on the R-1 rerun; the current campaign is unblocked by reclaiming
   space instead.
2. *Silence* — the guard should record the refusal where the pipeline looks for outcomes, not
   only in a log. A station that is skipped must leave a result record saying so, or it cannot
   be counted, reported or retried. This is the more important of the two and is **not yet
   fixed**.

**Space reclaimed 2026-10-05:** 1.1 TB of raw SEED from `archive/test_roots/*/scratch_work`
(superseded canary runs). Packaged shards in those roots were preserved.

**Repair.** Re-run the 14 once the threshold is raised. They need a full download — nothing of
theirs exists.

---

## R-12 · 240 ROUTED stations return no response from any provider

**Identified** 2026-10-05 · **Status** OPEN, not yet investigated · **Severity** medium

After the forced re-fetch of all 1,999 stations, **387 have no obtainable response**:

| Discovery status | Count | Reading |
|---|---|---|
| `NO_WANTED_BAND` | 145 | no channel in our bands — nothing to get, **not a defect** |
| `NO_DATA_AT_SERVICE` | 2 | same |
| **`ROUTED`** | **240** | **the real gap** — the federator says data is there, no provider returns a response |

The 240 are the only ones worth pursuing. The shape is familiar from R-2: "routed but nothing
comes back" turned out then to be our own routing, not the archive. Known contributors already
observed in the error signatures, none yet quantified for this set:

* `ValueError: The FDSN service shortcut 'nan' is unknown` — a NaN provider field is being
  formatted into a client constructor (147 manifest rows have no provider and no URL).
* `FDSNException: No FDSN services could be discovered at 'http...'` — an endpoint that is not
  a valid FDSN root.
* `ValueError: The current client does not have a station service` — an endpoint serving
  waveforms but not metadata (obspy's `USP` entry is a known case).

**Do not conclude these stations lack metadata until those three are separated out.** By
network type the overall response rate was temporary 84% / permanent 75%, so there is no
evidence operators withhold responses — the PI's hypothesis held everywhere it could be
tested (1,609 of 1,612 complete).

**Repair.** A bounded investigation on `debug`: take a sample of the 240, record which
endpoint each was tried against and why it failed, and separate our routing errors from
genuine absence. Only what survives that is a real decision about excluding stations.

---

## R-11 · Tasks OOM-killed during packaging, cause NOT established

**Identified** 2026-10-05 · **Status** SOLVED 2026-10-06 — cause found and fix verified ·
**Severity** was high, 182 stations failed

> **RESOLVED.** Cause: the same unbounded evalresp cache that OOM-killed 76 stations on
> terravibranium on 2026-09-30, never propagated to BH3. `_RESPONSE_CACHE` is a plain dict at
> `orchestrator.py:234`, keyed on `nfft` which derives from each file's own sample count, so
> partial and gap-filled days mint near-but-not-equal entries of 39-79 MB each that are never
> evicted (349 distinct `nfft` for `G.CRZF`).
>
> Verified by isolated before/after on the same three stations: `G.PAF` went from **7,163 MB
> at 2,902 days to 1,406 MB at 2,894 days** — same work, 5.1x less memory. `G.CRZF` reached
> MORE days (2,153 vs 1,910) at 1,545 MB against 7,174 MB.
>
> Output verified bit-identical on 21 of 24 channels; the 3 that differ are R-15, a separate
> pre-existing nondeterminism unrelated to the cache.
>
> The fix (byte-capped LRU, 512 MB, `WAVENET_RESP_CACHE_MB`) is ported from terravibranium's
> live script and NOT yet deployed to production.

**Symptom.** 182 tasks of the 2026-10-05 relaunch killed with `OUT_OF_MEMORY`, MaxRSS
**6.8-7.2 GB against a 7 GB limit**, after 1.5-2.3 h. The slurm wrapper then treated the kill
as a transient import race and retried the whole station four times, compounding it.

### What is VERIFIED

| | |
|---|---|
| Phase | **Packaging**, not download. 91 of 92 checkable OOM tasks wrote a day-state checkpoint **during their own run**. |
| Scales with | **History length.** 0% OOM below 500 target days · 5% at 500-1,500 · **16% at 1,500-4,000**. Median target 1,924 days for killed tasks vs 728 for completed. |
| Does NOT scale with | **Per-day cost.** OOM is *higher* for low-rate stations (15%) than 100 Hz HH/EH ones (6%) — the opposite of what response-removal cost predicts. |

So memory grows with the NUMBER OF DAYS packaged, not with the size of any one day.

### What is DISPROVEN — including my own reasoning

1. *"It dies in the download loop."* No. Checkpoints written during the run prove packaging.
2. *"Response removal on 100 Hz days."* No. Rejected by the sampling-rate test above.
3. *"The HDF5 handle is held open and its chunk cache accumulates."* No. `with h5py.File(h5_path, "a")`
   sits INSIDE the per-day loop and is opened and closed every day, deliberately, to bound
   crash damage.

**The cause is not established.** Memory grows with days packaged and nothing yet identifies
what grows.

### Measurement is CONFOUNDED by my own changes — read this before evaluating

Two speculative changes were deployed to `orchestrator.py` on 2026-10-05 **while the campaign
was running**, both resting on hypotheses since weakened:

* `RECYCLE_EVERY=365` — rebuild the MassDownloader every 365 windows.
* `del mdl` + `gc.collect()` before packaging begins.

Neither is validated. Neither can corrupt data (freeing an unused object; rebuilding a
*pinned* downloader via the factory), but both change the memory profile, so **any RSS
measurement must be split by whether the task started before or after that deploy.** Tasks in
jobs `2062581`/`2062582` span both. This is exactly why speculative fixes during production
are expensive: they cost the ability to measure cleanly.

### Why no test caught it

Every verification run used short windows or small stations, so none ever executed thousands
of day iterations in one process. The failure needs ~1,500+ days of history to appear at all,
and no test had that shape. Same family as R-1: the test and production differed in the one
dimension that mattered.

### RESOLVED: speculative changes REVERTED, baseline is clean

The two speculative changes (`RECYCLE_EVERY`, `del mdl`) were **reverted on 2026-10-05** and
the revert deployed, so repo and deployed tree now match exactly (md5 `0ed15903...`). The
verified fixes are untouched: `WINDOW_DAYS` (daily windows), `discovery_lookup` (pinning) and
epoch bounding all remain.

PI's reason, and it is the right one: the code must sit in the state that PRODUCED this
failure, or the eventual measurement has nothing clean to measure against. The earlier
confound — tasks within one job spanning two code states — ends here. Anything starting after
the revert runs the verified code.

**Known consequence, accepted deliberately:** long-history stations will keep OOMing, because
nothing now mitigates it. Those failures join the existing 182 and are re-run together. A
clean baseline was judged worth more than a speculative patch.

**If the bleeding needs stopping before the diagnosis**, use a CONFIGURATION mitigation that
leaves code untouched — raise `--mem-per-cpu` above 7G in `orchestrator.slurm`, or lower
concurrency. Configuration is trivially reversible and does not confound the measurement.
See `docs/PIPELINE_PRINCIPLES.md` §15.

### Repair

1. The 182 stations need re-running; they are failures, not partial successes.
2. **Diagnosis, after the campaign:** run one reliably-OOMing long-history station with RSS
   sampled per day and identify what grows. One job, not a campaign.
3. **Mitigation available regardless of cause:** raise `--mem-per-cpu` for long-history
   stations. Treats the symptom knowingly; the ceiling is 7 GB and consumption scales with
   days.

---

## R-10 · 108 manifest rows have no usable target

**Identified** 2026-10-05 · **Status** OPEN · **Severity** low, but it corrupts every ratio

`manifest/fps_stations.csv` has `days = NaN` for 108 of 1,999 rows. Those stations cannot be
scored for completeness, so `campaign_report.py` excludes them from every ratio and reports
them separately rather than scoring them as zero.

Found only because the denominator check was made explicit: the first version tested
`days <= 0`, and `NaN <= 0` is False, so NaN passed straight through. Exactly the shape of
error the check exists to prevent — the same family as dividing by an open-ended 2599 epoch.

**Repair.** Recompute `days` for those rows from the key index, or mark them explicitly
unknown. Until then they are counted as stations but never as a target.

---

## R-17 · Result JSON reports a different day count than the shard holds

**Identified** 2026-10-08 · **Status** OPEN · **Severity** minor · **Deferred to the
completion campaign** (PI, 2026-10-08)

`XE.ES31` reports 49 days in its result JSON and carries 47 in `_coverage`. The JSON is a
summary written by the packaging loop; `_coverage` is the data. They are two counts of the
same quantity computed in two places, which is the shape of defect this pipeline keeps
producing (see `docs/PIPELINE_PRINCIPLES.md` §12).

Not yet measured at scale — one station, found while verifying the R-1 test downloads. The
disagreement is small and in the direction of the JSON over-reporting, so any ratio built on
JSONs is slightly optimistic.

**This is resolvable** (PI): the shard is authoritative and the JSON is derived, so the fix is
to write the summary FROM `_coverage` after the shard is closed rather than from the loop's
own running tally. Until then, every completeness number must come from the HDF5 — which is
already the standing rule in "How these numbers must be produced" below.

**Repair, during the completion campaign.**
1. Measure the gap across all shards: JSON `days` vs `len(_coverage)` non-zero, per station.
2. Make the JSON a read-back of the shard, not a parallel count.
3. Re-emit JSONs for existing shards from their `_coverage` — no re-download needed.

---

## R-18 · Not every packaged day carries a full 3-component set

**Identified** 2026-10-08 · **Status** OPEN · **Severity** minor · **Deferred to the
completion campaign** (PI, 2026-10-08) · **May be irreducible**

Measured across 1,049 stations / 1,485 three-component bands / 1,128,622 operating
channel-days, with horizontal naming collapsed to slots (`1`→H1, `2`→H2, so BH1/BH2/BHZ counts
as a complete set — see the measurement note below):

| | days | share |
|---|---:|---:|
| 3 of 3 slots | 918,828 | 81.41% |
| 2 of 3 slots | 92,438 | 8.19% |
| 1 of 3 slots | 117,356 | 10.40% |

**It is not a packaging defect.** The per-station distribution is bimodal — median 0.996,
mean 0.785:

| fraction of operating days complete | stations | share |
|---|---:|---:|
| 100% | 487 | 46.4% |
| 95–100% | 116 | 11.1% |
| 80–95% | 92 | 8.8% |
| 50–80% | 142 | 13.5% |
| <50% | 212 | 20.2% |

A bug in the packaging loop would impose a roughly constant per-day failure rate on every
station and would move the median. Instead 487 stations are untouched at 100% and a distinct
~20% sub-population is chronically incomplete. That is a per-station property, upstream of
packaging — i.e. what the provider actually held.

**Two unexplained signals, not yet investigated:**
* The absent slot is most often **Z (56.9%)**, not a horizontal (H1 30.5%, H2 12.6%). That is
  seismologically backwards and does not fit "the provider is missing a horizontal". It fits a
  channel dropped at packaging for a RESPONSE reason better than for a data reason — candidate
  link to **R-12** (240 routed stations with no response) and **R-3**. Untested.
* **LH is 93.0% complete while BH is 74.2%** across 4× fewer operating days, on the same
  stations and the same download path. Again points at per-channel availability rather than
  the packaging loop.

Separately, 533 bands were excluded from the denominator entirely because they never had 3
slots packaged at all (314 one-slot, 219 two-slot). Those are stations that never had a
3-component set — a different class from a day losing one, and they must not be folded into
the same number.

**PI's framing (2026-10-08):** the day-count gap (R-17) is resolvable; this one "may or may
not be, based on provider availability". Both are reviewed during issue resolution, not now.

### Measurement note — how to get this number wrong

The first attempt grouped channels by band prefix and counted raw channels, giving 86.71%. It
was wrong in a way worth recording. Horizontals are named `N`/`E` on some instruments and
`1`/`2` on others, and a station that changed convention between epochs has **five or six**
channels in one band (BH1, BH2, BHE, BHN, BHZ). No day can carry all of them, so every one of
that station's days scored as a deficit. Collapse to the three physical SLOTS first. The
corrected figure is lower (81.41%) because the slot collapse also unions multi-epoch channels
into one row and widens the operating-day denominator — the first number was not merely
noisier, it was measuring a different thing.

Measurement script: `comp_completeness.py` (scratch run, 2026-10-08). The rule from
"How these numbers must be produced" applies: read `_coverage`, never the result JSONs.

---

## R-19 · Channels packaged in COUNTS although the response was already in the shard

**Identified** 2026-10-08 · **Status** OPEN · **Severity** medium · **Cheap to repair, and
the repair needs no provider**

Measured across all packaged shards:

| | stations | channel-days | clean (`units == m`) | not clean |
|---|---:|---:|---:|---:|
| v1 | 1,090 | 2,427,774 | 97.23% | 67,179 |
| v2 | 1,514 | 3,180,469 | 95.51% | 142,688 |

**v1 is CLEANER than v2.** That contradicts the working assumption that v1 is the buggy
campaign and v2 the fixed one. It is not a code-version effect: `patch_level` is 3 on every
shard in both. "counts" tracks RESPONSE AVAILABILITY, and v2 simply reaches more hard
stations, inheriting more of R-12's routed-but-no-response population.

### The actual defect

Sampling 40 affected stations per campaign and parsing each shard's own
`_stationxml_raw`:

| | repairable offline | channel absent from XML | in XML, no response stages |
|---|---:|---:|---:|
| v1 | 110 ch / **54,723 days** | 6 ch / 5,711 d | 1 ch / 612 d |
| v2 | 54 ch / **78,626 days** | 59 ch / 33,176 d | 4 ch / 818 d |

**~77% of non-metre channel-days have a usable response embedded in the very file that was
written in counts.** The packager had what it needed and did not apply it.

This is NOT R-12. R-12 is "no provider holds a response for this channel". This is "we held
the response and packaged raw counts anyway" — a different mechanism with a much cheaper fix,
and it was hidden inside R-12's population because both surface as `units != m`.

### Why this matters beyond the unit label

These days are **recoverable offline** — no provider contact, no re-download, no rate limit.
Any cleanup that DELETES non-metre days as "buggy" would destroy ~133k recoverable
channel-days across the two campaigns to reclaim roughly 60 GB against 42 TB of free archive
space. Repair, do not delete (`docs/PIPELINE_PRINCIPLES.md` §5).

### Repair — END-TO-END, by PI decision 2026-10-08

Offline deconvolution of the already-packaged counts data was considered and **rejected**:
that data has already been decimated and filtered, so deconvolving afterwards is not
operation-order-identical. It would probably be fine in the NCF band, and "probably fine" is
not a property to put underneath an archive others treat as correct.

**End-to-end does not mean "download everything again."** The decision is about the
processing chain, and re-packaging from raw SEED already in hand runs the identical correct
chain. Measured scope:

| campaign | affected sta | bad days | raw SEED location |
|---|---:|---:|---|
| V2 | 70 | 152,924 | 60 sta / 144,667 d still on scratch; 9 sta absent |
| V1 | 51 | 67,179 | 29 sta / 38,596 d on terravibranium; 22 sta absent |
| union | **93** (28 in both) | | **~89% of days have raw in hand** |

1. Find the call site where a response is present but not applied — grep the packaging path
   first, per §11, before theorising about provider behaviour.
2. **Tier 1, 60 v2 stations:** repackage from `scratch_work/` via STEP 4's existing, tested
   `WAVENET_SKIP_DOWNLOAD=1` path. No new code, no network.
3. **Tier 2, 29 v1 stations:** pull 359,813 files back from
   `/RAID6/lab_archive/wavenet_ncf_raw_seed/v1` by tar-stream (the archive move in reverse),
   then tier 1.
4. **Tier 3, ~two dozen stations:** raw in neither place — genuine re-download, inside the
   compliant 5-connection cap.
5. **Compute the residual first.** A channel-day dirty in v1 but clean in v2 needs no repair;
   consolidation selects the clean copy. Only days dirty in EVERY campaign holding them are
   real targets, so the true set is smaller than 93.
6. Validation gate: the 45 station-channels packaged as counts in one campaign and correct
   metres in the other (see `stage_6_consolidation_and_archive.md`). Known-good answer, not a
   plausibility argument (§14).
7. The channels genuinely absent from the embedded XML are R-12's population and need a
   response re-fetch regardless.

**Likely connected to R-18.** A channel whose response is missing or unapplied is a candidate
mechanism for the Z-dominant per-day component gaps. Work R-12, R-18 and R-19 together;
confirming or killing that link resolves them as a set.

---

## Closed

**I-2 · the window-truncation measurement itself** — see R-1, fixed `9844584`.
**I-5 · `CHANNELS` duplicated across `orchestrator.py` and `fetch_station_metadata.py`** — still
duplicated, but both now carry the same six bands and the coverage check in R-3 makes a
divergence detectable rather than silent. Reclassified from OPEN to monitored.

---

## How these numbers must be produced

Use `campaign_health.py` and read the **HDF5 files**. Never judge from result JSONs: they
reported success on truncated stations (R-1) and hid 536 silent zero-byte failures behind a
guard that could not fire (R-2). On 2026-10-02 a healthy campaign was declared a near-total
failure and cancelled on three compounding measurement errors — a span ratio whose denominator
came from open-ended 2599 epochs, a population narrowed to the residual tail and then reported
as the whole, and a verdict reached without opening a single data file.
