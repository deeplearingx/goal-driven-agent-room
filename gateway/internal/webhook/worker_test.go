package webhook

import (
	"context"
	"io"
	"net/http"
	"strings"
	"testing"

	"github.com/agent-room/agent-room/gateway/internal/model"
	"github.com/agent-room/agent-room/gateway/internal/secrets"
)

type fakeStore struct {
	deliveries []model.WebhookDelivery
	completed  []int64
	failed     []int64
}

func (s *fakeStore) ClaimWebhookDeliveries(context.Context, int) ([]model.WebhookDelivery, error) {
	items := s.deliveries
	s.deliveries = nil
	return items, nil
}
func (s *fakeStore) CompleteWebhookDelivery(_ context.Context, id int64, _ int) error {
	s.completed = append(s.completed, id)
	return nil
}
func (s *fakeStore) FailWebhookDelivery(_ context.Context, id int64, _ int, _ int, _ error) error {
	s.failed = append(s.failed, id)
	return nil
}

type roundTripperFunc func(*http.Request) (*http.Response, error)

func (f roundTripperFunc) RoundTrip(request *http.Request) (*http.Response, error) { return f(request) }

func TestWorkerDeliversGenericPayloadWithSignature(t *testing.T) {
	protector, err := secrets.NewAESGCM("MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=")
	if err != nil {
		t.Fatal(err)
	}
	endpoint, _ := protector.Encrypt([]byte("https://hooks.example.com/v1/events"))
	secret, _ := protector.Encrypt([]byte("secret"))
	store := &fakeStore{deliveries: []model.WebhookDelivery{{ID: 7, SubscriptionID: "sub-1", Kind: "generic", EndpointHost: "hooks.example.com", EndpointEncrypted: endpoint, SigningSecretEncrypted: secret, EventType: "task_finished", EventMessageID: "event-1", Payload: []byte(`{"task_id":"task-1","type":"task_finished","data":{"status":"completed"}}`), Attempts: 1}}}
	worker := NewWorker(store, protector, []string{"hooks.example.com"}, 0, 1)
	worker.httpClient = &http.Client{Transport: roundTripperFunc(func(request *http.Request) (*http.Response, error) {
		if request.URL.Hostname() != "hooks.example.com" || request.Header.Get("X-Agent-Room-Event") != "task_finished" || !strings.HasPrefix(request.Header.Get("X-Agent-Room-Signature"), "sha256=") {
			t.Fatalf("url=%s headers=%v", request.URL, request.Header)
		}
		return &http.Response{StatusCode: http.StatusAccepted, Body: io.NopCloser(strings.NewReader("{}")), Header: make(http.Header)}, nil
	})}
	if err = worker.Poll(t.Context()); err != nil {
		t.Fatal(err)
	}
	if len(store.completed) != 1 || store.completed[0] != 7 || len(store.failed) != 0 {
		t.Fatalf("completed=%v failed=%v", store.completed, store.failed)
	}
}

func TestWorkerMarksFailedDeliveriesForRetry(t *testing.T) {
	protector, _ := secrets.NewAESGCM("MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=")
	endpoint, _ := protector.Encrypt([]byte("https://hooks.example.com/v1/events"))
	secret, _ := protector.Encrypt([]byte(""))
	store := &fakeStore{deliveries: []model.WebhookDelivery{{ID: 8, Kind: "generic", EndpointHost: "hooks.example.com", EndpointEncrypted: endpoint, SigningSecretEncrypted: secret, Payload: []byte(`{"type":"task_error"}`), Attempts: 3}}}
	worker := NewWorker(store, protector, []string{"hooks.example.com"}, 0, 1)
	worker.httpClient = &http.Client{Transport: roundTripperFunc(func(*http.Request) (*http.Response, error) {
		return &http.Response{StatusCode: http.StatusBadGateway, Body: io.NopCloser(strings.NewReader("unavailable")), Header: make(http.Header)}, nil
	})}
	if err := worker.Poll(t.Context()); err != nil {
		t.Fatal(err)
	}
	if len(store.failed) != 1 || store.failed[0] != 8 || len(store.completed) != 0 {
		t.Fatalf("completed=%v failed=%v", store.completed, store.failed)
	}
}
