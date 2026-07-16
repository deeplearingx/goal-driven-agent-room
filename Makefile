PYTHON ?= .venv/bin/python
UI_DIR ?= mulit_agent_web_ui

.PHONY: eval test lint typecheck build-ui demo

eval:
	$(PYTHON) -m evals.run

test:
	$(PYTHON) -m pytest -q

lint:
	$(PYTHON) -m ruff check agent_room evals tests

typecheck:
	$(PYTHON) -m mypy agent_room

# Build the pixel UI into $(UI_DIR)/dist (installs deps on first run).
build-ui:
	cd $(UI_DIR) && (test -d node_modules || npm install) && npm run build

# One command: build the UI, then serve everything (UI at /app, API same-origin).
demo: build-ui
	$(PYTHON) -m agent_room.cli serve
