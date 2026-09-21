"""Load the repo-root Hermes plugin (__init__.py) against a fake PluginContext."""
import importlib.util
import os
import uuid
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]


class FakeLLM:
    def __init__(self, text='{"verdict": "unknown", "reason": "no signal"}'):
        self.text, self.calls = text, 0

    def complete(self, messages, **kw):
        self.calls += 1
        return SimpleNamespace(text=self.text)


class FakeCtx:
    def __init__(self, config=None, llm=None):
        self.config = dict(config or {})
        self.llm = llm or FakeLLM()
        self.hooks, self.commands, self.cli, self.aux_tasks, self.sections, self.skills = {}, {}, {}, {}, {}, {}

    def get_config(self, key, default=None):
        return self.config.get(key, default)

    def register_hook(self, name, fn):
        self.hooks[name] = fn

    def register_command(self, name, handler, description="", **kw):
        self.commands[name] = handler

    def register_cli_command(self, name, help="", setup_fn=None, handler_fn=None, **kw):
        self.cli[name] = (setup_fn, handler_fn)

    def register_auxiliary_task(self, key, *, display_name, description, defaults=None):
        self.aux_tasks[key] = {"display_name": display_name, "description": description, "defaults": defaults or {}}

    def register_system_prompt_section(self, id, content, **kw):
        self.sections[id] = content

    def register_skill(self, name, path, description="", **kw):
        assert Path(path).exists(), path
        self.skills[name] = Path(path)


def load_plugin(tmp_path, config=None, llm=None):
    """Fresh module instance per test: HERMES_HOME under tmp_path, auto_evolve off unless asked."""
    hh = tmp_path / "hermes"
    hh.mkdir(exist_ok=True)
    os.environ["HERMES_HOME"] = str(hh)
    os.environ.pop("SKILLHEX_CAPTURE_DIR", None)
    os.environ.pop("SKILLHEX_REPLAY", None)
    cfg = {"auto_evolve": False, **(config or {})}
    ctx = FakeCtx(cfg, llm)
    spec = importlib.util.spec_from_file_location(f"skillhex_plugin_{uuid.uuid4().hex[:6]}", ROOT / "__init__.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.register(ctx)
    return mod, ctx, hh


def skill_turn(mod, ctx, sid, prompt="count notes", answer="5", skill="notes"):
    """Drive one skill-guided turn through the hooks."""
    ctx.hooks["on_skill_lifecycle"](action="loaded", skill_name=skill, session_id=sid)
    ctx.hooks["post_tool_call"](tool_name="terminal", args={"command": "ls"}, result="a b", session_id=sid, tool_call_id="c1")
    ctx.hooks["post_llm_call"](session_id=sid, turn_id=f"{sid}-t1",
                               conversation_history=[{"role": "user", "content": prompt}, {"role": "assistant", "content": answer}],
                               assistant_response=answer, user_message=prompt, model="m")
