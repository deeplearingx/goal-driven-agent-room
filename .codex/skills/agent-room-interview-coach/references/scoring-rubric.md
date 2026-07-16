# Interview Scoring Rubric

Score six dimensions from 0 to 5, total 30.

| Dimension | 0-1 | 2-3 | 4-5 |
|---|---|---|---|
| Correctness | materially wrong | mostly right, imprecise | exact semantics and boundaries |
| Evidence | unsupported claims | names a component or test | cites concrete failure, code, test, or measured result |
| Causality | feature list only | partial why/how | clear symptom -> cause -> decision -> outcome chain |
| Tradeoffs | no alternatives | generic pros/cons | rejected alternative and consequence are explicit |
| Ownership | vague passive voice | contribution is implied | decisions, investigation, and limits are owned truthfully |
| Communication | rambling or unclear | understandable | front-loaded, structured, concise, calibrated to time |

## Verdicts

- 26-30: strong hire signal; use a harder follow-up.
- 21-25: pass; tighten one weakness, then advance when gate requirements are met.
- 15-20: mixed; give an answer skeleton and require a retry.
- 0-14: weak; teach the missing concept, then ask a narrower version.
- Any invented metric, scale, ownership, or security guarantee: factual-integrity failure, regardless of total.

## Gate readiness

A gate is complete after two distinct questions score at least 21/30 and there is no factual-integrity failure. A memorized answer to the same prompt twice does not count.

## Feedback shape

Keep feedback compact:

```text
Verdict: 19/30 — mixed
Strongest signal: You identified recursion accounting as infrastructure, not model quality.
Main gap: You skipped why the budget was wrong and how the regression test proves the fix.
Evidence to add: agent_room/service.py; tests/test_goal_hard_task.py.
Retry structure: symptom -> competing hypotheses -> root cause -> repair -> offline + live validation -> remaining limit.
```

