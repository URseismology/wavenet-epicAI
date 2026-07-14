# ML_pipeline

U-Net segmentation pipeline for extracting Rayleigh wave group velocity dispersion curves from synthetic FTAN images.

Trains on `wavenetv2_dataset_10k_full.h5` (~10,000 synthetic simulations). Runs locally or on any CUDA-capable machine — no GeoLab dependency.

---

## Pipeline Flow

```
wavenetv2_dataset_10k_full.h5
  ↓ SimDataset (U_NET_array.py)
    regrid FTAN → (80 × 300), build binary mask from theoretical curve
  ↓ UNetSeg
    encoder (16→32→64→128) + bottleneck (256) + decoder with skip connections
  ↓ CombinedLoss
    Focal (0.3) + Dice (0.3) + WeightedBCE (0.2) + Sharpening (0.2)
  ↓ checkpoint.pt
    predicted dispersion curve mask over period 1–20 s, velocity 2–5 km/s
```

---

## Files

| File | Role |
|------|------|
| `U_NET_array.py` | Dataset, model, loss functions, training loop |
| `h5_wavenet_tools.py` | HDF5 reader/writer for the dataset |
| `verify_main.py` | 6-panel verification: source geometry, FTAN, mask, velocity profile, CCF, coherence |
| `inspect_h5.py` | Prints dataset schema and group structure |
| `plot_ftan.py` | Plots individual FTAN dispersion images |

---

## How to Run

```bash
# Inspect the HDF5 schema
python inspect_h5.py wavenetv2_dataset_10k_full.h5
python inspect_h5.py wavenetv2_dataset_10k_full.h5 dive   # deep dive into first simulation

# Verify dataset contents (generates PNG frames)
python verify_main.py --h5 wavenetv2_dataset_10k_full.h5 --outdir verify_out/

# Train the U-Net
python U_NET_array.py
# → saves model_best.pt and training_curves.png to FTAN_SEG_MODELS/run_<timestamp>/
```

---

## HDF5 Schema (`wavenetv2_dataset_10k_full.h5`)

```
simulations/
  {sim_key}/                           e.g. "M01_0000"  (domain_index)
    theoretical/
      period                           1D float32 — period axis (s), fundamental mode
      group_velocity_dispersion        1D float32 — group velocity (km/s)
      phase_velocity_dispersion        1D float32 — phase velocity (km/s)
    velocity_profile/
      VP_kms                           1D float32 — P-wave velocity layers (km/s)
      VS_kms                           1D float32 — S-wave velocity layers (km/s)
      H_km                             1D float32 — layer thicknesses (km)
    geometries/
      separation_{dist}km/             e.g. "separation_127.0km"
        ccf_isotropic/
          CCF_ZZ                       1D float32 — time-domain CCF (isotropic average)
          COH_REAL_ZZ                  1D float32 — real part of frequency coherence
          freqs_hz                     1D float32 — frequency axis (Hz)
        empirical_ftan_dispersion/
          FTAN_ZZ                      2D float32 — raw FTAN image (period × velocity)
          period_s                     1D float32 — period axis of raw FTAN (s)
          velocity_kms                 1D float32 — velocity axis of raw FTAN (km/s)
```

---

## Model Input / Output

| | Shape | Description |
|---|---|---|
| Input | `(1, 80, 300)` | FTAN regridded to period 1–20 s (76 bins) + 4 zero-pad rows, velocity 2–5 km/s (300 bins). Per-row normalized. |
| Target | `(1, 80, 300)` | Binary mask — ±2 velocity bins around the theoretical group velocity curve. Zero in pad rows. |
| Output | `(1, 80, 300)` | Raw logits — apply `sigmoid` externally for probabilities. |

---

## Key Configuration

| Parameter | Value | Notes |
|-----------|-------|-------|
| `PERIOD_BINS` | 76 | Period range 1–20 s at 0.25 s spacing |
| `VEL_BINS` | 300 | Velocity range 2.0–5.0 km/s |
| `PAD_ROWS` | 4 | Zero-padding to make height a power of 2 (80 total) |
| `MASK_WIDTH` | 2 | Half-width of dispersion curve mask in velocity bins |
| `features` | (16, 32, 64, 128) | UNet encoder channel widths |
| `batch_size` | 16 | |
| `learning_rate` | 1e-4 | Adam with weight decay 1e-5 |
| `patience` | 25 | Early stopping patience (epochs) |

---

## Data Split

Simulations are grouped into **families** by the first 3 characters of the key (e.g. `M01`, `M02`). The 70/30 train/val split is done at the family level — all simulations from a given family go entirely into train or val. This prevents data leakage between structurally similar velocity models.
