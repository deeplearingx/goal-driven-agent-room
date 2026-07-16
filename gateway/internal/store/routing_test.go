package store

import (
	"encoding/json"
	"testing"

	"github.com/agent-room/agent-room/gateway/internal/model"
)

func TestCommandRoutingKeyPartitionsOnlyRunCommands(t *testing.T) {
	for _, test := range []struct{ name, kind, runtime, want string }{
		{"python", "run", "python_langgraph", "run.python_langgraph"},
		{"eino", "run", "go_eino", "run.go_eino"},
		{"legacy", "run", "", "run"},
		{"resume", "resume", "go_eino", "resume"},
	} {
		t.Run(test.name, func(t *testing.T) {
			payload, _ := json.Marshal(map[string]string{"execution_runtime": test.runtime})
			if got := commandRoutingKey(model.Command{Type: test.kind, Payload: payload}); got != test.want {
				t.Fatalf("got %q want %q", got, test.want)
			}
		})
	}
}
