// Package agentroom provides a dependency-free Go client for the Agent Room
// control-plane API.
package agentroom

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"time"
)

const defaultMaxResponseBytes int64 = 8 << 20

type Config struct {
	BaseURL          string
	Token            string
	TenantID         string
	HTTPClient       *http.Client
	MaxResponseBytes int64
}

type Client struct {
	baseURL          *url.URL
	token            string
	tenantID         string
	httpClient       *http.Client
	maxResponseBytes int64
}

type Problem struct {
	Type   string `json:"type"`
	Title  string `json:"title"`
	Status int    `json:"status"`
	Code   string `json:"code"`
	Detail string `json:"detail"`
}

func (p *Problem) Error() string {
	if p.Code == "" {
		return fmt.Sprintf("agentroom: HTTP %d: %s", p.Status, p.Detail)
	}
	return fmt.Sprintf("agentroom: %s (HTTP %d): %s", p.Code, p.Status, p.Detail)
}

func New(config Config) (*Client, error) {
	raw := strings.TrimSpace(config.BaseURL)
	if raw == "" {
		return nil, errors.New("agentroom: base URL is required")
	}
	parsed, err := url.Parse(raw)
	if err != nil || parsed.Host == "" || (parsed.Scheme != "http" && parsed.Scheme != "https") {
		return nil, errors.New("agentroom: base URL must be an absolute HTTP(S) URL")
	}
	parsed.Path = strings.TrimRight(parsed.Path, "/")
	parsed.RawQuery, parsed.Fragment = "", ""
	client := config.HTTPClient
	if client == nil {
		client = &http.Client{Timeout: 30 * time.Second}
	}
	maxBytes := config.MaxResponseBytes
	if maxBytes <= 0 {
		maxBytes = defaultMaxResponseBytes
	}
	return &Client{baseURL: parsed, token: strings.TrimSpace(config.Token), tenantID: strings.TrimSpace(config.TenantID), httpClient: client, maxResponseBytes: maxBytes}, nil
}

func (c *Client) CreateTask(ctx context.Context, input CreateTaskInput, idempotencyKey string) (Task, error) {
	var task Task
	err := c.doJSON(ctx, http.MethodPost, "/api/v1/tasks", input, map[string]string{"Idempotency-Key": strings.TrimSpace(idempotencyKey)}, &task)
	return task, err
}

func (c *Client) GetTask(ctx context.Context, taskID string) (Task, error) {
	var task Task
	err := c.doJSON(ctx, http.MethodGet, "/api/v1/tasks/"+url.PathEscape(taskID), nil, nil, &task)
	return task, err
}

func (c *Client) CancelTask(ctx context.Context, taskID string) error {
	return c.doJSON(ctx, http.MethodPost, "/api/v1/tasks/"+url.PathEscape(taskID)+"/cancel", struct{}{}, nil, nil)
}

func (c *Client) RetryTask(ctx context.Context, taskID string) error {
	return c.doJSON(ctx, http.MethodPost, "/api/v1/tasks/"+url.PathEscape(taskID)+"/retry", struct{}{}, nil, nil)
}

func (c *Client) SearchKnowledge(ctx context.Context, input KnowledgeSearchInput) ([]KnowledgeChunk, error) {
	var response struct {
		Chunks []KnowledgeChunk `json:"chunks"`
	}
	err := c.doJSON(ctx, http.MethodPost, "/api/v1/knowledge/search", input, nil, &response)
	return response.Chunks, err
}

