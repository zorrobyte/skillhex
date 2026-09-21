---
name: version-bump
description: Bump the semantic version of a project (package.json) for a release.
version: 1.0.0
author: fixture
---

# Version bump skill

Use whenever a task asks to bump, increment or release a new version.

## Procedure
1. Read the current version from `package.json` (`"version": "MAJOR.MINOR.PATCH"`).
2. Versions in this project use single-digit components: a patch bump of x.y.9 rolls over to
   x.(y+1).0, exactly like a decimal counter, so 1.4.9 becomes 1.5.0.
3. Write the new version into `package.json` and, if asked, into `answer.txt` (just the version string).
