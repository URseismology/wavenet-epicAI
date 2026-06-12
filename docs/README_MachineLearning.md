# Seismic Data Analysis and Machine Learning Pipeline Wiki

## 1. Project Overview

### Introduction
This project is designed to analyze seismic data using machine learning techniques, specifically focusing on Full Tensor Analysis (FTAN) methods. The pipeline includes preprocessing raw seismic data, training machine learning models, evaluating model performance, and making predictions on new data. The project is structured to run efficiently in a high-performance computing (HPC) environment, leveraging tools like TensorFlow for deep learning and Slurm for job management.

### Objectives
- **Data Preprocessing**: Convert raw seismic data into a format suitable for machine learning.
- **Model Training**: Train neural networks to segment and analyze seismic data.
- **Evaluation**: Assess model performance using validation datasets.
- **Prediction**: Use trained models to make predictions on new, unseen data.

### Key Components
- **Data Directory**: `FTAN_ML_INPUT` - Contains raw and preprocessed seismic data files.
- **Metadata File**: `metadata.csv` - Stores information about the dataset.
- **Output Directory**: `FTAN_ML_MODELS_4CH_SHARP` - Saves trained models and results.

## 2. Data Architecture

### Data Structure
The project's data is organized into specific directories and files, each serving a distinct purpose:

#### Input Data Directory (`FTAN_ML_INPUT`)
- **Raw Seismic Data**: Files in formats like `.sac` containing raw seismic traces.
- **Metadata File**: `metadata.csv` - Contains information about each seismic trace, such as timestamps, locations, and other attributes.

#### Output Data Directory (`FTAN_ML_MODELS_4CH_SHARP`)
- **Trained Models**: Saved model checkpoints and final models.
- **Logs and Metrics**: Logs of training metrics, validation results, and performance evaluations.
- **Predictions**: Results from applying the trained models to new data.

### Data Patterns
- **Seismic Data Files**:
  - Pattern: `*.sac` - Raw seismic traces in SAC format.
- **Metadata File**:
  - Filename: `metadata.csv`
  - Columns: 
    - `file_name`: Name of the corresponding .sac file.
    - `timestamp`: Time of data acquisition.
    - `location`: Geographic location where the data was collected.
    - `label`: Ground truth labels for supervised learning tasks.

### Example Data Flow
1. **Raw Seismic Data**: `.sac` files are stored in `FTAN_ML_INPUT`.
2. **Metadata**: `metadata.csv` provides additional information about each `.sac` file.
3. **Preprocessed Data**: Preprocessing scripts convert `.sac` files into a format suitable for machine learning, such as NumPy arrays or TensorFlow datasets.
4. **Trained Models**: Trained models are saved in `FTAN_ML_MODELS_4CH_SHARP`.
5. **Evaluation Results**: Performance metrics and logs are stored in the output directory.

## 3. Code Reference

### Modern Data Aggregation & PyTorch Workflow (v2)
The project has transitioned from `.npy` files to a unified HDF5 dataset structure to support efficient data loading and PyTorch integration.

> [!IMPORTANT]
> **Complete Database Schema:** For a deep dive into the HDF5 schema (`ftan_inputs`, `target_masks`, `velocity_models`) and future PyTorch U-Net integration, please read the [HDF5 Dataset Architecture Documentation](./HDF5_Dataset_Architecture.md).

**1. HDF5 Aggregation (`h5_wavenet_tools.py`)**
This module defines the `HDF5Writer` and `HDF5Reader` classes. 
- The schema stores `raw_waveforms`, `ftan_inputs`, `target_masks`, and `velocity_models` with a metadata log.
- `HDF5Reader` acts as a PyTorch Dataset wrapper, allowing direct streaming of batches during training without loading the 1.7GB+ dataset into memory.

**2. Automated Dataset Packaging (`build_ml_dataset.py` & `submit_dataset_builder.sh`)**
These scripts are used to automatically extract features (FTAN) from the raw `WAVE_SIM` output folders on Bluehive.
- `build_ml_dataset.py` runs in parallel, diffs the current HDF5 file against the raw outputs, and efficiently appends only the new simulations.
- `submit_dataset_builder.sh` is the SLURM job submission wrapper.

