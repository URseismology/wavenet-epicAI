# XD.MTAN – XD.RUNG BHZ smoke test of the NCF acquisition pipeline

A real station pair (109.5 km apart, 1994-05-25 → 1994-10-30, `channel="BH?"`) was downloaded, preprocessed, packaged, merged, read back and cross-correlated with FastMSPEC, then benchmarked against ADAMA's Rayleigh phase velocity for the same pair.
The test found one defect that matters (day placement → ~1 s inter-station timing error and dropped days) and four smaller issues; **patched copies** of the two affected files were verified on the full window.

| if you want to… | read |
|---|---|
| the whole story with figures (humans) | [`REPORT.pdf`](REPORT.pdf) |
| the same, machine-readable | [`REPORT.md`](REPORT.md) |
| exactly what was changed, why, and the evidence per change | [`PATCHES.md`](PATCHES.md) |
| apply / audit the changes | [`patches/`](patches/) — `all.diff`, per-file diffs, `patched_files/`, `hunks.md` (line-level map) |
| re-run the checks on your own copy | [`tests/test_patches.py`](tests/test_patches.py), [`code/neg_control.py`](code/neg_control.py), [`code/timing_replay.py`](code/timing_replay.py) |
| the raw outputs behind each claim | [`evidence/`](evidence/) |
| small result files (metrics, coherence, NCF, per-day tables) | [`results/`](results/) |
| all figures | [`figures/`](figures/) |
| every script and Bluehive job file used | [`code/`](code/) |

**Nothing in the pipeline source was edited in place.** The patches are copies plus diffs against `add-september-ncf-pipeline` @ `ba845b6`.

**Headline numbers** (all computed from the files in `results/`; see REPORT §1):

