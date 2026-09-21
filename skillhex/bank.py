"""Persistent self-verifier test bank (paper §3.1, Appendix A case schema).

A test is a standalone Python script that receives ``SKILLHEX_EPISODE`` (a
directory holding ``episode.json``) and prints exactly one of
``SELF_VERIFIER_RESULT=PASS`` / ``SELF_VERIFIER_RESULT=FAIL``. Tests only see
public information: the episode transcript, tool outputs and any files the
attempt produced. They never see the hidden verifier.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .episodes import _atomic_write

STRENGTHS = ("hard_contract", "environment_preflight", "deterministic_oracle", "proxy_quality", "diagnostic")
HARD_STRENGTHS = ("hard_contract", "environment_preflight")
SEMANTIC_WEIGHTS = {"deterministic_oracle": 1.0, "proxy_quality": 0.5}
RESULT_PREFIX = "SELF_VERIFIER_RESULT="


@dataclass
class TestCase:
    __test__ = False  # keep pytest from collecting this dataclass
    id: str
    hypothesis_ids: List[str]
    assertion_strength: str
    summary: str
    script: str = ""
    test_type: str = "other"
    oracle_source: str = ""
    public_support: str = ""
    diagnosis: Dict[str, str] = field(default_factory=dict)

    def meta(self) -> Dict:
        d = asdict(self)
        d.pop("script")
        return d


class TestBank:
    __test__ = False
    def __init__(self, root: Path | str, timeout: int = 180, python: Optional[str] = None):
        self.root = Path(root).resolve()
        self.timeout = timeout
        self.python = python or sys.executable

    def dir(self, test_id: str) -> Path:
        return self.root / test_id

    def add(self, case: TestCase) -> Path:
        d = self.dir(case.id)
        _atomic_write(d / "case.json", json.dumps(case.meta(), indent=2))
        (d / "test.py").write_text(case.script)
        return d

    def get(self, test_id: str) -> TestCase:
        d = self.dir(test_id)
        meta = json.loads((d / "case.json").read_text())
        return TestCase(script=(d / "test.py").read_text(), **meta)

    def list(self) -> List[TestCase]:
        if not self.root.is_dir():
            return []
        return [self.get(d.name) for d in sorted(self.root.iterdir()) if (d / "case.json").exists()]

    def remove(self, test_id: str) -> None:
        shutil.rmtree(self.dir(test_id), ignore_errors=True)

    def _execute(self, script_path: Path, episode_dir: Path) -> Tuple[Optional[int], str]:
        env = {"SKILLHEX_EPISODE": str(episode_dir), "PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(Path.home())}
        try:
            proc = subprocess.run([self.python, str(Path(script_path).resolve())], capture_output=True, text=True,
                                  timeout=self.timeout, cwd=str(Path(episode_dir).resolve()), env=env)
        except subprocess.TimeoutExpired:
            return None, "timeout"
        out = proc.stdout + "\n" + proc.stderr
        results = [ln.strip()[len(RESULT_PREFIX):] for ln in out.splitlines() if ln.strip().startswith(RESULT_PREFIX)]
        if not results:
            return None, f"no {RESULT_PREFIX}PASS|FAIL line in output (rc={proc.returncode}): {out[-500:]}"
        verdict = results[-1].strip().upper()
        if verdict not in ("PASS", "FAIL"):
            return None, f"unrecognised result {verdict!r}"
        return (1 if verdict == "PASS" else 0), out

    def run(self, test_id: str, episode_dir: Path | str) -> int:
        """phi(c_j, Y_v): 1 pass, 0 fail. Timeouts and malformed output count as fail."""
        verdict, _ = self._execute(self.dir(test_id) / "test.py", Path(episode_dir))
        return verdict if verdict is not None else 0

    def validate(self, case: TestCase, episode_dir: Path | str) -> Tuple[bool, str]:
        """Rule-based validator: schema, syntax, and a dry run that must yield a verdict."""
        if case.assertion_strength not in STRENGTHS:
            return False, f"assertion_strength must be one of {STRENGTHS}"
        if not case.id or not case.hypothesis_ids:
            return False, "test needs an id and at least one hypothesis id"
        try:
            compile(case.script, f"{case.id}.py", "exec")
        except SyntaxError as e:
            return False, f"syntax error: {e}"
        tmp = self.root / ".validate" / case.id
        tmp.mkdir(parents=True, exist_ok=True)
        script = tmp / "test.py"
        script.write_text(case.script)
        verdict, out = self._execute(script, Path(episode_dir))
        shutil.rmtree(tmp, ignore_errors=True)
        if verdict is None:
            return False, out
        return True, f"dry run verdict={'PASS' if verdict else 'FAIL'}"