> **Future Architecture Blueprint (Cron Automation):**
> A future script `auto_hdf5_submit.sh` should be created and scheduled via the Bluehive user `crontab` (e.g. `0 */12 * * *`). It should execute `squeue -u $USER | grep wavenet_hdf5_builder`. If empty, it automatically triggers `sbatch submit_dataset_builder.sh`. Because the Python builder is "state-aware" (it parses `.h5` natively), this creates a bulletproof auto-appending pipeline that never duplicates data.

**3. PyTorch Model (`U_NET_array.py`)**
This script houses the preliminary PyTorch implementation of the U-Net architecture designed to consume the `(80, 400)` FTAN inputs and output the structural target masks.

---

### Legacy Workflow (v1)

### Configuration File (`config.json`)
The configuration file sets up key parameters and paths for the machine learning pipeline:

```json
{
    "data_dir": "FTAN_ML_INPUT",
    "metadata_file": "FTAN_ML_INPUT/metadata.csv",
    "output_dir": "FTAN_ML_MODELS_4CH_SHARP",
    "batch_size": 8,
    "learning_rate": 0.0001,
    "num_epochs": 200,
    "patience": 20,
    "augment": true
}
```

### Preprocessing Script (`preprocess_data.py`)
This script handles the preprocessing of raw seismic data:

```python
import pandas as pd
import numpy as np
import os
from obspy import read

def preprocess_data(config):
    # Load metadata
    metadata_file = config['metadata_file']
    df = pd.read_csv(metadata_file)
    
    # Directory paths
    data_dir = config['data_dir']
    output_dir = config['output_dir']
    
    for index, row in df.iterrows():
        file_name = os.path.join(data_dir, row['file_name'])
        try:
            st = read(file_name)
            # Apply preprocessing steps (e.g., filtering, normalization)
            data = st[0].data
            # Save preprocessed data
            np.save(os.path.join(output_dir, f"preprocessed_{row['file_name']}.npy"), data)
        except Exception as e:
            print(f"Error processing {file_name}: {e}")

if __name__ == "__main__":
    import json
    with open('config.json', 'r') as f:
        config = json.load(f)
    preprocess_data(config)
```

### Training Script (`train_model.py`)
This script trains the machine learning model:

```python
import tensorflow as tf
from tensorflow.keras.models import Sequential
from tensorflow.keras.layers import Dense, Conv1D, Flatten, Dropout
import numpy as np
import pandas as pd
import os
from sklearn.model_selection import train_test_split

def build_model(input_shape):
    model = Sequential([
        Conv1D(32, kernel_size=3, activation='relu', input_shape=input_shape),
        Conv1D(64, kernel_size=3, activation='relu'),
        Dropout(0.5),
        Flatten(),
        Dense(128, activation='relu'),
        Dense(1, activation='sigmoid')  # Example for binary classification
    ])
    model.compile(optimizer='adam', loss='binary_crossentropy', metrics=['accuracy'])
    return model

def train_model(config):
    # Load metadata
    metadata_file = config['metadata_file']
    df = pd.read_csv(metadata_file)
    
    # Directory paths
    data_dir = config['data_dir']
    output_dir = config['output_dir']
    
    # Load preprocessed data
    X, y = [], []
    for index, row in df.iterrows():
        file_name = os.path.join(output_dir, f"preprocessed_{row['file_name']}.npy")
        data = np.load(file_name)
        label = row['label']
        X.append(data)
        y.append(label)
    
    X = np.array(X)
    y = np.array(y)
    
    # Split data into training and validation sets
    X_train, X_val, y_train, y_val = train_test_split(X, y, test_size=0.2, random_state=42)
    
    # Build model
    input_shape = (X_train.shape[1], 1)
    model = build_model(input_shape)
    
    # Train model
    history = model.fit(
        X_train, y_train,
        validation_data=(X_val, y_val),
        batch_size=config['batch_size'],
        epochs=config['num_epochs'],
        callbacks=[
            tf.keras.callbacks.EarlyStopping(patience=config['patience']),
            tf.keras.callbacks.ModelCheckpoint(os.path.join(output_dir, 'model.h5'), save_best_only=True)
        ]
    )
    
    # Save model
    model.save(os.path.join(output_dir, 'final_model.h5'))

if __name__ == "__main__":
    import json
    with open('config.json', 'r') as f:
        config = json.load(f)
    train_model(config)
```

### Evaluation Script (`evaluate_model.py`)
This script evaluates the performance of the trained model:

