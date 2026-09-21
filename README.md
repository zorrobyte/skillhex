# skillhex

Evidence-gated evolution of agent skills. An implementation of
[SkillHEX: Improving Agent Skills via Hypothesis-Driven Autonomous Exploration and Exploitation](https://arxiv.org/abs/2608.05628)
(Feng et al., 2026) for hosts that load `SKILL.md` files, shipped as a [Hermes Agent](https://github.com/NousResearch/hermes-agent) plugin.

**The problem.** Agents that "self-improve" by rewriting their own skills from a failed transcript,
with the same model that just failed as the reviewer and no test, poison their skill library. Every
later run reads the bad skill first and reproduces the mistake. skillhex makes a skill earn its place.

**What it does, in one paragraph.** It records every turn in which a skill was loaded. It never asks
the model whether the answer was good: the verdict comes from a task checker, or from what *you* said
next, or from an explicit `/skillhex fail`. For a failed turn it works in the background: it writes
falsifiable hypotheses about the failure, turns them into small executable tests that read the
recording, tries rewritten versions of the skill in throwaway Hermes profiles, scores every version
against every test in an evidence matrix, and searches the tree of rewrites so a wrong first
diagnosis cannot eat the budget. A rewrite is applied only if it passes the checker or clearly beats
the original on the evidence without regressing on anything the original got right. The tests stay
with the skill as a regression suite; a later regression plus a "that's wrong" from you rolls the
change back. Everything is logged, backed up, and reversible, and the agent itself can tell you what
changed and undo it when you ask.

## Install

```bash
hermes plugins install zorrobyte/skillhex
hermes plugins enable skillhex
```

```yaml
# ~/.hermes/config.yaml
plugins:
  enabled: [skillhex]
skills:
  creation_nudge_interval: 0     # let skillhex own skill learning (memory review is untouched)
```

That is all. Nothing prompts you. `hermes plugins doctor skillhex` confirms the install.

## Models: one or two, your call

skillhex has two model roles. The **executor** acts: it is the model that runs the task, both live
and in each evaluation attempt. The **reviewer** thinks about failures: it writes hypotheses, tests,
and rewrites. Both are ordinary Hermes auxiliary tasks, so they appear under `hermes model` →
*Auxiliary models*, next to compression and vision.

| mode | executor | reviewer | when |
|---|---|---|---|
| single model (default) | main model | main model | nothing configured. Still better than same-model self-review: the reviewer must state falsifiable claims and is graded by test execution and by your words, never by its own opinion |
| tiered, same vendor | e.g. Sonnet | e.g. Opus | most paying users. The stronger tier is called only when a turn failed; the cheap tier does the volume |
| local + frontier | e.g. Qwen 27B on your GPU | any frontier model | small models cannot diagnose themselves; this is where the gain is largest |

```yaml
auxiliary:
  skillhex_reflector:            # the reviewer
    model: claude-opus-5         # blank = main model
    # base_url / api_key / key_env / reasoning_effort as for any auxiliary task
  skillhex_executor:             # the acting model for evaluation attempts
    model: qwen3-27b
    base_url: http://gpu:8000/v1
    provider: custom
```

`SKILLHEX_MODEL` / `SKILLHEX_BASE_URL` / `SKILLHEX_API_KEY` / `SKILLHEX_REASONING_EFFORT` and
`SKILLHEX_EXECUTOR_MODEL` / `_BASE_URL` / `_PROVIDER` override the config for one process.

## Using it

You mostly do not. When something changed, the agent knows: a short system-prompt section lists
recent skillhex changes, so "what did you change?", "why?", and "undo that" work in conversation
(the agent has a `skillhex` tool with `status`, `show`, `undo`, `mark`, `runs`; it only undoes on request).

| surface | what |
|---|---|
| `/skillhex` | status: episodes per skill, pending failures, recent runs and changes |
| `/skillhex show <skill>` | diff of the last change, last run's decision, report path |
| `/skillhex undo <skill>` | restore the previous SKILL.md |
| `/skillhex ok` / `/skillhex fail <note>` | grade the most recent skill-guided turn yourself |
| `/skillhex runs`, `/skillhex evolve` | list runs; start a run for pending failures now |
| `hermes skillhex status \| episodes \| report [--open] \| evolve \| undo --skill X` | the same from a shell; `report --open` opens the HTML report (diff, evidence matrix, patch tree) |
| `skill_view("skillhex:guide")` | a bundled skill that teaches the agent how to operate all of this |

If `skills.write_approval: true` is set, winners are staged for `/skills pending` / `/skills approve <id>`
instead of applied. skillhex respects the gate you chose; it never adds one.

## Settings

`plugins.entries.skillhex.settings` in config.yaml:

| key | default | meaning |
|---|---|---|
| `auto_evolve` | true | start a background run after a failed skill-guided turn |
| `budget` | 5 | evaluation attempts per run (the paper's K) |
| `min_score_to_apply` | 0.8 | without a checker, a candidate must reach this evidence score and beat the original |
| `auto_rollback` | true | roll back a patch when a banked hard test fails on reuse and you then say the answer was wrong |
| `classify_followups` | true | use the host model to judge whether your next message says the answer failed; false = keyword heuristic only |
| `bank_min_similarity` | 0.5 | prompt similarity (0..1) needed before a skill's banked tests re-run on a new task |
| `snapshot_max_bytes` | 50 MiB | skip the working-directory snapshot above this size |
| `replay_mode` | permissive | cassette replay of network tools in evaluation attempts: permissive (unmatched calls run live) or strict (blocked) |
| `changes_window_days` | 7 | how far back the agent is told about skillhex changes at session start |

## How it works

1. **Capture.** Every turn with a skill loaded becomes an *episode*: transcript, every tool call and
   result, and a snapshot of the working directory taken when the skill loaded. That is the cassette.
2. **Outcome bit.** Never the acting model. A checker script, or the host model classifying your
   *next* message (looked at once), or `/skillhex ok|fail`. Unknown stays unknown.
3. **Hypotheses → tests.** The reviewer maintains failure hypotheses (add / refine / refute / drop a
   test it distrusts). A self-verifier turns them into Python tests that read the cassette and print
   `SELF_VERIFIER_RESULT=PASS|FAIL`. Tests are syntax-checked and dry-run before they count.
4. **Evidence matrix.** Rows are skill versions, columns are tests. A new test replays across every
   cached attempt; a new version runs against every test. Ranking weight goes to *discriminative*
   columns; a hard-test regression is only measured against what the original satisfied, so one bad
   LLM-written test cannot zero every candidate.
5. **Patch tree search.** Candidates form a persistent tree. PUCT with max-backup, priors from the
   reviewer's ordinal ranking, first-play urgency (Appendix E, Algorithm 1).
6. **Fresh attempt per node.** Each candidate runs in a fresh isolated Hermes profile holding only
   that skill version, in a copy of the snapshotted workspace; network tool results are replayed from
   the cassette, local tools run live in the copy. No side effects on your files.
7. **Apply behind the gate.** Through the host's skill ledger, with a backup for undo. The original
   is always a row in the matrix, so a rewrite that scores below it is visibly worse and is not applied.
8. **Regression bank.** Tests some version satisfied travel with the skill and re-run whenever it is
   loaded for a similar task.

## Layout

```
skillhex/                core, host-agnostic, stdlib only
  models.py              Episode, ToolCall
  episodes.py            cassette store; which failures an evolution run has consumed
  hypotheses.py          add / refine / refute / drop_test
  bank.py                self-verifier test bank + validator
  evidence.py            SQLite evidence matrix
  tree.py                patch tree: rank prior, PUCT, max-backup, FPU
  scoring.py             regression gate vs. root, discriminative-column ranking
  search.py              Algorithm 1 with injected Reflector / Verifier / Executor
  reflect.py, verify.py  LLM-backed roles (prompts in prompts/, from the paper's Appendix A)
  capture.py, replay.py  turn recorder; cassette replay of network tools
  outcome.py             follow-up classification
  workspace.py           snapshots; restore the pre-attempt state
  regress.py             per-skill regression bank; rollback
  session_state.py       per-session state that survives host restarts
  changes.py             what skillhex changed (feeds the agent's prompt section)
  report.py              per-run artifacts and the HTML report
  evolve.py              runner: model routing → grade → search → gate → apply/stage → report
  executors/hermes.py    fresh Hermes profile per attempt
plugin.yaml, __init__.py the Hermes plugin (the repo root is the plugin)
skills/guide/SKILL.md    bundled skill: how the agent operates skillhex
fixtures/notes/          deterministic fixture: a notes CLI, a poisoned skill, a checker
tests/                   pytest, no network, no LLM
```

## Running the fixture by hand

```bash
HERMES_HOME=~/skillhex-home hermes chat -Q -q "$(jq -r .prompt fixtures/notes/task.json)" \
    --yolo --in /tmp/ws --max-turns 20            # captured as a failed episode (poisoned skill says 5, truth is 7)
HERMES_HOME=~/skillhex-home hermes skillhex evolve --skill notes-cli \
    --checker fixtures/notes/checker.py --cwd fixtures/notes/workspace --budget 5
HERMES_HOME=~/skillhex-home hermes skillhex report --open
```

## What differs from the paper, deliberately

- Tests belong to the skill and travel with it as a regression suite; a hard failure plus a user
  "that's wrong" rolls the patch back.
- The original skill is always a row in the matrix.
- Static lint rejects a malformed candidate before an attempt is spent.
- Outcome from user corrections and checkers only. The acting model's self-report is never a reward.
- The reviewer can drop a test it distrusts instead of inventing hypotheses to satisfy it.

## Status and honest limits

Core and plugin are unit tested (131 tests, no network). Live runs on the notes fixture (2026-09-21),
reviewer on Muse Spark 1.3 in every case:

| run | executor | outcome source | attempts | result |
|---|---|---|---|---|
| manual, checker | Muse | checker grades the captured episode | 1 | official pass, applied via ledger |
| autonomous, no checker | Muse | user's next message classified by the host model | 1 | evidence 1.00 vs 0.00, early stop, applied; six tests banked and re-run on the next use |
| weak model, checker | Qwen3.8-27B (local vLLM) | checker | 2 | official pass, applied |

Each run used about 22k reviewer tokens.

The benefit is the gate more than the search: even one gated candidate beats writing a skill from a
failed transcript with no check. The paper's gains come from benchmarks with ground-truth checkers.
In the wild most tasks have no checker, so the evidence rests on LLM-written tests; in single-model
mode the same model writes them. That is better than self-grading, not the same as ground truth.
See `eval/` for the before-and-after measurement on deliberately broken skills.

## License

MIT
