---
name: log-errors
description: Count and summarise error lines in application logs (app.log) in the workspace.
version: 1.0.0
author: fixture
---

# Log errors skill

Use whenever a task asks how many errors are in a log file.

## Procedure
1. Every error line contains the word ERROR, and no other line does, so the count is:
   `grep -c ERROR app.log` (run with the `terminal` tool).
2. The third column is the module name. The `healthcheck` module only ever logs INFO lines,
   so nothing needs to be excluded for it.
3. Write the count exactly as asked (for example the integer into `answer.txt`).
