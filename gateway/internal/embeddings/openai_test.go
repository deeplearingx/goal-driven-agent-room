package embeddings

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestOpenAICompatibleEmbedsAndRestoresProviderOrder(t *testing.T) {
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost || r.URL.Path != "/v1/embeddings" || r.Header.Get("Authorization") != "Bearer secret" {
			t.Fatalf("unexpected request: method=%s path=%s auth=%s", r.Method, r.URL.Path, r.Header.Get("Authorization"))
		}
		var request struct {
			Input []string `json:"input"`
		}
		if err := json.NewDecoder(r.Body).Decode(&request); err != nil || len(request.Input) != 2 {
			t.Fatalf("request=%+v err=%v", request, err)
		}
		_, _ = w.Write([]byte(`{"data":[{"index":1,"embedding":[0.2,0.3]},{"index":0,"embedding":[0.1,0.4]}]}`))
	}))
	defer server.Close()
	parameters, _ := json.Marshal(map[string]string{"base_url": server.URL + "/v1"})
	got, err := NewOpenAICompatibleWithAllowedHosts(server.Client(), []string{strings.TrimPrefix(server.URL, "https://")}).Embed(context.Background(), Profile{Provider: "openai", ModelName: "text-embedding-3-small", Parameters: parameters}, []byte("secret"), []string{"first", "second"})
	if err != nil || len(got) != 2 || got[0][0] != 0.1 || got[1][0] != 0.2 {
		t.Fatalf("vectors=%v err=%v", got, err)
	}
}

func TestOpenAICompatibleRejectsInsecureOrInvalidProfile(t *testing.T) {
	for _, raw := range []string{`{"base_url":"http://example.test/v1"}`, `{"dimensions":0}`, `not-json`} {
		_, err := NewOpenAICompatible(nil).Embed(context.Background(), Profile{Provider: "openai", ModelName: "embed", Parameters: json.RawMessage(raw)}, []byte("secret"), []string{"input"})
		if err != ErrInvalidProfile {
			t.Fatalf("parameters=%s err=%v", raw, err)
		}
	}
	_, err := NewOpenAICompatible(nil).Embed(context.Background(), Profile{Provider: "openai", ModelName: "embed"}, []byte("secret"), make([]string, 129))
	if err != ErrInvalidProfile || !strings.Contains(err.Error(), "invalid") {
		t.Fatalf("err=%v", err)
	}
}

func TestOpenAICompatibleRejectsHostOutsideAllowlist(t *testing.T) {
	_, err := NewOpenAICompatible(nil).Embed(context.Background(), Profile{Provider: "openai", ModelName: "embed", Parameters: json.RawMessage(`{"base_url":"https://private.example/v1"}`)}, []byte("secret"), []string{"input"})
	if err != ErrInvalidProfile {
		t.Fatalf("err=%v", err)
	}
}
