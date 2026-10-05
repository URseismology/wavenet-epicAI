# NCF campaign — issue log and repair plan

**Authoritative copy: this file (versioned in the repo). Mirrored to
`/scratch/tolugboj_lab/wavenet_ncf/ISSUES.md` so it is readable at the point of work.**

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

**Identified** 2026-10-05 · **Status** FIXED in code (this commit), data repair OUTSTANDING
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

**Repair.** STEP 1 then STEP 4 — **NO re-download.** Raw SEED is retained; re-fetch the
metadata and re-package.

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

## R-9 · terravibranium packaging: 9 stations with raw-counts channels

**Identified** 2026-10-05 · **Status** OPEN · **Severity** medium · **Separate pipeline**

South America noise packaging (`/RAID6/lab_archive/packaged_v2`, 895 shards, 427 GB). Same class
as R-3 but a different codebase (`sam_package_one.py`).

| Station | Uncorrected | Days |
|---|---|---|
| `AS.ZOBO` | 2 of 3 | **3,914** |
| `SR.BOCO` | 3 of 3 | **1,220** |
| `XF.HB30` | 6 of 6 | 41 |
| `TC.ICR3` | 3 of 3 | 36 |
| 5 others | — | 1-86 |

`AS.ZOBO` and `SR.BOCO` are the substantial ones. **Repair after that campaign finishes**, and
only once the code-provenance question (R-8) is settled for that pipeline.

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
