# singleNCFtest

Validated ambient-noise cross-correlation pipeline reproducing **ADAMA Figure 3** (Xue & Olugboji 2022, SRL) for the station pair `XD.RUNG — XD.MTAN`.

Runs on **GeoLab (EarthScope JupyterHub)**. NoisePy is not installed locally on Windows.

---

## Pipeline Flow

```
EarthScope S3
  ↓ download_pairs.py          — downloads overlapping MiniSEED days
Local MiniSEED (raw_data/)
  ↓ preprocess.py              — response removal, detrend, decimate to 1 Hz
  ↓ datastore.py               — loads preprocessed streams into RAM (InMemoryDataStore)
  ↓ xcorr_pairs.py             — NoisePy cross_correlate() → per-day NCF HDF5 files
NCF_output/{pair}/
  ↓ rotate_and_stack.py        — NEZ → RTZ rotation + PWS stacking
  ↓ compare_plot.py            — overlay against ADAMA reference cross-spectra
```

All steps are orchestrated by `stream_xcorr_pipeline.py`.

---

## Files

| File | Role |
|------|------|
| `config.py` | Pipeline parameters (sampling rate, freq bands, NoisePy config) |
| `preprocess.py` | Instrument response removal, decimation, channel selection |
| `datastore.py` | In-memory NoisePy-compatible data store |
| `download_pairs.py` | Parallel MiniSEED downloader from EarthScope S3 |
| `xcorr_pairs.py` | Cross-correlation worker (chunked preprocessing + NoisePy) |
| `rotate_and_stack.py` | NEZ→RTZ rotation and phase-weighted stacking |
| `stream_xcorr_pipeline.py` | End-to-end pipeline entry point |
| `merge_ncf.py` | Post-processing utility to merge per-pair `*_ncf.h5` into one archive |
| `compare_plot.py` | Validation: NoisePy vs ADAMA cross-spectra comparison plot |

---

## How to Run

```bash
# Full pipeline: download + cross-correlate + rotate/stack
python stream_xcorr_pipeline.py \
    --pairs ../metadata/XD.RUNG_XD.MTAN.csv \
    --keyindex ../metadata/keys_partitioned_year/ \
    --outdir raw_data \
    --ncfdir NCF_output

# Validate against ADAMA Figure 3
python compare_plot.py \
    --noisepy NCF_output/XD.RUNG_XD.MTAN_ncf.h5 \
    --adama ADAMA_ncfs_ZZ_fr.h5 \
    --pair XD.RUNG-XD.MTAN \
    --sensor LH_BH \
    --out compare_plot.png

# For multiple ncfs: merge per-pair files into a single archive
python merge_ncf.py --indir NCF_output --out Wavenet_ncfs.h5
```

Use `--skip-download` if `raw_data/` is already populated. Use `--pair-index` to process a single row for SLURM job arrays.

---

## Output HDF5 Schema

Each `*_ncf.h5` file follows this structure:

```
{pair_label}/                 attrs: dt, maxlag, stack_days
  {sensor_key}/               attrs: n_windows_ZZ, n_windows_RR, n_windows_TT
    freq_axis                 1D float64 — frequency bins (Hz)
    time_axis                 1D float64 — lag time axis (s), -maxlag to +maxlag
    ZZ/
      time_domain             1D float64
      cross_spectrum          1D complex128
    RR/  (same layout)
    TT/  (same layout)
```

---

## Key Configuration (`config.py`)

| Parameter | Value | Notes |
|-----------|-------|-------|
| `sampling_rate` | 1.0 Hz | Target after decimation |
| `freqmin` | 1/60 Hz | ADAMA passband lower bound |
| `freqmax` | 1/3 Hz | ADAMA passband upper bound (plot only) |
| `cc_len` | 14400 s | NoisePy sub-window length (4h) |
| `step` | 7200 s | Sub-window step (2h overlap) |
| `maxlag` | 7200 s | Max cross-correlation lag |
| `chunk_days` | 30 | Days per preprocessing chunk — raise if RAM allows |

NoisePy is configured with `FreqNorm.RMA` + `TimeNorm.RMA`. `FreqNorm.WHITEN` 
