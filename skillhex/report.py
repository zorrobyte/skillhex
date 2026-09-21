"""Per-run artifacts and a self-contained HTML report (evidence matrix, patch tree, diff).

``write_artifacts`` saves everything the report needs as JSON next to REPORT.md, so the HTML
can be (re)rendered later without re-running anything: ``hermes skillhex report --open``.
"""
from __future__ import annotations

import difflib
import html
import json
import time
from pathlib import Path
from typing import Any, Dict, Optional

ARTIFACTS = "artifacts.json"
HTML = "report.html"


def write_artifacts(run_dir: Path, **data: Any) -> Path:
    data.setdefault("at", time.time())
    p = Path(run_dir) / ARTIFACTS
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    return p


def _diff(a: str, b: str) -> str:
    return "".join(difflib.unified_diff(a.splitlines(keepends=True), b.splitlines(keepends=True),
                                        fromfile="original/SKILL.md", tofile="candidate/SKILL.md"))


def _diff_html(diff: str) -> str:
    out = []
    for line in diff.splitlines():
        cls = "ctx"
        if line.startswith("+") and not line.startswith("+++"):
            cls = "add"
        elif line.startswith("-") and not line.startswith("---"):
            cls = "del"
        elif line.startswith("@@"):
            cls = "hunk"
        out.append(f'<div class="{cls}">{html.escape(line) or "&nbsp;"}</div>')
    return "\n".join(out) or "<div class='ctx'>(no textual change)</div>"


_CSS = """
:root{--bg:#fff;--fg:#1a1a1a;--muted:#666;--line:#e5e5e5;--add:#e6ffed;--del:#ffeef0;--hunk:#f1f8ff;--ok:#1a7f37;--bad:#cf222e}
@media(prefers-color-scheme:dark){:root{--bg:#0d1117;--fg:#e6edf3;--muted:#8b949e;--line:#30363d;--add:#12261e;--del:#2d1215;--hunk:#121d2f;--ok:#3fb950;--bad:#f85149}}
body{margin:0;padding:24px 16px;background:var(--bg);color:var(--fg);font:15px/1.5 -apple-system,system-ui,sans-serif;max-width:1100px;margin:0 auto}
h1{font-size:22px;margin:0 0 4px}h2{font-size:17px;margin:28px 0 8px;border-bottom:1px solid var(--line);padding-bottom:4px}
.meta{color:var(--muted);margin-bottom:12px}.decision{padding:10px 14px;border:1px solid var(--line);border-radius:8px;margin:12px 0}
.ok{color:var(--ok);font-weight:600}.bad{color:var(--bad);font-weight:600}
pre{background:var(--hunk);border:1px solid var(--line);border-radius:8px;padding:12px;overflow:auto;font:13px/1.45 ui-monospace,Menlo,monospace;white-space:pre}
.diff{border:1px solid var(--line);border-radius:8px;overflow:auto;font:13px/1.45 ui-monospace,Menlo,monospace}
.diff div{padding:0 10px;white-space:pre}.add{background:var(--add)}.del{background:var(--del)}.hunk{background:var(--hunk);color:var(--muted)}
details summary{cursor:pointer;color:var(--muted)}
"""


def render_html(run_dir: Path, data: Optional[Dict[str, Any]] = None) -> Path:
    run_dir = Path(run_dir)
    if data is None:
        data = json.loads((run_dir / ARTIFACTS).read_text())
    e = html.escape
    skill = str(data.get("skill", ""))
    passed = bool(data.get("passed"))
    decision = str(data.get("decision", ""))
    applied = data.get("applied") or ""
    root_c, best_c = str(data.get("root_content") or ""), str(data.get("best_content") or "")
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(float(data.get("at") or time.time())))
    usage = data.get("llm_usage") or {}
    verdict_cls = "ok" if decision.startswith("apply") else "bad"
    parts = [
        "<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>",
        f"<title>skillhex: {e(skill)}</title><style>{_CSS}</style></head><body>",
        f"<h1>skillhex run: {e(skill)}</h1>",
        f"<div class='meta'>{e(when)} · {int(data.get('executor_calls') or 0)} evaluation attempt(s) · "
        f"reflector calls {e(str(usage.get('calls', '?')))}, tokens {e(str(usage.get('prompt_tokens', '?')))}+{e(str(usage.get('completion_tokens', '?')))}</div>",
        f"<div class='decision'><span class='{verdict_cls}'>{e(decision)}</span>"
        + (f"<br><small>{e(str(applied))}</small>" if applied else "")
        + f"<br><small>official check: {'PASS' if passed else 'none / not passed'}; best node {e(str(data.get('best_id')))} "
        f"score {float(data.get('best_score') or 0):.2f} vs original {float(data.get('root_score') or 0):.2f}</small></div>",
        "<h2>Task</h2><pre>" + e(str(data.get("task_prompt") or ""))[:4000] + "</pre>",
        "<h2>Diff: original → chosen candidate</h2><div class='diff'>" + _diff_html(_diff(root_c, best_c)) + "</div>",
        "<h2>Hypotheses</h2><pre>" + e(str(data.get("hypotheses") or "(none)")) + "</pre>",
        "<h2>Evidence matrix</h2><p class='meta'>rows: skill versions · columns: self-verifier tests · ✓ satisfied, ✗ not, · not run</p>"
        "<pre>" + e(str(data.get("matrix_text") or "")) + "</pre>",
        "<h2>Patch tree</h2><pre>" + e(str(data.get("tree_text") or "")) + "</pre>",
        "<details><summary>Chosen candidate SKILL.md</summary><pre>" + e(best_c) + "</pre></details>",
        "<details><summary>Original SKILL.md</summary><pre>" + e(root_c) + "</pre></details>",
        "</body></html>",
    ]
    out = run_dir / HTML
    out.write_text("\n".join(parts))
    return out
