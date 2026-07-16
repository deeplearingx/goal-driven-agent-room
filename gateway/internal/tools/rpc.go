package tools

import (
	"encoding/json"
	"fmt"
	"strings"
)

// JSONRPCRequest is shared by MCP Streamable HTTP and the A2A JSON-RPC
// binding. Network transport remains subject to the same HTTPS host policy as
// REST tools.
type JSONRPCRequest struct {
	JSONRPC string `json:"jsonrpc"`
	ID      string `json:"id"`
	Method  string `json:"method"`
	Params  any    `json:"params"`
}

func MCPToolCall(id, name string, arguments json.RawMessage) (JSONRPCRequest, error) {
	if strings.TrimSpace(id) == "" || strings.TrimSpace(name) == "" || !json.Valid(arguments) {
		return JSONRPCRequest{}, fmt.Errorf("invalid MCP tool call")
	}
	var value any
	if err := json.Unmarshal(arguments, &value); err != nil {
		return JSONRPCRequest{}, err
	}
	return JSONRPCRequest{JSONRPC: "2.0", ID: id, Method: "tools/call", Params: map[string]any{"name": name, "arguments": value}}, nil
}

// A2AMessageSend creates the standard JSON-RPC transport envelope. The
// concrete message/task body is versioned inside the immutable ToolSpec.
func A2AMessageSend(id string, message json.RawMessage) (JSONRPCRequest, error) {
	if strings.TrimSpace(id) == "" || !json.Valid(message) {
		return JSONRPCRequest{}, fmt.Errorf("invalid A2A message")
	}
	var value any
	if err := json.Unmarshal(message, &value); err != nil {
		return JSONRPCRequest{}, err
	}
	return JSONRPCRequest{JSONRPC: "2.0", ID: id, Method: "message/send", Params: map[string]any{"message": value}}, nil
}
