# Empire AI (Alpha/Grace): Canonical Connection Reference

Canonical copy — migrated from `antigravity-context/Empire_AI_Cluster_Access.md` on 2026-08-21 to consolidate all HPC connection docs under this project. That file now just points here.

## 1. Identity

- **Hostname:** `alpha.empire-ai.org`
- **IP:** `67.99.173.2`
- **Username:** `tolugboji`
- **SSH command:** `ssh tolugboji@alpha.empire-ai.org`
- **Host key:** ED25519, fingerprint `SHA256:/43TThaokD/qXrrWQJDzOXL8H2WzVVHjHQTUif9URRU`

## 2. Authentication

- **2FA (authenticator code) + password required.**
- Same category of constraint as BlueHive (see `bluehive_connections.md` §5) — plain SSH key auth alone won't produce a passwordless connection here either. The same candidate strategies (ControlMaster/ControlPersist, checking for a non-interactive data-transfer path, push model, or asking support about automation-friendly auth) should be re-evaluated for this cluster once Phase 2 (`empireai_discovery.md`) starts — no need to re-derive them from scratch.

## 3. Account Status

- **Early Adopter Access is LIVE** — account already active, no onboarding/access-request step needed.
- Hardware: includes new **Grace CPU-only nodes**, available under the `grace` partition (partition/scheduler details beyond this are Phase 2 — see `empireai_discovery.md`).

## 4. Support & Office Hours

- **NVIDIA Office Hours:** Thursdays, 2:00–3:00 PM ET — optimize workflows & prepare for Beta.
  - [Microsoft Teams link](https://teams.microsoft.com/meet/23999902644638?p=2bJ3FPwXnLdsfXKwfV)
  - [Pre-survey (Google Forms)](https://forms.gle/yA1TiLVFz37YoCDf7)
- **Support Portal / Ticketing:** [Freshdesk Portal](https://empireai.freshdesk.com/support/home)
- **Getting Started Guide:** [Documentation](https://empireai.freshdesk.com/support/solutions/articles/157000374441)

## 5. Administrative Requirements

> **Research Acknowledgments (mandatory):** Empire AI must be cited in all papers/presentations using the cluster, and project highlights must be reported back to them.
> - [Citation guidelines](https://empireai.freshdesk.com/support/solutions/articles/157000359451)
> - [Submit highlights](https://empireai.freshdesk.com/support/solutions/articles/157000363495)

## 6. Status Log

- **2026-08-21:** Content migrated here from `antigravity-context/Empire_AI_Cluster_Access.md`. No new discovery performed yet — Phase 2, starts after BlueHive discovery is complete.
- **When we start this:** apply `access_protocols.md` directly — both the push-via-intermediary and ControlMaster patterns worked out on BlueHive should transfer with just a hostname/user swap (`alpha.empire-ai.org` / `tolugboji`). Worth first confirming this cluster's 2FA behaves the same way (outbound not gated, only inbound) before assuming Protocol A works unchanged.