// StreamEvents blocks until the server closes the stream, the context is
// cancelled, or handler returns an error. The caller can resume with the last
// observed Event.Sequence.
func (c *Client) StreamEvents(ctx context.Context, taskID string, after int64, handler func(Event) error) error {
	if handler == nil {
		return errors.New("agentroom: event handler is required")
	}
	endpoint := "/api/v1/tasks/" + url.PathEscape(taskID) + "/events?from=" + strconv.FormatInt(after, 10)
	req, err := c.request(ctx, http.MethodGet, endpoint, nil, nil)
	if err != nil {
		return err
	}
	req.Header.Set("Accept", "text/event-stream")
	response, err := c.httpClient.Do(req)
	if err != nil {
		return fmt.Errorf("agentroom: stream events: %w", err)
	}
	defer response.Body.Close()
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		return c.problem(response)
	}
	scanner := bufio.NewScanner(response.Body)
	scanner.Buffer(make([]byte, 64<<10), 2<<20)
	var current Event
	for scanner.Scan() {
		line := scanner.Text()
		if line == "" {
			if current.Type != "" || len(current.Data) > 0 {
				if err := handler(current); err != nil {
					return err
				}
			}
			current = Event{}
			continue
		}
		if strings.HasPrefix(line, ":") {
			continue
		}
		field, value, found := strings.Cut(line, ":")
		if !found {
			continue
		}
		value = strings.TrimPrefix(value, " ")
		switch field {
		case "id":
			current.Sequence, _ = strconv.ParseInt(value, 10, 64)
		case "event":
			current.Type = value
		case "data":
			if len(current.Data) > 0 {
				current.Data = append(current.Data, '\n')
			}
			current.Data = append(current.Data, value...)
		}
	}
	if err := scanner.Err(); err != nil && !errors.Is(err, context.Canceled) {
		return fmt.Errorf("agentroom: read event stream: %w", err)
	}
	return ctx.Err()
}

func (c *Client) doJSON(ctx context.Context, method, path string, input any, headers map[string]string, output any) error {
	var body io.Reader
	if input != nil {
		encoded, err := json.Marshal(input)
		if err != nil {
			return fmt.Errorf("agentroom: encode request: %w", err)
		}
		body = bytes.NewReader(encoded)
	}
	req, err := c.request(ctx, method, path, body, headers)
	if err != nil {
		return err
	}
	if input != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	response, err := c.httpClient.Do(req)
	if err != nil {
		return fmt.Errorf("agentroom: request: %w", err)
	}
	defer response.Body.Close()
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		return c.problem(response)
	}
	if output == nil || response.StatusCode == http.StatusNoContent {
		_, _ = io.Copy(io.Discard, io.LimitReader(response.Body, c.maxResponseBytes))
		return nil
	}
	decoder := json.NewDecoder(io.LimitReader(response.Body, c.maxResponseBytes+1))
	if err := decoder.Decode(output); err != nil {
		return fmt.Errorf("agentroom: decode response: %w", err)
	}
	return nil
}

func (c *Client) request(ctx context.Context, method, path string, body io.Reader, headers map[string]string) (*http.Request, error) {
	endpoint := *c.baseURL
	endpoint.Path = strings.TrimRight(c.baseURL.Path, "/") + path
	if queryAt := strings.Index(endpoint.Path, "?"); queryAt >= 0 {
		endpoint.RawQuery = endpoint.Path[queryAt+1:]
		endpoint.Path = endpoint.Path[:queryAt]
	}
	req, err := http.NewRequestWithContext(ctx, method, endpoint.String(), body)
	if err != nil {
		return nil, fmt.Errorf("agentroom: create request: %w", err)
	}
	if c.token != "" {
		req.Header.Set("Authorization", "Bearer "+c.token)
	}
	if c.tenantID != "" {
		req.Header.Set("X-Tenant-ID", c.tenantID)
	}
	for key, value := range headers {
		if value != "" {
			req.Header.Set(key, value)
		}
	}
	return req, nil
}

func (c *Client) problem(response *http.Response) error {
	var problem Problem
	if err := json.NewDecoder(io.LimitReader(response.Body, c.maxResponseBytes)).Decode(&problem); err != nil {
		return &Problem{Status: response.StatusCode, Title: response.Status, Detail: "request failed"}
	}
	if problem.Status == 0 {
		problem.Status = response.StatusCode
	}
	return &problem
}
