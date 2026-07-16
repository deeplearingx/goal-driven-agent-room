"""Typer CLI tests with fake LLMs and a per-test temp SQLite DB."""

from __future__ import annotations

import os

import pytest
from typer.testing import CliRunner

from agent_room.cli import app
from agent_room.config import RoleBindings
from agent_room.schemas import ReviewerDecision
from tests.fakes import bindings_with_fakes

runner = CliRunner()


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    db = tmp_path / "ar.db"
    monkeypatch.setenv("AGENT_ROOM_DB", str(db))
    return db


def _patch_bindings(monkeypatch, **kw):
    """Replace `agent_room.cli.RoleBindings(...)` with a constant fakes binding."""

    fake = bindings_with_fakes(**kw)

    def _factory(*_args, **_kwargs) -> RoleBindings:
        return fake

    monkeypatch.setattr("agent_room.cli.RoleBindings", _factory)
    return fake


def test_help_lists_commands() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for cmd in ("run", "show", "resume", "serve"):
        assert cmd in result.stdout


def test_run_command_completes(tmp_db, monkeypatch) -> None:
    _patch_bindings(monkeypatch, delivery_response="# CLI delivered")

    result = runner.invoke(
        app,
        ["run", "demo task", "--description", "do a thing"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.stdout
    assert "completed" in result.stdout
    assert "CLI delivered" in result.stdout
    assert os.path.exists(tmp_db)


def test_run_then_resume_round_trip(tmp_db, monkeypatch) -> None:
    """Run halts on need_user_decision → resume completes."""

    _patch_bindings(
        monkeypatch,
        code_responses=["v1", "v2"],
        decisions=[
            ReviewerDecision(decision="need_user_decision", feedback="A vs B?"),
            ReviewerDecision(decision="approved", feedback="ok", confidence=0.95),
        ],
        delivery_response="# Resumed via CLI",
    )

    r1 = runner.invoke(
        app,
        ["run", "halts", "--description", "needs guidance"],
        catch_exceptions=False,
    )
    assert r1.exit_code == 0, r1.stdout
    assert "awaiting_user" in r1.stdout

    task_id = next((tok for tok in r1.stdout.split() if tok.startswith("task-")), None)
    assert task_id is not None, f"no task id in CLI output:\n{r1.stdout}"

    r2 = runner.invoke(
        app,
        ["resume", task_id, "--decision", "use approach A"],
        catch_exceptions=False,
    )
    assert r2.exit_code == 0, r2.stdout
    assert "completed" in r2.stdout
    assert "Resumed via CLI" in r2.stdout

    r3 = runner.invoke(app, ["show", task_id], catch_exceptions=False)
    assert r3.exit_code == 0
    assert "completed" in r3.stdout


def test_run_with_solo_preset(tmp_db, monkeypatch) -> None:
    """`--graph solo` runs only the developer node — no reviewer / delivery."""

    _patch_bindings(monkeypatch, code_responses=["solo answer"])

    result = runner.invoke(
        app,
        ["run", "solo task", "--description", "be brief", "--graph", "solo"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.stdout
    # Solo preset has no delivery node and no reviewer → status stays "running".
    assert "running" in result.stdout
    assert "solo answer" in result.stdout
