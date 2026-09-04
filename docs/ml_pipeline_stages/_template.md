Template for per-stage experiment-log reports. Copy this file, fill in every field —
these are fixed-field reports (hypothesis -> what happened -> decision), not free-form
narrative, so a PI can review async without having watched the work happen. See
docs/HANDOFF.md sec 9 for how this rolls up, and PROGRESS.md for the at-a-glance
cross-stage checklist.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STAGE <X> — <NAME>
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

HYPOTHESIS
  What this stage's code claims to do.

SETUP
  Commit hash, data used, config/params.

WHAT WAS TRIED
  Edits/alternatives attempted, including rejected dead ends. Per the plan's
  cross-cutting principle, record what was actually VERIFIED and how — not just that
  inherited/promoted code was reused as-is.

RESULTS
  Concrete numbers/plots. Links to notebook/job-log/checkpoint paths.

HARDWARE TIER LOG
  | Tier                | Status | Date | Job ID | Log link |
  |---------------------|--------|------|--------|----------|
  | Local               |        |      |  n/a   |          |
  | terravibranium-gpu  |        |      |        |          |
  | Alpha               |        |      |        |          |
  | Beta                |        |      |        |          |

DECISION
  What was accepted as canonical going forward, and why.

OPEN QUESTIONS FOR PI
  Anything needing an explicit go/no-go before the next tier/stage.

APPROVAL LOG
  [ ] Reviewed by PI (tolulope.olugboji@rochester.edu) — date, verdict
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
