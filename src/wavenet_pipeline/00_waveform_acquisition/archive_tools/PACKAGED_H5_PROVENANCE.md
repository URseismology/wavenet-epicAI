# packaged_h5_ncf — what this data is, and what it is not

**This is NOT the NCF (noise cross-correlation function) product.** It is the
**preprocessed waveform data that NCF computation is built from**: per-station,
per-channel-day HDF5, instrument response removed (DISP output, water level 60),
decimated to 1 Hz, high-pass filtered above 1/3600 Hz, lowpass at 0.4 Hz where the
native rate exceeds 0.8 Hz. One file per station: `<network>.<station>.h5`.

No cross-correlation, no stacking, no dispersion analysis has been applied to this
data. It is an intermediate product, one stage upstream of the actual NCF pipeline.

## Provenance

- **Source repo**: `github.com/URseismology/wavenet-epicAI`,
  `src/wavenet_pipeline/00_waveform_acquisition/` (unmerged branch
  `add-september-ncf-pipeline` at the time this mirror began; later merged to `main`)
- **Origin machine**: BlueHive3 (`bluehive3.circ.rochester.edu`), account `tolugboj`,
  account `tolugboj_lab`
- **Campaign root(s) mirrored**: see `MANIFEST.md` in this directory for which
  campaign run(s) (`wavenet_ncf_production`, `wavenet_ncf_production_v2`, ...)
  contributed each file, and when. No distinction is drawn between v1/v2/v3 shards
  here — this directory is the union of every verified shard produced so far,
  regardless of which campaign root produced it (PI, 2026-09-30).
- **Station set**: farthest-point-sampled, 2,000-station (later 1,999-station, v2)
  global network defined in `metadata3/fps_stations.csv` /
  `fps_stations_v2.csv`.
- **Processing code version**: recorded per-transfer in `MANIFEST.md` by git commit
  hash of `production/orchestrator.py` at the time each batch was packaged, since the
  channel/location-priority defaults changed 2026-09-30 (see
  `docs/ncf_pipeline_stages/` in the source repo for the full history).

## What is intentionally absent

- Raw miniSEED (downloaded, preprocessed, then discarded or purged by the
  campaign's `inspector.py` once a station is verified complete — see the source
  repo's `docs/ncf_pipeline_stages/PROGRESS.md`).
- Any cross-correlation, stacking, or dispersion-curve product. That is future
  work, downstream of this data, not yet built as of this mirror.

## Full history / provenance / quality issues

For the complete account of how this dataset was built — station selection,
channel and location-code fixes, replacement stations, known data-loss events,
memory/performance fixes in the packaging code, and anything else materially
affecting what is or isn't in this archive — see
`docs/ncf_pipeline_stages/` in the source repo, not just this file. This README
is a pointer and a provenance summary, not the authoritative record.
