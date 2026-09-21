"""Static checks on a candidate SKILL.md before an evaluation attempt is spent.

Errors reject the candidate outright; warnings are passed back to the
reflector as context. Mirrors the cheapest rules of Hermes' skill linter.
"""
from __future__ import annotations

import re
from typing import List, Tuple

_FM = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.S)
_TRUNC = re.compile(r"(\.\.\.\s*\(?(rest|remaining|unchanged|omitted|truncated)|\[\.\.\.\]|<snip>|…\s*\(?unchanged)", re.I)
_INCIDENT = re.compile(r"(\b20\d\d-\d\d-\d\d\b|\bPR #?\d+|\bissue #\d+|Traceback \(most recent|KeyError: )", re.I)
MAX_DESC = 60


def lint_skill(md: str, skill: str) -> Tuple[List[str], List[str]]:
    errors: List[str] = []
    warnings: List[str] = []
    m = _FM.match(md)
    if not m:
        return ["missing YAML frontmatter (--- name/description ---)"], warnings
    fm = m.group(1)
    name = re.search(r"^name:\s*(.+?)\s*$", fm, re.M)
    if not name:
        errors.append("frontmatter has no name")
    elif name.group(1).strip().strip('"\'') != skill:
        errors.append(f"frontmatter name {name.group(1)!r} must be {skill!r}")
    desc = re.search(r"^description:\s*(.+?)\s*$", fm, re.M)
    if not desc:
        warnings.append("frontmatter has no description")
    elif len(desc.group(1).strip().strip('"\'')) > MAX_DESC:
        warnings.append(f"description longer than {MAX_DESC} chars")
    body = md[m.end():].strip()
    if not body:
        errors.append("empty body")
    if _TRUNC.search(body):
        errors.append("body contains truncation markers; candidates must be complete files")
    if _INCIDENT.search(body):
        warnings.append("body reads like an incident log (dates, PR numbers, tracebacks); skills should hold generalizable rules")
    return errors, warnings
