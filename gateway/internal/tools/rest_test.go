package tools

import (
	"context"
	"encoding/json"
	"testing"
)

func TestRESTExecutorRejectsUnapprovedAndUntrustedEndpoints(t *testing.T) {
	call := Call{TaskID: "task-1", Tool: Spec{ID: "deploy-v1", Kind: REST, Risk: High}, Input: json.RawMessage(`{"target":"prod"}`)}
	_, decision, err := ExecuteREST(context.Background(), Approval, call, RESTConfig{Endpoint: "https://api.example.test/deploy", AllowedHosts: []string{"api.example.test"}})
	if err != nil || decision.Outcome != NeedsApproval {
		t.Fatalf("decision=%+v err=%v", decision, err)
	}
	call.Tool.ReadOnly = true
	call.Tool.Risk = Low
	_, _, err = ExecuteREST(context.Background(), ReadOnly, call, RESTConfig{Endpoint: "http://127.0.0.1/admin", AllowedHosts: []string{"127.0.0.1"}})
	if err == nil {
		t.Fatal("insecure endpoint was accepted")
	}
}
