# Docker vs. Apptainer vs. Registries — What We Actually Use Where

Written 2026-09-10 to clear up a real point of confusion: these are three different
*kinds* of things, not three competing options for the same job. Once that's clear,
"which one do I use" has an easy answer depending on which machine you're on.

---

## The three concepts

**Docker** — a tool that (1) builds container images, (2) runs them locally via a
background daemon, and (3) established the de facto *image format* almost everything
else in this space still uses. Needs a persistent daemon process and (traditionally)
root/admin rights. This is what our lab's existing workflow
([`OlugbojiLab-Wiki/DeployRsrch2PrivateDockerRegistry.md`](https://github.com/URseismology/OlugbojiLab-Wiki/blob/main/DeployRsrch2PrivateDockerRegistry.md))
uses to package research code (the kind with heavy Fortran/C dependencies — SAC, GMT,
GDAL — that make `pip install` alone unreliable) into portable images.

**A container registry** — just a storage/distribution service for already-built
images, like PyPI but for containers. It speaks a standard HTTP protocol ("Docker
Registry API v2") and doesn't care what tool built the image or what tool will
eventually run it. **Ours is `urseismogate.earth.rochester.edu`** — a DMZ reverse-proxy
gateway in front of the actual registry on the lab's internal NAS, with public pulls
and authenticated pushes. Functionally identical (same protocol) to Docker Hub or
NVIDIA's NGC registry — just self-hosted, so we're not paying AWS ECR/Docker Hub
storage or egress fees for 5-20GB seismology images.

**Apptainer** (formerly Singularity) — a *different* container runtime, purpose-built
for shared HPC systems where giving every user root/a Docker daemon would be a security
problem. No daemon, unprivileged by design, produces a single immutable file (`.sif`)
instead of a daemon-managed image store. **This is what Alpha uses.**

**Pyxis/Enroot** (mentioned for completeness) — yet another runtime, architected as a
Slurm plugin rather than a CLI tool you invoke yourself. **This is what Beta uses.**

## The key insight tying them together

"Docker" names both a specific tool *and* the image format/protocol nearly everything
else also understands. Our registry only cares about the format, not which tool
produced or will consume it. That's why **one image, pushed once, can be consumed by
completely different runtimes without rebuilding anything**:

```
                     docker build + push
                            |
                            v
            urseismogate.earth.rochester.edu
           (registry — format-agnostic storage)
              /                          \
             /                            \
   apptainer pull docker://...    srun --container-image=...
    -> runs on Alpha (.sif)         -> runs on Beta (Pyxis/Enroot,
       via apptainer exec/run          ephemeral per job)
```

## Where each fits on our actual infrastructure

| Machine | Docker daemon? | How you BUILD | How you RUN |
|---|---|---|---|
| **axon-1** (or your own laptop) | Wherever Docker is installed, per the lab wiki | `docker build` (or `jupyter-repo2docker` if there's no Dockerfile yet), then `docker push urseismogate.../image:tag` | `docker run` locally if you want, but this isn't where our actual compute happens |
| **terravibranium** | No — not used | N/A | N/A — plain conda/Python env today, no containers |
| **Empire AI Alpha** | No | Two options: **(a)** `apptainer pull my.sif docker://urseismogate.../image:tag` — reuses whatever was built with Docker and pushed to our registry, no rebuild needed; **(b)** `apptainer build --fakeroot my.sif my.def` — build natively on Alpha's login node from a small text file, no Docker/registry involved at all (confirmed working 2026-09-10, see `docs/empireai_alpha_slurm_faq.md`) | `apptainer exec/run my.sif ...` — **only on an allocated compute node**, fails on the login node (confirmed 2026-09-10) |
| **Empire AI Beta** | No | You never build on Beta — always reference an existing registry image | `srun --container-image=urseismogate.../image:tag ...` — Pyxis/Enroot pulls and runs it fresh per job; nothing persists afterward |

## A real gotcha we already hit — worth internalizing, not just reading

Pushing an image with the modern default (`docker buildx build --push`) silently adds
provenance/SBOM attestations, producing an **OCI multi-platform index** instead of a
plain manifest. This broke Beta outright: `srun --container-image=...` failed with
`MANIFEST_UNKNOWN` — Pyxis/Enroot couldn't negotiate the index. The exact same image,
pulled by **Apptainer on Alpha**, got past that step fine (all layers resolved and
copied) — the two runtimes handle the same registry content differently.

**Lesson: "it worked on Alpha" does not mean "it'll work on Beta," and vice versa —
always test on the actual target cluster.** The concrete fix, if you hit
`MANIFEST_UNKNOWN` again: rebuild/push with
`docker buildx build --provenance=false --sbom=false -t <image> --push .`

**Second gotcha, confirmed 2026-09-10, on the Apptainer side of the same test**: the
image resolved fine, but at 7.4GB it OOM-killed on **Alpha's login node** during the
final SIF-compression step — over an hour of runtime, climbing past 4-5GB of RAM,
ending in a bare `signal: killed` with no memory-related error message (easy to mistake
for a hang). Retrying the identical `apptainer pull` inside a real compute-node
allocation (`srun ... --mem=32G ...`) completed cleanly in ~3 minutes. **Lesson: any
multi-GB `apptainer pull`/`build`, not just `apptainer exec`/`run`, should happen inside
an allocation on Alpha — the login node's resource limits apply to more than just
container execution.**

## Practical guidance — which do I actually use?

- **Need an image that works on *both* Alpha and Beta, guaranteed identical?** Build
  once with Docker (per the lab wiki), push to our registry (remember
  `--provenance=false --sbom=false`), then pull with Apptainer on Alpha / reference
  directly on Beta. One build, two consumers.
- **Need it on Alpha only, and don't want to deal with Docker/registries at all?** Write
  a `.def` file and `apptainer build --fakeroot` it directly on Alpha's login node. No
  Docker daemon needed anywhere, no push/pull round-trip. Simpler if Beta compatibility
  isn't a requirement.
- **Need it on Beta?** No choice here — Beta only ever pulls from a registry via Pyxis.
  Build with Docker (anywhere with a daemon) and push to `urseismogate.earth.rochester.edu`.
- **Just running plain Python/PyTorch with no exotic system dependencies?** You may not
  need a container at all — `module load` + a venv works fine on Alpha (see
  `docs/empireai_alpha_slurm_tutorial.md` step 4), same as our current simulation
  pipeline does on terravibranium without any container.

## See also

- `docs/empireai_alpha_slurm_faq.md` / `_tutorial.md` — Apptainer specifics on Alpha,
  including the login-node-vs-compute-node execution limitation and the custom-build
  workflow, both directly tested.
- `CLAUDE.md` §Empire AI — Beta's Pyxis/Enroot mechanics and the `MANIFEST_UNKNOWN` fix.
- [`OlugbojiLab-Wiki/DeployRsrch2PrivateDockerRegistry.md`](https://github.com/URseismology/OlugbojiLab-Wiki/blob/main/DeployRsrch2PrivateDockerRegistry.md)
  — the lab's existing Docker build/push workflow and registry architecture (DMZ
  gateway + internal NAS, auth model, `repo2docker` for Dockerfile-less repos).
