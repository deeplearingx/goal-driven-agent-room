package main

import (
	"encoding/json"
	"log/slog"
	"net/http"
	"os"
	"time"

	"github.com/agent-room/agent-room/gateway/internal/sandboxrunner"
)

func main() {
	var profiles map[string]string
	if err := json.Unmarshal([]byte(os.Getenv("SANDBOX_IMAGE_PROFILES")), &profiles); err != nil {
		slog.Error("invalid SANDBOX_IMAGE_PROFILES", "error", err)
		os.Exit(1)
	}
	timeout, err := time.ParseDuration(env("SANDBOX_EXECUTION_TIMEOUT", "60s"))
	if err != nil {
		slog.Error("invalid SANDBOX_EXECUTION_TIMEOUT", "error", err)
		os.Exit(1)
	}
	handler, err := sandboxrunner.NewHandler(sandboxrunner.Config{
		Root:  env("SANDBOX_WORKSPACE_ROOT", "/var/lib/agent-room/workspaces"),
		Token: os.Getenv("SANDBOX_RUNNER_TOKEN"), Profiles: profiles, Timeout: timeout,
	}, nil, slog.Default())
	if err != nil {
		slog.Error("sandbox runner configuration failed", "error", err)
		os.Exit(1)
	}
	server := &http.Server{
		Addr: env("SANDBOX_LISTEN_ADDR", ":8090"), Handler: handler,
		ReadHeaderTimeout: 5 * time.Second, ReadTimeout: 15 * time.Second,
		WriteTimeout: timeout + 10*time.Second, IdleTimeout: 60 * time.Second,
	}
	slog.Info("sandbox runner listening", "address", server.Addr)
	if err := server.ListenAndServe(); err != nil && err != http.ErrServerClosed {
		slog.Error("sandbox runner stopped", "error", err)
		os.Exit(1)
	}
}

func env(name, fallback string) string {
	if value := os.Getenv(name); value != "" {
		return value
	}
	return fallback
}
