# Implementation Plan: Incremental HDF5 ML Aggregation

## Goal
To parse the massively parallel synthetic waveforms across the Bluehive directories, compute their FTAN matrices, and securely append them into an incremental, high-performance `HDF5` database. This dataset will permanently archive the raw waveforms while supplying the exact `(80, 400)` matrices expected by the `UNetSeg` model.

> [!TIP]
> **Download the Dataset:** You can download the fully packaged 1.7GB `wavenet_training_data.h5` or the 11GB 'wavenet_training_data.h5 file directly from our secure NAS server:
> [Download WaveNet Training Data (HDF5)](https://repovibranium.synology.me:5001/fsdownload/Vl16kNsdR/traindatawavenet)

## Proposed Architecture

### 1. Incremental Data Aggregation Strategy
Because simulations are actively running (only ~5% complete), the pipeline must be stateful.
- **State Tracking:** The HDF5 file will contain a dedicated dataset/group tracking all successfully ingested `simulation_id` strings.
- **Append Mode:** When the aggregator runs, it will load the existing HDF5 file, cross-reference the `outputs_v2/` directory, and only process new, unseen `.txt` waveforms. 

### 2. The HDF5 Database Structure
The unified `wavenet_training_data.h5` schema will now permanently harbor both raw and processed data to prevent future deep scans.
```text
/
├── raw_waveforms      (Dataset shape: [N, time_steps, 6_channels], dtype: float32)   <- Stores R1 (E, N, Z) & R2 (E, N, Z)
├── ftan_inputs        (Dataset shape: [N, 80, 400], dtype: float32)
├── target_masks       (Dataset shape: [N, 80, 400], dtype: uint8)
├── theoretical_curves (Dataset shape: [N, num_periods], dtype: float32)              <- 1D dispersion curve from SREGN.ASC
├── sdispl_curves      (Dataset shape: [N, num_periods], dtype: float32)              <- 1D Love wave curve from SDISPL.ASC
├── sdispr_curves      (Dataset shape: [N, num_periods], dtype: float32)              <- 1D Rayleigh wave curve from SDISPR.ASC
├── velocity_models    (Dataset shape: [N, num_layers, num_params], dtype: float32)   <- 1D velocity structure from model.d
└── metadata/
    ├── simulation_id (Dataset shape: [N], dtype: string)
    ├── domain        (Dataset shape: [N], dtype: string)   <- Extracted from map_topology.json
    ├── distance_km   (Dataset shape: [N], dtype: float)
    ├── radius_range  (Dataset shape: [N], dtype: string)
    ├── azimuth_range (Dataset shape: [N], dtype: string)
    ├── stack_length  (Dataset shape: [N], dtype: int32)    <- Extracted from WAVE_SIM_meta.txt
    ├── delta         (Dataset shape: [N], dtype: float32)
    └── processed_log (Dataset shape: [N], dtype: string)   <- Timestamp log for incremental runs
```

### 3. Data Transformation Logic
- **Raw Archive:** The 6 distinct synthetic waveforms (R1_E, R1_N, R1_Z, R2_E, R2_N, R2_Z) are bound into a `[time_steps, 6]` matrix and loaded into the `raw_waveforms` block. The exact `time_steps` dimension will be dynamically inferred from the `STACK_LENGTH` metadata attribute and rigorously confirmed via NumPy array shapes during load.
- **FTAN Computation:** We execute `pycwt.cwt` using the exact identical logic extracted from the legacy codebase (`FTAN_ML_array.py -> compute_ftan()`). This mapping will be heavily documented directly in the Python source.
- **Guidance Row (Documentation):** A Gaussian bump row is injected at row index 76 to provide a soft spatial hint to the network. *(Origin tracking: This logic was originally authored in `FTAN_ML_array.py` inside the `build_observed_array()` function on lines 166-201)*.
- **Mask Generation & Curve Archival:** We extract the theoretical 1D dispersion curve from `SREGN.ASC`. We use it to construct the binary mask `(±2 bins)` as the target label `Y`, AND we permanently save the raw 1D curve itself into the `theoretical_curves` HDF5 block. Additionally, the `SDISPL.ASC` and `SDISPR.ASC` 1D curves, along with the 1D velocity structure from `model.d` (`[num_layers, num_parameters]`), are parsed and securely stored in their respective dedicated datasets for total archiving completeness.

### 4. The HDF5 Toolset (Robust Architecture)
To ensure the HDF5 file is robust to future additions, we will not just write a raw script. We will build a complete Python utility module (`h5_wavenet_tools.py`) containing:
1. `class HDF5Writer(IncrementalAppender)`: Handles the chunked, thread-safe appending of new simulations.
2. `class HDF5Reader(PyTorchDataset)`: A ready-made class for PyTorch dataloaders to stream the dataset.
3. `def query_attributes()`: A CLI function to print the size, schema, and metadata distribution of the current database.

### 5. Execution & Testing (`submit_dataset_builder.sh`)
- The aggregator will utilize `ProcessPoolExecutor` to crunch through text files in parallel.
- We will strictly run the first iteration on the Bluehive `debug` node to process the first 100 simulations, verifying the writer and appending logic.

## Verification Plan
### Automated Tests
- Run the aggregator twice on the `debug` node. The second run should safely skip the first 100 simulations, proving the incremental append logic works without duplication.

### Manual Verification
- We will use the `query_attributes()` utility to print the internal state of the HDF5 file.
- Hook the PyTorch `HDF5Reader` class directly into your `U_NET_array.py` to prove that the loss function natively accepts the data.
