# Go SDK and kbctl

The SDK lives in the gateway Go module and has no third-party runtime
dependencies. It supports tenant-aware authentication, idempotent task
creation, task transitions, hybrid knowledge search, RFC 7807-style errors and
SSE resume cursors.

```go
client, err := agentroom.New(agentroom.Config{
    BaseURL:  "https://agents.example.com",
    Token:    os.Getenv("AGENT_ROOM_API_KEY"),
    TenantID: "tenant-a",
})
if err != nil {
    log.Fatal(err)
}

task, err := client.CreateTask(ctx, agentroom.CreateTaskInput{
    Title:             "release review",
    Description:       "Review the release and return blocking risks",
    ExecutionRuntime:  "go_eino",
    RuntimeSnapshotID: "snapshot-v7",
}, "release-2026-07-15")
```

Import path:

```text
github.com/agent-room/agent-room/gateway/pkg/agentroom
```

Build the CLI locally:

```bash
cd gateway
go build -o kbctl ./cmd/kbctl
```

Connection settings can be supplied through flags or environment variables:

```bash
export AGENT_ROOM_URL=https://agents.example.com
export AGENT_ROOM_API_KEY=replace-me
export AGENT_ROOM_TENANT=tenant-a

kbctl run --title "release review" --description "Review the release" \
  --runtime go_eino --snapshot snapshot-v7 --idempotency-key release-2026-07-15
kbctl get task-123
kbctl events task-123 --after 42
kbctl cancel task-123
kbctl retry task-123
kbctl search --query "refund policy" --profile embedding-v2 --limit 5
```

`events --after` maps to the gateway's persistent SSE sequence and can resume
after process or network interruption without replaying older events. Tokens
are accepted only from explicit flags/environment variables; the CLI does not
persist credentials to disk.
