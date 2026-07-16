package main

import (
	"bytes"
	"context"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestRunCommandCreatesTask(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/v1/tasks" || r.Header.Get("Authorization") != "Bearer token-1" {
			t.Fatalf("unexpected request: %s auth=%q", r.URL.Path, r.Header.Get("Authorization"))
		}
		w.WriteHeader(http.StatusCreated)
		fmt.Fprint(w, `{"task_id":"task-1","status":"queued","created_at":"2026-07-15T00:00:00Z","updated_at":"2026-07-15T00:00:00Z"}`)
	}))
	defer server.Close()
	var stdout, stderr bytes.Buffer
	err := run(context.Background(), []string{"--url", server.URL, "--token", "token-1", "run", "--title", "release", "--description", "ship"}, &stdout, &stderr)
	if err != nil || !strings.Contains(stdout.String(), `"task_id":"task-1"`) {
		t.Fatalf("stdout=%q stderr=%q err=%v", stdout.String(), stderr.String(), err)
	}
}

func TestRunCommandRequiresDescription(t *testing.T) {
	var stdout, stderr bytes.Buffer
	err := run(context.Background(), []string{"run", "--title", "release"}, &stdout, &stderr)
	if err == nil || !strings.Contains(err.Error(), "--description") {
		t.Fatalf("err=%v", err)
	}
}

func TestEventsAcceptsCursorAfterTaskID(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/v1/tasks/task-1/events" || r.URL.Query().Get("from") != "42" {
			t.Fatalf("url=%s", r.URL.String())
		}
		w.Header().Set("Content-Type", "text/event-stream")
		fmt.Fprint(w, "id: 43\nevent: task_finished\ndata: {\"status\":\"completed\"}\n\n")
	}))
	defer server.Close()
	var stdout, stderr bytes.Buffer
	err := run(context.Background(), []string{"--url", server.URL, "events", "task-1", "--after", "42"}, &stdout, &stderr)
	if err != nil || !strings.Contains(stdout.String(), `"sequence":43`) {
		t.Fatalf("stdout=%q stderr=%q err=%v", stdout.String(), stderr.String(), err)
	}
}
