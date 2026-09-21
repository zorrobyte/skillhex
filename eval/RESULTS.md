# skillhex before/after (in progress)

Run started 2026-09-21 11:50, Muse Spark 1.3 (contributor) as both executor and reviewer, n=2 attempts per
condition, budget K=5. Every attempt is a real Hermes run in an isolated profile, graded by the fixture's
checker. Columns: **before** = the poisoned skill as shipped; **no skill** = same task with the skill's
procedure removed (is the task solvable unaided?); **after** = whatever skill the evolution run left in place.

| fixture | before (poisoned) | no skill | after | evolve decision | attempts | reviewer tokens |
|---|---|---|---|---|---|---|
| config-units | 1/2 | 2/2 | 2/2 | apply (official pass) | 2 | 8361 |
| csv-report | 2/2 | 2/2 | 2/2 | keep original | 1 | 0 |
| log-errors | 2/2 | 2/2 | 2/2 | keep original | 1 | 0 |

mean pass rate: before 83%, no skill 100%, after 100%

Partial: 3 of 5 fixtures done; this file is regenerated when the run finishes. A second run with
Qwen3 27B (local) as executor and Muse as reviewer follows, since a frontier executor often ignores a
poisoned skill and gets the answer right anyway (csv-report, log-errors above), which leaves nothing to fix.

Reproduce: `HERMES_HOME=<profile> python eval/run_eval.py --out eval/results/<label> --n 2 --budget 5`.