| variant | days | correlation with ADAMA-predicted coherence | ADAMA zero crossings found | observed crossings that are real | median velocity offset vs ADAMA | 95th percentile of absolute coherence |
|---|---|---|---|---|---|---|
| Original pipeline, as packaged (the smoke test proper) | 156 | +0.65 | 8/12 | 0.67 | -2.3 % | 0.17 |
| Original + downstream timing correction | 153 | +0.83 | 12/12 | 1.00 | -0.5 % | 0.18 |
| **FIXED pipeline, as packaged (no downstream correction)** | 157 | +0.83 | 12/12 | 1.00 | -0.6 % | 0.17 |
| Fixed pipeline, outlier days removed | 122 | +0.88 | 12/12 | 1.00 | -0.7 % | 0.24 |
| Single-taper, original pipeline | all | +0.69 | 10/12 | 0.80 | -1.7 % | 0.19 |
| Single-taper, fixed pipeline | all | +0.87 | 12/12 | 1.00 | -0.2 % | 0.19 |
| item | value |
|---|---|
| stations / window / channel | XD.MTAN (−7.9073, 33.3203), XD.RUNG (−6.9372, 33.5180); 1994-05-25 to 1994-10-30; `BH?`; correlation on BHZ |
| where | Bluehive, isolated root `/scratch/tolugboj_lab/wavenet_ncf_xd_pair_test/` (production and canary roots untouched), partition `urseismo`, account `tolugboj_lab`; `instaseis` environment for the pipeline, `fastmspec_batch` for correlation |
| original run | `orchestrator.py` as a patched copy with **only four lines changed** (start/end date, channel list/priority — the tool hard-codes 1970–yesterday and `BH?,LH?`); `build_master_h5.py`, `load_master_h5.py` unmodified |
| fixed run | patched `orchestrator.py` + `build_master_h5.py` (§5), reprocessing the already-downloaded raw files in 5 date-range chunks per station, merged with the patched `build_master_h5.py` |
| correlation | FastMSPEC `compute_crosscorr_mtc_fastmspec`, bandwidth 0.001, 3-hour windows, 50 % overlap, 15 per UTC day |
| benchmark | ADAMA `ADAMAraw_co_ral.h5`, pair `XD.RUNG-XD.MTAN` (ZZ, Rayleigh), 0.028–0.196 Hz (periods 5–35 s), 3.28–3.77 km/s |
| station | download | download volume / time | preprocess | preprocess volume / time | package (.h5) |
|---|---|---|---|---|---|
| XD.MTAN | yes | 477 files, 902 MB, 1 min | yes | 159 days, 2.86 h | yes, 166 MB |
| XD.RUNG | yes | 477 files, 897 MB, 1 min | yes | 159 days, 2.89 h | yes, 183 MB |
| station, refused day | channels refused | previous day | previous-day raw samples after merging record segments (normal 1,728,000) | previous-day decimated samples (normal 86,400) | record segments | previous-day data run past midnight by (s) |
|---|---|---|---|---|---|---|
| XD.MTAN 19940920 | BHE, BHN, BHZ | 19940919 | 1,728,001 | 86,401 | 1 | 0.003 |
| XD.RUNG 19940603 | BHE, BHN, BHZ | 19940602 | 1,728,001 | 86,401 | 2 | 0.000 |
| XD.RUNG 19940916 | BHE, BHN, BHZ | 19940915 | 1,728,001 | 86,401 | 2 | 0.000 |
| XD.RUNG 19940923 | BHE, BHN, BHZ | 19940922 | 1,728,001 | 86,401 | 2 | 0.000 |
| XD.RUNG 19941020 | BHE, BHN | 19941019 | 1,728,847 | 86,443 | 1 | 42.347 |
| id | file | change | why | benefit (measured) |
|---|---|---|---|---|
| 1 | `build_master_h5.py` (new `align_to_integer_second`) + call in `orchestrator.py` | after decimation, shift each processed day (band-limited fractional delay) so sample 0 sits exactly on an integer UTC second | removes the k×0.05 s placement error of §4.1 | measured shift between original and fixed data equals the replay's prediction to a median of 0.0 ms (§4.1); benchmark in §6 |
| 1b | `build_master_h5.py::append_channel_data` | trim ≤ 2 coinciding leading samples of the new data instead of refusing the day; skip data already stored; still refuse larger overlaps | §4.2 | the 4 hard cases all pass; **0 refused days** on the full window (original: 4) |
| 2 | `orchestrator.py` | a refused day is not marked done and is counted (`n_days_refused`, `day_errors`); benign skips go to `day_notes` | a silently lost day | failure is visible in the result file |
| 3 | `orchestrator.py` | per-day raw-quality sidecar `<shard>.qc.json` (RMS / robust σ, int32 saturation, record segments, flag) — flags only, nothing removed | §4.3 | flags MTAN 05-28 (ratio 102.6, 2 segments) and no quiet day in the 4-day test (`evidence/fixed_test_verification.txt`); over the window BHZ is flagged on 30 (MTAN) and 52 (RUNG) of 159 days — mostly the 5-day periodic events of §4.3 — and RUNG BHN on 115 (a persistently heavy-tailed component, §4.3). Any channel: MTAN: 32 days, RUNG: 117 days |
| 4 | `orchestrator.py` | derive the output unit from the response input units; rescale NM/S→m; label unrecognised units instead of calling them "m" | §4.4 | quiet-day RMS ≈ 7×10⁻⁷ m; ratio to the old label exactly 10⁹ |
| 6 | `orchestrator.py` | `WAVENET_START/END/CHANNELS/CHANNEL_PRIORITIES/SKIP_DOWNLOAD/DAY_START/DAY_END/TAPER_PCT` | §4.6 (and reprocessing without re-download, date-range parallelism) | the fixed full run used exactly these |
| — | (not patched) taper | needs overlap-padded per-day processing | §4.5 | recommendation |
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
| variant | days | correlation with ADAMA-predicted coherence | ADAMA zero crossings found | observed crossings that are real | median velocity offset vs ADAMA | 95th percentile of absolute coherence |
|---|---|---|---|---|---|---|
| Original pipeline, as packaged (the smoke test proper) | 156 | +0.65 | 8/12 | 0.67 | -2.3 % | 0.17 |
| Original, outlier days removed | 121 | +0.69 | 8/12 | 0.67 | -2.5 % | 0.24 |
| Original + downstream timing correction | 153 | +0.83 | 12/12 | 1.00 | -0.5 % | 0.18 |
| Original + timing correction + outlier days removed | 118 | +0.88 | 12/12 | 1.00 | -0.7 % | 0.24 |
| FIXED pipeline, as packaged | 157 | +0.83 | 12/12 | 1.00 | -0.6 % | 0.17 |
| FIXED pipeline, outlier days removed | 122 | +0.88 | 12/12 | 1.00 | -0.7 % | 0.24 |
| Single-taper, original pipeline | all | +0.69 | 10/12 | 0.80 | -1.7 % | 0.19 |
| Single-taper, fixed pipeline | all | +0.87 | 12/12 | 1.00 | -0.2 % | 0.19 |
| picker reference curve | coherence used | converged | frequency coverage | picked band (Hz) | mean offset vs ADAMA | root-mean-square offset |
|---|---|---|---|---|---|---|
| flat 3.5 km/s | original, as packaged | yes | 0.95 | 0.033-0.178 | -3.1 % | 3.2 % |
| flat 3.5 km/s | original, outliers removed | yes | 0.95 | 0.031-0.178 | -3.2 % | 3.4 % |
| flat 3.5 km/s | original + timing correction | yes | 0.95 | 0.033-0.187 | -1.6 % | 2.2 % |
| flat 3.5 km/s | original + timing corr. + outliers removed | yes | 0.95 | 0.031-0.187 | -2.0 % | 2.6 % |
| flat 3.5 km/s | FIXED pipeline | yes | 0.95 | 0.033-0.187 | -1.7 % | 2.3 % |
| flat 3.5 km/s | fixed pipeline, outliers removed | yes | 0.95 | 0.031-0.187 | -2.9 % | 3.8 % |
| flat 3.5 km/s | single-taper (original) | **no** |  |  |  |  |
| ADAMA c(f) | original, as packaged | yes | 0.95 | 0.033-0.178 | -2.1 % | 2.2 % |
| ADAMA c(f) | original, outliers removed | yes | 0.95 | 0.031-0.178 | -3.0 % | 3.1 % |
| ADAMA c(f) | original + timing correction | yes | 0.95 | 0.033-0.187 | -0.0 % | 0.9 % |
| ADAMA c(f) | original + timing corr. + outliers removed | yes | 0.95 | 0.031-0.187 | -0.9 % | 1.5 % |
| ADAMA c(f) | FIXED pipeline | yes | 0.95 | 0.033-0.187 | -0.2 % | 0.8 % |
| ADAMA c(f) | fixed pipeline, outliers removed | yes | 0.95 | 0.031-0.187 | -1.3 % | 2.1 % |
| ADAMA c(f) | single-taper (original) | **no** |  |  |  |  |
| variant | causal peak lag | acausal peak lag | asymmetry (0 = symmetric) | implied group velocity |
|---|---|---|---|---|
| Original pipeline, as packaged (the smoke test proper) | +34.23 s | -35.31 s | -1.08 s | 3.20 / 3.10 km/s |
| Original, outlier days removed | +26.51 s | -35.34 s | -8.83 s | 4.13 / 3.10 km/s |
| Original + downstream timing correction | +35.14 s | -34.47 s | +0.67 s | 3.12 / 3.18 km/s |
| Original + timing correction + outlier days removed | +27.44 s | -34.47 s | -7.04 s | 3.99 / 3.18 km/s |
| FIXED pipeline, as packaged | +35.12 s | -34.46 s | +0.66 s | 3.12 / 3.18 km/s |
| finding | universal? | reason |
|---|---|---|
| day-placement timing error (§4.1) | **mechanism universal, size station-specific** | every station's grid is anchored to a mid-day first sample at an arbitrary sub-second phase; error = k×0.05 s, k uniform over 0…19 for 20 Hz data. For a random pair: RMS offset **0.41 s**, > 0.25 s for **54 %** of pairs, > 0.5 s for **23 %**. Native 1 Hz (LH) channels should be spared (no decimation phase) — expected from the mechanism, not tested here. Matches both stations here (k = 4 → +0.20 s, k = 14 → +0.70 s). |
| dropped days (§4.2) | universal logic, data-dependent rate | any day whose data run past midnight (an extra end sample, or a record straddling midnight); here 5 of 318 station-days (4 lost on all channels, 1 on two) |
| 5 % taper (§4.5) | universal, deterministic | hard-coded for every day of every station |
| unit label (§4.4) | conditional | depends on the input units in each StationXML; 6 of 6 other readable stations were M/S |
| corrupt raw days (§4.3) | universal in kind, specific in which days | every archive has some; here mostly a 5-day periodic event at both stations (§4.3), plus recorder restarts and one saturated day; earthquake days also trip a heavy-tail flag, so the flag means "suspect", not "bad" |
| hard-coded parameters (§4.6) | universal by design | |

**Conventions.** Times are UTC. "Original pipeline" = the branch as found (with only the download window and channel list overridden in a copy of `orchestrator.py`); "fixed pipeline" = patched `orchestrator.py` + `build_master_h5.py`, applied to the same raw files. Bluehive root used: `/scratch/tolugboj_lab/wavenet_ncf_xd_pair_test/` (production and canary roots were not touched).
