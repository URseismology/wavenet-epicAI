# Data-provider limits we must comply with

**Read before changing download concurrency, array limits, or `threads_per_client`.**

These are other institutions' published operating limits, not internal tuning parameters.
Exceeding them degrades a service other researchers depend on, and in EarthScope's case the
network equipment simply drops us.

---

## EarthScope / IRIS DMC

Source: <https://ds.iris.edu/ds/nodes/dmc/services/usage/> (retrieved 2026-10-06)

> Users should limit usage to **no more than 5 concurrent connections** to any combination of
> web services.

> Users should limit their clients to **no more than 10 connections per second** regardless of
> the return capability of the service interfaces.

> The DMC's network equipment will **deny connections (via a TCP RESET)** when the limits have
> been exceeded.

The same 5-connection limit applies to their SeedLink service. Polling for real-time
continuous data via web services is explicitly prohibited — use SeedLink for that.

---

## The trap: obspy opens 3 connections per task, invisibly

`MassDownloader.download()` defaults to **`threads_per_client=3`**. So the number the provider
sees is **not** your SLURM array limit — it is

```
provider-facing connections  =  (array concurrency)  x  threads_per_client
```

A run at `--array=...%23` is **69 connections**, fourteen times the published limit. Nothing in
our configuration said "69"; the multiplier only appears if you go looking for it in obspy's
signature.

### What that cost us (2026-10-06)

| Configuration | Connections | Outcome |
|---|---|---|
| `%23` x 3 threads | **69** | 20x HTTP 503, 19x HTTP 429; delivered **0.70x** of what we already held |
| `%1` x 3 threads | 3 | **97% of a station's span**, 3 errors in total |
| `%5` x 1 thread | **5** | **zero** throttling, ~26 days/min per station, 5 stations in parallel |

The 0.70x result was very nearly misread as "re-downloading truncated stations does not
recover data", which would have written off ~1.3M recoverable station-days. The actual fault
was running fourteen times over a documented limit.

---

## How to stay compliant

Set **`WAVENET_DOWNLOAD_THREADS=1`** and cap the array at **5**. That makes provider-facing
concurrency equal the task count — one honest knob instead of a hidden product.

```bash
export WAVENET_DOWNLOAD_THREADS=1      # orchestrator passes this to threads_per_client
sbatch --array=0-N%5 ...               # 5 tasks x 1 thread = 5 connections
```

The orchestrator's default remains 3 for backward compatibility, so **this must be set
explicitly** on any campaign that runs more than one task at a time.

**Do not split across BlueHive and BlueHive3 to obtain 10 connections.** The guideline says
*"users should limit usage"*, not per source address. This is an archive the lab depends on and
whose staff we work with.

---

## Recognising throttling in the data

* **Throttled:** a RAGGED delivered-day pattern across parallel stations, plus `HTTP 429`,
  `503`, or `Connection reset` in task stderr. MassDownloader logs each rejection and
  **continues**, so the intervals are silently absent — no exception, no failure flag.
* **Not throttled:** delivered days converge UNIFORMLY across parallel stations.

Grep for the error text specifically — `Unknown HTTP code: [0-9]+`, `Too Many Requests`,
`Connection reset`. A bare `grep 429` matches byte counts inside ordinary `INFO: Downloaded`
lines and will report throttling that is not there.

---

## Other providers

GEOFON, ORFEUS, RESIF, INGV and the rest have their own policies, not yet collected here. The
federator routes a station to whichever centre holds it, so a campaign spread across providers
divides its connections among them rather than concentrating on one. **When adding a provider
to the chain, find and record its limits here first.**
