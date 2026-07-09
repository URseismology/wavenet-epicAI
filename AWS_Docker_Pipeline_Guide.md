# AWS & Docker Pipeline Guide

This document explains the granular details of running the EarthScope/AWS containerized pipeline. It is designed to help researchers new to Docker understand how the infrastructure operates under the hood.

## The Architecture: What Uses AWS and What Doesn't?

When running this pipeline on your local Mac (or on the ATOS-NAS), it is important to distinguish between local compute and cloud resources.

*   **What DOES NOT use AWS (runs locally):** The Docker container itself is running on your physical Mac's CPU and RAM. When you type `docker run`, your computer is executing the Python code locally.
*   **What DOES use AWS (the Cloud):** The Python scripts inside the container (`boto3`) reach out over the internet to Amazon Web Services to query the EarthScope S3 buckets. When the script downloads `.mseed` files, AWS servers package the data and stream it across the internet down to your local Mac.
*   **Future Goal (The Cloud Orchestrator):** In the ultimate production architecture, we will move the *Docker container itself* to an AWS EC2 instance. In that scenario, everything (both compute and data storage) will happen in the cloud, and your local machine will just trigger the process.

## Why is `es login` Necessary?

You might wonder: *"If I already provided my AWS Access Keys, why do I need to log into EarthScope?"*

EarthScope hosts its massive seismic database on AWS S3 Open Data. However, access to this data is managed by EarthScope's own identity systems. 
When you run `es login` (EarthScope Login), you are proving to EarthScope that you are a registered researcher. EarthScope then takes your saved token and securely instructs AWS to allow your specific AWS Keys to access their S3 buckets. 

Without the `es login` token, AWS will reject your requests with a `Forbidden` error because EarthScope hasn't vouched for you.

---

## 🛑 Critical Architecture Note: The Isolated AWS Clone
Before running any cloud orchestration, you **MUST** ensure you are working from the dedicated AWS clone located at:
`~/wavenet-epicAI/`

**Do NOT** run AWS orchestration from inside your local `Admin8_LabAI` project clones (e.g., `~/SynologyDrive/1.UofR_Seismology/1_Admin/Admin8_LabAI/wavenet-epicAI`). 
The `~/wavenet-epicAI` directory is specifically maintained as an isolated cloud-execution environment. Running orchestrator scripts directly from your synchronized NAS/project drives can cause unintended syncing issues with large data downloads.

---

## Step-by-Step Local Execution Guide

### 1. Authenticate with EarthScope (Interactive Mode)
Before running the pipeline, you must authenticate the EarthScope SDK.

```bash
docker run -it --rm \
  -v ~/.earthscope:/home/jovyan/.earthscope \
  urseismogate.earth.rochester.edu/chrisscripts:latest \
  es login
```

### 2. Build the S3 Key Index
This command scans the entire S3 bucket to build a local Parquet index for lightning-fast lookups.

```bash
docker run --rm \
  -v ~/.aws:/home/jovyan/.aws \
  -v ~/.earthscope:/home/jovyan/.earthscope \
  -v ~/wavenet-epicAI:/home/jovyan/work \
  urseismogate.earth.rochester.edu/chrisscripts:latest \
  python chrisScripts/singleNCFtest/build_key_index.py --outdir chrisScripts/singleNCFtest/keys_partitioned_year --workers 10
```

### 3. Download the Raw Data Pairs
This uses the index built in Step 2 to download overlapping data.

```bash
docker run --rm \
  -v ~/.aws:/home/jovyan/.aws \
  -v ~/.earthscope:/home/jovyan/.earthscope \
  -v ~/wavenet-epicAI:/home/jovyan/work \
  urseismogate.earth.rochester.edu/chrisscripts:latest \
  python chrisScripts/singleNCFtest/download_pairs.py --pairs chrisScripts/singleNCFtest/test_pairs.csv --keyindex chrisScripts/singleNCFtest/keys_partitioned_year --outdir chrisScripts/singleNCFtest/raw_data
```

---

## Understanding the Docker Commands

If you are new to Docker, the commands above can look intimidating. Here is a breakdown of exactly what each flag does:

*   **`docker run`**: Tells Docker to start a new, isolated container.
*   **`-it`**: Stands for Interactive TTY. It links your terminal's keyboard inputs directly to the container. We use this for `es login` because the command requires you to read a link and sometimes type responses.
*   **`--rm`**: "Remove." This tells Docker to completely delete the container the exact second the script finishes. This keeps your hard drive clean and prevents thousands of dead, useless containers from piling up over time.
*   **`-v <Host_Path>:<Container_Path>`**: "Volume Mount". A container is completely isolated from your Mac. By using `-v`, we drill a "wormhole" between a folder on your Mac and a folder inside the container. We use exactly three of these wormholes:
    1.  **The AWS Mount (`-v ~/.aws:/home/jovyan/.aws`)**: This maps your hidden Mac folder where the `aws configure` command saved your IAM Access Keys. Without this, the container wouldn't know your AWS identity.
    2.  **The EarthScope Mount (`-v ~/.earthscope:/home/jovyan/.earthscope`)**: This maps the hidden Mac folder where the `es login` command saved your EarthScope authentication token. Without this, EarthScope won't authorize your AWS IAM identity.
    3.  **The Project Mount (`-v ~/wavenet-epicAI:/home/jovyan/work`)**: This maps your actual code repository. It serves two purposes: it allows the container to see the Python scripts it needs to run, and it provides a place for the container to save the downloaded `.mseed` files so they don't disappear when the container is deleted!

