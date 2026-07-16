package tools

import (
	"bytes"
	"context"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"
)

type RESTConfig struct {
	Endpoint         string
	Method           string
	AllowedHosts     []string
	MaxResponseBytes int64
	Timeout          time.Duration
}

type RESTResult struct {
	StatusCode int
	Body       []byte
}

// ExecuteREST performs a policy-gated JSON request. It does not follow
// redirects: a redirect could bypass the versioned ToolSpec host allowlist.
func ExecuteREST(ctx context.Context, mode Mode, call Call, config RESTConfig) (RESTResult, Decision, error) {
	decision := Evaluate(mode, call)
	if decision.Outcome != Allowed {
		return RESTResult{}, decision, nil
	}
	if call.Tool.Kind != REST {
		return RESTResult{}, decision, fmt.Errorf("REST executor requires a REST ToolSpec")
	}
	u, err := url.Parse(config.Endpoint)
	if err != nil || u.Scheme != "https" || u.Hostname() == "" || !hostAllowed(u.Hostname(), config.AllowedHosts) {
		return RESTResult{}, decision, fmt.Errorf("REST endpoint is not allowed")
	}
	method := strings.ToUpper(strings.TrimSpace(config.Method))
	if method == "" {
		method = http.MethodPost
	}
	if method != http.MethodGet && method != http.MethodPost && method != http.MethodPut && method != http.MethodPatch && method != http.MethodDelete {
		return RESTResult{}, decision, fmt.Errorf("REST method is not allowed")
	}
	if config.MaxResponseBytes <= 0 {
		config.MaxResponseBytes = 1 << 20
	}
	if config.MaxResponseBytes > 8<<20 {
		config.MaxResponseBytes = 8 << 20
	}
	if config.Timeout <= 0 {
		config.Timeout = 10 * time.Second
	}
	req, err := http.NewRequestWithContext(ctx, method, u.String(), bytes.NewReader(call.Input))
	if err != nil {
		return RESTResult{}, decision, err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Accept", "application/json")
	client := &http.Client{Timeout: config.Timeout, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	response, err := client.Do(req)
	if err != nil {
		return RESTResult{}, decision, err
	}
	defer response.Body.Close()
	body, err := io.ReadAll(io.LimitReader(response.Body, config.MaxResponseBytes+1))
	if err != nil {
		return RESTResult{}, decision, err
	}
	if int64(len(body)) > config.MaxResponseBytes {
		return RESTResult{}, decision, fmt.Errorf("REST response exceeds configured limit")
	}
	return RESTResult{StatusCode: response.StatusCode, Body: body}, decision, nil
}

func hostAllowed(host string, allowed []string) bool {
	for _, value := range allowed {
		if strings.EqualFold(strings.TrimSpace(value), host) {
			return true
		}
	}
	return false
}
