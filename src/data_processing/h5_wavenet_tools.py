import h5py
import numpy as np

import json
import os

class HDF5Writer:
    """
    Incremental HDF5 Appender for WaveNet ML data.
    """
    def __init__(self, filename, mode='a'):
        self.filename = filename
        self.mode = mode
        
        # Initialize if doesn't exist
        if not os.path.exists(self.filename) or mode == 'w':
            with h5py.File(self.filename, 'w') as f:
                # Create datasets with maxshape=(None, ...) to allow appending
                # We define chunk sizes to optimize reading slices
                f.create_dataset('raw_waveforms', shape=(0, 0, 6), maxshape=(None, None, 6), dtype='float32', chunks=True)
                f.create_dataset('ftan_inputs', shape=(0, 80, 400), maxshape=(None, 80, 400), dtype='float32', chunks=(16, 80, 400))
                f.create_dataset('target_masks', shape=(0, 80, 400), maxshape=(None, 80, 400), dtype='uint8', chunks=(16, 80, 400))
                f.create_dataset('theoretical_curves', shape=(0, 76), maxshape=(None, None), dtype='float32', chunks=True)
                f.create_dataset('sdispl_curves', shape=(0, 76), maxshape=(None, None), dtype='float32', chunks=True)
                f.create_dataset('sdispr_curves', shape=(0, 76), maxshape=(None, None), dtype='float32', chunks=True)
                f.create_dataset('velocity_models', shape=(0, 0, 4), maxshape=(None, None, 4), dtype='float32', chunks=True)
                
                # Metadata
                meta_grp = f.create_group('metadata')
                dt_str = h5py.string_dtype(encoding='utf-8')
                meta_grp.create_dataset('simulation_id', shape=(0,), maxshape=(None,), dtype=dt_str)
                meta_grp.create_dataset('domain', shape=(0,), maxshape=(None,), dtype=dt_str)
                meta_grp.create_dataset('distance_km', shape=(0,), maxshape=(None,), dtype='float32')
                meta_grp.create_dataset('radius_range', shape=(0,), maxshape=(None,), dtype=dt_str)
                meta_grp.create_dataset('azimuth_range', shape=(0,), maxshape=(None,), dtype=dt_str)
                meta_grp.create_dataset('stack_length', shape=(0,), maxshape=(None,), dtype='int32')
                meta_grp.create_dataset('delta', shape=(0,), maxshape=(None,), dtype='float32')
                meta_grp.create_dataset('processed_log', shape=(0,), maxshape=(None,), dtype=dt_str)
    
    def get_processed_simulations(self):
        """Returns a set of simulation IDs that have already been processed."""
        if not os.path.exists(self.filename):
            return set()
        with h5py.File(self.filename, 'r') as f:
            if 'metadata/simulation_id' in f:
                sim_ids = f['metadata/simulation_id'][:]
                return set([s.decode('utf-8') for s in sim_ids])
            return set()

    def append_batch(self, batch_data):
        """
        Appends a batch of simulation data.
        batch_data should be a dict of lists containing the arrays and metadata.
        """
        n_new = len(batch_data['simulation_id'])
        if n_new == 0:
            return

        with h5py.File(self.filename, 'a') as f:
            # Resize and append for each array dataset
            for key in ['raw_waveforms', 'ftan_inputs', 'target_masks', 
                        'theoretical_curves', 'sdispl_curves', 'sdispr_curves', 'velocity_models']:
                if key in batch_data and len(batch_data[key]) > 0:
                    dset = f[key]
                    
                    # Handle variable length dimensions for raw_waveforms and velocity_models
                    current_shape = list(dset.shape)
                    new_max_dim1 = max([arr.shape[0] for arr in batch_data[key]])
                    if current_shape[1] < new_max_dim1:
                        # Resize the variable dimension if needed
                        new_shape = list(dset.shape)
                        new_shape[1] = new_max_dim1
                        dset.resize(tuple(new_shape))
                    
                    new_size = dset.shape[0] + n_new
                    dset.resize((new_size, *dset.shape[1:]))
                    
                    # Pad and write
                    for i, arr in enumerate(batch_data[key]):
                        # Pad variable dimensions with NaNs or zeros if necessary
                        if arr.shape[0] < dset.shape[1]:
                            pad_width = [(0, dset.shape[1] - arr.shape[0])] + [(0,0)] * (arr.ndim - 1)
                            padded_arr = np.pad(arr, pad_width, mode='constant', constant_values=np.nan)
                            dset[-n_new + i] = padded_arr
                        else:
                            dset[-n_new + i] = arr
            
            # Resize and append metadata
            meta_grp = f['metadata']
            for key in ['simulation_id', 'domain', 'distance_km', 'radius_range', 
                        'azimuth_range', 'stack_length', 'delta', 'processed_log']:
                if key in batch_data:
                    dset = meta_grp[key]
                    dset.resize((dset.shape[0] + n_new,))
                    if dset.dtype.kind == 'S' or dset.dtype.kind == 'O':
                        dset[-n_new:] = [s.encode('utf-8') for s in batch_data[key]]
                    else:
                        dset[-n_new:] = batch_data[key]


