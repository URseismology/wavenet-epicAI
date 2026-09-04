# FTAN Dispersion Pipeline — Progress

At-a-glance checklist across all 5 stages x 4 hardware tiers. Detailed experiment logs
are in the per-stage docs linked below. See docs/HANDOFF.md sec 9 for the project-level
rollup (updated only at full-stage-passing milestones, not every tier).

| Stage                          | Local | terravibranium-gpu | Alpha | Beta | Doc |
|---------------------------------|:-----:|:-------------------:|:-----:|:----:|-----|
| A. Data Definition               |  [x]  |         [ ]          |  [ ]  |  [ ] | [stage_a_data_definition.md](stage_a_data_definition.md) |
| B. Model Definition                |  [x]  |         [ ]          |  [ ]  |  [ ] | [stage_b_model_definition.md](stage_b_model_definition.md) |
| C. Input Batching                   |  [ ]  |         [ ]          |  [ ]  |  [ ] | stage_c_input_batching.md (not started) |
| D. Training Process                  |  [ ]  |         [ ]          |  [ ]  |  [ ] | stage_d_training_process.md (not started) |
| E. Prediction/Evaluation               |  [ ]  |         [ ]          |  [ ]  |  [ ] | stage_e_prediction_evaluation.md (not started) |
| Capstone: A-E chained on Beta            |   —   |          —           |   —   |  [ ] | (see submit_pipeline_e2e_beta.sh log, not started) |

Rule: whoever completes a stage/tier run updates three things in the same commit — the
cell here, that stage's Hardware Tier Log row (detailed doc), and (only at a
full-stage-passing milestone, not every tier) docs/HANDOFF.md sec 9's summary table.

Last updated: 2026-09-04 (Stage A & B local-tier verification, this session).
