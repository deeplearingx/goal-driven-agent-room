package webhook

import (
	"bytes"
	"context"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/netip"
	"time"

	"github.com/agent-room/agent-room/gateway/internal/model"
	"github.com/agent-room/agent-room/gateway/internal/secrets"
	"golang.org/x/time/rate"
)

type DeliveryStore interface {
	ClaimWebhookDeliveries(context.Context, int) ([]model.WebhookDelivery, error)
	CompleteWebhookDelivery(context.Context, int64, int) error
	FailWebhookDelivery(context.Context, int64, int, int, error) error
}

type Worker struct {
	store        DeliveryStore
	decryptor    secrets.Decryptor
	allowedHosts []string
	pollInterval time.Duration
	batchSize    int
	httpClient   *http.Client
	feishuRate   *rate.Limiter
}

func NewWorker(store DeliveryStore, decryptor secrets.Decryptor, allowedHosts []string, pollInterval time.Duration, batchSize int) *Worker {
	if pollInterval <= 0 {
		pollInterval = 500 * time.Millisecond
	}
	if batchSize <= 0 {
		batchSize = 25
	}
	return &Worker{store: store, decryptor: decryptor, allowedHosts: allowedHosts, pollInterval: pollInterval, batchSize: batchSize, httpClient: safeHTTPClient(), feishuRate: rate.NewLimiter(5, 5)}
}

func (w *Worker) Run(ctx context.Context) error {
	ticker := time.NewTicker(w.pollInterval)
	defer ticker.Stop()
	for {
		if err := w.Poll(ctx); err != nil && ctx.Err() == nil {
			return err
		}
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-ticker.C:
		}
	}
}

func (w *Worker) Poll(ctx context.Context) error {
	deliveries, err := w.store.ClaimWebhookDeliveries(ctx, w.batchSize)
	if err != nil {
		return fmt.Errorf("claim webhook deliveries: %w", err)
	}
	for _, delivery := range deliveries {
		if err := w.deliver(ctx, delivery); err != nil {
			if markErr := w.store.FailWebhookDelivery(ctx, delivery.ID, delivery.Attempts, responseStatus(err), err); markErr != nil {
				return fmt.Errorf("mark webhook delivery failed: %w", markErr)
			}
		}
	}
	return nil
}

func (w *Worker) deliver(ctx context.Context, delivery model.WebhookDelivery) error {
	if w.decryptor == nil {
		return fmt.Errorf("webhook decryptor is unavailable")
	}
	endpointBytes, err := w.decryptor.Decrypt(delivery.EndpointEncrypted)
	if err != nil {
		return fmt.Errorf("decrypt webhook endpoint: %w", err)
	}
	defer wipe(endpointBytes)
	endpoint, err := ValidateEndpoint(string(endpointBytes), delivery.Kind, w.allowedHosts)
	if err != nil || endpoint.Hostname() != delivery.EndpointHost {
		return fmt.Errorf("invalid stored webhook endpoint")
	}
	secret, err := w.decryptor.Decrypt(delivery.SigningSecretEncrypted)
	if err != nil && len(delivery.SigningSecretEncrypted) > 0 {
		return fmt.Errorf("decrypt webhook signing secret: %w", err)
	}
	defer wipe(secret)
	if delivery.Kind == "feishu" {
		if err := w.feishuRate.Wait(ctx); err != nil {
			return err
		}
	}
	body, headers, err := BuildPayload(delivery.Kind, delivery.Payload, secret, time.Now().UTC())
	if err != nil {
		return err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, endpoint.String(), bytes.NewReader(body))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("User-Agent", "agent-room-webhook-worker/1")
	req.Header.Set("X-Agent-Room-Event", delivery.EventType)
	req.Header.Set("X-Agent-Room-Delivery", fmt.Sprintf("%d", delivery.ID))
	for key, value := range headers {
		req.Header.Set(key, value)
	}
	response, err := w.httpClient.Do(req)
	if err != nil {
		return err
	}
	defer response.Body.Close()
	responseBody, _ := io.ReadAll(io.LimitReader(response.Body, 64<<10))
	if err = DeliverySucceeded(delivery.Kind, response.StatusCode, responseBody); err != nil {
		return &deliveryError{status: response.StatusCode, err: err}
	}
	return w.store.CompleteWebhookDelivery(ctx, delivery.ID, response.StatusCode)
}

type deliveryError struct {
	status int
	err    error
}

func (e *deliveryError) Error() string { return e.err.Error() }
func (e *deliveryError) Unwrap() error { return e.err }

func responseStatus(err error) int {
	if typed, ok := err.(*deliveryError); ok {
		return typed.status
	}
	return 0
}

func safeHTTPClient() *http.Client {
	dialer := &net.Dialer{Timeout: 5 * time.Second, KeepAlive: 30 * time.Second}
	transport := http.DefaultTransport.(*http.Transport).Clone()
	transport.Proxy = nil
	transport.DialContext = func(ctx context.Context, network, address string) (net.Conn, error) {
		host, port, err := net.SplitHostPort(address)
		if err != nil {
			return nil, err
		}
		ips, err := net.DefaultResolver.LookupNetIP(ctx, "ip", host)
		if err != nil {
			return nil, err
		}
		for _, ip := range ips {
			if publicIP(ip) {
				return dialer.DialContext(ctx, network, net.JoinHostPort(ip.String(), port))
			}
		}
		return nil, fmt.Errorf("webhook host %q resolved only to non-public addresses", host)
	}
	return &http.Client{Timeout: 10 * time.Second, Transport: transport, CheckRedirect: func(_ *http.Request, _ []*http.Request) error { return http.ErrUseLastResponse }}
}

func publicIP(ip netip.Addr) bool {
	if ip.IsLoopback() || ip.IsPrivate() || ip.IsLinkLocalUnicast() || ip.IsLinkLocalMulticast() || ip.IsMulticast() || ip.IsUnspecified() {
		return false
	}
	return !ip.IsInterfaceLocalMulticast()
}

func wipe(value []byte) {
	for i := range value {
		value[i] = 0
	}
}