---

## AWS Billing and the Boto3 Session

When the `boto3` session starts inside the Python code, here is exactly how AWS handles it and how billing works:

1.  **Authentication:** The Python script uses `boto3` to look for keys in `~/.aws`. Because we mounted that folder, `boto3` finds your `atos-orchestrator` IAM keys. It sends these keys to AWS alongside the EarthScope token to prove who you are.
2.  **API Calls:** `boto3` starts issuing commands like `ListObjectsV2` (to scan the S3 bucket) and `GetObject` (to download the files). AWS logs every single one of these API calls against your AWS account.
3.  **Are you using your credits?** Yes, but for local testing, it is practically free.
    *   **API Requests:** S3 API requests cost about $0.005 per 1,000 requests. Scanning the index or downloading a few files costs fractions of a penny.
    *   **Data Egress (Downloading to Mac):** Downloading data from an AWS server down to your physical Mac over the internet is called "Egress." AWS typically charges for Egress (about $0.09 per GB after the free tier). 
    *   **Why we need EC2 (The Final Goal):** This is exactly why your Manifesto states we must run the final orchestrator on an **EC2 instance in `us-east-2`**. When an AWS EC2 instance in `us-east-2` downloads data from an S3 bucket in `us-east-2`, the data never leaves the AWS data center, so the **Egress fee is $0.00**.
4.  **How to Verify:** You can track every penny in real-time! Log into your AWS Console in your web browser, click on your username in the top right, and select **Billing and Cost Management**. You can see exactly how many S3 `GetObject` or `ListBucket` requests your `atos-orchestrator` user has made, and what it cost.
*   **`urseismogate.earth.../chrisscripts:latest`**: This is the name of the "image blueprint" we are running, which we previously pulled from your private NAS registry.
*   **`python chrisScripts/...`**: Everything after the image name is the exact command you want the container to execute once it boots up.

## The FgaAccessDenied Restriction (Why Local Downloads Fail)

When trying to download data locally or from EC2 using `download_pairs.py`, you might encounter an `FgaAccessDenied` error or an infinite loop of `403 Forbidden` errors during the `GetObject` S3 call. 

This is not a bug with your token. EarthScope has specifically configured their **S3 Object Lambda Access Point** (`earthscope-mseed-res-...`) with strict policies.
Because this is a *Restricted* dataset that could incur thousands of dollars in AWS Egress fees, there are two critical architectural quirks:

1. **Zero-Egress Architecture Requirement:** EarthScope's bucket policy blocks all `GetObject` download requests that do not physically originate from an AWS server inside the `us-east-2` region. This proves exactly why we designed the architecture in the Manifesto: **We must run Step 3 on an EC2 instance inside `us-east-2`.**
2. **The `RequestPayer` Object Lambda Quirk:** Even when running inside `us-east-2`, if you pass `RequestPayer='requester'` to the `boto3` `get_object` call, the EarthScope Object Lambda Access Point will instantly reject the request with `FgaAccessDenied`. S3 Object Lambda Access Points natively handle requester-pays egress and explicitly forbid this parameter. **Do not use the `RequestPayer` parameter.**
3. **Public Internet Routing:** Do NOT attach an S3 VPC Gateway Endpoint to your EC2 instance. The EarthScope S3 Object Lambda Access Point relies on cross-account public AWS endpoints to resolve IAM permissions for your temporary EarthScope token. VPC Gateway Endpoints will result in `FgaAccessDenied`. Always route the S3 traffic over the AWS Public Internet gateway.

---

## 4. Running the `orchestrator.py` (Bypassing the Restriction)

To bypass the EarthScope VPC restriction, we use `orchestrator.py`. This script fully automates spinning up a cloud server to download and process the data.

```bash
python chrisScripts/singleNCFtest/orchestrator.py
```

### How the Orchestrator Works
1.  **AWS Provisioning:** It uses `boto3` to automatically create a Security Group and launch a tiny Amazon EC2 server (`t3.micro`) inside `us-east-2`.
    *   *Cost:* A `t3.micro` costs ~$0.01 per hour. For a 5-minute test, this costs practically $0.00.
2.  **Temporary SSH Key:** It generates a new SSH private key (`atos-orchestrator-key.pem`) and saves it to your `~/.ssh/` directory. This is absolutely required because AWS strictly forbids password logins; you can only log into the EC2 instance using this cryptographic key.
3.  **Transfer Identity:** It uses native `scp` to securely copy your `~/.earthscope` token and your `test_pairs.csv` to the remote EC2 server.
4.  **Execute Pipeline:** It SSHs into the server, installs Docker, and runs the exact same `download_pairs.py` Docker command we tried locally. Because the EC2 server is inside `us-east-2`, EarthScope allows the download!
5.  **Retrieve Data:** It SCPs the downloaded `.mseed` files back to your Mac. 
    *   *Egress Costs:* The EC2 server downloading data from S3 is free. However, copying the final `.mseed` files from the EC2 server down to your Mac incurs a tiny Egress fee ($0.09 per GB). For our test pair, this is negligible. In future production workflows, the EC2 server will process the `.mseed` into tiny `.csv` feature files, and *only* the `.csv` will be downloaded to your Mac, ensuring Egress costs remain incredibly low.
6.  **Auto-Termination:** To ensure you are not billed indefinitely for an idle server, the script is programmed to automatically terminate (delete) the EC2 instance and Security Group as soon as it finishes. For your first run, it pauses for 3 minutes before terminating so you can inspect the AWS console.
