package broker

import (
	"context"
	"errors"
	"io"
	"log/slog"
	"sync"
	"testing"
	"time"

	"github.com/agent-room/agent-room/gateway/internal/model"
)

type outboxTestStore struct {
	mu        sync.Mutex
	messages  []model.OutboxMessage
	published []int64
	failed    []int64
	failure   chan struct{}
}

func (s *outboxTestStore) ClaimOutbox(context.Context, int) ([]model.OutboxMessage, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	items := s.messages
	s.messages = nil
	return items, nil
}

func (s *outboxTestStore) MarkOutboxPublished(_ context.Context, id int64) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.published = append(s.published, id)
	return nil
}

func (s *outboxTestStore) MarkOutboxFailed(_ context.Context, id int64, _ error) error {
	s.mu.Lock()
	s.failed = append(s.failed, id)
	s.mu.Unlock()
	select {
	case s.failure <- struct{}{}:
	default:
	}
	return nil
}

type outboxTestPublisher struct{ err error }

func (p outboxTestPublisher) PublishCommand(context.Context, string, string, []byte) error {
	return p.err
}

func TestRunOutboxRecordsFailedPublishBeforeRetry(t *testing.T) {
	store := &outboxTestStore{messages: []model.OutboxMessage{{ID: 19, MessageID: "message-1"}}, failure: make(chan struct{}, 1)}
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() {
		done <- RunOutbox(ctx, store, outboxTestPublisher{err: errors.New("rabbit unavailable")}, time.Millisecond, slog.New(slog.NewTextHandler(io.Discard, nil)))
	}()
	select {
	case <-store.failure:
	case <-time.After(time.Second):
		t.Fatal("failed publish was not persisted")
	}
	cancel()
	if err := <-done; !errors.Is(err, context.Canceled) {
		t.Fatalf("RunOutbox error = %v, want context cancellation", err)
	}
	store.mu.Lock()
	defer store.mu.Unlock()
	if len(store.failed) != 1 || store.failed[0] != 19 || len(store.published) != 0 {
		t.Fatalf("failed=%v published=%v", store.failed, store.published)
	}
}

func TestRunOutboxMarksSuccessfulPublish(t *testing.T) {
	store := &outboxTestStore{messages: []model.OutboxMessage{{ID: 21, MessageID: "message-2"}}, failure: make(chan struct{}, 1)}
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() {
		done <- RunOutbox(ctx, store, outboxTestPublisher{}, time.Millisecond, slog.New(slog.NewTextHandler(io.Discard, nil)))
	}()
	deadline := time.After(time.Second)
	for {
		store.mu.Lock()
		published := len(store.published)
		store.mu.Unlock()
		if published == 1 {
			break
		}
		select {
		case <-deadline:
			t.Fatal("successful publish was not persisted")
		case <-time.After(time.Millisecond):
		}
	}
	cancel()
	if err := <-done; !errors.Is(err, context.Canceled) {
		t.Fatalf("RunOutbox error = %v, want context cancellation", err)
	}
}
