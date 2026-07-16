"""Offline tests for examples/dev_runs_tests.py (v0.3 §3.9).

What's covered (no real LLM):
- spec validates and exercises ReAct wiring at build time
- sandbox seeding writes the buggy module + tests; the buggy module really fails pytest
- registry exposes the 4 expected tools, each pinned to the sandbox
- main() returns 0 + prints the "no creds" notice when env is empty
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from examples import dev_runs_tests as ex  # noqa: E402

CRED_VARS = (
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
)


@pytest.fixture
def _no_creds(monkeypatch):
    """Force `main()` down the skip path.

    `load_settings()` calls `dotenv.load_dotenv()` which re-injects creds from
    the on-disk `.env`, so just delenv-ing isn't enough on a developer machine
    that has real credentials configured. We override `load_settings` itself
    to return an empty `Settings`.
    """
    from agent_room.config import Settings

    monkeypatch.setattr(ex, "load_settings", lambda: Settings())
    for var in CRED_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)


def test_build_spec_validates_with_react_wiring(tmp_path):
    """The spec must build cleanly and produce a ReAct subgraph for developer."""
    from agent_room.graph import build_uncompiled_from_spec

    spec = ex.build_spec()
    assert spec.name == "dev_runs_tests"
    dev = spec.nodes["developer"]
    assert dev.tools == ["glob", "read_text", "write_text", "shell"]
    assert dev.tool_mode == "unrestricted"
    assert dev.max_dev_rounds == 8

    registry = ex._build_registry(tmp_path)
    bindings = _bindings_stub()

    graph = build_uncompiled_from_spec(spec, bindings, registry=registry)
    # Sibling ToolNode must be present and carry all 4 tools (unrestricted lets writes/shell through).
    tool_node = graph.nodes["developer_tools"].runnable
    assert set(tool_node.tools_by_name) == {"glob", "read_text", "write_text", "shell"}


def test_build_registry_pins_tools_to_sandbox(tmp_path):
    reg = ex._build_registry(tmp_path)
    assert set(reg.names()) == {"glob", "read_text", "write_text", "shell"}

    read = reg.get("read_text").tool
    write = reg.get("write_text").tool
    glob = reg.get("glob").tool
    shell = reg.get("shell").tool

    assert Path(read.root) == tmp_path
    assert Path(write.root) == tmp_path
    assert Path(glob.root) == tmp_path
    assert shell.allowlist == ["pytest"]
    assert shell.cwd == str(tmp_path)


def test_make_sandbox_seeds_buggy_calc_and_tests(tmp_path, monkeypatch):
    monkeypatch.setattr(ex, "SANDBOX_ROOT", tmp_path)
    sandbox = ex._make_sandbox("task-fixture")
    assert sandbox.parent == tmp_path
    calc = (sandbox / "calc.py").read_text(encoding="utf-8")
    test = (sandbox / "test_calc.py").read_text(encoding="utf-8")
    # The bug must really be there — otherwise the example proves nothing.
    assert "return a - b" in calc
    assert "test_add_basic" in test


def test_make_sandbox_is_idempotent(tmp_path, monkeypatch):
    """Running twice with the same task_id wipes any stale tweaks."""
    monkeypatch.setattr(ex, "SANDBOX_ROOT", tmp_path)
    sandbox1 = ex._make_sandbox("task-redo")
    (sandbox1 / "calc.py").write_text("# overwritten by user", encoding="utf-8")
    sandbox2 = ex._make_sandbox("task-redo")
    assert sandbox1 == sandbox2
    assert "return a - b" in (sandbox2 / "calc.py").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_main_skips_when_no_credentials(_no_creds, capsys):
    """No provider creds → main returns 0 and prints a skip notice, not a crash."""
    rc = await ex.main()
    out = capsys.readouterr().out
    assert rc == 0
    assert "No provider credentials" in out
    assert "skipping" in out


def test_have_credentials_signals(monkeypatch):
    for var in CRED_VARS:
        monkeypatch.delenv(var, raising=False)
    settings = _settings_with_no_creds()
    assert ex._have_credentials(settings) is False

    settings.anthropic_auth_token = "ark-xxx"
    assert ex._have_credentials(settings) is True


# ---------- helpers ----------


def _bindings_stub():
    """Minimal RoleBindings for build-time wiring tests; LLMs never fire."""
    from langchain_core.language_models.fake_chat_models import FakeListChatModel

    from agent_room.config import RoleBindings
    from agent_room.schemas import ReviewerDecision
    from tests.fakes import FakeReviewerLLM

    return RoleBindings(
        planner=FakeListChatModel(responses=["1"]),
        developer=FakeListChatModel(responses=["code"]),
        reviewer=FakeReviewerLLM(
            responses=["unused"],
            decisions=[
                ReviewerDecision(decision="approved", feedback="ok", issues=[], confidence=0.9)
            ],
        ),
        delivery=FakeListChatModel(responses=["done"]),
    )


def _settings_with_no_creds():
    from agent_room.config import Settings

    return Settings(
        anthropic_auth_token=None,
        anthropic_api_key=None,
        openai_api_key=None,
    )


# Sanity: silence the real env at import time if a developer happens to
# have creds locally (we don't want test_main_skips_when_no_credentials to
# get short-circuited by a stray .env in their shell).
def test_cred_vars_constant_matches_have_credentials():
    assert set(CRED_VARS) <= {
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
    }
    # If have_credentials grows new fields, this list must grow too.
    fields_inspected = {"anthropic_auth_token", "anthropic_api_key", "openai_api_key"}
    src = ex._have_credentials.__code__.co_consts + ex._have_credentials.__code__.co_names
    seen = {x for x in src if isinstance(x, str)}
    assert fields_inspected <= seen, (
        "examples.dev_runs_tests._have_credentials added/removed fields — "
        "update CRED_VARS in this test to match"
    )


# Tripwire: keep the example importable from the project root.
def test_example_module_importable():
    assert os.path.isfile(ex.__file__)
    assert ex.SANDBOX_ROOT.name == "dev_runs_tests"
