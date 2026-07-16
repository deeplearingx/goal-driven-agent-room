// Package embeddings contains provider adapters used by trusted background
// workers. It deliberately has no dependency on HTTP handlers, so decrypted
// provider credentials never cross the public API boundary.
package embeddings

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"net/http"
	"net/url"
	"strings"
	"time"
)

var ErrInvalidProfile = errors.New("invalid embedding profile")

type Profile struct {
	Provider   string
	ModelName  string
	Parameters json.RawMessage
}

type OpenAICompatible struct {
	client       *http.Client
	allowedHosts map[string]struct{}
}

func NewOpenAICompatible(client *http.Client) *OpenAICompatible {
	return NewOpenAICompatibleWithAllowedHosts(client, []string{"api.openai.com"})
}

func NewOpenAICompatibleWithAllowedHosts(client *http.Client, allowedHosts []string) *OpenAICompatible {
	if client == nil {
		client = &http.Client{Timeout: 45 * time.Second}
	}
	hosts := make(map[string]struct{}, len(allowedHosts))
	for _, host := range allowedHosts {
		if normalized := strings.ToLower(strings.TrimSpace(host)); normalized != "" {
			hosts[normalized] = struct{}{}
		}
	}
	return &OpenAICompatible{client: client, allowedHosts: hosts}
}

// Embed sends a single bounded batch to the OpenAI embeddings API. A custom
// base URL is supported for OpenAI-compatible private deployments, but must be
// HTTPS to prevent API keys being sent over plaintext transport.
func (c *OpenAICompatible) Embed(ctx context.Context, profile Profile, apiKey []byte, inputs []string) ([][]float32, error) {
	if strings.TrimSpace(profile.Provider) != "openai" || strings.TrimSpace(profile.ModelName) == "" || len(apiKey) == 0 || len(inputs) == 0 || len(inputs) > 128 {
		return nil, ErrInvalidProfile
	}
	endpoint, dimensions, err := embeddingEndpoint(profile.Parameters, c.allowedHosts)
	if err != nil {
		return nil, err
	}
	requestBody := struct {
		Model      string   `json:"model"`
		Input      []string `json:"input"`
		Dimensions *int     `json:"dimensions,omitempty"`
	}{Model: profile.ModelName, Input: inputs, Dimensions: dimensions}
	body, err := json.Marshal(requestBody)
	if err != nil {
		return nil, err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, endpoint, bytes.NewReader(body))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Authorization", "Bearer "+string(apiKey))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Accept", "application/json")
	response, err := c.client.Do(req)
	if err != nil {
		return nil, err
	}
	defer response.Body.Close()
	limited := io.LimitReader(response.Body, 8<<20)
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		_, _ = io.Copy(io.Discard, limited)
		return nil, fmt.Errorf("embedding provider returned HTTP %d", response.StatusCode)
	}
	var decoded struct {
		Data []struct {
			Embedding []float32 `json:"embedding"`
			Index     int       `json:"index"`
		} `json:"data"`
	}
	if err = json.NewDecoder(limited).Decode(&decoded); err != nil {
		return nil, err
	}
	if len(decoded.Data) != len(inputs) {
		return nil, fmt.Errorf("embedding provider returned %d vectors for %d inputs", len(decoded.Data), len(inputs))
	}
	result := make([][]float32, len(inputs))
	for _, item := range decoded.Data {
		if item.Index < 0 || item.Index >= len(result) || result[item.Index] != nil || !validVector(item.Embedding) {
			return nil, fmt.Errorf("embedding provider returned invalid vector")
		}
		result[item.Index] = item.Embedding
	}
	return result, nil
}

func embeddingEndpoint(raw json.RawMessage, allowedHosts map[string]struct{}) (string, *int, error) {
	parameters := struct {
		BaseURL    string `json:"base_url"`
		Dimensions *int   `json:"dimensions"`
	}{BaseURL: "https://api.openai.com/v1"}
	if len(raw) > 0 {
		if !json.Valid(raw) || json.Unmarshal(raw, &parameters) != nil {
			return "", nil, ErrInvalidProfile
		}
	}
	base, err := url.Parse(strings.TrimRight(strings.TrimSpace(parameters.BaseURL), "/"))
	if err != nil || base.Scheme != "https" || base.Host == "" || base.User != nil || base.RawQuery != "" || base.Fragment != "" {
		return "", nil, ErrInvalidProfile
	}
	if _, ok := allowedHosts[strings.ToLower(base.Host)]; !ok {
		return "", nil, ErrInvalidProfile
	}
	if parameters.Dimensions != nil && (*parameters.Dimensions < 1 || *parameters.Dimensions > 8192) {
		return "", nil, ErrInvalidProfile
	}
	return base.String() + "/embeddings", parameters.Dimensions, nil
}

func validVector(vector []float32) bool {
	if len(vector) == 0 || len(vector) > 8192 {
		return false
	}
	for _, value := range vector {
		if math.IsNaN(float64(value)) || math.IsInf(float64(value), 0) {
			return false
		}
	}
	return true
}
