// Package tools provides the runtime-neutral policy boundary for every tool
// transport (REST, internal SDK, MCP, A2A, and sandbox execution).
package tools

import (
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"strings"
)

type Kind string

const (
	REST    Kind = "rest"
	MCP     Kind = "mcp"
	A2A     Kind = "a2a"
	Sandbox Kind = "sandbox"
)

type Mode string

const (
	ReadOnly     Mode = "read_only"
	Approval     Mode = "approval"
	Unrestricted Mode = "unrestricted"
)

type Risk string

const (
	Low      Risk = "low"
	High     Risk = "high"
	Critical Risk = "critical"
)

type Spec struct {
	ID       string
	Kind     Kind
	ReadOnly bool
	Risk     Risk
	// Sandbox and external callbacks are never considered read-only by name;
	// a versioned ToolSpec must opt in explicitly.
	Endpoint string
}

type Call struct {
	TaskID string
	Tool   Spec
	Input  json.RawMessage
}

type Outcome string

const (
	Allowed       Outcome = "allowed"
	Denied        Outcome = "denied"
	NeedsApproval Outcome = "needs_approval"
)

type Decision struct {
	Outcome Outcome
	Reason  string
	// OperationHash binds a human decision to exact task, tool version and
	// arguments. Changing arguments requires a new approval.
	OperationHash string
}

func Evaluate(mode Mode, call Call) Decision {
	hash := operationHash(call)
	if strings.TrimSpace(call.TaskID) == "" || strings.TrimSpace(call.Tool.ID) == "" || !json.Valid(call.Input) {
		return Decision{Outcome: Denied, Reason: "invalid tool call", OperationHash: hash}
	}
	if call.Tool.Kind != REST && call.Tool.Kind != MCP && call.Tool.Kind != A2A && call.Tool.Kind != Sandbox {
		return Decision{Outcome: Denied, Reason: "unsupported tool kind", OperationHash: hash}
	}
	if mode == ReadOnly {
		if call.Tool.ReadOnly && call.Tool.Risk == Low {
			return Decision{Outcome: Allowed, OperationHash: hash}
		}
		return Decision{Outcome: Denied, Reason: "tool is not permitted in read_only mode", OperationHash: hash}
	}
	if mode == Approval {
		if call.Tool.ReadOnly && call.Tool.Risk == Low {
			return Decision{Outcome: Allowed, OperationHash: hash}
		}
		return Decision{Outcome: NeedsApproval, Reason: "side-effecting or elevated-risk tool", OperationHash: hash}
	}
	if mode == Unrestricted {
		if call.Tool.Risk == Critical {
			return Decision{Outcome: NeedsApproval, Reason: "critical-risk tool requires approval", OperationHash: hash}
		}
		return Decision{Outcome: Allowed, OperationHash: hash}
	}
	return Decision{Outcome: Denied, Reason: "unknown permission mode", OperationHash: hash}
}

func operationHash(call Call) string {
	input := call.Input
	if len(input) == 0 {
		input = json.RawMessage(`{}`)
	}
	var value any
	if json.Unmarshal(input, &value) == nil {
		input, _ = json.Marshal(value)
	}
	return fmt.Sprintf("%x", sha256.Sum256([]byte(call.TaskID+"\n"+call.Tool.ID+"\n"+string(input))))
}
