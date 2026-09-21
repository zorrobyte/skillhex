# skillhex before/after

Five deliberately poisoned skills, each with a task and a checker (`eval/fixtures/`). Every attempt is a
real Hermes run in an isolated profile, graded by the checker. Columns: **before** = the poisoned skill as
shipped; **no skill** = the same task with the skill's procedure removed (is the task solvable unaided?);
**after** = whatever skill the evolution run left in place. n=2 attempts per condition, budget K=5,
2026-09-21.

## Run 1: Muse Spark 1.3 (contributor) acts and reviews

22 minutes wall clock, 42,538 reviewer tokens.

| fixture | before (poisoned) | no skill | after | evolve decision | attempts | reviewer tokens |
|---|---|---|---|---|---|---|
| config-units | 1/2 | 2/2 | 2/2 | apply (official pass) | 2 | 8361 |
| csv-report | 2/2 | 2/2 | 2/2 | keep original | 1 | 0 |
| log-errors | 2/2 | 2/2 | 2/2 | keep original | 1 | 0 |
| notes | 0/2 | 2/2 | 2/2 | apply (official pass) | 2 | 24843 |
| version-bump | 0/2 | 2/2 | 2/2 | apply (official pass) | 2 | 9334 |

mean pass rate: before 50%, no skill 100%, after 100%

## Run 2: Qwen3 27B (local vLLM on a 5090) acts, Muse reviews

14 minutes wall clock, 44,713 reviewer tokens. Qwen attempts take about 10 seconds each.

| fixture | before (poisoned) | no skill | after | evolve decision | attempts | reviewer tokens |
|---|---|---|---|---|---|---|
| config-units | 0/2 | 2/2 | 2/2 | apply (official pass) | 2 | 8624 |
| csv-report | 2/2 | 2/2 | 2/2 | keep original | 1 | 0 |
| log-errors | 2/2 | 2/2 | 2/2 | keep original | 1 | 0 |
| notes | 2/2 | 2/2 | 2/2 | apply (official pass) | 2 | 25852 |
| version-bump | 0/2 | 2/2 | 2/2 | apply (official pass) | 2 | 10237 |

mean pass rate: before 60%, no skill 100%, after 100%

## Reading it

- Three skills (notes, version-bump, config-units) made the agent fail on a task it solves every time
  without them. That is the poisoning failure mode. In both runs skillhex fixed each of them in two
  attempts (the original, then one rewrite) and applied the fix; afterwards the agent passed every time.
- Two skills (csv-report, log-errors) were poisoned but both models ignored the bad instruction and got the
  answer right anyway. skillhex ran one attempt, saw the checker pass, kept the original, and spent no
  reviewer tokens. Not changing what isn't broken is part of the point.
- n=2 is small. In run 2 the notes baseline passed twice by luck, then the evolution run's own first attempt
  failed, so it fixed the skill anyway; in run 1 the same skill failed both baseline attempts. Which poisons
  stick is partly chance at this sample size. The direction is not: nothing got worse, and every skill that
  caused a checker failure was repaired.
- The poisons that were ignored produce an obviously wrong intermediate (an awk that prints 0.00, a grep
  count that contradicts the prompt's own wording). Poisons that produce a plausible number (2500 seconds,
  1.5.0, "5 notes") stick, even with a frontier model. Those are the ones a real skill library accumulates.

## Run 3: production regime, partial (Muse acts and reviews, NO checker during evolution, held-out variants)

This is how the plugin actually runs from a session: the evolution step gets no checker and the evidence
gate alone decides what to apply. The checker only grades the after columns. Each fixture also has a
held-out variant (same task text, different data) so "after" isn't just the example the skill was repaired
on. Stopped after two of five fixtures on 2026-09-21; the table is what completed.

| fixture | before (poisoned) | no skill | after | before held-out | after held-out | evolve decision | attempts | reviewer tokens |
|---|---|---|---|---|---|---|---|---|
| config-units | 0/2 | 2/2 | 2/2 | 0/2 | 2/2 | apply (evidence score 1.00 ≥ 0.8, no checker available) | 2 | 13698 |
| csv-report | 2/2 | 2/2 | 2/2 | 2/2 | 2/2 | apply (evidence score 1.00 ≥ 0.8, no checker available) | 5 | 161684 |

mean pass rate: before 50%, no skill 100%, after 100%; held-out before 50%, after 100%

Both rewrites were applied on evidence alone and then passed the checker on both the original and the
held-out data. csv-report was rewritten even though the model was already passing, because the skill really
is wrong (bad column mapping) and the reviewer's tests found that; in a live session nothing would have
triggered a run there, since only a failure verdict starts one. Remaining fixtures (log-errors, notes,
version-bump) not run in this mode yet.

Reproduce: `HERMES_HOME=<profile> python eval/run_eval.py --out eval/results/<label> --n 2 --budget 5`,
with `SKILLHEX_EXECUTOR_MODEL` / `SKILLHEX_EXECUTOR_BASE_URL` / `SKILLHEX_EXECUTOR_PROVIDER` set for run 2.
