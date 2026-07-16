//go:build eino

package runtime

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"

	openai "github.com/cloudwego/eino-ext/components/model/openai"
	einoModel "github.com/cloudwego/eino/components/model"
)

// OpenAIProfile is the non-secret portion of an immutable Model Profile
// version. The decrypted account key is supplied separately and is never
// retained by this value or emitted in runtime events.
type OpenAIProfile struct {
	Provider   string
	ModelName  string
	Parameters json.RawMessage
}

func NewOpenAIModel(ctx context.Context, profile OpenAIProfile, apiKey []byte) (einoModel.ToolCallingChatModel, error) {
	if strings.TrimSpace(profile.Provider) != "openai" || strings.TrimSpace(profile.ModelName) == "" || len(apiKey) == 0 {
		return nil, ErrInvalidRequest
	}
	var parameters struct {
		Temperature         *float32 `json:"temperature"`
		MaxCompletionTokens *int     `json:"max_completion_tokens"`
		MaxTokens           *int     `json:"max_tokens"`
	}
	if len(profile.Parameters) > 0 && !json.Valid(profile.Parameters) {
		return nil, fmt.Errorf("invalid model profile parameters")
	}
	if len(profile.Parameters) > 0 {
		if err := json.Unmarshal(profile.Parameters, &parameters); err != nil {
			return nil, err
		}
	}
	return openai.NewChatModel(ctx, &openai.ChatModelConfig{
		APIKey:              string(apiKey),
		Model:               profile.ModelName,
		Temperature:         parameters.Temperature,
		MaxCompletionTokens: parameters.MaxCompletionTokens,
		MaxTokens:           parameters.MaxTokens,
	})
}
