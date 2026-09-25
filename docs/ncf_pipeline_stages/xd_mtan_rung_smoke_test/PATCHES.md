# Patches to `00_waveform_acquisition` from the XD.MTAN / XD.RUNG smoke test

**Base:** `add-september-ncf-pipeline` @ `ba845b6` (2026-09-25).
**Files changed:** `production/orchestrator.py`, `rover_download/build_master_h5.py` — 2 files changed, 137 insertions(+), 17 deletions(-). No other file is touched.
**Not applied in place.** The authors' source tree on the branch is unchanged. Apply `patches/all.diff` (`git apply`) or copy `patches/patched_files/*` over the originals.
Measured evidence for every claim below is in `REPORT.md`; raw outputs are in `evidence/`; the failing-before / passing-after tests are in `tests/`.

## How an evaluating agent can check this in ten minutes
1. `git apply --check patches/all.diff` on a clean checkout of the branch.
2. `pytest tests/test_patches.py` — 6 tests, all pass on the patched file. On the unpatched file the module cannot even import `align_to_integer_second`; step 3 shows the unpatched append behaviour directly.
3. Reproduce the two original-code bugs in isolation (no Bluehive needed): `code/neg_control.py` (set `BM` to the directory holding `build_master_h5.py`).
4. Audit timing on any station from raw headers with `code/timing_replay.py` (needs the raw miniSEED day files only).
5. Read `REPORT.md` §4 (findings) and §5 (fixes + verification), then §8 (universal vs specific).

## Patch 1 — integer-second alignment (`align_to_integer_second`, called in `orchestrator.py`)
* **Problem.** The per-station dataset grid is anchored at the first stored sample, which begins mid-day at an arbitrary sub-second phase; later days begin 0.0–0.05 s after midnight; `append_channel_data`'s `max(round(gap) − 1, 0)` can only *delay* data. Placement error is constant at `k × 0.05 s` (k = which 20 Hz sample started the record) between gaps and re-randomises after a real gap. Measured: RUNG +0.68 s, MTAN +0.21 s, steps of ≈ 1 s after gaps. → REPORT §4.1, `evidence/timing_replay_output.txt`, `evidence/diag_alignment_lag.txt`.
* **Change.** After decimation, apply a band-limited fractional delay (FFT) so sample 0 lies exactly on an integer UTC second and set `starttime` to it. The data are already low-passed at 0.4 Hz (< Nyquist), so the delay is exact to ~10⁻⁶ of the signal amplitude in the interior; the residual edge effect lies inside the taper zone.
* **Compatibility.** Shard/master `start_time` attributes become whole seconds; readers that index by `round(start − ds_start)` become exact. Intended for the 1 Hz output; harmless for data already on integer seconds.
* **Guard.** `test_align_puts_sample0_on_integer_second_without_distorting_the_signal`.
* **Result.** The measured shift between original and fixed data matches the replay-predicted placement error to a median of a few milliseconds over the full window (REPORT §4.1 figure); benchmark agreement with ADAMA improves without any downstream correction (REPORT §6).

## Patch 1b — trim small overlaps, skip stored data (`append_channel_data`, new `max_trim_samples=2`)
* **Problem.** When the previous day's data run past midnight, the next day's first decimated sample is placed at or before the stored end and the original code refused the **whole next day** for that channel. Two forms occurred: a day with one extra raw sample (1,728,001 → 86,401 decimated; the inclusive end sample, or two record segments joined across a one-sample gap) and a record straddling midnight (RUNG BHE/BHN on 1994-10-19 run 42 s into 1994-10-20). Here 5 of 318 station-days were affected — 4 lost on all three channels, 1 (RUNG 1994-10-20) on BHE and BHN — 14 of 954 channel-days. → REPORT §4.2 (table of the previous-day sample counts).
* **Change.** If the new data overlap the stored end by ≤ `max_trim_samples` whole samples, drop the coinciding leading samples of the new data and append the rest; if the new data are entirely inside stored data, return `SKIPPED` (idempotent re-append); larger overlaps are still `REFUSED`.
* **Compatibility.** Return strings gain `SKIPPED (...)` and a `trimmed N overlapping sample(s)` suffix on `appended`. `logger.py` (via `merge_channel`) only prints the status, so nothing breaks; the merge also benefits.
* **Guard.** `test_one_extra_boundary_sample_is_trimmed_not_refused`, `test_real_overlap_is_still_refused`, `test_reappend_of_stored_data_is_skipped_not_refused`, `test_real_gap_is_zero_filled`.

