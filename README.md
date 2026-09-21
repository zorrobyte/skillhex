# skillhex

Evidence-gated evolution of agent skills. An implementation of
[SkillHEX: Improving Agent Skills via Hypothesis-Driven Autonomous Exploration and Exploitation](https://arxiv.org/abs/2608.05628)
(Feng et al., 2026) for hosts that load `SKILL.md` files, with a Hermes Agent plugin as the first host.

The problem it solves: agents that "self-improve" by rewriting their own skills from a failed
transcript, with the same model that just failed as the reviewer, poison their skill library.
Every later run reads the bad skill first and reproduces the mistake. skillhex makes a skill
earn its place:

1. **Capture.** Every turn in which a skill was loaded is recorded as an *episode*: the
   transcript, every tool call and result, the files produced. That recording is a cassette.
2. **Outcome bit.** The acting model never grades itself. The verdict comes from a task
   checker, or from what the user said next (classified by the host model), or an explicit
   `/skillhex fail`. Unknown stays unknown.
3. **Hypotheses → tests.** For a failed episode, a reflection step maintains falsifiable
   failure hypotheses. A self-verifier turns them into small Python tests that read the
   cassette and print `SELF_VERIFIER_RESULT=PASS|FAIL`. Tests are validated before they
   count; refuted hypotheses have their tests pruned.
4. **Evidence matrix.** Rows are skill versions, columns are tests. A new test replays across
   every cached attempt; a new skill version runs against every test. Cheap, dense signal.
5. **Patch tree search.** Candidate skill revisions are kept as a persistent tree. Selection
   is PUCT with max-backup, priors from the reflector's ordinal ranking, and first-play
   urgency, so a wrong early diagnosis cannot eat the whole budget: the search jumps back to
   a preserved sibling when a branch stalls.
6. **Fresh attempt per node.** Each candidate is evaluated in a fresh, isolated Hermes profile
   holding only that skill version, with recorded tool results replayed from the cassette so
   evaluation has no side effects.
7. **Apply behind the gate.** A patch is applied only if it passes the official checker on
   replay, or, when no checker exists, reaches a high evidence score and beats the original.
   Writes go through the host's skill ledger and are rollback-able. The original row (no
   patch) stays in the matrix forever, so a patch that scores below it is visibly worse.

Nothing asks the user for anything. It just runs: a failed skill-guided turn is detected from
the user's next message, the evolution runs as a detached background process, and the winner is
applied (or not) on the evidence. Session state persists on disk, so this works across CLI
`--resume` and gateway restarts. Without a checker the search stops early once a candidate reaches
the apply threshold on the evidence, so it does not burn the whole budget on an already-fixed skill.

## Layout

```
skillhex/                core, host-agnostic, stdlib only
  models.py              Episode, ToolCall
  episodes.py            cassette store (one JSON per episode)
  hypotheses.py          add / refine / refute, tests linked per hypothesis
  bank.py                self-verifier test bank + rule-based validator
  evidence.py            SQLite evidence matrix (rows × tests)
  tree.py                patch tree: rank prior, PUCT, max-backup, FPU
  scoring.py             hard-constraint gate, then weighted semantic mean
  search.py              Algorithm 1 with injected Reflector / Verifier / Executor
  reflect.py, verify.py  LLM-backed roles (prompts in prompts/, ported from the paper's Appendix A)
  capture.py, replay.py  turn recorder and cassette replay policy
  outcome.py             follow-up classification (user's next message → pass/fail/unknown)
  evolve.py              runner: grade → search → gate → apply → report
  executors/hermes.py    fresh Hermes profile per attempt
plugin.yaml, __init__.py  the Hermes plugin entry (repo root is the plugin)
fixtures/notes/          deterministic fixture: a notes CLI, a poisoned skill, a checker
tests/                   pytest, no network, no LLM
```

## Hermes plugin

The repository root is the plugin (`plugin.yaml` + `__init__.py` beside the `skillhex/` package):

```bash
hermes plugins install <owner>/skillhex        # or: git clone ... ~/.hermes/plugins/skillhex
hermes plugins enable skillhex
# config.yaml
plugins:
  enabled: [skillhex]
skills:
  creation_nudge_interval: 0     # let skillhex own skill learning; memory review is untouched
```

Hooks used: `on_skill_lifecycle`, `post_tool_call`, `pre_tool_call` (replay), `pre_llm_call`
(follow-up judging), `post_llm_call` (episode finalize), `on_session_end` (background
evolution). Commands: `/skillhex status|ok|fail <note>|evolve`, `hermes skillhex status|episodes|evolve|report`.

Settings (`plugins.entries.skillhex.settings`): `auto_evolve` (default true), `replay_mode`
(`permissive` | `strict`), `classify_followups` (default true), `min_score_to_apply` (0.8).

Reflection and self-verification use the profile's configured model by default; set
`SKILLHEX_BASE_URL` / `SKILLHEX_API_KEY` / `SKILLHEX_MODEL` to route them elsewhere (a stronger
model for reflection, a cheap one for executor attempts).

## Running the fixture

```bash
HERMES_HOME=~/skillhex-home hermes chat -Q -q "$(jq -r .prompt fixtures/notes/task.json)" \
    --yolo --in /tmp/ws --max-turns 20            # captured as a failed episode (poisoned skill says 5, truth is 7)
HERMES_HOME=~/skillhex-home hermes skillhex evolve --skill notes-cli \
    --checker fixtures/notes/checker.py --cwd fixtures/notes/workspace --budget 5
HERMES_HOME=~/skillhex-home hermes skillhex report
```

## What differs from the paper, deliberately

- Tests belong to the skill and travel with it as a regression suite that re-runs whenever the skill is loaded for a similar task; a hard failure plus a user "that's wrong" rolls the patch back.
- The original skill is always a row in the matrix; a no-skill baseline row is planned.
- Static lint (frontmatter, name, completeness) rejects a candidate before an attempt is spent.
- Outcome from user corrections and checkers only. The acting model's self-report is never a reward.

## Status

Core and plugin are implemented and unit tested. The Hermes executor and the plugin have been
exercised live against Muse Spark 1.3 (reflection, verification and attempts) on the notes fixture. SkillsBench reproduction and
SkillFlow transfer evaluation are next.

## License

MIT
