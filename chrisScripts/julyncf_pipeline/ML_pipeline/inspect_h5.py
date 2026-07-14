import sys
import h5py
import numpy as np


def _walk(node, indent, max_depth, max_keys):
    pad = "  " * indent
    keys = list(node.keys())
    for key in keys[:max_keys]:
        item = node[key]
        if isinstance(item, h5py.Group):
            print(f"{pad}[group]   {key}/  ({len(item)} children)")
            if indent < max_depth:
                _walk(item, indent + 1, max_depth, max_keys)
        elif isinstance(item, h5py.Dataset):
            try:
                s = np.asarray(item[0])
                tip = f"  e.g. shape={s.shape} dtype={s.dtype}" if s.ndim > 0 else f"  e.g. {s!r}"
            except Exception:
                tip = ""
            print(f"{pad}[dataset] {key}  shape={item.shape}  dtype={item.dtype}{tip}")
    if len(keys) > max_keys:
        print(f"{pad}  ... +{len(keys) - max_keys} more")


def inspect(path, max_depth=4, max_keys=10):
    print(f"\n=== {path} ===\n")
    with h5py.File(path, "r") as f:
        _walk(f, indent=0, max_depth=max_depth, max_keys=max_keys)


def dive(path, sim_key=None):
    """Print every dataset in one simulation, fully."""
    with h5py.File(path, "r") as f:
        sims = f["simulations"]
        key = sim_key or sorted(sims.keys())[0]
        print(f"\n=== Deep dive: simulations/{key} ===\n")
        _walk(sims[key], indent=0, max_depth=99, max_keys=999)


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "wavenetv2_dataset_10k_full.h5"
    mode = sys.argv[2] if len(sys.argv) > 2 else "summary"

    if mode == "dive":
        dive(path, sys.argv[3] if len(sys.argv) > 3 else None)
    else:
        inspect(path)
