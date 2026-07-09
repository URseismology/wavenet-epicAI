# MetadataGloba: Seismic Station Spatial Indexing & Metadata Pipeline

This project is a high-performance pipeline designed to automate the discovery, spatial indexing, and metadata extraction of EarthScope (formerly IRIS) seismic stations. 

When dealing with ambient noise cross-correlation at a global scale, researchers face a massive data bottleneck: downloading decades of continuous waveforms for over 60,000 stations is computationally unfeasible. This project solves that problem through intelligent spatial downsampling and a highly optimized, zero-egress cloud architecture.

---

## 🚀 The Zero-Egress Cloud Orchestration Pipeline (New)

Previously, our workflows relied on local machines to download massive seismic datasets directly from EarthScope's AWS S3 buckets. Due to recent EarthScope Object Lambda policy changes, this "legacy" local-download approach now results in `FgaAccessDenied` errors and risks massive AWS Egress fees.

To adapt, we have built a fully automated **Zero-Egress Cloud Orchestrator**. This pipeline automatically spins up ephemeral EC2 instances inside the `us-east-2` AWS region, executes pre-compiled Docker images from our private lab registry, performs S3 downloads with zero egress costs, and securely SCPs the data back to your local machine before terminating the server.

**Documentation Series:**
To get started with the new cloud orchestrator, please read our 3-part documentation series:
1. **[Part 1: AWS Setup Guide](./AWS_Setup_Guide.md)** - Setting up your AWS account, IAM keys, and CLI.
2. **[Part 2: Cloud Pipeline Guide](./AWS_Docker_Pipeline_Guide.md)** - Launching the EC2 orchestrator and running the `singleNCFtest` end-to-end test.
3. **[Part 3: Private Registry Architecture](./URseismogate_Registry_Architecture.md)** - How our zero-trust `urseismogate` Docker registry feeds the cloud environment.

---

## Core Architecture & Workflow (GridMeta Pipeline)

The primary workflow is split into two stages. First, we safely inventory the entirety of the EarthScope AWS S3 bucket. Second, we use Uber's H3 grid system to intelligently downsample the network before computing distances.

### 1. Secure Cloud Inventory (`s3_inventory.ipynb`)
* **Authentication:** The script uses `earthscope-sdk` to generate temporary AWS S3 credentials scoped specifically to the `s3-miniseed` role, granting high-bandwidth access to EarthScope's massive buckets.
* **Network & Epoch Discovery:** It scans the live S3 bucket to catalog all currently available seismic networks. It then leverages `obspy.clients.fdsn` to query the FDSN web services, fetching the geographic coordinates (latitude/longitude) and exact operational epochs for over 53,000 individual seismic stations.
* **RAM-Safe Export:** The raw, massive catalog of every active day for every station is safely flushed to disk in chunks as `s3_inventory.csv`.

### 2. Spatial Downsampling via H3 Grid (`gridmeta.ipynb`)
Instead of correlating every single station globally (which yields hundreds of millions of overlapping pairs), this script optimizes the network using **Uber's H3 equal-area hexagonal grid system** (Resolution 3).
* **Highest Uptime Selection:** The script snaps all 53,000+ stations into their respective hexagonal grid cells. For each hexagon, it filters the stations to only keep the **single station with the highest total uptime** (most days active). 
* **The Result:** The global dataset is intelligently downsampled from over 53,000 redundant stations to just ~4,300 highly reliable, geographically distributed representative stations.

### 3. Spatial Indexing via Haversine BallTree
**The Problem:** Calculating the distance between thousands of stations using a naive O(N²) pairwise loop is computationally expensive.
**The Solution:** The pipeline utilizes a highly optimized `BallTree` from `scikit-learn` on the downsampled grid. 
* By converting latitude/longitude into radians and using the `haversine` metric (accounting for the Earth's spherical curvature), the BallTree queries spatial relationships in **O(log N) time**. 
* We instantly grab all station pairs that fall within our target cross-correlation window (e.g., between 60 km and 6000 km) and export the finalized pairs to `global_station_pairs10k.csv`.

---

## Legacy Pipeline (`globalmeta.py`)

*Note: This is the previous version of the pipeline. It was designed to correlate ALL stations globally without grid-based downsampling, resulting in an exponentially massive dataset.*

The `globalmeta.py` script attempts to map every possible combination of overlapping stations without dropping redundant nearby stations. Because this generates hundreds of millions of pairs, it relies on entirely different data handling paradigms:

* **O(1) Temporal Validation:** Constructs a unique temporal identifier for every active day and performs instantaneous Python Set Intersections to verify if two stations shared overlapping operational days.
* **Why Parquet? (Data Export):** Instead of CSV, the output is streamed into an Apache Parquet file (`master_pairs_final.parquet`). Parquet's columnar storage, dictionary encoding, and snappy compression shrink the massive file size drastically and allow for batch streaming without crashing RAM.
* **Partitioned Daily Key Index:** Builds a secondary dataset mapping every station's active days to its exact AWS S3 `dataacess_key`. It is saved as a directory of Parquet files partitioned by `year`, turning massive queries into instant, low-memory OS lookups.

---

## Why Only Station Pairs?

You may notice that the GridMeta pipeline outputs only station pair metadata (network codes, station codes, coordinates, and distances) rather than the exact AWS S3 object keys for each day of data. This is intentional.

**NoisePy handles all data fetching for us.** NoisePy's `DataStore` architecture can stream MiniSEED data directly from the EarthScope S3 bucket (`s3://earthscope-geophysical-data`) given just the station network and station codes. It automatically resolves the correct S3 paths, downloads the waveforms, and feeds them into the cross-correlation and stacking pipeline. 

This means our job is purely to answer the question: *"Which stations should be correlated with which?"* — and that is exactly what `global_station_pairs10k.csv` provides. NoisePy takes care of the rest.
