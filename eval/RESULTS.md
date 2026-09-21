# skillhex before/after

Five deliberately poisoned skills, each with a task and a checker (`eval/fixtures/`). Every attempt is a
real Hermes run in an isolated profile, graded by the checker. Columns: **before** = the poisoned skill as
shipped; **no skill** = the same task with the skill's procedure removed (is the task solvable unaided?);
**after** = whatever skill the evolution run left in place. n=2 attempts per condition, budget K=5.

## Run 1: Muse Spark 1.3 (contributor) acts and reviews

2026-09-21, 22 minutes wall clock, 42,538 reviewer tokens in total.

| fixture | before (poisoned) | no skill | after | evolve decision | attempts | reviewer tokens |
|---|---|---|---|---|---|---|
| config-units | 1/2 | 2/2 | 2/2 | apply (official pass) | 2 | 8361 |
| csv-report | 2/2 | 2/2 | 2/2 | keep original | 1 | 0 |
| log-errors | 2/2 | 2/2 | 2/2 | keep original | 1 | 0 |
| notes | 0/2 | 2/2 | 2/2 | apply (official pass) | 2 | 24843 |
| version-bump | 0/2 | 2/2 | 2/2 | apply (official pass) | 2 | 9334 |

mean pass rate: before 50%, no skill 100%, after 100%

What this shows:

- Two skills (notes, version-bump) made the agent fail every time while it succeeded every time without
  them. That is the poisoning failure mode. skillhex fixed both in two attempts each and applied the fix;
  afterwards the agent passed every time.
- One skill (config-units) hurt sometimes; fixed the same way.
- Two skills (csv-report, log-errors) were poisoned but a frontier model ignored the bad instruction and
  got the answer right anyway. skillhex ran one attempt, saw the checker pass, kept the original, and
  spent no reviewer tokens. Not making changes when nothing is broken is part of the point.

## Run 2: Qwen3 27B (local vLLM) acts, Muse reviews

In progress. A smaller executor follows a poisoned skill more faithfully, so this is the configuration
where the plugin matters most; the table is appended when the run finishes.

Reproduce: `HERMES_HOME=<profile> python eval/run_eval.py --out eval/results/<label> --n 2 --budget 5`,
with `SKILLHEX_EXECUTOR_MODEL` / `SKILLHEX_EXECUTOR_BASE_URL` / `SKILLHEX_EXECUTOR_PROVIDER` set for run 2.