## Patch 2 — refused days stay visible (`orchestrator.py`)
* **Problem.** A refused trace was recorded in `day_errors`, but the day was still added to `done_days` and counted as processed — a resubmit would not retry it and the result JSON reported success.
* **Change.** A day with any refused trace is not marked done and increments `n_days_refused` (new result field); benign `SKIPPED` statuses go to `day_notes`.
* **Guard.** Full-window run: `n_days_refused == 0` (REPORT §5); the 4 originally fully-refused days pass in `evidence/fixed_test_verification.txt`; the fifth case (RUNG 1994-10-20, BHE/BHN) is covered by the full-window run (all three channels non-zero on that day in the fixed master).

## Patch 3 — per-day raw-quality sidecar (`orchestrator.py`, `_raw_qc`, `<shard>.qc.json`)
* **Problem.** Corrupt raw days (MTAN 1994-05-30 pegged at the int32 limit; spikes and recorder restarts on 05-28 / RUNG 05-29, 05-30) pass into the master file unflagged. → REPORT §4.3.
* **Change.** For every channel-day compute, from the *raw counts*, RMS, robust σ, `rms/robust σ`, max |x|, int32 saturation and the number of record segments; `flag` = saturated **or** ratio > 30 **or** > 1 segment. **Flags only — nothing is removed or altered.** Thresholds come from this data (quiet ≤ ~24, corrupt > 100) and should be tuned; large earthquakes also trip the heavy-tail test, so `flag` means *suspect*.
* **Result.** Flags MTAN 05-28 (ratio 102.6, 2 segments) and no quiet day in the 4-day test.

## Patch 4 — response-derived unit label (`orchestrator.py`, `_disp_units`)
* **Problem.** `remove_response(output="DISP")` returns displacement in the response's own length unit; both XD StationXML files declare NM/S, so data are nanometres but were labelled `units="m"` (10⁹ off). → REPORT §4.4. Not universal (6/6 other readable stations were M/S), so the fix detects rather than assumes.
* **Change.** Read the channel's declared input units; rescale NM/S, MM/S, UM/S (and the length equivalents) to metres; label unrecognised units explicitly and leave them unscaled.
* **Result.** Quiet-day RMS ≈ 7×10⁻⁷ m; ratio to the old label exactly 10⁹.

## Patch 6 — per-run configuration (`orchestrator.py`, environment variables; defaults unchanged)
`WAVENET_START`, `WAVENET_END`, `WAVENET_CHANNELS`, `WAVENET_CHANNEL_PRIORITIES`, `WAVENET_SKIP_DOWNLOAD=1` (reprocess raw files already on disk, no network), `WAVENET_DAY_START` / `WAVENET_DAY_END` (`YYYYMMDD`; process only a date range, for parallel chunks merged afterwards in chronological order), `WAVENET_TAPER_PCT`.
* **Problem.** Window (1970–yesterday) and channels (`BH?,LH?`, LH preferred) were hard-coded, so a specific station/window/channel test needed a code edit.
* **Result.** The fixed full-window run used exactly these variables (`code/run_fixed_chunk.sh`, `code/fixed_full.sbatch`).

## Line-level map (generated from the diff)
| file | original lines | patched lines | patch id | added / removed |
|---|---|---|---|---|
| `orchestrator.py` | 41-41 | 41 | 1 | +1 / -1 |
| `orchestrator.py` | 52-53 | 52-97 | 3, 4, 6 | +46 / -2 |
| `orchestrator.py` | 138-138 | 182 | 6 | +1 / -1 |
| `orchestrator.py` | 141-141 | 185 | 6 | +1 / -1 |
| `orchestrator.py` | 149-149 | 193 | 6 | +1 / -1 |
| `orchestrator.py` | 151-151 | 195 | 6 | +1 / -1 |
| `orchestrator.py` | 193 | 238 | 2 | +1 / -0 |
| `orchestrator.py` | 194 | 240 | 1b/2 | +1 / -0 |
| `orchestrator.py` | 219 | 266-279 | 3, 6 | +14 / -0 |
| `orchestrator.py` | 236 | 297-299 | 3 | +3 / -0 |
| `orchestrator.py` | 239 | 303 | 3 | +1 / -0 |
| `orchestrator.py` | 240 | 305 | 3 | +1 / -0 |
| `orchestrator.py` | 243 | 309 | 4 | +1 / -0 |
| `orchestrator.py` | 249 | 316-318 | 4 | +3 / -0 |
| `orchestrator.py` | 261 | 331 | 1 | +1 / -0 |
| `orchestrator.py` | 263-264 | 333-336 | 3, 4, 6 | +4 / -2 |
| `orchestrator.py` | 275 | 348 | 2 | +1 / -0 |
| `orchestrator.py` | 286-286 | 359 | 4 | +1 / -1 |
| `orchestrator.py` | 289-289 | 362 | 4 | +1 / -1 |
| `orchestrator.py` | 291 | 365-367 | 1b/2 | +3 / -0 |
| `orchestrator.py` | 296 | 373 | 2 | +1 / -0 |
| `orchestrator.py` | 300 | 378-382 | 2 | +5 / -0 |
| `orchestrator.py` | 316-316 | 398-400 | 2, 3 | +3 / -1 |
| `build_master_h5.py` | 24 | 25-48 | 1 | +24 / -0 |
| `build_master_h5.py` | 26-26 | 50 | 1b | +1 / -1 |
| `build_master_h5.py` | 69-71 | 93-106 | 1b | +14 / -3 |
| `build_master_h5.py` | 97-97 | 132-133 | 1b | +2 / -1 |

