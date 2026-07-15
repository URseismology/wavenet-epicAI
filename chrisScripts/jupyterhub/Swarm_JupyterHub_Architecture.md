# Elastic GPU Docker Swarm & JupyterHub Architecture

This document outlines the successful migration of the LabAI JupyterHub environment from a single-node setup to an elastic, multi-node Docker Swarm architecture with NVIDIA GPU passthrough.

## 1. Architecture Overview

- **Swarm Manager (`terra4-classnode`)**: 
  - Hosts the `jupyterhub` central routing server via Docker Compose.
  - Spawns lightweight, CPU-bound JupyterLab instances (`student-lab:latest`).
- **Swarm Worker (`terravibranium-gpu`)**: 
  - A dedicated compute node explicitly labeled in the swarm (`gpu=true`).
  - Native NVIDIA container toolkit is configured as the default Docker runtime.
  - Spawns heavy, PyTorch-enabled JupyterLab instances (`student-lab-gpu:latest`) with AI extensions.
- **NAS Backend (`128.151.53.230`)**: 
  - An NFS server exporting `/RAID6/mac_uploads`.
  - Both Swarm nodes explicitly mount this as `/mnt/production_uploads` so student notebooks persist regardless of which machine they are spawned on.

## 2. Key Challenges and Fixes

To achieve seamless spawning across the physical machines, several deeply embedded container routing issues had to be resolved:

### A. Docker Overlay Networking
* **Issue**: `SwarmSpawner` threw a `403 Forbidden` error because `docker-compose.yml` was forcing the Hub to use a local `bridge` network. Swarm services cannot attach to local bridges.
* **Fix**: Recreated the network as an attachable Swarm Overlay network (`jupyter-swarm-net`) and explicitly linked JupyterHub to it.

### B. Swarm Image Pull Policies
* **Issue**: Spawning the GPU profile resulted in a `404 Image Not Found` error from the Manager node, even though the image existed on the GPU Worker node. `SwarmSpawner` inherently runs a `docker inspect` validation check on the *Manager* before dispatching to the Swarm.
* **Fix**: Created a dummy tag (`urseismo/student-lab-gpu:latest`) on the Manager node to bypass the safety check, and explicitly set `pull_policy="Never"` in `jupyterhub_config.py` so the Worker uses its native, locally-built 20GB PyTorch image rather than attempting an internet download.

### C. JupyterHub Container IP Binding
* **Issue**: The Swarm successfully booted the containers, but JupyterHub timed out after 30 seconds when trying to route traffic to them. By default, JupyterHub 4+ injects `JUPYTERHUB_SERVICE_URL=http://127.0.0.1:0/` into the container, forcing the internal Jupyter server to listen exclusively on `localhost` (rejecting external overlay traffic).
* **Fix**: Injected `ip="0.0.0.0"` and `port=8888` into `get_common_swarm_settings()` inside `jupyterhub_config.py`. This forces the Jupyter backend to listen on all internal network interfaces, allowing the Hub's reverse proxy to tunnel traffic into the container perfectly.

### D. NFS IP Whitelisting
* **Issue**: The GPU worker node received a `Permission denied` error when trying to mount the RAID6 NAS drive.
* **Fix**: Logged into the NFS Server (`terravibranium`) and explicitly appended the GPU node's IP address (`128.151.53.156`) to the `/etc/exports` file. 

## 3. Usage

When users log into JupyterHub, they are presented with a dynamic dropdown menu powered by `wrapspawner.ProfilesSpawner`. 
Selecting the **CPU Basic Lab** deploys the container onto the manager node. Selecting the **PyTorch GPU Lab** automatically triggers Swarm scheduling constraints (`node.labels.gpu == true`) to route the container to `terravibranium-gpu`.
