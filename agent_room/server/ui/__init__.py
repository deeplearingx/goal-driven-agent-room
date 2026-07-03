"""In-repo thin UI (ADR-0012): Jinja2 templates + single app.js, mounted at /ui.

The UI consumes the same public HTTP surface (`/tasks*`) as any other client.
ADR-0011's 12→4 state mapping is the contract; this UI is one consumer.
"""

from agent_room.server.ui.routes import build_ui_router, ui_static_dir

__all__ = ["build_ui_router", "ui_static_dir"]
