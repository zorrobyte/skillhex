**skillhex installed.** It runs by itself: when a skill-guided answer turns out wrong (your next
message says so, or a task checker does), it investigates in the background, tries rewrites of the
skill in throwaway profiles, and keeps only what the evidence supports. Every change is logged,
backed up, and reversible with `/skillhex undo <skill>`.

Recommended config (`~/.hermes/config.yaml`):

```yaml
plugins:
  enabled: [skillhex]
skills:
  creation_nudge_interval: 0     # let skillhex own skill learning (memory review is untouched)
```

Models: `hermes model` → *Auxiliary models* → **SkillHEX reviewer** (pick a stronger tier than the
model that acts, e.g. Sonnet acts / Opus reviews) and **SkillHEX executor** (a cheaper or local
model for the many evaluation attempts). Both blank = your main model, which also works.

Check on it any time: `/skillhex`, `/skillhex show <skill>`, `hermes skillhex report --open`.
