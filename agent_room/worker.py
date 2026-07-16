"""RabbitMQ worker for horizontally-scaled Agent Room executions.

The Go gateway owns admission, idempotency and the durable outbox.  Workers
consume at-least-once commands, persist LangGraph checkpoints in PostgreSQL,
and publish replayable events.  A small inbox/lease table suppresses duplicate
LLM work when RabbitMQ redelivers a message.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import uuid
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any

import aio_pika
import psycopg
import structlog
from aio_pika import DeliveryMode, ExchangeType, Message

from agent_room.config import RoleBindings, load_settings
from agent_room.events import StreamEventFormatter
from agent_room.graph import GraphCompiler, open_postgres_checkpointer
from agent_room.schemas import TaskRequest
from agent_room.server.react_runtime import (
    build_server_registry,
    build_server_spec,
    load_server_mcp_tools,
    task_workspace,
)
from agent_room.service import AgentRoomService

COMMANDS_EXCHANGE = "agent-room.commands"
EVENTS_EXCHANGE = "agent-room.events"
DEAD_EXCHANGE = "agent-room.dlx"
JOBS_QUEUE = "agent-room.jobs"


class RetryLater(RuntimeError):
    pass


class SkipExecution(RuntimeError):
    """The command is valid but its task is no longer eligible to run."""


class Worker:
    def __init__(self, rabbit_url: str, database_url: str) -> None:
        self.rabbit_url = rabbit_url
        self.database_url = database_url
        self.settings = load_settings()
        self.bindings = RoleBindings(
            settings=self.settings,
            budget=self.settings.budget(),
            guardrail=self.settings.guardrail(),
        )
        self.concurrency = max(1, int(os.getenv("AGENT_ROOM_WORKER_CONCURRENCY", "4")))
        self.lease_seconds = max(60, int(os.getenv("AGENT_ROOM_WORKER_LEASE_SECONDS", "600")))
        self.log = structlog.get_logger("agent_room.worker")
        self.compile_with: GraphCompiler | None = None
        self.events_exchange: aio_pika.abc.AbstractExchange | None = None
        self.running: dict[str, asyncio.Task[None]] = {}
        self.cancelled_by_user: set[str] = set()
        self.spec_cache: dict[str, Any] = {}
        self.mcp_tools: list[Any] = []

    async def run(self) -> None:
        connection = await aio_pika.connect_robust(self.rabbit_url)
        async with connection:
            channel = await connection.channel(publisher_confirms=True)
            await channel.set_qos(prefetch_count=self.concurrency)
            commands = await channel.declare_exchange(
                COMMANDS_EXCHANGE, ExchangeType.DIRECT, durable=True
            )
            self.events_exchange = await channel.declare_exchange(
                EVENTS_EXCHANGE, ExchangeType.TOPIC, durable=True
            )
            await channel.declare_exchange(DEAD_EXCHANGE, ExchangeType.DIRECT, durable=True)
            jobs = await channel.declare_queue(
                JOBS_QUEUE,
                durable=True,
                arguments={
                    "x-queue-type": "quorum",
                    "x-dead-letter-exchange": DEAD_EXCHANGE,
                    "x-dead-letter-routing-key": "dead.command",
                    "x-delivery-limit": 5,
                },
            )
            # New commands are partitioned by immutable execution runtime.
            # Retain the legacy `run` binding so messages accepted before the
            # Go/Eino rollout remain executable.
            await jobs.bind(commands, "run")
            await jobs.bind(commands, "run.python_langgraph")
            await jobs.bind(commands, "resume")
            cancel_queue = await channel.declare_queue(exclusive=True, auto_delete=True)
            await cancel_queue.bind(commands, "cancel")

            self.mcp_tools = list(await load_server_mcp_tools(self.settings))
            async with open_postgres_checkpointer(
                self.bindings, self.database_url
            ) as compile_with:
                self.compile_with = compile_with
                await jobs.consume(self._on_job, no_ack=False)
                await cancel_queue.consume(self._on_cancel, no_ack=False)
                self.log.info("worker.ready", concurrency=self.concurrency)
                await asyncio.Future()

    async def _on_job(self, message: aio_pika.abc.AbstractIncomingMessage) -> None:
        try:
            command = json.loads(message.body)
            message_id = str(command.get("message_id") or message.message_id or "")
            task_id = str(command["task_id"])
            tenant_id = str(command.get("tenant_id") or "")
            command_type = str(command["type"])
            if not message_id or not task_id or not tenant_id:
                raise ValueError("message_id, task_id and tenant_id are required")
            if command_type not in {"run", "resume"}:
                raise ValueError("command type must be run or resume")
        except (ValueError, KeyError, json.JSONDecodeError) as exc:
            self.log.error("worker.invalid_command", error=str(exc))
            await message.reject(requeue=False)
            return

        claimed = await self._claim(message_id, task_id, tenant_id)
        if claimed == "done":
            await message.ack()
            return
        if claimed == "skip":
            await message.ack()
            return
        if claimed == "busy":
            await asyncio.sleep(2)
            await message.nack(requeue=True)
            return

        current = asyncio.current_task()
        assert current is not None
        self.running[task_id] = current
        renewer = asyncio.create_task(self._renew_lease(message_id))
        try:
            await self._execute(command)
            await self._complete(message_id)
            await message.ack()
        except SkipExecution:
            # A user may cancel after the delivery was received but before the
            # graph starts. The gateway already owns the terminal status; do
            # not emit a contradictory task_error or invoke an LLM.
            await self._complete(message_id)
            await message.ack()
        except asyncio.CancelledError:
            if task_id not in self.cancelled_by_user:
                # Process shutdown / channel loss: leave the delivery unacked so
                # RabbitMQ can redeliver it to another worker.
                await self._release(message_id)
                raise
            await self._publish(
                command,
                "task_error",
                {
                    "type": "task_error",
                    "task_id": task_id,
                    "error": "任务已被用户停止",
                    "cancelled": True,
                },
            )
            await self._complete(message_id)
            await message.ack()
        except RetryLater:
            await message.nack(requeue=True)
        except Exception as exc:
            self.log.exception("worker.command_failed", task_id=task_id, error=str(exc))
            delivery_count = int((message.headers or {}).get("x-delivery-count", 0))
            if delivery_count < 4:
                await self._release(message_id)
                await message.nack(requeue=True)
                return
            try:
                await self._publish(
                    command,
                    "task_error",
                    {"type": "task_error", "task_id": task_id, "error": str(exc)},
                )
                await self._complete(message_id)
                # Preserve the poison command in the configured DLQ after the
                # final attempt, while also surfacing a terminal UI event.
                await message.reject(requeue=False)
            except Exception:
                await self._release(message_id)
                await message.nack(requeue=True)
        finally:
            renewer.cancel()
            with suppress(asyncio.CancelledError):
                await renewer
            self.running.pop(task_id, None)
            self.cancelled_by_user.discard(task_id)

    async def _on_cancel(self, message: aio_pika.abc.AbstractIncomingMessage) -> None:
        try:
            command = json.loads(message.body)
            task = self.running.get(str(command.get("task_id", "")))
            if task is not None and not task.done():
                self.cancelled_by_user.add(str(command["task_id"]))
                task.cancel()
            await message.ack()
        except Exception:
            await message.reject(requeue=False)

    async def _execute(self, command: dict[str, Any]) -> None:
        payload = command.get("payload") or {}
        if not isinstance(payload, dict):
            raise ValueError("command payload must be an object")
        if payload.get("execution_runtime", "python_langgraph") != "python_langgraph":
            raise SkipExecution
        task_id = str(command["task_id"])
        tenant_id = str(command.get("tenant_id") or "")
        if not await self._task_is_executable(task_id, tenant_id):
            raise SkipExecution
        assert self.compile_with is not None
        graph_name = payload.get("graph") or self.settings.graph_preset
        if graph_name not in {"full_react", "goal"}:
            raise ValueError("graph must be full_react or goal")
        spec = self.spec_cache.get(graph_name)
        if spec is None:
            spec = build_server_spec(
                self.settings,
                graph_preset=graph_name,
                mcp_tool_names=tuple(tool.name for tool in self.mcp_tools),
            )
            self.spec_cache[graph_name] = spec
        registry = build_server_registry(
            self.settings,
            workspace_override=task_workspace(self.settings, task_id, tenant_id or "default"),
            mcp_tools=self.mcp_tools,
            sandbox_tenant_id=tenant_id or "default",
            sandbox_task_id=task_id,
        )
        service = AgentRoomService(self.compile_with(spec, registry))
        await self._publish(command, "task_started", {"type": "task_started", "task_id": task_id})

        if command["type"] == "resume":
            result = await service.resume(
                task_id,
                str(payload.get("decision", "")),
                at_node=str(payload.get("at_node") or "reviewer"),
            )
        else:
            request = TaskRequest.model_validate(payload)
            formatter = StreamEventFormatter()
            async for raw in service.stream(request, task_id=task_id):
                formatted = formatter.format(raw)
                if formatted is not None:
                    await self._publish(command, formatted["type"], formatted)
            for formatted in formatter.flush():
                await self._publish(command, formatted["type"], formatted)
            result = await service.snapshot(task_id)
        await self._publish(command, "task_finished", result.model_dump(mode="json"))

    async def _publish(self, command: dict[str, Any], event_type: str, data: dict[str, Any]) -> None:
        if self.events_exchange is None:
            raise RuntimeError("events exchange is unavailable")
        if event_type == "usage":
            data = enrich_usage_event(command, data, self.settings)
        message_id = str(uuid.uuid4())
        envelope = {
            "message_id": message_id,
            "task_id": command["task_id"],
            "tenant_id": command.get("tenant_id") or "default",
            "type": event_type,
            "data": data,
            "created_at": datetime.now(UTC).isoformat(),
        }
        body = json.dumps(envelope, ensure_ascii=False, default=str).encode()
        routing_key = f"task.{command.get('tenant_id') or 'default'}.{command['task_id']}.{event_type}"
        await self.events_exchange.publish(
            Message(
                body,
                content_type="application/json",
                delivery_mode=DeliveryMode.PERSISTENT,
                message_id=message_id,
                timestamp=datetime.now(UTC),
                headers={"x-schema-version": "1"},
            ),
            routing_key=routing_key,
            mandatory=True,
        )
    async def _claim(self, message_id: str, task_id: str, tenant_id: str) -> str:
        async with await psycopg.AsyncConnection.connect(self.database_url) as conn, conn.transaction():
            task = await (
                await conn.execute(
                    "SELECT status FROM tasks WHERE task_id=%s AND tenant_id=%s FOR UPDATE",
                    (task_id, tenant_id),
                )
            ).fetchone()
            if task is None or task[0] in {"cancelled", "completed", "failed"}:
                return "skip"

            row = await (
                await conn.execute(
                    """
                    INSERT INTO worker_inbox(message_id,task_id,status,lease_until)
                    VALUES(%s,%s,'processing',now()+(%s * interval '1 second'))
                    ON CONFLICT (message_id) DO NOTHING
                    RETURNING status
                    """,
                    (message_id, task_id, self.lease_seconds),
                )
            ).fetchone()
            if row is not None:
                # A newly observed command may only start from queued. A stale
                # redelivery uses the existing inbox row below.
                if task[0] != "queued":
                    await conn.execute(
                        "UPDATE worker_inbox SET status='done',lease_until=now(),updated_at=now() WHERE message_id=%s",
                        (message_id,),
                    )
                    return "skip"
                await conn.execute(
                    "UPDATE tasks SET status='running',updated_at=now() WHERE task_id=%s AND tenant_id=%s AND status='queued'",
                    (task_id, tenant_id),
                )
                return "claimed"

            row = await (
                await conn.execute(
                    "SELECT status,lease_until<now() FROM worker_inbox WHERE message_id=%s FOR UPDATE",
                    (message_id,),
                )
            ).fetchone()
            if row is None:
                return "busy"
            if row[0] == "done":
                return "done"
            if not row[1]:
                return "busy"
            await conn.execute(
                "UPDATE worker_inbox SET attempts=attempts+1,lease_until=now()+(%s * interval '1 second'),updated_at=now() WHERE message_id=%s",
                (self.lease_seconds, message_id),
            )
            return "claimed"

    async def _task_is_executable(self, task_id: str, tenant_id: str) -> bool:
        async with await psycopg.AsyncConnection.connect(self.database_url) as conn:
            row = await (
                await conn.execute(
                    "SELECT status FROM tasks WHERE task_id=%s AND tenant_id=%s",
                    (task_id, tenant_id),
                )
            ).fetchone()
        return row is not None and row[0] == "running"

    async def _renew_lease(self, message_id: str) -> None:
        while True:
            await asyncio.sleep(max(20, self.lease_seconds // 3))
            async with await psycopg.AsyncConnection.connect(self.database_url) as conn:
                await conn.execute(
                    "UPDATE worker_inbox SET lease_until=now()+(%s * interval '1 second'),updated_at=now() WHERE message_id=%s AND status='processing'",
                    (self.lease_seconds, message_id),
                )

    async def _complete(self, message_id: str) -> None:
        async with await psycopg.AsyncConnection.connect(self.database_url) as conn:
            await conn.execute(
                "UPDATE worker_inbox SET status='done',lease_until=now(),updated_at=now() WHERE message_id=%s",
                (message_id,),
            )

    async def _release(self, message_id: str) -> None:
        async with await psycopg.AsyncConnection.connect(self.database_url) as conn:
            await conn.execute(
                "UPDATE worker_inbox SET lease_until=now(),updated_at=now() WHERE message_id=%s",
                (message_id,),
            )


def enrich_usage_event(
    command: dict[str, Any], data: dict[str, Any], settings: Any
) -> dict[str, Any]:
    """Attach immutable runtime identity and configured cost to a usage event.

    Token counts come from the provider response. Prices are operator-managed
    estimates, so the event keeps the explicit ``estimated_cost_usd`` name.
    """
    enriched = dict(data)
    payload = command.get("payload") or {}
    snapshot = (payload.get("runtime_snapshot") or {}) if isinstance(payload, dict) else {}
    if isinstance(snapshot, dict):
        enriched["runtime_snapshot_id"] = str(snapshot.get("id") or "")
        enriched["prompt_version_id"] = str(snapshot.get("prompt_version_id") or "")
        enriched["model_profile_version_id"] = str(
            snapshot.get("model_profile_version_id") or ""
        )

    input_tokens = max(0, int(enriched.get("input_tokens") or 0))
    output_tokens = max(0, int(enriched.get("output_tokens") or 0))
    input_price = getattr(settings, "price_per_1k_input", None)
    output_price = getattr(settings, "price_per_1k_output", None)
    if input_price is not None or output_price is not None:
        cost = input_tokens / 1000 * float(input_price or 0)
        cost += output_tokens / 1000 * float(output_price or 0)
        enriched["estimated_cost_usd"] = round(cost, 8)
    return enriched


async def _run() -> None:
    rabbit_url = os.getenv("RABBITMQ_URL", "")
    database_url = os.getenv("DATABASE_URL", "")
    if not rabbit_url or not database_url:
        raise RuntimeError("RABBITMQ_URL and DATABASE_URL are required")
    worker = Worker(rabbit_url, database_url)
    loop = asyncio.get_running_loop()
    task = asyncio.create_task(worker.run())
    for sig in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError):
            loop.add_signal_handler(sig, task.cancel)
    await task


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
