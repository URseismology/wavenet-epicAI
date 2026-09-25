import h5py
import numpy as np

path = "/scratch/tolugboj_lab/wavenet_ncf_quicktest/packaged_h5/G.SSB.h5"

def show(name, obj):
    if isinstance(obj, h5py.Dataset):
        uncompressed = obj.size * obj.dtype.itemsize
        try:
            stored = obj.id.get_storage_size()
        except Exception:
            stored = -1
        ratio = (1 - stored / uncompressed) * 100 if uncompressed and stored >= 0 else float("nan")
        print(f"  DATASET {name}: shape={obj.shape} dtype={obj.dtype} "
              f"uncompressed={uncompressed:,}B stored={stored:,}B compression={ratio:.1f}% "
              f"attrs={dict(obj.attrs)}")
        data = obj[:]
        print(f"    sample stats: min={data.min():.6g} max={data.max():.6g} "
              f"mean={data.mean():.6g} std={data.std():.6g} nan_count={np.isnan(data).sum()}")
    else:
        print(f"  GROUP {name}")

with h5py.File(path, "r") as f:
    print(f"File: {path}")
    print(f"Total on-disk size: {f.id.get_filesize():,} bytes")
    f.visititems(show)
