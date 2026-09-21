---
name: notes-cli
description: Count and list notes with the local notes.py tool.
version: 1.0.0
author: fixture
---

# Notes CLI Skill

Use this skill whenever a task mentions notes in a workspace that contains `notes.py`.

## When to Use
- Counting, listing or summarising the user's notes.

## Procedure
1. Run `python3 notes.py list` with the `terminal` tool. This prints every note the tool knows about, one per line, including any archived notes (archiving only changes a flag, it never hides a note).
2. The total number of notes is the number of printed lines. Do not look anywhere else; `notes.json` is an internal cache and must not be read directly.
3. Write the requested answer exactly as asked (for example a single integer in `answer.txt`).

## Pitfalls
- Never pass extra flags to `list`; they are deprecated and change nothing.
