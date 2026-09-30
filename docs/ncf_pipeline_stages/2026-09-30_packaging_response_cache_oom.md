# Terravibranium packaging: the evalresp cache was unbounded, and it cost us 76 stations

**Date:** 2026-09-30
**Component:** `sam_package_one.py` (SAmericaNoise packaging, terravibranium — packaging only, no download)
**Status:** Diagnosed, fixed, verified bit-exact, campaign relaunched

---

## Summary

The per-station evalresp memoization cache introduced on 2026-09-28 (a genuine ~25x
speedup) had no size bound. It grew until 40 concurrent workers exhausted a 251 GB
machine, and the Linux kernel OOM killer SIGKILLed **76 stations**. The cache is now
capped by total bytes with LRU eviction. Output is unchanged — verified bit-for-bit.

This is worth reading even if you never touch this script, because the failure was
invisible in the obvious place to look: the run reported **zero timeouts**, which was the
metric we had just fixed and were watching.

---

## How it was missed

The 2026-09-29 fix removed a wall-clock timeout that had been killing large stations on
sight. After that change the log showed no further `TIMEOUT` lines, and the run was
reported as healthy on that basis.

It was not healthy. The stations were still dying — the cause of death had simply moved
from the clock to the kernel:

| | Before 2026-09-29 | After |
|---|---|---|
| Failure line | `TIMEOUT after 10800s` | `FAILED rc=-9` |
| Killer | `subprocess.run(timeout=)` | Linux OOM killer |
| Stations lost | 87 | 76 |

`rc=-9` is SIGKILL. Nothing in the log says "out of memory" — the signal arrives with no
message, so the failure reads as a generic non-zero exit.

**Lesson:** checking that the failure you just fixed has stopped is not the same as
checking that the work is succeeding. Count successes against the expected total; do not
infer health from the absence of one specific error string.

---

## Root cause

`_cached_remove_response()` memoizes the expensive evalresp evaluation under this key:

```python
key = (tr.id, str(chan.start_date), str(chan.end_date), nfft, output, water_level)
```

`nfft` comes from `_npts2nfft(npts)` — the sample count of *that file's* merged trace.
The module docstring stated the assumption directly:

> *"every day of a station shares the same sampling rate and day length (hence the same nfft)"*

That is false for real archive data. Partial days, gap-filled days, and differing trace
spans produce near-but-not-equal lengths. Measured on `G.HDC`:

```
3456000   <- clean full day (86400 s x 40 Hz)
3456024   <- +24 samples
3452112 / 3452020 / 3450678   <- three more, within 0.15% of each other
3032188, 2827916, 2685600, 1211310, 578826
```

Each distinct value mints a separate cache entry holding `freq_response` (complex128) and
`freqs` (float64) over ~nfft/2 points — **39.6 MB per entry at 40 Hz, 79.1 MB at 80 Hz** —
and nothing ever evicted them.

### Measured growth (G.HDC, unpatched)

| Files processed | Cache entries | Distinct nfft | Cache | Process RSS |
|---:|---:|---:|---:|---:|
| 25 | 12 | 6 | 0.35 GB | 0.61 GB |
| 45 | 60 | 23 | 0.81 GB | 1.01 GB |

**The cache was 80% of process RSS and still climbing steeply at file 45.** G.HDC has
9,486 files.

### Why it detonated when it did

The driver orders work smallest-first (`key=lambda p: file_count[p]`). Small stations have
few distinct day lengths and finish quickly. By the end of the run every one of the 40
workers was simultaneously on a giant — precisely the stations that accumulate the most
cache entries. Memory went to 234/251 GB with swap fully consumed, and the OOM killer
started firing.

---

## The fix

Byte-capped LRU, default 512 MB per worker, tunable via `WAVENET_RESP_CACHE_MB`:

```python
_RESPONSE_CACHE = OrderedDict()
_CACHE_BYTES = 0
_CACHE_MAX_BYTES = int(os.environ.get("WAVENET_RESP_CACHE_MB", "512")) * 2**20
```

On insert, evict least-recently-used until back under the cap; on hit, `move_to_end`.

