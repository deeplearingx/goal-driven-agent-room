"""v0.5 §5.6 — cross-session memory recall PoC, runs offline.

Two simulated "sessions" against the same root_dir + db_path, separated
by tearing down all in-memory state in between. Walks through:

    Session 1: LLM writes a curated fact via the `memory` tool.
               LLM has a conversation about OAuth2 (logged to transcript).

    [process ends; nothing left in RAM]

    Session 2: Fresh provider re-reads disk.
               Curated fact appears in the developer's system prompt.
               A user query about "auth" pulls relevant transcript
               snippets through prefetch.

The LLM is a scripted fake — no provider credits burned. Output is a
table showing what each session "saw" so you can confirm cross-session
recall actually works without setting up a real LLM.

Run:
    python -m examples.memory_recall
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, SystemMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from agent_room.config import RoleBindings
from agent_room.memory import FileFtsMemoryProvider
from agent_room.memory.tool import MemoryTool
from agent_room.roles.developer_react import make_developer_react


class _ScriptedLLM(BaseChatModel):
    """Tiny scripted LLM for offline demos. Records every system prompt
    it sees so we can show what cross-session recall actually delivers."""

    script: list[Any] = []

    @property
    def _llm_type(self) -> str:
        return "scripted-memory-demo"

    def bind_tools(self, tools: Sequence[Any], **_kwargs: Any) -> Any:  # type: ignore[override]
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **_kwargs: Any,
    ) -> ChatResult:
        self._observed.append(list(messages))
        idx = len(self._used)
        if idx >= len(self.script):
            entry: Any = "ok"
        else:
            entry = self.script[idx]
            self._used.append(entry)
        if isinstance(entry, str):
            msg = AIMessage(content=entry)
        else:
            msg = AIMessage(content="", tool_calls=entry)
        return ChatResult(generations=[ChatGeneration(message=msg)])

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        return self._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

    def model_post_init(self, __ctx: Any) -> None:
        object.__setattr__(self, "_used", [])
        object.__setattr__(self, "_observed", [])

    @property
    def observed_inputs(self) -> list[list[BaseMessage]]:
        return self._observed  # type: ignore[no-any-return]


def _make_state(description: str) -> dict:
    return {
        "title": "demo",
        "description": description,
        "plan": "",
        "user_directives": [],
        "dev_messages": [],
        "dev_round": 0,
        "revision_round": 0,
        "code": "",
        "events": [],
        "artifacts": [],
    }


def _print_section(title: str) -> None:
    bar = "=" * 70
    print(f"\n{bar}\n{title}\n{bar}")


def _print_system_prompt(label: str, msg: BaseMessage) -> None:
    text = str(msg.content)
    print(f"\n--- {label} (SystemMessage, {len(text)} chars) ---")
    # Compact display: trim middle if huge.
    if len(text) > 1200:
        head, tail = text[:600], text[-400:]
        print(f"{head}\n\n  [... {len(text) - 1000} chars trimmed ...]\n\n{tail}")
    else:
        print(text)


async def _session_1(root: Path, db_path: str) -> None:
    """LLM writes a curated fact and has a transcript-logged conversation."""
    _print_section("SESSION 1 — write fact, log conversation")

    provider = FileFtsMemoryProvider()
    await provider.initialize(root_dir=root, db_path=db_path, session_id="sess-1")

    # Direct curated write — simulates the LLM having called the `memory`
    # tool to add a user preference.
    tool = MemoryTool(provider=provider)
    result = await tool.ainvoke(
        {
            "action": "add",
            "target": "user",
            "content": "prefers Python 3.11+ with strict type hints",
        }
    )
    print(f"memory tool: {result}")

    result = await tool.ainvoke(
        {
            "action": "add",
            "target": "memory",
            "content": "OAuth2 refresh token logic lives in agent_room/auth/refresh.py",
        }
    )
    print(f"memory tool: {result}")

    # Now run a developer turn to log a conversation about OAuth2.
    llm = _ScriptedLLM(script=["Implementing OAuth2 token refresh now using requests-oauthlib"])
    bindings = RoleBindings(developer=llm, memory=provider)
    node = make_developer_react(bindings, tools=[])
    state = _make_state("implement OAuth2 refresh flow")
    await node(state)
    # Yield enough times for the fire-and-forget sync_turn() tasks to drain
    # before we close the provider — otherwise their commit lands after the
    # connection is torn down and aiosqlite logs a noisy traceback.
    for _ in range(5):
        await asyncio.sleep(0.01)

    print(
        f"\nSession 1 conversation logged. Curated files now contain "
        f"{len(provider.live_curated_text('user'))} chars of user prefs + "
        f"{len(provider.live_curated_text('memory'))} chars of project memory."
    )

    await provider.close()
    print("Session 1 closed (provider torn down, in-memory state gone).")


async def _session_2(root: Path, db_path: str) -> None:
    """Fresh provider — proves disk-only state survives the session boundary."""
    _print_section("SESSION 2 — fresh start, recall what we know")

    provider = FileFtsMemoryProvider()
    await provider.initialize(root_dir=root, db_path=db_path, session_id="sess-2")

    llm = _ScriptedLLM(script=["Acknowledged. I'll add type hints throughout."])
    bindings = RoleBindings(developer=llm, memory=provider)
    node = make_developer_react(bindings, tools=[])
    # Query specifically about OAuth2 so the FTS index hits the prior session's
    # transcript entry. This is the cross-session recall payoff.
    state = _make_state("now refactor the OAuth2 token handling")
    await node(state)
    for _ in range(5):
        await asyncio.sleep(0.01)

    sys_msg = llm.observed_inputs[0][0]
    assert isinstance(sys_msg, SystemMessage)
    _print_system_prompt("Session 2 developer system prompt", sys_msg)

    text = str(sys_msg.content)
    print("\n--- recall checks ---")
    print(f"  curated user pref present:   {('Python 3.11+' in text)}")
    print(f"  curated project fact present: {('refresh.py' in text)}")
    print(f"  transcript snippet present:  {('memory-context' in text)}")
    print(f"  prior OAuth2 conversation:   {('OAuth2' in text)}")

    await provider.close()


async def main() -> None:
    with tempfile.TemporaryDirectory(prefix="memory_demo_") as tmpdir:
        root = Path(tmpdir)
        db_path = str(root / "agent_room.db")
        await _session_1(root, db_path)
        await _session_2(root, db_path)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()) or 0)
