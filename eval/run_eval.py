"""Run the before/after evaluation.

    HERMES_HOME=~/skillhex-home python eval/run_eval.py --out eval/results/<label> [--n 2] [--budget 5] [--fixtures notes csv-report]

Uses the HERMES_HOME profile as the base (its model, key, and plugin symlink); each fixture gets its
own isolated copy. Writes results.json and RESULTS.md into --out, updating after every fixture.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import load_fixtures, render_table, run_fixture  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--n", type=int, default=2)
    ap.add_argument("--budget", type=int, default=5)
    ap.add_argument("--fixtures", nargs="*", default=None)
    ap.add_argument("--conditions", nargs="*", default=["before", "no_skill", "evolve", "after"])
    a = ap.parse_args(argv)
    base = Path(os.environ.get("HERMES_HOME") or "~/.hermes").expanduser()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    fixtures = [f for f in load_fixtures(Path(__file__).resolve().parent / "fixtures") if not a.fixtures or f.name in a.fixtures]
    rows = []
    started = time.time()

    def log(msg):
        print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)

    for fx in fixtures:
        log(f"=== {fx.name} ===")
        try:
            row = run_fixture(fx, base, out, n=a.n, budget=a.budget, conditions=tuple(a.conditions), log=log)
        except Exception as e:  # noqa: BLE001
            log(f"[{fx.name}] FAILED: {e!r}")
            row = {"fixture": fx.name, "error": repr(e), "before": {}, "after": {}}
        rows.append(row)
        (out / "results.json").write_text(json.dumps(rows, indent=2))
        meta = (f"# skillhex before/after\n\nbase profile: {base}  ·  n={a.n} attempts per condition  ·  budget K={a.budget}  ·  "
                f"{time.strftime('%Y-%m-%d %H:%M')}  ·  elapsed {round(time.time() - started)}s\n\n")
        (out / "RESULTS.md").write_text(meta + render_table([r for r in rows if r.get("before") is not None]) + "\n")
    log("done")
    print(render_table(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
