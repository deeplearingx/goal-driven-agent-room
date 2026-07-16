from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from agent_room.config import Settings
from agent_room.worker import SkipExecution, Worker, enrich_usage_event


class FakeMessage:
    def __init__(self, delivery_count: int = 0) -> None:
        self.body = json.dumps(
            {
                "message_id": "message-1",
                "type": "run",
                "task_id": "task-1",
                "tenant_id": "default",
                "payload": {"title": "x", "description": "y"},
            }
        ).encode()
        self.message_id = "message-1"
        self.headers = {"x-delivery-count": delivery_count}
        self.actions: list[tuple[str, Any]] = []

    async def ack(self) -> None:
        self.actions.append(("ack", None))

    async def nack(self, *, requeue: bool) -> None:
        self.actions.append(("nack", requeue))

    async def reject(self, *, requeue: bool) -> None:
        self.actions.append(("reject", requeue))


def worker() -> Worker:
    return Worker("amqp://unused", "postgresql://unused")


def test_usage_event_is_attributed_to_frozen_snapshot_and_prices() -> None:
    command = {
        "payload": {
            "runtime_snapshot": {
                "id": "snapshot-1",
                "prompt_version_id": "prompt-v3",
                "model_profile_version_id": "model-v2",
            }
        }
    }
    settings = Settings(price_per_1k_input=0.01, price_per_1k_output=0.03)

    enriched = enrich_usage_event(
        command,
        {"type": "usage", "role": "developer", "input_tokens": 1250, "output_tokens": 400},
        settings,
    )

    assert enriched["runtime_snapshot_id"] == "snapshot-1"
    assert enriched["prompt_version_id"] == "prompt-v3"
    assert enriched["model_profile_version_id"] == "model-v2"
    assert enriched["estimated_cost_usd"] == 0.0245


@pytest.mark.asyncio
async def test_transient_failure_is_requeued(monkeypatch: pytest.MonkeyPatch) -> None:
    instance = worker()
    message = FakeMessage(delivery_count=0)
    released: list[str] = []

    async def claim(*_: Any) -> str:
        return "claimed"

    async def execute(_: Any) -> None:
        raise RuntimeError("temporary model outage")

    async def release(message_id: str) -> None:
        released.append(message_id)

    monkeypatch.setattr(instance, "_claim", claim)
    monkeypatch.setattr(instance, "_execute", execute)
    monkeypatch.setattr(instance, "_release", release)

    await instance._on_job(message)  # noqa: SLF001

    assert released == ["message-1"]
    assert message.actions == [("nack", True)]


@pytest.mark.asyncio
async def test_poison_command_is_dead_lettered_after_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instance = worker()
    message = FakeMessage(delivery_count=4)
    published: list[str] = []
    completed: list[str] = []

    async def claim(*_: Any) -> str:
        return "claimed"

    async def execute(_: Any) -> None:
        raise RuntimeError("permanent failure")

    async def publish(_: Any, event_type: str, __: Any) -> None:
        published.append(event_type)

    async def complete(message_id: str) -> None:
        completed.append(message_id)

    monkeypatch.setattr(instance, "_claim", claim)
    monkeypatch.setattr(instance, "_execute", execute)
    monkeypatch.setattr(instance, "_publish", publish)
    monkeypatch.setattr(instance, "_complete", complete)

    await instance._on_job(message)  # noqa: SLF001

    assert published == ["task_error"]
    assert completed == ["message-1"]
    assert message.actions == [("reject", False)]


@pytest.mark.asyncio
async def test_shutdown_leaves_delivery_unacked(monkeypatch: pytest.MonkeyPatch) -> None:
    instance = worker()
    message = FakeMessage()
    released: list[str] = []

    async def claim(*_: Any) -> str:
        return "claimed"

    async def execute(_: Any) -> None:
        raise asyncio.CancelledError

    async def release(message_id: str) -> None:
        released.append(message_id)

    monkeypatch.setattr(instance, "_claim", claim)
    monkeypatch.setattr(instance, "_execute", execute)
    monkeypatch.setattr(instance, "_release", release)

    with pytest.raises(asyncio.CancelledError):
        await instance._on_job(message)  # noqa: SLF001

    assert released == ["message-1"]
    assert message.actions == []


@pytest.mark.asyncio
async def test_terminal_or_cancelled_task_is_acked_without_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instance = worker()
    message = FakeMessage()
    executed = False

    async def claim(*_: Any) -> str:
        return "skip"

    async def execute(_: Any) -> None:
        nonlocal executed
        executed = True

    monkeypatch.setattr(instance, "_claim", claim)
    monkeypatch.setattr(instance, "_execute", execute)

    await instance._on_job(message)  # noqa: SLF001

    assert not executed
    assert message.actions == [("ack", None)]


@pytest.mark.asyncio
async def test_cancelled_between_claim_and_execution_is_not_started(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instance = worker()
    message = FakeMessage()
    completed: list[str] = []

    async def claim(*_: Any) -> str:
        return "claimed"

    async def executable(*_: Any) -> bool:
        return False

    async def complete(message_id: str) -> None:
        completed.append(message_id)

    monkeypatch.setattr(instance, "_claim", claim)
    monkeypatch.setattr(instance, "_task_is_executable", executable)
    monkeypatch.setattr(instance, "_complete", complete)

    await instance._on_job(message)  # noqa: SLF001

    assert completed == ["message-1"]
    assert message.actions == [("ack", None)]


@pytest.mark.asyncio
async def test_python_worker_refuses_eino_command_before_task_lookup() -> None:
    instance = worker()
    with pytest.raises(SkipExecution):
        await instance._execute(  # noqa: SLF001
            {
                "type": "run",
                "task_id": "task-1",
                "tenant_id": "default",
                "payload": {
                    "title": "x",
                    "description": "y",
                    "execution_runtime": "go_eino",
                },
            }
        )
