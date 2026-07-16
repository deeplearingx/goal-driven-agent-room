"""Remote sandbox execution tool.

The credential-bearing model worker never invokes Docker itself. It sends an
approved profile + argv vector to a separately deployed sandbox-runner; tenant
and task identity are injected by the server, not accepted from model input.
"""

from __future__ import annotations

import json
from typing import ClassVar
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field, SecretStr


class SandboxExecInput(BaseModel):
    profile: str = Field(min_length=1, max_length=64)
    command: list[str] = Field(min_length=1, max_length=64)


class SandboxExecTool(BaseTool):
    name: str = "sandbox_exec"
    description: str = (
        "Run an argv command inside an isolated, network-disabled container. "
        "The profile is operator-managed and the task workspace is fixed by the server."
    )
    args_schema: ClassVar[type[BaseModel]] = SandboxExecInput
    metadata: dict = {"read_only": False, "risk": "critical"}

    runner_url: str
    runner_token: SecretStr
    tenant_id: str = ""
    task_id: str = ""
    timeout_s: float = 75.0

    def _run(self, profile: str, command: list[str]) -> str:
        if not self.tenant_id or not self.task_id:
            return "[sandbox unavailable: task context is not initialized]"
        if any(not item or len(item) > 4096 for item in command):
            return "[sandbox rejected: command contains an invalid argument]"
        endpoint = self.runner_url.rstrip("/") + "/v1/execute"
        parsed = urlparse(endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username:
            return "[sandbox rejected: runner URL is invalid]"
        payload = json.dumps(
            {
                "tenant_id": self.tenant_id,
                "task_id": self.task_id,
                "profile": profile,
                "command": command,
            },
            separators=(",", ":"),
        ).encode()
        request = Request(
            endpoint,
            data=payload,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.runner_token.get_secret_value()}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with urlopen(request, timeout=self.timeout_s) as response:  # noqa: S310 - URL operator-controlled
                raw = response.read(128 << 10)
        except HTTPError as exc:
            return f"[sandbox runner rejected request: HTTP {exc.code}]"
        except (URLError, TimeoutError, OSError) as exc:
            return f"[sandbox runner unavailable: {type(exc).__name__}]"
        try:
            result = json.loads(raw)
            exit_code = int(result["exit_code"])
            output = str(result.get("output") or "")
            truncated = bool(result.get("truncated"))
            timed_out = bool(result.get("timed_out"))
        except (ValueError, TypeError, KeyError, json.JSONDecodeError):
            return "[sandbox runner returned an invalid response]"
        flags = []
        if timed_out:
            flags.append("timed out")
        if truncated:
            flags.append("output truncated")
        suffix = f" ({', '.join(flags)})" if flags else ""
        return f"[sandbox exit {exit_code}{suffix}]\n{output}"
