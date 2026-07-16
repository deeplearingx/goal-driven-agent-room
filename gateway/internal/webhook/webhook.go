package webhook

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"net/url"
	"strconv"
	"strings"
	"time"
)

const maxFeishuBodyBytes = 20 << 10

func ValidateEndpoint(raw, kind string, allowedHosts []string) (*url.URL, error) {
	endpoint, err := url.Parse(strings.TrimSpace(raw))
	if err != nil || endpoint.Scheme != "https" || endpoint.Hostname() == "" || endpoint.User != nil || endpoint.Fragment != "" {
		return nil, errors.New("webhook endpoint must be an absolute HTTPS URL without userinfo or fragment")
	}
	if kind != "generic" && kind != "feishu" {
		return nil, errors.New("webhook kind must be generic or feishu")
	}
	if kind == "feishu" && endpoint.Hostname() != "open.feishu.cn" {
		return nil, errors.New("feishu webhook endpoint must use open.feishu.cn")
	}
	if !HostAllowed(endpoint.Hostname(), allowedHosts) {
		return nil, errors.New("webhook endpoint host is not allowlisted")
	}
	return endpoint, nil
}

func HostAllowed(host string, allowedHosts []string) bool {
	host = strings.ToLower(strings.TrimSuffix(host, "."))
	if ip := net.ParseIP(host); ip != nil {
		return false
	}
	for _, allowed := range allowedHosts {
		allowed = strings.ToLower(strings.TrimSpace(strings.TrimSuffix(allowed, ".")))
		if allowed == host || strings.HasPrefix(allowed, "*.") && strings.HasSuffix(host, allowed[1:]) && host != allowed[2:] {
			return true
		}
	}
	return false
}

func BuildPayload(kind string, event json.RawMessage, secret []byte, now time.Time) ([]byte, map[string]string, error) {
	timestamp := strconv.FormatInt(now.Unix(), 10)
	if kind == "feishu" {
		signature := ""
		if len(secret) > 0 {
			key := []byte(timestamp + "\n" + string(secret))
			mac := hmac.New(sha256.New, key)
			signature = base64.StdEncoding.EncodeToString(mac.Sum(nil))
		}
		text := feishuText(event)
		payload := map[string]any{"timestamp": timestamp, "sign": signature, "msg_type": "text", "content": map[string]string{"text": text}}
		body, err := json.Marshal(payload)
		if err != nil {
			return nil, nil, err
		}
		if len(body) > maxFeishuBodyBytes {
			return nil, nil, errors.New("feishu webhook payload exceeds 20 KiB")
		}
		return body, map[string]string{}, nil
	}
	if kind != "generic" {
		return nil, nil, errors.New("unsupported webhook kind")
	}
	body := append([]byte(nil), event...)
	headers := map[string]string{"X-Agent-Room-Timestamp": timestamp}
	if len(secret) > 0 {
		mac := hmac.New(sha256.New, secret)
		_, _ = mac.Write([]byte(timestamp + "."))
		_, _ = mac.Write(body)
		headers["X-Agent-Room-Signature"] = "sha256=" + hex.EncodeToString(mac.Sum(nil))
	}
	return body, headers, nil
}

func DeliverySucceeded(kind string, status int, response []byte) error {
	if status < 200 || status >= 300 {
		return fmt.Errorf("webhook returned HTTP %d", status)
	}
	if kind != "feishu" {
		return nil
	}
	var result struct {
		Code       int    `json:"code"`
		StatusCode int    `json:"StatusCode"`
		Message    string `json:"msg"`
	}
	if err := json.Unmarshal(response, &result); err != nil {
		return errors.New("feishu returned invalid JSON")
	}
	if result.Code != 0 || result.StatusCode != 0 {
		return fmt.Errorf("feishu rejected webhook: code=%d status_code=%d message=%s", result.Code, result.StatusCode, result.Message)
	}
	return nil
}

func feishuText(event json.RawMessage) string {
	var envelope struct {
		TaskID string          `json:"task_id"`
		Type   string          `json:"type"`
		Data   json.RawMessage `json:"data"`
	}
	if json.Unmarshal(event, &envelope) != nil {
		return "Agent Room 事件通知"
	}
	status := ""
	var data map[string]any
	if json.Unmarshal(envelope.Data, &data) == nil {
		if value, ok := data["status"].(string); ok {
			status = value
		}
		if status == "" {
			if value, ok := data["error"].(string); ok {
				status = value
			}
		}
	}
	text := fmt.Sprintf("Agent Room · %s\n任务：%s", envelope.Type, envelope.TaskID)
	if status != "" {
		text += "\n状态：" + status
	}
	return text
}
