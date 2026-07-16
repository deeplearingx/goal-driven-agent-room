from __future__ import annotations

import json

from agent_room.tools.sandbox import SandboxExecTool


class _Response:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit: int) -> bytes:
        return json.dumps(self.payload).encode()


def test_sandbox_tool_injects_server_context_and_token(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["authorization"] = request.headers["Authorization"]
        captured["payload"] = json.loads(request.data)
        captured["timeout"] = timeout
        return _Response({"exit_code": 0, "output": "passed", "truncated": False, "timed_out": False})

    monkeypatch.setattr("agent_room.tools.sandbox.urlopen", fake_urlopen)
    tool = SandboxExecTool(
        runner_url="https://runner.internal",
        runner_token="secret",
        tenant_id="tenant-a",
        task_id="task-1",
    )
    result = tool.invoke({"profile": "python", "command": ["python", "check.py"]})
    assert result == "[sandbox exit 0]\npassed"
    assert captured["url"] == "https://runner.internal/v1/execute"
    assert captured["authorization"] == "Bearer secret"
    assert captured["payload"] == {
        "tenant_id": "tenant-a",
        "task_id": "task-1",
        "profile": "python",
        "command": ["python", "check.py"],
    }


def test_sandbox_tool_without_task_context_fails_closed(monkeypatch):
    monkeypatch.setattr(
        "agent_room.tools.sandbox.urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("network called")),
    )
    tool = SandboxExecTool(runner_url="https://runner.internal", runner_token="secret")
    assert "task context is not initialized" in tool.invoke(
        {"profile": "python", "command": ["python", "check.py"]}
    )
