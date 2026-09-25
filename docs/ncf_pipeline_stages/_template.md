Template for per-stage experiment-log reports. Copy this file, fill in every field —
these are fixed-field reports (hypothesis -> what happened -> decision), not free-form
narrative, so a PI can review async without having watched the work happen. Mirrors
docs/ml_pipeline_stages/_template.md's format exactly, for consistency across the two
staged efforts in this repo. See PROGRESS.md in this directory for the at-a-glance
cross-stage checklist.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STAGE <X> — <NAME>
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

HYPOTHESIS
  What this stage claims to establish or produce.

SETUP
  Commit hash, data/credentials used, config/params.

WHAT WAS TRIED
  Edits/alternatives attempted, including rejected dead ends and things confirmed by
  direct test rather than assumed from documentation.

RESULTS
  Concrete numbers/plots. Links to report/dataset/log paths.

HARDWARE TIER LOG
  | Tier            | Status | Date | Job ID | Log link |
  |------------------|--------|------|--------|----------|
  | axon-1 (local)    |        |      |  n/a   |          |
  | mothership         |        |      |        |          |
  | terravibranium      |        |      |        |          |
  | Bluehive             |        |      |        |          |

DECISION
  What was accepted as canonical going forward, and why.

OPEN QUESTIONS FOR PI
  Anything needing an explicit go/no-go before the next tier/stage.

APPROVAL LOG
  [ ] Reviewed by PI (tolulope.olugboji@rochester.edu) — date, verdict
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