class HDF5Reader:
    """
    PyTorch Dataset wrapper for streaming from the HDF5 file.
    """
    def __init__(self, filename):
        self.filename = filename
        with h5py.File(self.filename, 'r') as f:
            self.length = f['ftan_inputs'].shape[0]
            
    def __len__(self):
        return self.length
        
    def __getitem__(self, idx):
        # Local import to prevent torch dependency when only writing
        import torch
        # Open file in __getitem__ to support multiprocessing in DataLoaders
        with h5py.File(self.filename, 'r') as f:
            ftan = f['ftan_inputs'][idx]
            mask = f['target_masks'][idx]
            
            metadata = {
                'simulation_id': f['metadata/simulation_id'][idx].decode('utf-8'),
                'domain': f['metadata/domain'][idx].decode('utf-8'),
                'distance_km': f['metadata/distance_km'][idx],
                'radius_range': f['metadata/radius_range'][idx].decode('utf-8'),
                'azimuth_range': f['metadata/azimuth_range'][idx].decode('utf-8')
            }
            
            return (torch.from_numpy(ftan).unsqueeze(0), 
                    torch.from_numpy(mask).unsqueeze(0).float(), 
                    metadata)


def query_attributes(filename):
    """
    CLI utility to print database size, schema, and metadata distribution.
    """
    if not os.path.exists(filename):
        print(f"File {filename} does not exist.")
        return
        
    print(f"--- HDF5 Database Query: {filename} ---")
    with h5py.File(filename, 'r') as f:
        print("\nDatasets:")
        for key in ['raw_waveforms', 'ftan_inputs', 'target_masks', 
                    'theoretical_curves', 'sdispl_curves', 'sdispr_curves', 'velocity_models']:
            if key in f:
                dset = f[key]
                print(f"  {key}: shape={dset.shape}, dtype={dset.dtype}")
        
        print("\nMetadata:")
        meta_grp = f['metadata']
        for key in meta_grp.keys():
            dset = meta_grp[key]
            print(f"  {key}: shape={dset.shape}, dtype={dset.dtype}")
            
        print(f"\nTotal Simulations Indexed: {f['ftan_inputs'].shape[0]}")
        
        # Example metadata distribution (Domain)
        if 'domain' in meta_grp and meta_grp['domain'].shape[0] > 0:
            domains = [s.decode('utf-8') for s in meta_grp['domain'][:]]
            unique, counts = np.unique(domains, return_counts=True)
            print("\nDomain Distribution:")
            for u, c in zip(unique, counts):
                print(f"  {u}: {c}")

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        query_attributes(sys.argv[1])
    else:
        print("Usage: python h5_wavenet_tools.py <path_to_h5>")
