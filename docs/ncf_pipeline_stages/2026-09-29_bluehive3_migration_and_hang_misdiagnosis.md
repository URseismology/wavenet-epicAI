# 2026-09-29 — BlueHive3 migration, and a hang that turned out not to be one

Two things happened on 2026-09-29 and they are easy to conflate, so this record separates
them: the NCF campaign moved from BlueHive to BlueHive3, and an apparent "multi-day hang"
was investigated, misdiagnosed, and then corrected. **The migration is a real and
justified win. The hang was mostly not real.** Both conclusions matter, and the second one
matters more, because the reasoning error is the kind that repeats.

---

## 1. What looked like a hang

Symptom (observed ~13:00): campaign throughput had collapsed — 4 stations completed in 5
hours. Investigation found 32 of 46 RUNNING array tasks had produced **no log output for
4+ hours**, some for **76 hours**, while sitting at **95-99% CPU**. A `gdb` backtrace on a
live process showed it deep inside ObsPy's compiled `evalresp` C code
(`fir_asym_trans` → `sincos` → `__sin_avx`), evaluating `nfreqs=1,724,878`.

That produced a confident-sounding but **wrong** conclusion: ~70% of the fleet is hung on
a pathological floating-point slowdown (the leading hypothesis was subnormal floats hitting
AVX's slow microcode path).

## 2. Why it was wrong

Two independent errors compounded:

**Error 1 — station misidentification.** Array task `31377596_0` was assumed to be manifest
index 0 (`PS.KOR`, 1,196 days). It is not. `master.py` splits the manifest into
work-balanced chunks and passes the true starting row via `WAVENET_IDX_OFFSET`; job
`31377596` was the `urseismo` chunk with **offset 1957**. So `_0` is index 1957 —
`AI.ORCD`, **7,918 days**. Every reproduction attempt was therefore run against the wrong,
much smaller station, which is exactly why nothing ever reproduced.

**Error 2 — "silent log" is not a hang signal.** `MassDownloader` logs continuously during
the *download* phase. Preprocessing emits **no log output at all**. So a station that is
healthily grinding through a multi-year preprocessing loop is indistinguishable, by log
silence, from a stuck one. High CPU meant it was *working*, not stuck — the opposite of how
it was read at the time.

## 3. What was actually happening

Mapping each "stuck" job to its true station and comparing against the then-current
preprocessing rate (~34 s per station-day) explains almost all of them:

| Job | True station | Days | Expected @34 s/day | Observed |
|---|---|---|---|---|
| 31377592_5 | PS.TSK | 12,001 | 114 h | ~77 h |
| 31377592_3 | PS.SYO | 10,960 | 104 h | ~77 h |
| 31377596_10 | US.HAWA | 8,901 | 85 h | ~74 h |
| 31377596_0 | AI.ORCD | 7,918 | **75.5 h** | **77.6 h** |
| 31377595_39 | US.AMTX | 7,579 | 72 h | ~9 h |

These are enormous multi-decade stations processing at the expected rate. Nothing was hung.

## 4. The residual anomaly — still open

Three cases are **not** explained by station size and remain genuinely unexplained:

| Job | Station | Days | Expected | Observed silence |
|---|---|---|---|---|
| 31377592_293 | `6H.CHID` | **90** | ~0.9 h | **59 h** (~65x) |
| 31377593_58 | `X5.SHMN` | 560 | ~5.3 h | 51 h (~10x) |
| 31377593_59 | `1E.CNF` | 557 | ~5.3 h | 50 h (~10x) |

If a real pathological-slowdown bug exists, it lives here, not in the big stations. These
are the stations to target for reproduction (BH3's own compute, via `salloc`/`smux`) —
small enough that a full run is cheap, and with a known-wrong runtime to compare against.

## 5. The actual fix was the response cache, not the migration

The real inefficiency was that `evalresp` was recomputed for **every day** of every
station, when it depends only on `(response epoch, delta, nfft)` — never on the waveform
data. Caching it per station (deployed 10:21) is bit-exact (verified `maxdiff=0.0`) and
measured in production:

| | Stations | Station-days | sec/day (median) |
|---|---|---|---|
| Pre-patch | 194 | 108,615 | **34.33** |
| Post-patch | 41 | 14,224 | **1.27** |

**27x median speedup**, closely matching the 25.3x measured in isolated bench testing.
Concretely: `PS.TSK` (12,001 days) drops from ~114 hours to ~4.2 hours. The apparent
"hang" was really "this station needs 3-5 days because we recompute the same response
~10,000 times," and it is now gone.

> [!IMPORTANT]
> **Do not add a timeout / circuit-breaker to cure this class of symptom.** The PI pushed
> back on that proposal and was right: a wall-clock cap conflates "hung" with "legitimately
> large" and would have killed correctly-working multi-decade stations. On the SAmericaNoise
> packaging side a 3 h cap had already destroyed 87 stations' work this way before it was
> removed. If stall detection is ever wanted, key it on **lack of progress** (checkpoint
> file not advancing), never on elapsed time — and have it *report*, not kill.

## 6. BlueHive3 migration — still justified on its own merits

Independent of the hang question, BH3 is genuinely better and the campaign now runs there:

- **Same CPU silicon** (Xeon Gold 6126 on both) but the newer stack (RHEL9, SLURM 24.05 vs
  16.05.9) measured **2-3x faster** on identical floating-point work — pointing at
  glibc/libm/kernel, not hardware.
- **Scheduling throughput**: ~46 concurrent tasks on old BH vs **260+** on BH3.
- `/scratch/tolugboj_lab` is **shared**, so no data migration; the same conda env and code
  ran unchanged. Same account, partition names, and `--qos` convention.
- Verified end-to-end before scaling: one real station (`BL.PP1A`) completed cleanly on BH3
  including FDSN downloads from compute nodes, which was the main unknown.

**Division of labour (PI, 2026-09-29):** BH3 for long production runs; **old BH kept in
rotation for tests, debugging and short jobs** — it still has more short-job capacity (91
`standard`, 21 `interactive`, 12 `debug` nodes vs BH3's 20 / 1 / none). Do not retire it.

## 7. Lessons worth carrying

1. **Resolve array-task index → station identity before investigating.** Offsets are real;
   `_0` rarely means row 0. One wrong lookup invalidated a whole day of reproduction work.
2. **Know what your logs actually cover.** Silence in a phase that never logs is not
   evidence of anything.
3. **High CPU means working, not stuck.** A genuinely blocked process sits near 0%.
4. **Check the cheap explanation first.** "This station is 12,000 days long" required no
   exotic hypothesis and explained nearly everything; subnormal-float AVX pathology was
   an interesting theory that predicted a reproduction which never happened.
5. **A fix that removes work beats a guard that hides its absence** — the cache made the
   problem disappear; a timeout would only have hidden it while destroying good runs.
