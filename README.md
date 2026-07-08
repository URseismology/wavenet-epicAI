# WaveNet-EpicAI

Welcome to the central repository for the WaveNet-EpicAI project. This repository houses the unified codebase for launching massive parallel WaveSim simulations on Bluehive, efficiently packaging the outputs into HDF5 datasets, and training PyTorch U-Net models for structural seismology.

## 🚀 Collaborators Start Here

If you are joining the project, please start by reviewing our active development priorities and task assignments in the roadmap:
👉 **[Collaborative Roadmap](docs/Collaborative_Roadmap.md)**

## Documentation Directory

Our detailed architectural documentation is housed in the `docs/` folder:
- **[HDF5 Dataset Architecture](docs/HDF5_Dataset_Architecture.md)**: Detailed schema of the two `.h5` files and PyTorch streaming logic. *(Includes Direct Download Link)*
- **[Machine Learning Pipeline](docs/README_MachineLearning.md)**: Overview of the legacy `.npy` flows and the modern PyTorch transitions.
- **[WaveSim Architecture](docs/README_WaveSimArchitecture.md)**: Breakdown of the Bluehive HPC autosubmission and job-diffing components.

## Source Code Structure

- **`/src/simulation_runner`**: Robust Bluehive deployment scripts (`launch_wavesim_auto.sh`, `generate_jobmap_diff.py`).
- **`/src/data_processing`**: High-performance HDF5 aggregation tools (`build_ml_dataset.py`, `h5_wavenet_tools.py`).
- **`/src/machine_learning`**: PyTorch models and array scripts (`U_NET_array.py`).
- **`/legacy_scans`**: Isolated, timestamped archives of earlier Bluehive workflows and topologies for historical reference.
