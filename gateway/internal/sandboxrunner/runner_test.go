package sandboxrunner

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"testing"

	toolpolicy "github.com/agent-room/agent-room/gateway/internal/tools"
)

type captureExecutor struct {
	config toolpolicy.SandboxConfig
}

func (e *captureExecutor) Execute(_ context.Context, config toolpolicy.SandboxConfig) (Result, error) {
	e.config = config
	return Result{ExitCode: 0, Output: "ok"}, nil
}

func TestHandlerRequiresAuthAndDerivesWorkspaceServerSide(t *testing.T) {
	root := t.TempDir()
	executor := &captureExecutor{}
	handler, err := NewHandler(Config{
		Root: root, Token: "secret-token",
		Profiles: map[string]string{"python": "python@sha256:" + strings.Repeat("a", 64)},
	}, executor, nil)
	if err != nil {
		t.Fatal(err)
	}
	body := `{"tenant_id":"tenant-a","task_id":"task-1","profile":"python","command":["python","check.py"]}`
	unauthorized := httptest.NewRequest(http.MethodPost, "/v1/execute", strings.NewReader(body))
	unauthorizedResult := httptest.NewRecorder()
	handler.ServeHTTP(unauthorizedResult, unauthorized)
	if unauthorizedResult.Code != http.StatusUnauthorized {
		t.Fatalf("expected 401, got %d", unauthorizedResult.Code)
	}

	request := httptest.NewRequest(http.MethodPost, "/v1/execute", strings.NewReader(body))
	request.Header.Set("Authorization", "Bearer secret-token")
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, request)
	if response.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", response.Code, response.Body.String())
	}
	expectedWorkspace := filepath.Join(root, "tenant-a", "task-1")
	actualWorkspace, _ := filepath.Abs(executor.config.Workspace)
	expectedWorkspace, _ = filepath.Abs(expectedWorkspace)
	if actualWorkspace != expectedWorkspace || executor.config.Root != root {
		t.Fatalf("unexpected sandbox paths: root=%q workspace=%q", executor.config.Root, executor.config.Workspace)
	}
	if executor.config.Image != "python@sha256:"+strings.Repeat("a", 64) {
		t.Fatal("profile did not resolve to the server-side digest")
	}
	var result Result
	if err := json.Unmarshal(response.Body.Bytes(), &result); err != nil || result.Output != "ok" {
		t.Fatalf("unexpected response: %s (%v)", response.Body.String(), err)
	}
}

func TestHandlerRejectsPathLikeIDsAndUnknownFields(t *testing.T) {
	handler, err := NewHandler(Config{
		Root: t.TempDir(), Token: "token",
		Profiles: map[string]string{"python": "python@sha256:" + strings.Repeat("b", 64)},
	}, &captureExecutor{}, nil)
	if err != nil {
		t.Fatal(err)
	}
	for _, body := range []string{
		`{"tenant_id":"../escape","task_id":"task","profile":"python","command":["true"]}`,
		`{"tenant_id":"tenant","task_id":"task","profile":"missing","command":["true"]}`,
		`{"tenant_id":"tenant","task_id":"task","profile":"python","command":["true"],"workspace":"/host"}`,
	} {
		request := httptest.NewRequest(http.MethodPost, "/v1/execute", strings.NewReader(body))
		request.Header.Set("Authorization", "Bearer token")
		response := httptest.NewRecorder()
		handler.ServeHTTP(response, request)
		if response.Code != http.StatusBadRequest {
			t.Fatalf("expected 400, got %d for %s", response.Code, body)
		}
	}
}

func TestHandlerRejectsMutableProfileImageAtStartup(t *testing.T) {
	_, err := NewHandler(Config{
		Root: t.TempDir(), Token: "token", Profiles: map[string]string{"python": "python:latest"},
	}, &captureExecutor{}, nil)
	if err == nil || !strings.Contains(err.Error(), "full sha256 digest") {
		t.Fatalf("expected mutable image rejection, got %v", err)
	}
}
