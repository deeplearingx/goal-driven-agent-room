# DeepSeek live goal-mode validation (2026-07-04)

The repaired goal mode was validated against the real `deepseek-v4-pro`
provider, not a fake model.

## Configuration correction

The original Path B values were still commented and used
`https://api.deepseek.com` as an Anthropic-compatible endpoint. DeepSeek's
Anthropic endpoint is `https://api.deepseek.com/anthropic`. The active
`AGENT_ROOM_DEFAULT_MODEL=claude-sonnet-4-6` also overrode the commented
`ANTHROPIC_MODEL` value.

Credentials were moved to the gitignored `.env`; `.env.example` now contains
no token and documents the corrected endpoint/model.

## Live task

The task asked the model to implement a standard `median(nums)` without
revealing the acceptance convention. The verifier required an even-length
input to return the lower middle element and exposed that rule only after a
failed check.

Observed trajectory:

1. Real DeepSeek planner/developer invocation.
2. Verification round 1 failed.
3. Failure output was pushed into the developer transcript.
4. The developer changed the implementation to the lower-middle convention.
5. Verification round 2 passed; delivery completed.

Result summary:

```text
model=deepseek-v4-pro
status=completed
verification_round=2
verification_passed=true
feedback_iteration_observed=true
```

Workspace snapshot: `snapshots/real-goal-da4f59ee/`.
