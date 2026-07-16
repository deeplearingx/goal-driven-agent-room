package tools

import (
	"encoding/json"
	"testing"
)

func TestPolicyIsDenyByDefaultAndBindsApprovalToArguments(t *testing.T) {
	read := Call{TaskID: "task-1", Tool: Spec{ID: "search-v1", Kind: MCP, ReadOnly: true, Risk: Low}, Input: json.RawMessage(`{"q":"status"}`)}
	if got := Evaluate(ReadOnly, read); got.Outcome != Allowed {
		t.Fatalf("read=%+v", got)
	}
	write := Call{TaskID: "task-1", Tool: Spec{ID: "deploy-v1", Kind: REST, Risk: High}, Input: json.RawMessage(`{"environment":"prod"}`)}
	if got := Evaluate(ReadOnly, write); got.Outcome != Denied {
		t.Fatalf("readonly=%+v", got)
	}
	first := Evaluate(Approval, write)
	if first.Outcome != NeedsApproval {
		t.Fatalf("approval=%+v", first)
	}
	write.Input = json.RawMessage(`{"environment":"staging"}`)
	if second := Evaluate(Approval, write); second.OperationHash == first.OperationHash {
		t.Fatal("approval hash did not bind arguments")
	}
}

func TestCriticalToolsRequireApprovalEvenUnrestricted(t *testing.T) {
	call := Call{TaskID: "task-1", Tool: Spec{ID: "shell-v1", Kind: Sandbox, Risk: Critical}, Input: json.RawMessage(`{"command":"rm -rf /"}`)}
	if got := Evaluate(Unrestricted, call); got.Outcome != NeedsApproval {
		t.Fatalf("got=%+v", got)
	}
}
