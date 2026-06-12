# Project WaveNet: End-to-End Synthetic Seismology Pipeline

## Executive Summary
Project WaveNet is a state-of-the-art hybrid pipeline designed to overcome the physical limitations of real-world seismic data collection. By combining high-performance cluster computing with deep neural networks, this project creates an end-to-end factory for generating, processing, and learning from synthetic seismograms.

The project is cleanly divided into two distinct, but tightly coupled, engines:

### 1. The Data Generation Engine (WaveSim)
Because collecting 100,000 distinct, high-resolution earthquake readings across the globe is physically impossible, we use the university's Bluehive supercomputer to simulate them.
- **Goal:** Robustly compute massive arrays of synthetic seismograms using Instaseis and MPI.
- **Scale:** Over 100,000 unique crustal geometries and waveforms.
- **Documentation:** See the **WaveSim Architecture Wiki** for details on the Orchestrator, Slurm routing, and fail-safe data bridging.

### 2. The Machine Learning Engine (FTAN_ML)
Once the synthetic data factory produces the traces, the Machine Learning Engine consumes them to train predictive models.
- **Goal:** Train a 1D Convolutional Neural Network (CNN) to perform Full Tensor Analysis (FTAN) and seismic segmentation.
- **Process:** Converts `.sac` traces into NumPy arrays, feeds them into TensorFlow, and outputs highly accurate predictive models.
- **Documentation:** See the **Machine Learning Pipeline Wiki** for details on data preprocessing, model architecture, and evaluation.

## Workflow Integration
1. **Simulation Phase:** The Master Orchestrator generates massive synthetic datasets via Slurm arrays and saves them to `outputs_v2/`.
2. **Bridge Phase:** The geometries and parameters from the simulations are logged into `metadata.csv`.
3. **Training Phase:** The ML pipeline reads `metadata.csv` and the generated `.sac` files to train the neural network to identify crustal structures.