Patch ids: **1** = integer-second alignment; **1b** = overlap trim / skip; **2** = refused days not marked done + counted; **3** = per-day raw-quality sidecar; **4** = response-derived unit label + scaling; **6** = env-configurable window/channels/day range/taper.


## Not patched — recommendations (not implemented or tested here)
1. **Edge taper.** `tr.taper(max_percentage=0.05)` on every day suppresses 72 min at both ends of every UTC day (hours 0 and 23 carry ≈ 0.35–0.5 of mid-day amplitude), and the zero-phase 1 h high-pass leaves transients of the same order. Correct fix: process each day with overlap padding (read ±N hours from the neighbouring raw files) and taper only at genuine record ends and gaps. `WAVENET_TAPER_PCT` only makes the current behaviour adjustable.
2. **`logger.py` writes `status="merged"` unconditionally**, ignoring the status returned by `merge_channel`; a refusal at the shard→master step is only printed.
3. **`inspector.py` checks presence / sample count / non-zero, not completeness.** Add: `n_days_refused == 0` from the orchestrator result, and `days packaged == days downloaded`. As it stands a dropped day is invisible end to end.
4. **Quality control at window level, not day level.** The corrupt days here are mostly a 5-day periodic event at ≈ 17:00:05 UTC on both stations (REPORT §4.3), lasting seconds to minutes; discarding whole days (35 of 157) is far more than needed, and masking the affected 3-hour windows is the better unit (not tested here). XD.RUNG BHN is persistently heavy-tailed (108 of 159 days above the flag threshold) and needs spike screening before transverse use.
5. Extend the timing audit (`code/timing_replay.py`) to a sample of the 2,000 stations before a production run.
6. If LH is available the tool already prefers it; native 1 Hz data should be spared from patch-1's problem (no decimation phase; expected from the mechanism, not tested here), so document that BH-only stations are the exposed set. IRIS metadata lists LH? channels for both XD stations over this window (`evidence/iris_channel_metadata.txt`); whether LH waveforms exist was not tested.

## Applied

Applied on 2026-09-25, commit `9edfb41` — all six patches (1, 1b, 2, 3, 4, 6), no
disagreements. Verified before applying (`git apply --check`, the 6 unit tests, and
`neg_control.py` against the live source) and re-verified the 6 tests against the
patched live source afterward. The live 2,000-station production run was confirmed
still mid-flight at apply time (170 tasks running/pending via `squeue`); this commit
touches only the git-tracked repo source, not the Bluehive deployment path the running
job actually executes from — rolling the fix into that live run is a separate decision.

## Behaviour changes to be aware of
| area | before | after |
|---|---|---|
| `start_time` attribute | arbitrary sub-second | whole UTC second |
| overlap ≤ 2 samples at append | day refused (all channels of the day lost) | leading samples trimmed, day kept |
| re-append of stored data | `REFUSED` | `SKIPPED` |
| refused day | marked done | not marked done, counted in `n_days_refused` |
| `units` attribute | `"m"` whenever a response was removed | `"m"` only when input units are converted; otherwise an explicit label |
| result JSON | — | adds `n_days_refused`, `n_days_qc_flagged`, `qc_sidecar`, `day_notes` |
| environment | fixed | overridable by `WAVENET_*` variables; defaults reproduce the old behaviour |
