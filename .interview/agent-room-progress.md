# Agent Room Interview Progress
- Target role: unknown
- Seniority: unknown
- Interview date: unknown
- Current gate: 1
- Session mode: coach
- Coaching preference: project-first; emphasize motivation, architecture, tradeoffs, and evidence; minimize source-level questions

## Scores
| Date | Gate | Question | Attempt | Score /30 | Result |
|---|---:|---|---:|---:|---|
| 2026-07-06 | 1 | 60-second project introduction | 1 | 9 | retry |
| 2026-07-06 | 3 | Why compute the recursion budget | 1 | 10 | retry |
| 2026-07-06 | 4 | Why execute argv instead of a shell string | 1 | 20 | retry |
| 2026-07-06 | 3 | Why persist graph_preset with TaskState | 1 | 18 | retry |
| 2026-07-06 | 1 | Three differences from a single agent | 1 | 23 | pass |
| 2026-07-06 | 2 | When to use multi-agent vs single-agent | 1 | 24 | pass |
| 2026-07-06 | 2 | Why use centralized LangGraph orchestration | 1 | 24 | pass |
| 2026-07-06 | 2 | Deterministic routing vs LLM strategy | 1 | 26 | pass |
| 2026-07-06 | 2 | Choose workflow or goal mode by task | 1 | 27 | pass |
| 2026-07-06 | 2 | Tests pass but reviewer finds poor design | 1 | 25 | pass |
| 2026-07-06 | 4 | Why passing tests do not prove full correctness | 1 | 27 | pass |
| 2026-07-06 | 2 | Why clarify requirements before development | 1 | 22 | pass |
| 2026-07-06 | 2 | Why persist checkpoints while awaiting user | 1 | 23 | pass |
| 2026-07-06 | 2 | Should a task stop when the browser closes | 1 | 12 | retry |
| 2026-07-06 | 4 | Why prompts are insufficient for tool safety | 1 | 25 | pass |
| 2026-07-06 | 2 | Why not retain only the latest N messages | 1 | 24 | pass |
| 2026-07-06 | 2 | Why retrieve memory instead of loading all history | 1 | 21 | pass |
| 2026-07-06 | 4 | Why iterations, token budget, and tool timeout are separate | 1 | 25 | pass |
| 2026-07-06 | 4 | Why real-model end-to-end validation is required | 1 | 28 | pass |
| 2026-07-06 | 5 | Highest-priority future improvement | 1 | 26 | pass |

## Evidence mastered
- Can explain three system-level values: controlled role workflow, traceability, and mode-based adaptation.
- Can explain the multi-agent tradeoff: specialization and reliability versus token cost, latency, and coordination overhead.
- Can explain why centralized orchestration reduces model-driven routing uncertainty and improves controllability.
- Can distinguish objective mechanical decisions from context-dependent strategy decisions.
- Can select workflow versus goal mode based on whether an executable objective acceptance criterion exists.
- Can separate functional completion from quality debt and communicate the remaining risk to the user.
- Can explain oracle incompleteness through both missing test coverage and ambiguous or incorrect requirements.
- Understands that early clarification avoids executing and revising against an undefined target.
- Understands that durable checkpoints let paused tasks survive process lifetime and resume without losing progress.
- Can explain soft prompt guidance versus deterministic code-enforced tool boundaries.
- Understands that middle-history working memory must be preserved semantically to avoid repeated exploration and goal drift.
- Understands that selective retrieval reduces token cost and irrelevant-history noise.
- Can distinguish overall model-cost controls from per-tool execution-time containment.
- Can explain why fake/unit tests miss model-tool protocol, context behavior, and feedback-loop failures.
- Can propose controlled role specialization while preserving a stable orchestration backbone.

## Active gaps
- Framework named incorrectly as LangChain instead of LangGraph.
- Problem statement is abstract; did not name controllability, observability, evaluation, or hard-task convergence.
- No concrete architecture path, technical decision, measured result, or live-model evidence.
- Ownership and design tradeoffs are unclear.
- Spoken structure contains repetition and unfinished phrases.
- Conflates LangGraph superstep protection, goal-mode max_iterations, and token-budget circuit breaking.
- Claimed a near-budget gradient warning that is not established by repository evidence.
- Needs to separate argv-based execution from command allowlisting: argv removes shell interpretation; the allowlist authorizes the executable.
- Understands state/graph consistency but needs a concrete semantic failure: a goal task restored as full_react can bypass objective verification.
- Overall explanation should additionally mention independent verification and recoverability.
- Avoid claiming multi-agent guarantees quality; describe it as improving reliability for decomposable, high-value, low-tolerance tasks.
- Replace absolute claims such as "independent of model capability" and "guarantees quality" with calibrated reliability claims.
- Conflates client disconnection with explicit cancellation; closing a browser should not terminate a detached run.

## Next action
- Synthesize the overall project into a two-minute interview narrative: problem, architecture, decisions, evidence, and limits.
