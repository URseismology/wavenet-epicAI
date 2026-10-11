# FTAN Dispersion Pipeline — Progress

At-a-glance checklist across all 5 stages x 5 hardware tiers. Detailed experiment logs
are in the per-stage docs linked below. See docs/HANDOFF.md sec 9 for the project-level
rollup (updated only at full-stage-passing milestones, not every tier).

| Stage                          | Local | Bluehive3 (H100) | terravibranium-gpu | Alpha | Beta | Doc |
|---------------------------------|:-----:|:----------------:|:-------------------:|:-----:|:----:|-----|
| A. Data Definition               |  [x]  |       [ ]*       |         [ ]          |  [ ]  |  [ ] | [stage_a_data_definition.md](stage_a_data_definition.md) |
| B. Model Definition                |  [x]  |       [ ]*       |         [ ]          |  [ ]  |  [ ] | [stage_b_model_definition.md](stage_b_model_definition.md) |
| C. Input Batching                   |  [ ]  |       [ ]*       |         [ ]          |  [ ]  |  [ ] | stage_c_input_batching.md (not started) |
| D. Training Process                  |  [ ]  |       [x]        |         [ ]          |  [ ]  |  [ ] | [stage_d_training_process.md](stage_d_training_process.md) |
| E. Prediction/Evaluation               |  [ ]  |       [ ]        |         [ ]          |  [ ]  |  [ ] | stage_e_prediction_evaluation.md (not started) |
| Capstone: A-E chained on Beta            |   —   |        —         |          —           |   —   |  [ ] | (see submit_pipeline_e2e_beta.sh log, not started) |

Bluehive3 tier = bluehive3.circ.rochester.edu, `coe` partition (1x H100 NVL per run), added
2026-10-11. Stage D passed there (final 3-seed run, job 2107428; test M09 IoU
0.783 +- 0.014, val IoU 0.641 +- 0.021). Stage D's local cell stays empty: axon-1 has no GPU,
so Stage D was never run as a local tier.

\* The Stage A cache, Stage B model and Stage C loaders all ran on Bluehive3 as part of
every Stage D job (the full 20,000-sample cache, not the local 100-sample subset), but
none of A-C has had its own Bluehive3 verification or doc update, so those cells are
not ticked.

Rule: whoever completes a stage/tier run updates three things in the same commit — the
cell here, that stage's Hardware Tier Log row (detailed doc), and (only at a
full-stage-passing milestone, not every tier) docs/HANDOFF.md sec 9's summary table.

Last updated: 2026-10-11 (Bluehive3 tier added; Stage D passing on Bluehive3).