**Eviction is safe by construction.** This memoizes a deterministic function of
`(response epoch, delta, nfft)` — a miss costs a recomputation and nothing else. The hot
full-day `nfft` stays resident under LRU, so the 25x speedup survives; only the long tail
of one-off partial days is recomputed.

---

## Verification

**Correctness — bit-exact.** The patched worker was run with a deliberately tiny 64 MB cap
(vs ~40 MB per entry) to force eviction on nearly every file, against the pre-patch
unbounded version over the same 30 files of `G.HDC`:

```
[ref] final cache entries = 24
[new] final cache entries = 4
datasets compared: 12
mismatches       : 0
max abs diff     : 0
VERDICT: BIT-EXACT
```

**Memory — bounded.** Same diagnostic, same station, after the patch: cache flat at the
0.50 GB cap and RSS flat at 0.81 GB across files 50–60, where the unpatched run was still
climbing. Per-file time unchanged (~1.5 s steady state).

**Worst case — measured, not modelled.** `IU.OTAV` (80 Hz on location `10`, two location
codes, largest entry 79.1 MB): peak RSS **1.00 GB**.

---

## A second, separate memory sink: sample rate and gap-filling

Bounding the cache was necessary but did not explain everything. After the relaunch, `BL.*`
workers still reached 5–7 GB while the cache sat at its 512 MB cap. Two compounding causes,
both measured on `BL.BEB21`:

**1. The remaining stations are high-rate.** `BL` records at **500 Hz** — 12.5x `G.HDC`. A
500 Hz day gives `nfft ~ 254M`, so the rfft output, the response array and the irfft output
are ~2 GB *each*. That is the working set, not a leak, and it is freed per file.

**2. `st.merge(fill_value=0)` inflates gappy files.** Merge materialises one contiguous
array from first-start to last-end, zero-filling the gaps. Measured per file:

| Real samples | After merge | Inflation |
|---:|---:|---:|
| 2.25 M | 111.2 M | **49x** |
| 2.35 M | 127.9 M | **54x** |
| 26.1 M | 126.4 M | 4.8x |

So a file holding minutes of real data can drive a full-day FFT that is mostly zeros —
wasting both memory and time. Changing this would alter gap handling, which is a
processing/physics decision: **do not change it without PI approval.** Recorded here as a
known inefficiency, not an applied fix.

### Rate distribution of the remaining work (105 pending stations)

| Max sample rate | Stations |
|---:|---:|
| 100 Hz | 87 |
| 200 Hz | 8 |
| 250 Hz | 1 |
| 500 Hz | 2 |
| <= 50 Hz | 7 |

**98 of 105 are >= 100 Hz** — the heavy tier is the workload, not an outlier. But only 11
stations are >= 200 Hz, which bounds the tail: even with all 11 running at once alongside 29
at ~1.5 GB, worst case is ~121 GB of 251 GB.

## Consequences for sizing

The previous failure was *not* "too many workers." Per-worker footprint was unbounded, so
no worker count would have been safe. With the cache bounded, 40 workers measures 55–68 GB
steady-state on a 251 GB machine, with a worst case of ~121 GB — so **cores bind before
memory does**, and the campaign relaunched at the same 40-way width it failed at.

Known headroom item, deliberately not applied mid-campaign: a single cache entry larger than
the cap is still retained (the eviction loop always keeps the just-inserted entry), which is
~3 GB on a 500 Hz station. With only two such stations pending it is not worth restarting
the run; revisit if a future campaign is dominated by >= 500 Hz data.

---

## Throughput, and one hypothesis that was wrong

After the fix, measured throughput is ~779 files/hr across 40 workers -> **~11.5 days** for
the 215,334 files remaining. Phase profile on `WI.MAGL` (100 Hz), measured under full
production load:

| phase | share |
|---|---|
| **response removal** | **93.9%** |
| detrend | 3.4% |
| filter | 1.7% |
| merge / read / write / decimate | <1% each |

**Refuted hypothesis:** that the 512 MB cap was starving the hit rate (one 100 Hz entry is
~207 MB, so the cap holds ~2, while a station has several channels each needing their own).
Tested at 512 MB vs 3072 MB over the same 6 files:

