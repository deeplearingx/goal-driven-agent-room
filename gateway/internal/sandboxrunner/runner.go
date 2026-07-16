package sandboxrunner

import (
	"bytes"
	"context"
	"crypto/subtle"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strings"
	"time"

	toolpolicy "github.com/agent-room/agent-room/gateway/internal/tools"
)

const maxOutputBytes = 64 << 10

var safeIDPattern = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$`)

type Config struct {
	Root     string
	Token    string
	Profiles map[string]string
	Timeout  time.Duration
}

type Request struct {
	TenantID string   `json:"tenant_id"`
	TaskID   string   `json:"task_id"`
	Profile  string   `json:"profile"`
	Command  []string `json:"command"`
}

type Result struct {
	ExitCode  int    `json:"exit_code"`
	Output    string `json:"output"`
	Truncated bool   `json:"truncated"`
	TimedOut  bool   `json:"timed_out"`
}

type Executor interface {
	Execute(context.Context, toolpolicy.SandboxConfig) (Result, error)
}

type DockerExecutor struct {
	Binary  string
	Timeout time.Duration
}

func (e DockerExecutor) Execute(ctx context.Context, config toolpolicy.SandboxConfig) (Result, error) {
	args, err := toolpolicy.DockerArgs(config)
	if err != nil {
		return Result{}, err
	}
	timeout := e.Timeout
	if timeout <= 0 {
		timeout = 60 * time.Second
	}
	ctx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	binary := e.Binary
	if binary == "" {
		binary = "docker"
	}
	var output limitedBuffer
	cmd := exec.CommandContext(ctx, binary, args...) //nolint:gosec -- fixed binary plus argv, never a shell
	cmd.Stdout = &output
	cmd.Stderr = &output
	err = cmd.Run()
	result := Result{ExitCode: 0, Output: output.String(), Truncated: output.truncated}
	if ctx.Err() == context.DeadlineExceeded {
		result.ExitCode = -1
		result.TimedOut = true
		return result, nil
	}
	if err == nil {
		return result, nil
	}
	var exitErr *exec.ExitError
	if errors.As(err, &exitErr) {
		result.ExitCode = exitErr.ExitCode()
		return result, nil
	}
	return Result{}, fmt.Errorf("start sandbox runtime: %w", err)
}

type limitedBuffer struct {
	buf       bytes.Buffer
	truncated bool
}

func (b *limitedBuffer) Write(p []byte) (int, error) {
	original := len(p)
	remaining := maxOutputBytes - b.buf.Len()
	if remaining <= 0 {
		b.truncated = true
		return original, nil
	}
	if len(p) > remaining {
		p = p[:remaining]
		b.truncated = true
	}
	_, _ = b.buf.Write(p)
	return original, nil
}

func (b *limitedBuffer) String() string { return b.buf.String() }

type Handler struct {
	config   Config
	executor Executor
	logger   *slog.Logger
}

func NewHandler(config Config, executor Executor, logger *slog.Logger) (*Handler, error) {
	if strings.TrimSpace(config.Root) == "" || strings.TrimSpace(config.Token) == "" || len(config.Profiles) == 0 {
		return nil, fmt.Errorf("sandbox runner requires root, token, and at least one image profile")
	}
	root, err := filepath.Abs(config.Root)
	if err != nil {
		return nil, fmt.Errorf("resolve sandbox root: %w", err)
	}
	if err := os.MkdirAll(root, 0o700); err != nil {
		return nil, fmt.Errorf("create sandbox root: %w", err)
	}
	config.Root = root
	for profile, image := range config.Profiles {
		if !safeIDPattern.MatchString(profile) {
			return nil, fmt.Errorf("invalid sandbox profile name %q", profile)
		}
		if err := toolpolicy.ValidateSandboxImage(image); err != nil {
			return nil, fmt.Errorf("invalid sandbox profile %q: %w", profile, err)
		}
	}
	if executor == nil {
		executor = DockerExecutor{Timeout: config.Timeout}
	}
	if logger == nil {
		logger = slog.Default()
	}
	return &Handler{config: config, executor: executor, logger: logger}, nil
}

func (h *Handler) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	if r.Method == http.MethodGet && r.URL.Path == "/healthz" {
		writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
		return
	}
	if r.Method != http.MethodPost || r.URL.Path != "/v1/execute" {
		http.NotFound(w, r)
		return
	}
	if !h.authorized(r.Header.Get("Authorization")) {
		writeJSON(w, http.StatusUnauthorized, map[string]string{"error": "unauthorized"})
		return
	}
	r.Body = http.MaxBytesReader(w, r.Body, 64<<10)
	defer r.Body.Close()
	var request Request
	decoder := json.NewDecoder(r.Body)
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&request); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid_request"})
		return
	}
	image, ok := h.config.Profiles[request.Profile]
	if !ok || !safeIDPattern.MatchString(request.TenantID) || !safeIDPattern.MatchString(request.TaskID) || len(request.Command) == 0 || len(request.Command) > 64 {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid_request"})
		return
	}
	workspace := filepath.Join(h.config.Root, request.TenantID, request.TaskID)
	if err := os.MkdirAll(workspace, 0o700); err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "workspace_unavailable"})
		return
	}
	result, err := h.executor.Execute(r.Context(), toolpolicy.SandboxConfig{
		Image: image, Root: h.config.Root, Workspace: workspace, Command: request.Command,
	})
	if err != nil {
		h.logger.Error("sandbox execution failed", "tenant_id", request.TenantID, "task_id", request.TaskID, "profile", request.Profile, "error", err)
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "execution_failed"})
		return
	}
	h.logger.Info("sandbox execution completed", "tenant_id", request.TenantID, "task_id", request.TaskID, "profile", request.Profile, "exit_code", result.ExitCode, "timed_out", result.TimedOut)
	writeJSON(w, http.StatusOK, result)
}

func (h *Handler) authorized(header string) bool {
	const prefix = "Bearer "
	if !strings.HasPrefix(header, prefix) {
		return false
	}
	provided := []byte(strings.TrimSpace(strings.TrimPrefix(header, prefix)))
	expected := []byte(h.config.Token)
	return len(provided) == len(expected) && subtle.ConstantTimeCompare(provided, expected) == 1
}

func writeJSON(w http.ResponseWriter, status int, value any) {
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "no-store")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(value)
}
