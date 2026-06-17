import h5py
import numpy as np
import matplotlib.pyplot as plt

h5_path = 'test_dataset_10.h5'
if not __import__('os').path.exists(h5_path):
    print("test_dataset_10.h5 not found!")
    exit(1)

with h5py.File(h5_path, 'r') as f:
    models = list(f['simulations'].keys())
    if not models:
        print("No models found in HDF5")
        exit(1)
        
    m_id = models[0]
    print(f"Plotting saved FTAN for model {m_id}")
    geom_grp = f[f'simulations/{m_id}/geometries/separation_127.0km']
    
    # Load FTAN
    ftan_grp = geom_grp['empirical_ftan_dispersion']
    rcwt = ftan_grp['FTAN_ZZ'][:]
    period_s = ftan_grp['period_s'][:]
    velocity_kms = ftan_grp['velocity_kms'][:]
    
    # Load Theory
    theory_grp = f[f'simulations/{m_id}/theoretical']
    t_per = theory_grp['period'][:]
    t_grp = theory_grp['group_velocity_dispersion'][:]
    
    # Extract fundamental mode theory (up to first negative diff)
    wrap_indices = np.where(np.diff(t_per) < 0)[0]
    if len(wrap_indices) > 0:
        idx = wrap_indices[0] + 1
        t_per = t_per[:idx]
        t_grp = t_grp[:idx]

    fig, ax = plt.subplots(figsize=(8, 6))
    
    # Note: rcwt shape is (len(period_s), len(velocity_kms))
    # PyCWT returns frequencies descending. Let's check period bounds.
    ax.imshow(np.transpose(rcwt), cmap='magma', 
              extent=[period_s.min(), period_s.max(), velocity_kms.min(), velocity_kms.max()],
              aspect='auto', origin='lower')
              
    ax.plot(t_per, t_grp, color='lime', ls='--', lw=2, label='Theory Group Velocity')
    
    ax.set_xlim(period_s.min(), period_s.max())
    ax.set_ylim(2.0, 5.0)
    ax.set_xlabel('Period (s)')
    ax.set_ylabel('Velocity (km/s)')
    ax.set_title(f'Saved FTAN HDF5 Validation: {m_id}')
    ax.legend()
    
    plt.savefig('saved_ftan_validation.png', dpi=150)
    print("Saved saved_ftan_validation.png")