| Cap | Wall | resp | calls | misses | Hit rate | Entries |
|---|---|---|---|---|---|---|
| 512 MB | 450.4 s | 422.0 s | 12 | 5 | 58.3% | 3 |
| 3072 MB | 455.2 s | 426.7 s | 12 | 5 | 58.3% | 5 |

At 3 GB nothing was evicted (5 entries retained = 5 misses) and the hit rate was unchanged.
The misses are **intrinsic** -- 5 genuinely distinct `(channel, epoch, nfft)` keys among 12
calls -- not eviction. **512 MB is the correct cap**; raising it buys nothing.

> Measurement trap worth repeating: counting cache misses via `len(CACHE)` changing is
> wrong, because an insert that also evicts leaves the length unchanged. An earlier run
> reported 75% using that method; instrumenting the insert site directly gave 58.3%.

So the 58% hit rate is a consequence of the same nfft variability that caused the OOM.
Raising it would require canonicalising day length (pad/trim to an exact 86400 s boundary)
so every day shares one nfft -- worth perhaps ~30%, but it changes gap/edge handling and is
a **processing decision requiring PI approval**, not a tuning knob.

The irreducible remainder is deconvolution at native sampling rate, which cannot be moved to
a lower rate: decimate-then-deconvolve was tested and is mathematically wrong (correlation
0.57; a two-stage 2 Hz variant also failed at 0.65), because the instrument response's
gain/phase varies across the passband. Day canonicalisation aside, the one remaining lever
is not processing channels we never needed -- see below.

---

## The channel-selection gap (the largest single win, and how it was missed)

**PI, 2026-09-30:** *"always choose the channels with lowest sample rate -- that was clear
from early design requirements. If no LH, then BH, if no BH then HH."*

The rule was already encoded — but only in the **download** pipeline:

```python
# orchestrator.py:70  (BlueHive3 acquisition)
CHANNEL_PRIORITIES = os.environ.get("WAVENET_CHANNEL_PRIORITIES", "LH?,BH?").split(",")
```

`sam_package_one.py` packages the pre-existing SAmericaNoise **ROVER** archive, fetched by a
different and earlier process that took whatever channels existed. The packaging layer walks
files on disk and preprocesses *every* trace it finds; it implicitly assumed selection had
already happened upstream. One rule, two pipelines, applied in one of them.

Cost of that gap, measured like-for-like on one file of `IU.SAML`:

| Band | npts | s/trace | vs LH |
|---|---:|---:|---:|
| HH | 5,781,680 | 20.25 | 43x |
| BH | 287,415 | 7.16 | 15x |
| **LH** | 14,607 | **0.48** | 1x |

The product is 1 Hz, so the high-rate bandwidth was being deconvolved at full cost and then
discarded in decimation.

### The rule is NOT "lowest sample rate"

A naive lowest-rate rule is unsafe. Scanning the pending stations' metadata found **794
channels below 1 Hz**, and most are not ground motion:

```
LOG 0.0 Hz (datalogger text)   ACE 0.0 Hz (clock)   OCF 0.0 Hz
VM0-VM6 0.1 Hz (mass position) VCO/VEA/VEC/VEP/VKI 0.1 Hz
UH/UM/UK/UE 0.01 Hz            Q* 0.05 Hz
```

`VM0`-`VM6` offers seven components at 0.1 Hz, so it satisfies a ">=3 components, lowest
rate" rule and would beat LH — packaging **mass-position telemetry as displacement**. A first
version of this patch had exactly that bug; it passed a single-station test only because that
station's data files happened to contain just BH/HH/LH.

Two guards make it safe, and together they reproduce the intended order exactly:

1. **SEED instrument code (2nd char) must be `H`** — high-gain seismometer. Drops VM/LOG/ACE
   and the Q* family, plus accelerometers (`?N?`) and low-gain seismometers (`?L?`).
2. **Sample rate >= 1 Hz.** `VH?` *is* a high-gain seismometer but records at 0.1 Hz. The
   floor is Nyquist: the product is 1 Hz and the pipeline lowpasses at 0.4 Hz, so 1 Hz
   sampling (Nyquist 0.5 Hz) is the minimum that preserves the target band. LH sits exactly
   on that floor.

