"""Role node implementations.

Each role is an async function `(state) -> partial state update`. They are
deliberately small — orchestration logic lives in graph.py.
"""

from __future__ import annotations

from agent_room.config import RoleBindings
from agent_room.roles._message import extract_text
from agent_room.roles.delivery import make_delivery
from agent_room.roles.developer import make_developer
from agent_room.roles.planner import make_planner
from agent_room.roles.reviewer import make_reviewer

__all__ = [
    "make_planner",
    "make_developer",
    "make_reviewer",
    "make_delivery",
    "RoleBindings",
    "extract_text",
]
