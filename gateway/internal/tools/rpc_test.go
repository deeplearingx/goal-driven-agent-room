package tools

import (
	"encoding/json"
	"testing"
)

func TestMCPAndA2AUseExplicitJSONRPCMethods(t *testing.T) {
	mcp, err := MCPToolCall("1", "search", json.RawMessage(`{"q":"status"}`))
	if err != nil || mcp.Method != "tools/call" {
		t.Fatalf("mcp=%+v err=%v", mcp, err)
	}
	a2a, err := A2AMessageSend("2", json.RawMessage(`{"role":"user","parts":[{"text":"hello"}]}`))
	if err != nil || a2a.Method != "message/send" {
		t.Fatalf("a2a=%+v err=%v", a2a, err)
	}
	if _, err := MCPToolCall("", "search", json.RawMessage(`{}`)); err == nil {
		t.Fatal("invalid MCP call accepted")
	}
}
