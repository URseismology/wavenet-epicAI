#!/usr/bin/env python3
"""
Generate a round-robin job map for Baowei_test.

Ordering: CIA_0001, Continental_0001, ..., WUS_0001, CIA_0002, ...
Skips any model that already has output in:
  - Baowei_test/outputs/<Family>/<Stem>/
  - Baowei_test/outputs_stalled/<Family>/<Stem>/

Usage:
    python generate_job_map_roundrobin.py
"""

from pathlib import Path
import re

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BAOWEI_DIR    = Path('Baowei_test')
CONFIGS_DIR   = BAOWEI_DIR / 'configs'
OUTPUTS_DIR   = BAOWEI_DIR / 'outputs'
STALLED_DIR   = BAOWEI_DIR / 'outputs_stalled'
JOB_MAP       = BAOWEI_DIR / 'job_map_roundrobin.csv'

FAMILIES = [
    'CIA', 'Continental', 'Craton', 'CUS', 'Interior',
    'KOREA', 'Rift', 'Shield', 'tak135sph', 'WUS'
]


def has_output(family, stem):
    """Check if sim output already exists in outputs_stalled."""
    stem_dir = STALLED_DIR / family / stem
    return stem_dir.exists() and any(stem_dir.glob('sim_*'))


def get_stem_number(stem):
    m = re.search(r'_([0-9]+)$', stem)
    return int(m.group(1)) if m else 0


def main():
    print("=" * 60)
    print("ROUND-ROBIN JOB MAP GENERATOR - BAOWEI TEST")
    print("=" * 60)

    # Collect all config files per family, keyed by stem number
    family_stems = {}
    for fam in FAMILIES:
        fam_dir = CONFIGS_DIR / fam
        if not fam_dir.exists():
            print(f"WARNING: Config dir not found: {fam_dir}")
            family_stems[fam] = []
            continue

        configs = sorted(fam_dir.glob('*.txt'))
        stems = []
        for cfg in configs:
            stem = re.sub(r'_dist_.*$', '', cfg.stem)
            stems.append((get_stem_number(stem), stem, cfg))
        stems.sort(key=lambda x: x[0])
        family_stems[fam] = stems
        print(f"  {fam}: {len(stems)} configs")

    # Determine max rounds
    max_rounds = max(len(v) for v in family_stems.values())
    print(f"\nMax rounds: {max_rounds}")

    # Generate round-robin order, skipping completed models
    job_id      = 0
    skipped     = 0
    written     = 0

    with open(JOB_MAP, 'w') as f:
        f.write("job_id,config_file,model_file,status\n")

        for round_idx in range(max_rounds):
            for fam in FAMILIES:
                stems = family_stems[fam]
                if round_idx >= len(stems):
                    continue

                _, stem, cfg = stems[round_idx]

                # Get model file path from config
                config_text = cfg.read_text()
                model_file = None
                for line in config_text.splitlines():
                    if line.startswith('# Model file:'):
                        model_file = line.split(':', 1)[1].strip()
                        break

                if not model_file:
                    # Reconstruct from stem
                    fam_key = re.sub(r'_[0-9]*$', '', stem)
                    model_file = f"experiments/perturbed_models/{stem}.mod"

                # Check if already done
                if has_output(fam, stem):
                    skipped += 1
                    continue

                job_id += 1
                f.write(f"{job_id},{cfg},{model_file},NO\n")
                written += 1

            if round_idx % 1000 == 999:
                print(f"  Round {round_idx + 1}/{max_rounds} processed...")

    print(f"\n{'='*60}")
    print(f"DONE")
    print(f"{'='*60}")
    print(f"  Jobs written : {written}")
    print(f"  Skipped      : {skipped} (already have output)")
    print(f"  Job map      : {JOB_MAP}")


if __name__ == '__main__':
    main()