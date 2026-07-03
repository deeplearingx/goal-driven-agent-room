# Thin UI smoke test (ADR-0012)

Five-minute walkthrough of the in-repo `/ui` introduced in v1.0 §6.1.4'-§6.1.6'.
No npm, no build step, no external frontend repository.

## Prereqs

```bash
cd /home/ly/agent-room-py
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env   # set ANTHROPIC_API_KEY (or AGENT_ROOM_*_MODEL env vars)
```

## Start the server

```bash
agent-room serve --port 8765
# or:  uvicorn agent_room.server.api:app --port 8765
```

Open <http://localhost:8765/> — root redirects to `/ui`.

## Walk a task end to end

1. **`/ui`** — fill in title `FizzBuzz`, description
   `Implement FizzBuzz with tests; ask if unsure about ranges.`. Click **Run**.
2. The browser navigates to **`/ui/tasks/<task-id>`** and immediately starts
   subscribing to `GET /tasks/<task-id>/stream` (the EventSource-friendly
   variant of `POST /tasks/stream`). The status pill cycles through
   `assigned → planned → in_progress → submitted_for_review → review_passed →
   delivering → completed` per the ADR-0011 derivation table.
3. The **Live events** card streams `task_started`, `node_start`, `node_end`,
   `token`, `review`, `task_finished` rows.
4. When the run finishes, the **Delivery** card shows the final markdown.

## Walk the human-in-the-loop branch

1. Use a description that the reviewer should escalate, e.g.
   `Refactor sorting; pick mergesort or quicksort but don't ask the user.`.
2. With a `planner_gate` or `two_call_review` graph, the reviewer is more
   likely to emit `need_user_decision`; the page status flips to
   `need_user_decision`, the **Reviewer needs your decision** card appears
   with the reviewer's `feedback`, and a textarea + Resume button gate the
   continuation.
3. Type `pick mergesort, document the choice` → **Resume**. The browser POSTs
   to `/tasks/<id>/resume`; the new snapshot lands as `completed`, delivery
   appears, the resume card hides.

## Confirm independence

```bash
ls /home/ly/hermes-web-ui 2>/dev/null && echo "(present, but not used)" || echo "(absent — UI still works)"
```

`agent-room serve` + `/ui` works whether or not hermes-web-ui exists on the
host. The contract is OpenAPI (`/docs`) for production SPAs.

## Screenshot

`docs/img/ui-smoke.png` — recommended capture: `/ui/tasks/<id>` mid-run
showing the status pill, live events, and (if applicable) the resume card.
