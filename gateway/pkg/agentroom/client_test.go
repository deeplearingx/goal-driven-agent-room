package agentroom

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestCreateTaskSendsAuthTenantAndIdempotency(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost || r.URL.Path != "/api/v1/tasks" || r.Header.Get("Authorization") != "Bearer secret" || r.Header.Get("X-Tenant-ID") != "tenant-a" || r.Header.Get("Idempotency-Key") != "request-1" {
			t.Fatalf("unexpected request: %s %s headers=%v", r.Method, r.URL.Path, r.Header)
		}
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusCreated)
		fmt.Fprint(w, `{"task_id":"task-1","status":"queued","created_at":"2026-07-15T00:00:00Z","updated_at":"2026-07-15T00:00:00Z"}`)
	}))
	defer server.Close()
	client, err := New(Config{BaseURL: server.URL, Token: "secret", TenantID: "tenant-a"})
	if err != nil {
		t.Fatal(err)
	}
	task, err := client.CreateTask(t.Context(), CreateTaskInput{Title: "test", Description: "ship it"}, "request-1")
	if err != nil || task.ID != "task-1" {
		t.Fatalf("task=%#v err=%v", task, err)
	}
}

func TestProblemPreservesCodeAndStatus(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusTooManyRequests)
		fmt.Fprint(w, `{"status":429,"code":"rate_limited","detail":"slow down"}`)
	}))
	defer server.Close()
	client, _ := New(Config{BaseURL: server.URL})
	_, err := client.GetTask(t.Context(), "task-1")
	var problem *Problem
	if !errors.As(err, &problem) || problem.Code != "rate_limited" || problem.Status != http.StatusTooManyRequests {
		t.Fatalf("err=%#v", err)
	}
}

func TestStreamEventsParsesSSEAndStopsOnHandlerError(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Query().Get("from") != "7" {
			t.Fatalf("from=%q", r.URL.Query().Get("from"))
		}
		w.Header().Set("Content-Type", "text/event-stream")
		fmt.Fprint(w, ": ping\n\nid: 8\nevent: usage\ndata: {\"input_tokens\":10}\n\n")
	}))
	defer server.Close()
	client, _ := New(Config{BaseURL: server.URL})
	stop := errors.New("stop")
	err := client.StreamEvents(context.Background(), "task-1", 7, func(event Event) error {
		if event.Sequence != 8 || event.Type != "usage" || string(event.Data) != `{"input_tokens":10}` {
			t.Fatalf("event=%#v", event)
		}
		return stop
	})
	if !errors.Is(err, stop) {
		t.Fatalf("err=%v", err)
	}
}
