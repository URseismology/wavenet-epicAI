#!/usr/bin/env python
"""Assemble docs/ncf_pipeline_stages/xd_mtan_rung_smoke_test/ inside a clone of wavenet-epicAI (working tree only; no commit/push).
Usage: assemble_repo_folder.py <project_dir> <clone_dir> [--figs figs_dir] [--results ORIG_CORR_DIR] [--fixed FIXED_CORR_DIR]
"""
import argparse, json, shutil, subprocess
from pathlib import Path
ap = argparse.ArgumentParser(); ap.add_argument("project"); ap.add_argument("clone")
ap.add_argument("--results", required=True); ap.add_argument("--fixed", required=True); ap.add_argument("--figs", required=True)
ap.add_argument("--logs", required=True); ap.add_argument("--report-md", required=True); ap.add_argument("--report-pdf", required=True)
a = ap.parse_args()
P, C = Path(a.project), Path(a.clone)
DEST = C / "docs" / "ncf_pipeline_stages" / "xd_mtan_rung_smoke_test"
shutil.rmtree(DEST, ignore_errors=True)
for sub in ("figures", "patches/patched_files", "tests", "code", "evidence", "results"):
    (DEST / sub).mkdir(parents=True, exist_ok=True)
def cp(src, dst): shutil.copy(src, DEST / dst)
# reports
cp(a.report_md, "REPORT.md"); cp(a.report_pdf, "REPORT.pdf")
base = subprocess.run(["git", "-C", str(C), "rev-parse", "--short", "origin/add-september-ncf-pipeline"], capture_output=True, text=True).stdout.strip()
stat = (P / "patch_artifacts" / "stat.txt").read_text().strip().splitlines()[-1].strip() if (P / "patch_artifacts" / "stat.txt").exists() else ""
hunks = (P / "patch_artifacts" / "hunks.md").read_text()
(DEST / "PATCHES.md").write_text((P / "PATCHES_template.md").read_text().replace("{BASE}", base).replace("{STAT}", stat or "see patches/all.diff").replace("{HUNKS}", hunks))
# summary table for README from the report's first table
rep = Path(a.report_md).read_text(); tbl = "\n".join(l for l in rep.split("\n## 1. Summary")[1].split("\n") if l.startswith("|"))
(DEST / "README.md").write_text((P / "README_template.md").read_text().replace("{BASE}", base).replace("{SUMMARY_TABLE}", tbl))
# patches + tests + code + evidence
for f in (P / "patch_artifacts").glob("*.diff"): cp(f, f"patches/{f.name}")
cp(P / "patch_artifacts" / "hunks.md", "patches/hunks.md")
for sub in ("production", "rover_download"):
    for f in (P / "patch_artifacts" / "patched_files" / sub).glob("*.py"):
        (DEST / "patches/patched_files" / sub).mkdir(parents=True, exist_ok=True); cp(f, f"patches/patched_files/{sub}/{f.name}")
cp(P / "tests" / "test_patches.py", "tests/test_patches.py")
for f in (P / "code").iterdir():
    if f.is_file() and not f.name.startswith(".") and f.name not in ("assemble_repo_folder.py",): cp(f, f"code/{f.name}")
cp(P / "code" / "assemble_repo_folder.py", "code/assemble_repo_folder.py")
for f in (P / "evidence").glob("*.txt"): cp(f, f"evidence/{f.name}")
# figures (all generated figures kept)
for f in Path(a.figs).glob("*"):
    if f.is_file(): cp(f, f"figures/{f.name}")
# small results
R, X, L = Path(a.results), Path(a.fixed), Path(a.logs)
for tag, d in (("original", R), ("fixed", X)):
    (DEST / "results" / tag).mkdir(parents=True, exist_ok=True)
    for f in d.glob("*"):
        if f.is_file(): shutil.copy(f, DEST / "results" / tag / f.name)
for name in ("placement_errors.json", "timing_measured_vs_replay.csv", "fixed_chunk_results.json", "fixed_qc_all.json", "refusals_detail.json", "0000_XD_MTAN.json", "0001_XD_RUNG.json"):
    if (L / name).exists(): cp(L / name, f"results/{name}")
for f in (P / "results" / "adama_reference").glob("*co_ral*"): (DEST / "results" / "adama_reference").mkdir(exist_ok=True); shutil.copy(f, DEST / "results" / "adama_reference" / f.name)
if (P / "results" / "adama_reference" / "adama_pair_summary.json").exists(): shutil.copy(P / "results" / "adama_reference" / "adama_pair_summary.json", DEST / "results" / "adama_reference")
tot = sum(f.stat().st_size for f in DEST.rglob("*") if f.is_file())
print(f"assembled {DEST} : {sum(1 for f in DEST.rglob('*') if f.is_file())} files, {tot / 1e6:.1f} MB")
big = sorted(((f.stat().st_size, f) for f in DEST.rglob("*") if f.is_file()), reverse=True)[:6]
for s, f in big: print(f"   {s / 1e6:6.2f} MB  {f.relative_to(DEST)}")