Survivors order LH(1) -> MH(5) -> BH(10-50) -> SH(20-50) -> HH(80-500) -> EH(100). If nothing
qualifies the stream is returned **unchanged**, never emptied.

### Verification

Six synthetic guard cases pass (VM vs LH, VH excluded, LN accelerometer excluded, LOG/ACE
junk, LH>BH>HH ordering, SOH-only returns unchanged). On real data, retained channels are
bit-identical to the full run:

| Station | Bands | Kept | Speedup | Retained datasets |
|---|---|---|---:|---|
| `G.HDC` | BH,LH | LH | **8.2x** | 6/6 identical |
| `IU.SAML` | BH,HH,LH | LH | **43.8x** | 6/6 identical |

### Projected campaign effect

The gain is **not** uniform — single-band stations have no cheaper fallback:

| Pattern | Example | Gain |
|---|---|---:|
| BH+HH+LH | IU.SAML, NU.CRIN, GE.BOAB | 58x |
| BH+LH | G.HDC, G.PEL, IU.OTAV | 16x |
| BH only / HH only | IU.SDV, GT.CPUP, WI.DHS, BL.RCLB | 1.0x |

Weighted across the pending work: **~2.0x overall**, so the measured ~11.5 days becomes
**~5.8 days**. (A per-band cost model put the absolute ETA at 3.2 days vs a measured 11.5;
the model understates absolute time by ~3.6x because it omits contention and I/O, so only
its *ratio* should be trusted.)

### Consistency note

The 792 already-packaged stations contain **all** bands — a superset, not a shortfall — so
nothing is lost by not reprocessing them. Downstream code should apply the same selection
rule when reading, rather than assuming one band per station.

The same misconception appears on the Bluehive3 side of this project, inverted: tasks there
request `--mem-per-cpu=2G` on nodes whose natural per-core share is ~7.6 GB (urseismo: 24
cores / 182 GB), and 511 of 2002 tasks were OOM-killed with `MaxRSS` pinned at the 2 GB
ceiling. Both cases came from sizing memory off core count rather than off measured
footprint.

> Note on `MaxRSS`: SLURM samples RSS periodically, so a short spike between samples is
> invisible. One killed task reported `MaxRSS` of 1260M against a 2048M limit. Do not size
> a request off `MaxRSS` alone.

---

## Operational notes

- **Disabling the station timeout means `WAVENET_STATION_TIMEOUT_S=0`, not a large number.**
  The driver maps `<= 0 -> None`. Passing `2592000` (30 days) re-triggers
  `OverflowError: timeout is too large`, because Python's selector `poll` overflows past
  ~24.8 days. This was re-discovered the hard way during this relaunch; the driver's own
  comment already warned about it.
- **`pkill -f sam_package_one.py` issued over SSH kills your own shell**, because the
  pattern matches the SSH command string. Collect PIDs with a bracketed grep
  (`grep '[s]am_package_one'`) and `kill` those instead.
- **Resume is safe.** Progress is checkpointed per file
  (`packaging_results/<net>.<sta>.done_files.json`) and per station
  (`<net>.<sta>.json`). OOM-killed stations write no result JSON, so they are retried
  automatically on the next run.
- **Swap does not drain on its own.** After the kill, RAM recovered (238 GB -> 25 GB used)
  but swap stayed at 4095/4095, held largely by an unrelated `java` service. Clearing it
  needs `sudo swapoff -a && sudo swapon -a` (password-gated on terravibranium). Worth doing
  so future pressure has a buffer, though with the leak fixed nothing should approach it.

---

## Files

| Path | Role |
|---|---|
| `/tmp/sam_package_one.py` (terravibranium) | Patched worker |
| `/tmp/sam_package_one.py.bak_precache` | Pre-patch reference, kept for comparison |
| `/tmp/sam_package_driver2.py` | Driver (unchanged) |
| `/RAID6/lab_archive/sam_packaging_run.log` | Campaign log |
| `/RAID6/lab_archive/packaging_results/` | Per-station results + per-file checkpoints |

State at relaunch (2026-09-30 11:08 EDT): 105 stations pending, 262,419 files, 40 workers.
