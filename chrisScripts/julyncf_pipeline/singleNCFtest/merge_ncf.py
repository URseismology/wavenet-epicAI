#!/usr/bin/env python
"""
Merges all per-pair *_ncf.h5 files produced by rotate_and_stack.py into a
single combined HDF5 archive (Wavenet_ncfs.h5).

Each station pair writes its own *_ncf.h5 during the pipeline. This script
copies each pair's HDF5 group into one file so downstream tools only need
to open a single archive. Existing groups are overwritten on re-run.

Usage:
    python merge_ncf.py --indir NCF_output --out Wavenet_ncfs.h5
"""
import argparse
import glob
import os
import h5py


def merge_ncf_files(indir, outpath):
    """Copies every pair group from all *_ncf.h5 files in indir into a single HDF5 at outpath."""
    pattern = os.path.join(indir, "*_ncf.h5")
    files = sorted(glob.glob(pattern))
    if not files:
        print(f"No *_ncf.h5 files found in {indir}")
        return

    print(f"Merging {len(files)} files into {outpath}")
    with h5py.File(outpath, 'w') as out_f:
        for fpath in files:
            with h5py.File(fpath, 'r') as in_f:
                for pair_key in in_f.keys():
                    if pair_key in out_f:
                        del out_f[pair_key]
                    in_f.copy(pair_key, out_f)
                    print(f"  Copied: {pair_key} from {os.path.basename(fpath)}")

    print(f"Done. {outpath} contains {len(files)} station pairs.")


def main():
    """Parses --indir and --out arguments, then calls merge_ncf_files."""
    parser = argparse.ArgumentParser(description="Merge per-pair NCF files into Wavenet_ncfs.h5")
    parser.add_argument("--indir", required=True,
                        help="Directory containing *_ncf.h5 files")
    parser.add_argument("--out", default="Wavenet_ncfs.h5",
                        help="Output path for merged file (default: Wavenet_ncfs.h5)")
    args = parser.parse_args()
    merge_ncf_files(args.indir, args.out)


if __name__ == "__main__":
    main()
