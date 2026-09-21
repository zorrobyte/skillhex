---
name: skillhex-guide
description: How the skillhex plugin evolves skills, how to read its reports, and how to inspect, undo, or grade its changes on the user's behalf.
---

# skillhex guide

skillhex watches skill-guided turns. When the user's next message (or a task checker) shows the
answer was wrong, it investigates in the background: it writes hypotheses about the failure,
writes small executable tests for them, tries rewritten versions of the skill in throwaway
Hermes profiles, and keeps the version the evidence supports. The acting model never grades
itself; only checkers and the user's words count as outcomes. Every change is logged, backed up,
and reversible.

## The `skillhex` tool

| action | args | what it does |
|---|---|---|
| `status` | | per-skill episode counts, pending failures, recent runs and changes |
| `show` | `skill` | unified diff between the skill's previous and current SKILL.md, plus the last run's decision |
| `undo` | `skill` | restore the previous SKILL.md (the backup skillhex made before applying) |
| `mark` | `verdict` = `ok` or `fail`, optional `note` | record the user's verdict on the most recent skill-guided turn; `fail` schedules an evolution run |
| `runs` | | list evolution runs with decisions and report paths |

Use `mark` when the user says a skill-guided answer was right or wrong in words that the automatic
judge may have missed. Use `undo` when the user wants a change reverted. Do not undo on your own.

## Reading a run

Each run directory under `<HERMES_HOME>/skillhex/runs/` has `REPORT.md`, `report.html` (open in a
browser), `summary.json`, and `attempts/` with one isolated profile per candidate evaluation.

- **Evidence matrix**: rows are skill versions, columns are self-verifier tests. ✓ means the
  attempt with that version satisfied the test. A candidate is only applied if it does not regress
  on any hard test the original satisfied, and either passes the official checker or beats the
  original by the configured evidence score.
- **Patch tree**: the search over rewrites. `*` marks the chosen node.

## Settings (config.yaml, `plugins.entries.skillhex.settings`)

`auto_evolve`, `budget`, `min_score_to_apply`, `auto_rollback`, `classify_followups`,
`bank_min_similarity`, `snapshot_max_bytes`, `replay_mode`. Models: `auxiliary.skillhex_reflector`
(the reviewer; pick a stronger tier than the acting model) and `auxiliary.skillhex_executor`
(the model that runs candidate attempts; pick a cheaper one), both editable under `hermes model`.
If `skills.write_approval` is on, winners are staged for `/skills pending` instead of applied.

## User-facing commands

`/skillhex` (status), `/skillhex show <skill>`, `/skillhex undo <skill>`, `/skillhex ok|fail <note>`,
`/skillhex runs`, `/skillhex evolve`; from a shell, `hermes skillhex status|episodes|report [--open]|evolve|undo`.
