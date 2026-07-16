package store

import (
	"encoding/json"
	"testing"

	"github.com/agent-room/agent-room/gateway/internal/model"
)

func TestInvocationFromPythonUsage(t *testing.T) {
	event := model.Event{Type: "usage", Data: json.RawMessage(`{"input_tokens":1250,"output_tokens":400,"estimated_cost_usd":0.0245,"runtime_snapshot_id":"snapshot-1","prompt_version_id":"prompt-v3","model_profile_version_id":"model-v2"}`)}

	got, project, err := invocationFromEvent(event)
	if err != nil {
		t.Fatal(err)
	}
	if !project || got.Runtime != "python_langgraph" || got.InvocationKind != "model" || got.Status != "succeeded" {
		t.Fatalf("unexpected projection: %#v, project=%v", got, project)
	}
	if got.InputTokens == nil || *got.InputTokens != 1250 || got.OutputTokens == nil || *got.OutputTokens != 400 {
		t.Fatalf("unexpected token counts: %#v", got)
	}
	if got.EstimatedCostUSD == nil || *got.EstimatedCostUSD != 0.0245 || got.RuntimeSnapshotID != "snapshot-1" {
		t.Fatalf("unexpected attribution: %#v", got)
	}
}

func TestInvocationFromUsageRejectsMissingCounts(t *testing.T) {
	_, _, err := invocationFromEvent(model.Event{Type: "usage", Data: json.RawMessage(`{"input_tokens":1}`)})
	if err == nil {
		t.Fatal("expected invalid usage event")
	}
}
