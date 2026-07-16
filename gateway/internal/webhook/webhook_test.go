package webhook

import (
	"encoding/json"
	"testing"
	"time"
)

func TestValidateEndpointRequiresHTTPSAndAllowlist(t *testing.T) {
	if _, err := ValidateEndpoint("https://hooks.example.com/v1/secret", "generic", []string{"hooks.example.com"}); err != nil {
		t.Fatal(err)
	}
	for _, endpoint := range []string{"http://hooks.example.com/x", "https://127.0.0.1/x", "https://evil.example/x"} {
		if _, err := ValidateEndpoint(endpoint, "generic", []string{"hooks.example.com"}); err == nil {
			t.Fatalf("expected rejection for %s", endpoint)
		}
	}
}

func TestBuildFeishuPayloadUsesOfficialSignatureShape(t *testing.T) {
	body, _, err := BuildPayload("feishu", json.RawMessage(`{"task_id":"task-1","type":"task_finished","data":{"status":"completed"}}`), []byte("secret"), time.Unix(1599360473, 0))
	if err != nil {
		t.Fatal(err)
	}
	var payload struct {
		Timestamp string `json:"timestamp"`
		Sign      string `json:"sign"`
		MsgType   string `json:"msg_type"`
		Content   struct {
			Text string `json:"text"`
		} `json:"content"`
	}
	if err = json.Unmarshal(body, &payload); err != nil {
		t.Fatal(err)
	}
	if payload.Timestamp != "1599360473" || payload.Sign == "" || payload.MsgType != "text" || payload.Content.Text == "" {
		t.Fatalf("payload=%s", body)
	}
}

func TestGenericSignatureIsStable(t *testing.T) {
	body, headers, err := BuildPayload("generic", json.RawMessage(`{"type":"task_finished"}`), []byte("secret"), time.Unix(100, 0))
	if err != nil || string(body) != `{"type":"task_finished"}` || headers["X-Agent-Room-Signature"] != "sha256=d0b87ffd928cb893c2eb9fdbf4788eaf1a68ac69f71989c74ac379adc3c86f8a" {
		t.Fatalf("body=%s headers=%v err=%v", body, headers, err)
	}
}
