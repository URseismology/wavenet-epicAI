# Empire AI: Discovery Workspace

**Status: Phase 2 — not started.**

Per the user's direction, BlueHive discovery (`bluehive_discovery.md`) comes first since it's the simpler of the two targets. This file is a placeholder for the same two-part structure once that's done:

- **Part A — Connectivity checks** (DNS, ping, traceroute, port 22/443, SSH banner against `alpha.empire-ai.org`) — Claude can run these directly, same as BlueHive's Part A.
- **Part B — Cluster resource checks** (partition/scheduler details beyond the known `grace` partition, storage/quota, module system, job queue) — will need the user to log in manually (2FA + password, see `empireai_connections.md`) and paste output back, same model as BlueHive's Part B.
- **Part C — Passwordless/automation strategy** — re-evaluate the four strategies from `bluehive_connections.md` §5 (ControlMaster/ControlPersist, DTN/OAuth-based transfer path, push model, ask support) against Empire AI's specific auth setup once we're here.

Nothing to fill in yet — revisit after `bluehive_discovery.md` Part B is complete.
