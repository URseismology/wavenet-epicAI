# MetadataGloba: Seismic Station Spatial Indexing & Metadata Pipeline

This project is a high-performance pipeline designed to automate the discovery, spatial indexing, and metadata extraction of EarthScope (formerly IRIS) seismic stations. 

When dealing with ambient noise cross-correlation at a global scale, researchers face a massive data bottleneck: downloading decades of continuous waveforms for over 60,000 stations is computationally unfeasible. This project solves that problem by building a highly compressed, pre-calculated "Master Blueprint" of valid station pairs and their exact AWS S3 data keys.

---

## 🛠️ Core Architecture & Workflow

### 1. Secure Cloud Authentication
The script uses the `earthscope-sdk` to authenticate via an interactive login. This dynamically generates temporary AWS S3 credentials scoped specifically to the `s3-miniseed` role. This allows direct, high-bandwidth read access to EarthScope’s massive `earthscope-mseed` bucket hosted on AWS without requiring a paid AWS account.

### 2. Network & Epoch Discovery
Rather than relying on outdated static text files, the script scans the live S3 bucket to catalog all currently available seismic networks (over 500 networks). It then leverages `obspy.clients.fdsn` to query the FDSN web services, fetching the geographic coordinates (latitude/longitude) and the exact operational epochs (start and end dates) for over 62,000 individual seismic stations.

### 3. Spatial Indexing via Haversine BallTree
**The Problem:** Calculating the distance between 62,000 stations using a naive O(N²) pairwise loop would require nearly **2 billion** individual distance calculations.
**The Solution:** The pipeline utilizes a highly optimized `BallTree` from `scikit-learn`. 
* **How it works:** A BallTree recursively partitions spatial data into a series of nested multi-dimensional spheres (balls). By converting our latitude and longitude coordinates into radians and using the `haversine` metric (which calculates the great-circle distance accounting for the Earth's spherical curvature), the BallTree allows us to query spatial relationships in **O(log N) time**. 
* **The Result:** We can instantly "draw a circle" around each station and grab all neighbors that fall within our target cross-correlation window (e.g., between 60 km and 6000 km), bypassing billions of unnecessary calculations.

### 4. O(1) Temporal Validation
Two stations being physically close means nothing if they didn't exist at the same time (e.g., a temporary array deployed in 1990 vs a permanent station built in 2010). The pipeline constructs a unique temporal identifier for every active day (e.g., `2024_152`) and stores them as Python `Sets`. When a valid spatial pair is found, it performs an **O(1) Set Intersection** to instantly verify if the two stations shared overlapping operational days, keeping only mathematically valid temporal pairs.

### 5. Why Parquet? (Data Export)
The pipeline generates hundreds of millions of valid station pairs. Saving this as a standard CSV would result in terabytes of data that would crash any standard computer's RAM. 
Instead, we stream the output into an Apache Parquet file (`master_pairs_final.parquet`).
* **Columnar Storage:** Parquet stores data by column rather than by row. Since columns like `network` contain highly repetitive data (e.g., repeating "IU" thousands of times), Parquet uses advanced dictionary encoding and snappy compression to shrink the file size drastically.
* **Batch Streaming:** The script flushes the data to the Parquet file in chunks of 1,000,000 pairs. This ensures the script never consumes more than a few megabytes of RAM, no matter how massive the final dataset becomes.
* **Predicate Pushdown:** When you later read the Parquet file in Pandas, you can filter rows *before* they are loaded into RAM (e.g., `columns=['dist_km']`), making data analysis lightning fast. A dataset that would be 100+ GB in CSV format is reduced to just 2.7 GB in Parquet.

### 6. Partitioned Daily Key Index
The script builds a secondary dataset (`keys_partitioned_year`) mapping every station's active days to its exact AWS S3 `dataacess_key`.
* **Why Partitioning?:** The index is saved as a directory of Parquet files split by `year` (e.g., `year=2020`, `year=2021`). When the `get_keys()` function queries the index for a specific year, the operating system only loads that single year's file into RAM. This turns a massive, slow database query into an instant, low-memory lookup.

---

## 🎧 Integration with NoisePy

The ultimate goal of this metadata extraction is to facilitate large-scale ambient noise cross-correlation using frameworks like **NoisePy**.

Instead of downloading years of continuous waveforms blindly, you use the outputs of this pipeline for precision data targeting:

1. **Identify Valid Pairs:** Read `master_pairs_final.parquet` to determine exactly which station pairs are physically valid (60km-6000km) and have overlapping operational dates.
2. **Retrieve S3 Keys:** Use the provided `get_keys()` function to query the `keys_partitioned_year` index. This returns a DataFrame containing the exact S3 object keys for the specific days where both stations were actively recording.
3. **Cloud-Native Streaming:** Feed these exact S3 keys into NoisePy. Since EarthScope's miniseed data is hosted publicly on AWS S3, NoisePy can use these keys to **directly stream** only the overlapping raw ambient noise data directly into your cross-correlation workflow. 
* *This completely eliminates the overhead of downloading and storing terabytes of unnecessary waveform data on your local hard drive.*

---

## 🚀 Setup & Requirements

Before running the notebook locally or in Google Colab, ensure you have the required dependencies:

```bash
pip install earthscope-cli earthscope-sdk obspy boto3 pyarrow scikit-learn pandas numpy
```

*Note: The script requires an interactive login via `!es login`. You will need an active EarthScope account to generate the temporary S3 credentials.*

### Running in Google Colab
If running in Colab, the notebook includes logic to package and download the resulting Parquet files directly to your local machine via the browser (`google.colab.files.download`). For maximum stability with the massive 2.7GB output file, it is highly recommended to mount your Google Drive and save the outputs directly to `/content/drive/MyDrive/` to bypass browser download timeouts.
