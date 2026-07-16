//go:build eino

package runtime

import (
	"testing"

	"github.com/cloudwego/eino/components/model"
)

func TestReActRuntimeRejectsInvalidConfiguration(t *testing.T) {
	if _, err := NewReAct(t.Context(), ReActConfig{}); err != ErrInvalidRequest {
		t.Fatalf("err=%v", err)
	}
}

func TestUsageAccumulatorAddsEveryModelCall(t *testing.T) {
	usage := &usageAccumulator{}
	usage.add(&model.TokenUsage{PromptTokens: 100, CompletionTokens: 20})
	usage.add(&model.TokenUsage{PromptTokens: 140, CompletionTokens: 30})
	input, output := usage.totals()
	if input != 240 || output != 50 {
		t.Fatalf("input=%d output=%d", input, output)
	}
}

func TestRunRejectsSnapshotMismatchBeforeModelInvocation(t *testing.T) {
	runtime := &ReActRuntime{snapshotID: "frozen-v1"}
	if _, err := runtime.Run(t.Context(), Request{SnapshotID: "frozen-v2", Input: "hello"}); err != ErrInvalidRequest {
		t.Fatalf("err=%v", err)
	}
}
