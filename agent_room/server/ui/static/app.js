// agent-room thin UI client (ADR-0012). No build step. Plain ES2020.
// Implements ADR-0011 §2: derive a 12-state UI label from the 4-state server
// status + last Event + ReviewerDecision.

(function () {
  "use strict";

  const page = window.AGENT_ROOM_PAGE;
  if (page === "index") return wireIndex();
  if (page === "task") return wireTask(window.AGENT_ROOM_TASK_ID);

  // -- index page ----------------------------------------------------------

  function wireIndex() {
    const form = document.getElementById("new-task");
    const status = document.getElementById("new-task-status");
    const open = document.getElementById("open-task");

    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const title = form.title.value.trim();
      const description = form.description.value.trim();
      if (!title || !description) return;
      status.textContent = "creating…";
      status.className = "pill running";
      try {
        const res = await fetch("/tasks", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ title, description }),
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const body = await res.json();
        window.location.href = `/ui/tasks/${encodeURIComponent(body.task_id)}`;
      } catch (err) {
        status.textContent = `failed: ${err.message}`;
        status.className = "pill failed";
      }
    });

    open.addEventListener("submit", (e) => {
      e.preventDefault();
      const id = document.getElementById("task-id").value.trim();
      if (id) window.location.href = `/ui/tasks/${encodeURIComponent(id)}`;
    });
  }

  // -- task page -----------------------------------------------------------

  function wireTask(taskId) {
    const statusEl = document.getElementById("status");
    const eventsEl = document.getElementById("events");
    const resumeCard = document.getElementById("resume-card");
    const resumeFb = document.getElementById("resume-feedback");
    const resumeForm = document.getElementById("resume-form");
    const deliveryCard = document.getElementById("delivery-card");
    const deliveryEl = document.getElementById("delivery");

    let lastEvent = null;
    let lastReview = null;

    const setStatus = (server, ev, review) => {
      const label = deriveStatus(server, ev, review);
      statusEl.textContent = label;
      statusEl.className = `pill ${label}`;
    };

    const appendEvent = (name, data) => {
      const row = document.createElement("div");
      row.className = "ev";
      const meta = data && (data.role || data.node || data.round)
        ? ` ${data.role || data.node || ""}${data.round !== undefined ? ` r${data.round}` : ""}`
        : "";
      row.innerHTML = `<span class="name"></span><span class="meta"></span>`;
      row.querySelector(".name").textContent = name;
      row.querySelector(".meta").textContent = meta;
      eventsEl.appendChild(row);
      eventsEl.scrollTop = eventsEl.scrollHeight;
      lastEvent = { type: name, payload: data || {} };
    };

    const onFinished = (snap) => {
      lastReview = pickLastReview(snap.events || []);
      setStatus(snap.status, lastEvent, lastReview);
      if (snap.delivery) {
        deliveryCard.hidden = false;
        deliveryEl.textContent = snap.delivery;
      }
      if (snap.status === "awaiting_user") {
        resumeCard.hidden = false;
        resumeFb.textContent = lastReview ? lastReview.feedback : "";
      }
    };

    resumeForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      const decision = document.getElementById("decision").value.trim();
      if (!decision) return;
      const submit = resumeForm.querySelector("button");
      submit.disabled = true;
      try {
        const res = await fetch(`/tasks/${encodeURIComponent(taskId)}/resume`, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ decision }),
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const snap = await res.json();
        resumeCard.hidden = true;
        appendEvent("resumed", { decision });
        onFinished(snap);
      } catch (err) {
        appendEvent("resume_error", { error: err.message });
      } finally {
        submit.disabled = false;
      }
    });

    const es = new EventSource(`/tasks/${encodeURIComponent(taskId)}/stream`);
    const handle = (name) => (e) => {
      let data = {};
      try { data = JSON.parse(e.data); } catch (_) {}
      if (name === "task_finished") {
        es.close();
        onFinished(data);
      } else if (name === "task_error") {
        es.close();
        appendEvent("task_error", data);
        setStatus("failed", null, null);
      } else {
        appendEvent(name, data);
        setStatus("running", lastEvent, lastReview);
        if (data && data.decision) lastReview = data;
      }
    };
    [
      "task_started", "task_finished", "task_error",
      "node_start", "node_end", "token", "review", "tool_call", "tool_result",
    ].forEach((n) => es.addEventListener(n, handle(n)));
    es.onerror = () => { /* connection closed naturally on task_finished */ };
  }

  // -- ADR-0011 §2 derivation ---------------------------------------------

  function deriveStatus(server, ev, review) {
    if (server === "completed") return "completed";
    if (server === "failed") return "failed";
    if (server === "awaiting_user") return "need_user_decision";

    // server === "running": refine via last event + reviewer decision.
    if (review) {
      if (review.decision === "approved") return "review_passed";
      if (review.decision === "revision_required") return "revision_required";
    }
    if (ev) {
      const node = (ev.payload && (ev.payload.role || ev.payload.node)) || "";
      if (ev.type === "node_start" && node === "reviewer") return "submitted_for_review";
      if (ev.type === "node_start" && node === "delivery") return "delivering";
      if (ev.type === "node_start" && node === "developer") return "in_progress";
      if (ev.type === "node_start" && node === "planner") return "planned";
      if (ev.type === "task_started") return "assigned";
    }
    return "running";
  }

  function pickLastReview(events) {
    for (let i = events.length - 1; i >= 0; i--) {
      const ev = events[i];
      if (ev && ev.type === "review" && ev.payload && ev.payload.decision) {
        return ev.payload;
      }
    }
    return null;
  }
})();
