"""Goal graph selection must survive the pause/resume and restart boundary."""

from pathlib import Path

from agent_room.server.sessions import SessionStore


async def test_session_persists_selected_graph_across_reopen(tmp_path: Path) -> None:
    db_path = str(tmp_path / "agent_room.db")
    first = SessionStore()
    await first.initialize(db_path)
    created = await first.create("hard goal", session_id="task-goal", graph_preset="goal")
    assert created.graph_preset == "goal"
    await first.close()

    reopened = SessionStore()
    await reopened.initialize(db_path)
    try:
        loaded = await reopened.get("task-goal")
        assert loaded is not None
        assert loaded.graph_preset == "goal"
    finally:
        await reopened.close()
