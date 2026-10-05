# /scratch/tolugboj_lab — shared lab scratch

This is **shared storage for the whole Olugboji lab**: ~100 top-level entries belonging to
many people and many projects. No one person owns this root.

This file is an **index and a convention**, not a description of anyone's work. It does not
document what lives in other people's directories, and nothing here should be read as
authoritative about them. If you own a directory here, the README inside it is the authority.

---

## The convention

**One directory per project, with its own README inside it.**

A project's directory should contain everything that project needs — its code, its run
outputs, its archive — so that the lab root gains one entry per project rather than one per
experiment. Anything that is finished, superseded, or dead belongs in that project's own
`archive/`, not at this level.

This matters because things at this level are indistinguishable by name. A directory called
`foo_framework` and one called `foo_production_v2` look like siblings; one may be source code
and the other a terabyte of output. Sorting them out costs whoever comes next real time.

## Projects that follow it

| Directory | README | What it is |
|---|---|---|
| `wavenet_ncf/` | `wavenet_ncf/README.md` | NCF waveform acquisition + packaging (wavenet-epicAI) |

Other top-level entries predate this convention or belong to other people. They are left
exactly as their owners made them.

## Why this file exists

On 2026-10-05 a 1,999-station campaign in the `wavenet_ncf` project ran to completion against
the wrong code tree. The fixes were real, deployed and verified — into a directory production
never used. The project had spread to twenty-one sibling entries at this level, two of which
were source trees distinguished only by the suffix `_recovery`, and nothing in the layout made
the mistake visible. It cost several days.

That is a failure mode this root makes easy. Hence the convention above.

## Housekeeping notes

- **Do not delete another project's directory**, even one that looks retired. Names like
  `_wiped` and `_retired` here have been found attached to directories holding tens of GB of
  real data.
- Prefer moving things into your own project's `archive/` over deleting them.
- Group quota is shared. Before a large run, check what you are about to add.
