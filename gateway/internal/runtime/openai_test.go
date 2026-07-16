//go:build eino

package runtime

import (
	"testing"
)

func TestOpenAIProfileRejectsMissingProviderOrSecret(t *testing.T) {
	if _, err := NewOpenAIModel(t.Context(), OpenAIProfile{Provider: "openai", ModelName: "gpt-5"}, nil); err != ErrInvalidRequest {
		t.Fatalf("err=%v", err)
	}
	if _, err := NewOpenAIModel(t.Context(), OpenAIProfile{Provider: "other", ModelName: "gpt-5"}, []byte("key")); err != ErrInvalidRequest {
		t.Fatalf("err=%v", err)
	}
}
