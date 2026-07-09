# The URseismogate Docker Registry Architecture

This document outlines the architecture of our lab's private Docker registry (`urseismogate.earth.rochester.edu`), how it integrates with our AWS orchestration scripts, and why it is critical for scaling to full production runs.

---

## 1. The Zero-Trust DMZ Architecture

To isolate our sensitive research environments from public internet threats while maintaining accessibility, our lab operates a zero-trust architecture:
*   **The DMZ Gateway (`urseismogate.earth.rochester.edu`):** An Nginx reverse proxy exposed to the public web that intercepts all incoming traffic.
*   **The Internal NAS (`ATOS-nas`):** A Synology NAS nestled safely behind our university firewall. It hosts a private Docker Registry (listening internally).
*   **The Reverse SSH Tunnel:** The NAS maintains a persistent, encrypted tunnel out to the DMZ. When a request hits `urseismogate`, Nginx securely pipes it through the tunnel directly into the private registry.

This allows us to securely host our own massive, custom Docker blueprints (containing ObsPy, EarthScope SDKs, ML environments) without paying public DockerHub hosting fees or exposing our NAS directly to the internet.

---

## 2. Integration with `orchestrator.py`

When running cloud automation via `orchestrator.py`, the registry plays a vital, money-saving role:

1. **Local Build & Push:** Before touching AWS, we build the heavy Docker image locally (on a Mac or the NAS) containing all our dependencies. We push this image directly into `urseismogate`.
2. **Lightweight EC2 Launch:** `orchestrator.py` spins up an empty, highly ephemeral EC2 instance in AWS `us-east-2`.
3. **Instant Provisioning:** Instead of spending 15+ minutes (and AWS compute costs) forcing the EC2 server to download, compile, and install complex Python libraries (like SciPy and ObsPy), the orchestrator simply tells the EC2 instance to run:
   ```bash
   docker run urseismogate.earth.rochester.edu/chrisscripts:latest
   ```
4. **Rapid Execution:** The EC2 server pulls the pre-compiled image from our gateway in seconds, executes the `download_pairs.py` script to fetch S3 data, and shuts down.

---

## 3. Why This is Crucial for Full Production

While downloading a single station pair takes seconds, full production requires analyzing millions of cross-correlations. 

**Cost Efficiency:** In full production, we will utilize AWS "Spot Instances" or launch clusters of parallel EC2 workers. If 50 parallel servers had to `pip install` and compile environments individually, we would waste significant compute budget on startup overhead. By pulling the pre-baked image from `urseismogate`, all 50 servers instantly begin executing real scientific workloads the exact second they boot.

**Reproducibility:** If a pipeline fails in AWS, we don't have to guess if a library version updated unexpectedly on the cloud server. The exact same Docker image stored in our registry is executed every single time, ensuring 100% scientific reproducibility across the entire computing cluster.
