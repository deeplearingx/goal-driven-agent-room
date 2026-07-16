# Model execution and file safety

The model never receives direct filesystem, subprocess, container-runtime, or
credential access. Normal production defaults are:

```env
AGENT_ROOM_SHELL_ALLOWLIST=
AGENT_ROOM_ALLOW_DIRECT_SHELL=0
AGENT_ROOM_TOOL_MODE=approval
```

With no environment overrides, the Python runtime is even stricter:
`tool_mode=read_only` and ShellTool is absent. `write_text`, MCP mutations and
`sandbox_exec` are filtered or stopped for approval before execution.

Approval mode is deliberately single-operation. If a model emits several
tool calls in one response, the runtime retains only the first call for the
current approval cycle and records a `parallel_tool_calls_serialized` audit
event for the deferred calls. The model must request each remaining operation
again after seeing the previous result. One click can therefore never approve
an implicit batch.

## Isolation boundary

`sandbox_exec` sends only `{tenant_id, task_id, profile, command[]}` to a
dedicated `sandbox-runner`. Tenant/task identity is injected by the trusted
Worker closure and is not part of model-controlled arguments. The Runner:

- maps an operator-owned profile to an exact `image@sha256:<64 hex>` digest;
- derives the workspace below its configured root and rejects path/symlink escape;
- invokes Docker as an argv vector, never through a shell;
- uses no network, a read-only root filesystem, UID 65532, dropped
  capabilities, `no-new-privileges`, PID/CPU/memory/file limits and a noexec
  tmpfs;
- returns at most 64 KiB of combined output and enforces an execution timeout;
- receives no model-provider, database, RabbitMQ or cloud credentials.

The Runner must be deployed on a dedicated rootless container host or isolated
runner node. Do **not** mount an application-cluster privileged Docker socket
into the Gateway or model Worker. Only Worker egress to `/v1/execute` should be
allowed, protected by TLS, NetworkPolicy/firewall and a separate rotated bearer
token. The workspace root must be a task/tenant-safe storage surface visible to
the Runner; never point it at a source checkout, home directory or host root.

Example runner configuration:

```env
SANDBOX_WORKSPACE_ROOT=/srv/agent-room/workspaces
SANDBOX_RUNNER_TOKEN=<random token distinct from all application keys>
SANDBOX_IMAGE_PROFILES={"python-check":"registry.example/sandbox-python@sha256:<64 hex>"}
SANDBOX_EXECUTION_TIMEOUT=60s
```

The model selects `python-check`, not an image reference or host path.

Tenant and task identifiers are validated as single safe path segments before
they are used for either SQLite/checkpoint paths or workspace derivation.
Absolute paths, `..`, separators and symlink escapes are rejected rather than
normalized.

## Direct shell escape hatch

`AGENT_ROOM_ALLOW_DIRECT_SHELL=1` exists only for isolated local development.
Allowlisting `python`, `pytest`, `node`, `bash` or similar programs is arbitrary
code execution even with `shell=False`; those programs can read every file and
environment variable visible to the credential-bearing Worker. Production
startup fails when an allowlist is present without this explicit escape hatch.

## Promotion boundary

Sandbox output is an untrusted artifact. A production workflow should scan it,
show a diff, require approval, then copy only allowlisted artifact paths into
versioned object storage or a Git branch. It must never write directly to a
production source tree or overwrite deployed files.
