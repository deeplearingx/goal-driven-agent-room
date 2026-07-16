//go:build eino

// Package runtime contains the Go execution contract used during the gradual
// migration from the existing Python LangGraph worker to Eino.
package runtime

import (
	"context"
	"errors"
	"strings"
	"sync"

	"github.com/cloudwego/eino/callbacks"
	"github.com/cloudwego/eino/components"
	"github.com/cloudwego/eino/components/model"
	"github.com/cloudwego/eino/compose"
	"github.com/cloudwego/eino/flow/agent/react"
	"github.com/cloudwego/eino/schema"
)

var ErrInvalidRequest = errors.New("invalid Eino runtime request")

// ReActConfig deliberately receives an already constructed provider model and
// tool node configuration. Provider credentials and tool authorization stay in
// the control plane; the runtime only consumes a frozen configuration snapshot.
type ReActConfig struct {
	SnapshotID string
	Model      model.ToolCallingChatModel
	Tools      compose.ToolsNodeConfig
	MaxSteps   int
}

type Request struct {
	SnapshotID string
	System     string
	Input      string
}

type Result struct {
	SnapshotID   string
	Content      string
	InputTokens  int
	OutputTokens int
}

type usageAccumulator struct {
	mu     sync.Mutex
	input  int
	output int
}

func (u *usageAccumulator) add(usage *model.TokenUsage) {
	if usage == nil {
		return
	}
	u.mu.Lock()
	u.input += usage.PromptTokens
	u.output += usage.CompletionTokens
	u.mu.Unlock()
}

func (u *usageAccumulator) totals() (int, int) {
	u.mu.Lock()
	defer u.mu.Unlock()
	return u.input, u.output
}

type ReActRuntime struct {
	snapshotID string
	agent      *react.Agent
}

func NewReAct(ctx context.Context, config ReActConfig) (*ReActRuntime, error) {
	if strings.TrimSpace(config.SnapshotID) == "" || config.Model == nil {
		return nil, ErrInvalidRequest
	}
	if config.MaxSteps <= 0 {
		config.MaxSteps = 12
	}
	agent, err := react.NewAgent(ctx, &react.AgentConfig{
		ToolCallingModel: config.Model,
		ToolsConfig:      config.Tools,
		MaxStep:          config.MaxSteps,
		GraphName:        "agent-room-react",
	})
	if err != nil {
		return nil, err
	}
	return &ReActRuntime{snapshotID: config.SnapshotID, agent: agent}, nil
}

// Run rejects mismatched snapshots so a queued task can never silently run
// with a newer prompt/model/tool configuration after it has been accepted.
func (r *ReActRuntime) Run(ctx context.Context, request Request) (Result, error) {
	if r == nil || r.agent == nil || request.SnapshotID != r.snapshotID || strings.TrimSpace(request.Input) == "" {
		return Result{}, ErrInvalidRequest
	}
	messages := make([]*schema.Message, 0, 2)
	if system := strings.TrimSpace(request.System); system != "" {
		messages = append(messages, &schema.Message{Role: schema.System, Content: system})
	}
	messages = append(messages, &schema.Message{Role: schema.User, Content: request.Input})
	usage := &usageAccumulator{}
	handler := callbacks.NewHandlerBuilder().OnEndFn(func(ctx context.Context, info *callbacks.RunInfo, output callbacks.CallbackOutput) context.Context {
		if info == nil || info.Component != components.ComponentOfChatModel {
			return ctx
		}
		if converted := model.ConvCallbackOutput(output); converted != nil {
			if converted.TokenUsage != nil {
				usage.add(converted.TokenUsage)
			} else if converted.Message != nil && converted.Message.ResponseMeta != nil && converted.Message.ResponseMeta.Usage != nil {
				meta := converted.Message.ResponseMeta.Usage
				usage.add(&model.TokenUsage{PromptTokens: meta.PromptTokens, CompletionTokens: meta.CompletionTokens, TotalTokens: meta.TotalTokens})
			}
		}
		return ctx
	}).Build()
	ctx = callbacks.InitCallbacks(ctx, nil, handler)
	message, err := r.agent.Generate(ctx, messages)
	if err != nil {
		return Result{}, err
	}
	inputTokens, outputTokens := usage.totals()
	return Result{SnapshotID: r.snapshotID, Content: message.Content, InputTokens: inputTokens, OutputTokens: outputTokens}, nil
}
