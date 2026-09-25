# Migration tooling, 2026-09-25

One-off scripts used to migrate the live 2,000-station run onto the XD.MTAN/XD.RUNG
smoke-test patches. Kept because the findings they produced are load-bearing and an
evaluating agent will want to re-run them, not just read about them. Full context and
numbers: `docs/ncf_pipeline_stages/2026-09-25_production_migration_decisions.md`.

| script | what it does | why it mattered |
|---|---|---|
| `timing_replay_production.py` | Replays `append_channel_data`'s placement arithmetic from raw miniSEED **headers only**, per channel, recovering each pre-patch day's timing offset *and* the days silently dropped. | The pre-patch archive is correctable downstream only via these offsets, and they are unrecoverable once raw SEED is purged. Banked 600 stations; 73 were already purged and are lost. |
| `backup_prepatch_partials.py` | Snapshots mid-flight pre-patch shards before they are wiped, with a size-verified manifest. | Gives a controlled pre/post pair on real production stations (same raw input, two pipelines). 151 stations, 213.9 GB, 0 discrepancies. |
| `select_redo.py` | Selects completed-but-incomplete stations for a full patched redo and **moves** (not copies) their pre-patch artefacts aside. | Found 464 stations holding ~20% of the days they should. A rename within one GPFS filesystem is free and reversible. |
| `repro_typeerror.py` | Reproduces ObsPy's `TypeError: sequence item 0: expected str instance, tuple found` with a full traceback. | Established the failure is **transient**, not deterministic -- isolated re-runs succeeded cleanly where production failed. |
| `watch_completeness.py` | Waits for patched completions, then measures days obtained vs the key index. | The metric that would have caught the ~86% data loss immediately. Completeness is now a measured gate, not an assumption. |
