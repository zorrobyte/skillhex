"""SkillHEX search loop: Appendix E, Algorithm 1.

Components are injected through small protocols so the loop is testable
with scripted fakes and host-agnostic in production:

* ``Reflector.reflect(ctx, must_emit)`` -> hypothesis ops, active ids, ranked patches
* ``Verifier.generate(ctx, active_hypotheses, feedback)`` -> test cases
* ``Executor.execute(skill_content, task, node_id)`` -> episode + binary reward
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol

from .bank import TestBank, TestCase
from .episodes import EpisodeStore
from .evidence import EvidenceMatrix
from .hypotheses import HypothesisStore, Hypothesis
from .lint import lint_skill
from .models import Episode
from .scoring import score_row
from .tree import PatchTree, Node

log = logging.getLogger("skillhex.search")


@dataclass
class Task:
    id: str
    skill: str
    prompt: str
    cwd: Optional[str] = None
    meta: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SearchConfig:
    K: int = 5          # evolved-attempt budget (executor calls after the root)
    B: int = 5          # max children per expansion
    L: int = 3          # reflection rounds per expansion
    C: int = 10         # new tests per verifier call
    V: int = 3          # validation/repair rounds
    beta: float = 1.0   # rank-prior temperature
    c_puct: float = 1.4
    fpu: float = 0.1
    test_timeout: int = 180
    early_stop_score: Optional[float] = None   # no-checker regime: stop once a node's evidence score reaches this


@dataclass
class ReflectionResult:
    decision: str                                   # "need_more_evidence" | "emit_patch"
    hypothesis_ops: List[Dict[str, Any]] = field(default_factory=list)
    active_hypothesis_ids: List[str] = field(default_factory=list)
    patch_candidates: List[Dict[str, Any]] = field(default_factory=list)   # content, rank, summary, hypothesis
    summary: str = ""


@dataclass
class ExecResult:
    episode: Episode
    reward: int


@dataclass
class ReflectionContext:
    node: Node
    tree: PatchTree
    hypotheses: HypothesisStore
    matrix: EvidenceMatrix
    bank: TestBank
    episodes: EpisodeStore
    task: Task
    round: int = 0

    @property
    def bank_ids(self) -> List[str]:
        return [c.id for c in self.bank.list()]

    def node_episode(self, node: Optional[Node] = None) -> Optional[Episode]:
        n = node or self.node
        if n.episode_id and self.episodes.exists(self.task.skill, n.episode_id):
            return self.episodes.load(self.task.skill, n.episode_id)
        return None


class Reflector(Protocol):
    def reflect(self, ctx: ReflectionContext, must_emit: bool) -> ReflectionResult: ...


class Verifier(Protocol):
    def generate(self, ctx: ReflectionContext, active: List[Hypothesis],
                 feedback: Optional[List[str]] = None) -> List[TestCase]: ...


class Executor(Protocol):
    def execute(self, skill_content: str, task: Task, node_id: str) -> ExecResult: ...


@dataclass
class SearchResult:
    passed: bool
    best: Optional[Node]
    executor_calls: int
    tree_text: str
    matrix_text: str


class SkillSearch:
    def __init__(self, workdir: Path | str, task: Task, initial_skill: str,
                 reflector: Reflector, verifier: Verifier, executor: Executor,
                 config: Optional[SearchConfig] = None):
        self.workdir = Path(workdir)
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.task = task
        self.initial_skill = initial_skill
        self.reflector, self.verifier, self.executor = reflector, verifier, executor
        self.cfg = config or SearchConfig()
        self.tree = PatchTree(self.workdir / "tree.json")
        self.hypotheses = HypothesisStore(self.workdir / "hypotheses.json")
        self.bank = TestBank(self.workdir / "tests", timeout=self.cfg.test_timeout)
        self.matrix = EvidenceMatrix(self.workdir / "evidence.db")
        self.episodes = EpisodeStore(self.workdir / "episodes")
        self.executor_calls = 0

    # ---- helpers -----------------------------------------------------------------
    def _ctx(self, node: Node, rnd: int = 0) -> ReflectionContext:
        return ReflectionContext(node=node, tree=self.tree, hypotheses=self.hypotheses, matrix=self.matrix,
                                 bank=self.bank, episodes=self.episodes, task=self.task, round=rnd)

    def _execute(self, node: Node) -> int:
        self.executor_calls += 1
        res = self.executor.execute(node.content, self.task, node.id)
        ep = res.episode
        ep.skill, ep.skill_version, ep.task_id = self.task.skill, node.id, self.task.id
        ep.outcome = "pass" if res.reward == 1 else "fail"
        ep.outcome_source = ep.outcome_source or "executor"
        self.episodes.save(ep)
        node.episode_id = ep.id
        return res.reward

    def _episode_dir(self, node: Node) -> Optional[Path]:
        if node.episode_id and self.episodes.exists(self.task.skill, node.episode_id):
            return self.episodes.dir(self.task.skill, node.episode_id)
        return None

    def _replay_tests(self, node: Node) -> None:
        """New row: run every bank test against this node's cached output (eq. 6, first line)."""
        d = self._episode_dir(node)
        if d is None:
            return
        for case in self.bank.list():
            if self.matrix.get(node.id, case.id) is None:
                self.matrix.record(node.id, case.id, self.bank.run(case.id, d))

    def _replay_column(self, case: TestCase) -> None:
        """New column: run one test against every evaluated node (eq. 6, second line)."""
        for n in self.tree.evaluated_nodes():
            d = self._episode_dir(n)
            if d is not None:
                self.matrix.record(n.id, case.id, self.bank.run(case.id, d))

    def _rescore(self, node: Node, reward: Optional[int] = None) -> None:
        r = node.reward if reward is None else reward
        s = score_row(self.matrix.row(node.id), self.bank.list(), reward=r)
        self.tree.evaluate(node.id, score=s, reward=r or 0, episode_id=node.episode_id)

    def _rescore_all(self) -> None:
        for n in self.tree.evaluated_nodes():
            self._rescore(n)

    def _prune_refuted(self, orphaned: List[str]) -> None:
        if not orphaned:
            return
        for tid in orphaned:
            self.bank.remove(tid)
        self.matrix.drop_tests(orphaned)
        self._rescore_all()

    def _self_verify(self, node: Node, active_ids: List[str]) -> int:
        """Generate, validate (with repair feedback), replay and link new tests. Returns count accepted."""
        active = [h for h in self.hypotheses.active() if not active_ids or h.id in active_ids]
        if not active:
            return 0
        ep_dir = self._episode_dir(node) or self._episode_dir(self.tree.root())
        if ep_dir is None:
            return 0
        accepted = 0
        feedback: Optional[List[str]] = None
        for _ in range(self.cfg.V):
            drafts = self.verifier.generate(self._ctx(node), active, feedback)
            if not drafts:
                break
            feedback = []
            for case in drafts[: self.cfg.C]:
                if any(c.id == case.id for c in self.bank.list()):
                    feedback.append(f"{case.id}: id already exists")
                    continue
                ok, msg = self.bank.validate(case, ep_dir)
                if not ok:
                    feedback.append(f"{case.id}: {msg}")
                    continue
                self.bank.add(case)
                log.info("accepted test %s [%s] for %s", case.id, case.assertion_strength, ",".join(case.hypothesis_ids))
                for hid in case.hypothesis_ids:
                    try:
                        self.hypotheses.link_test(hid, case.id)
                    except KeyError:
                        pass
                self._replay_column(case)
                accepted += 1
            if not feedback:
                break
        if accepted:
            self._rescore_all()
        return accepted

    def _expand(self, node: Node) -> bool:
        """Hypothesis-driven expansion (Algorithm 1, lines 20-36). Returns False if exhausted."""
        candidates: List[Dict[str, Any]] = []
        for l in range(1, self.cfg.L + 1):
            must_emit = l == self.cfg.L
            res = self.reflector.reflect(self._ctx(node, l), must_emit)
            ops = self.hypotheses.apply_ops(res.hypothesis_ops)
            self._prune_refuted(ops["orphaned_tests"])
            if res.patch_candidates:
                candidates = res.patch_candidates
                break
            self._self_verify(node, res.active_hypothesis_ids)
        kept = []
        for c in candidates:
            errors, warnings = lint_skill(c.get("content", ""), self.task.skill)
            if errors:
                log.info("rejected candidate (%s): %s", c.get("summary", "")[:60], "; ".join(errors))
                continue
            if warnings:
                c["summary"] = (c.get("summary", "") + " [lint: " + "; ".join(warnings) + "]")[:300]
            kept.append(c)
        candidates = kept
        if not candidates:
            self.tree.mark_exhausted(node.id)
            return False
        ranked = sorted(candidates, key=lambda c: c.get("rank", 99))[: self.cfg.B]
        self.tree.expand(node.id, ranked, beta=self.cfg.beta)
        return True

    # ---- main loop ---------------------------------------------------------------
    def run(self, initial_episode: Optional[Episode] = None) -> SearchResult:
        root = self.tree.root() if self.tree.root_id else self.tree.add_root(self.initial_skill)
        if not root.evaluated:
            if initial_episode is not None:
                initial_episode.skill, initial_episode.skill_version = self.task.skill, root.id
                initial_episode.outcome = initial_episode.outcome or "fail"
                self.episodes.save(initial_episode)
                root.episode_id = initial_episode.id
                r0 = 1 if initial_episode.outcome == "pass" else 0
            else:
                r0 = self._execute(root)
            if r0 == 1:
                self._rescore(root, 1)
                return self._result(True)
            res = self.reflector.reflect(self._ctx(root), False)      # seed hypotheses
            self.hypotheses.apply_ops(res.hypothesis_ops)
            self._self_verify(root, res.active_hypothesis_ids)
            self._replay_tests(root)
            self._rescore(root, 0)

        k = 0
        while k < self.cfg.K:
            vid = self.tree.select(self.cfg.c_puct, self.cfg.fpu)
            if vid is None:
                break
            v = self.tree.get(vid)
            if not v.evaluated:
                log.info("attempt %d/%d: executing node %s (%s)", k + 1, self.cfg.K, v.id, v.summary[:80])
                r = self._execute(v)
                k += 1
                log.info("node %s official=%s", v.id, "PASS" if r == 1 else "FAIL")
                if r == 1:
                    self._replay_tests(v)
                    self._rescore(v, 1)
                    return self._result(True)
                self._replay_tests(v)
                self._rescore(v, 0)
                if self.cfg.early_stop_score is not None and (v.score or 0) >= self.cfg.early_stop_score \
                        and self.bank.list():
                    log.info("early stop: node %s evidence score %.2f >= %.2f", v.id, v.score or 0, self.cfg.early_stop_score)
                    return self._result(False)
            else:
                log.info("expanding node %s", v.id)
                self._expand(v)
                log.info("tree:\n%s", self.tree.render())
        return self._result(False)

    def _result(self, passed: bool) -> SearchResult:
        return SearchResult(passed=passed, best=self.tree.best(), executor_calls=self.executor_calls,
                            tree_text=self.tree.render(), matrix_text=self.matrix.render())