```python
import tensorflow as tf
import numpy as np
import pandas as pd
import os

def evaluate_model(config):
    # Load metadata
    metadata_file = config['metadata_file']
    df = pd.read_csv(metadata_file)
    
    # Directory paths
    data_dir = config['data_dir']
    output_dir = config['output_dir']
    
    # Load preprocessed data
    X, y = [], []
    for index, row in df.iterrows():
        file_name = os.path.join(output_dir, f"preprocessed_{row['file_name']}.npy")
        data = np.load(file_name)
        label = row['label']
        X.append(data)
        y.append(label)
    
    X = np.array(X)
    y = np.array(y)
    
    # Load model
    model = tf.keras.models.load_model(os.path.join(output_dir, 'final_model.h5'))
    
    # Evaluate model
    loss, accuracy = model.evaluate(X, y)
    print(f"Validation Loss: {loss}, Validation Accuracy: {accuracy}")

if __name__ == "__main__":
    import json
    with open('config.json', 'r') as f:
        config = json.load(f)
    evaluate_model(config)
```

### Prediction Script (`predict.py`)
This script uses the trained model to make predictions on new data:

```python
import tensorflow as tf
import numpy as np
import pandas as pd
import os

def predict_model(config, new_data_dir):
    # Load metadata
    metadata_file = config['metadata_file']
    df = pd.read_csv(metadata_file)
    
    # Directory paths
    output_dir = config['output_dir']
    
    # Load preprocessed data
    X_new = []
    for file_name in os.listdir(new_data_dir):
        if file_name.endswith('.npy'):
            data = np.load(os.path.join(new_data_dir, file_name))
            X_new.append(data)
    
    X_new = np.array(X_new)
    
    # Load model
    model = tf.keras.models.load_model(os.path.join(output_dir, 'final_model.h5'))
    
    # Make predictions
    predictions = model.predict(X_new)
    
    # Save predictions
    np.save(os.path.join(output_dir, 'predictions.npy'), predictions)

if __name__ == "__main__":
    import json
    with open('config.json', 'r') as f:
        config = json.load(f)
    new_data_dir = 'path_to_new_data'
    predict_model(config, new_data_dir)
```

## 4. Workflows

### Data Flow and Script Execution

1. **Data Preprocessing**:
   - The `preprocess_data.py` script reads raw seismic data files (`.sac`) from `FTAN_ML_INPUT` and metadata from `metadata.csv`.
   - It applies preprocessing steps such as filtering, normalization, and saving the preprocessed data as NumPy arrays in `FTAN_ML_MODELS_4CH_SHARP`.

2. **Model Training**:
   - The `train_model.py` script loads the preprocessed data and metadata.
   - It builds a neural network model using TensorFlow and trains it on the preprocessed data.
   - The trained model is saved to `FTAN_ML_MODELS_4CH_SHARP`, along with training logs and metrics.

3. **Model Evaluation**:
   - The `evaluate_model.py` script loads the trained model and evaluates its performance on a validation dataset derived from the preprocessed data.
   - It prints out validation loss and accuracy, which are also saved to the output directory.

4. **Prediction**:
   - The `predict.py` script loads the trained model and makes predictions on new, unseen data.
   - The predictions are saved as NumPy arrays in `FTAN_ML_MODELS_4CH_SHARP`.

### Example Workflow
1. **Preprocessing**:
   ```bash
   python preprocess_data.py
   ```
2. **Training**:
   ```bash
   python train_model.py
   ```
3. **Evaluation**:
   ```bash
   python evaluate_model.py
   ```
4. **Prediction**:
   ```bash
   python predict.py --new_data_dir path_to_new_data
   ```

### HPC Job Submission (Optional)
For running the pipeline on an HPC cluster using Slurm, you can use a shell script like `train_model.sh`:

```bash
#!/bin/bash
#SBATCH --job-name=seismic_ml
#SBATCH --output=logs/seismic_ml_%j.out
#SBATCH --error=logs/seismic_ml_%j.err
#SBATCH --nodes=1
#SBATCH --ntasks-per-node=4
#SBATCH --time=24:00:00
#SBATCH --mem=32G

module load python/3.8
source /path/to/venv/bin/activate

python train_model.py
```

### Conclusion
This project provides a comprehensive pipeline for analyzing seismic data using machine learning. The scripts are designed to handle data preprocessing, model training, evaluation, and prediction, ensuring that the entire workflow is reproducible and efficient. The configuration file (`config.json`) centralizes key parameters and paths, making it easy to adapt the pipeline to different datasets or experimental setups.
