//go:build eino

package main

import (
	"context"
	"encoding/json"
	"errors"
	"log/slog"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/agent-room/agent-room/gateway/internal/broker"
	"github.com/agent-room/agent-room/gateway/internal/config"
	"github.com/agent-room/agent-room/gateway/internal/model"
	"github.com/agent-room/agent-room/gateway/internal/runtime"
	"github.com/agent-room/agent-room/gateway/internal/secrets"
	"github.com/agent-room/agent-room/gateway/internal/store"
	"github.com/google/uuid"
)

type worker struct {
	db        *store.Store
	rabbit    *broker.Rabbit
	protector secrets.Decryptor
	log       *slog.Logger
}

func main() {
	log := slog.New(slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{Level: slog.LevelInfo}))
	cfg, err := config.Load()
	if err != nil {
		log.Error("invalid configuration", "error", err)
		os.Exit(2)
	}
	if !cfg.EnableEinoRuntime || cfg.ConfigEncryptionKey == "" {
		log.Error("Eino worker requires GATEWAY_ENABLE_EINO_RUNTIME=true and GATEWAY_CONFIG_ENCRYPTION_KEY")
		os.Exit(2)
	}
	protector, err := secrets.NewAESGCM(cfg.ConfigEncryptionKey)
	if err != nil {
		log.Error("invalid configuration encryption key", "error", err)
		os.Exit(2)
	}
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()
	db, err := store.Open(ctx, cfg.DatabaseURL, cfg.DBMaxConnections)
	if err != nil {
		log.Error("database startup failed", "error", err)
		os.Exit(1)
	}
	defer db.Close()
	rabbit := broker.New(cfg.RabbitMQURL, log)
	defer rabbit.Close()
	if err = rabbit.Ping(); err != nil {
		log.Error("rabbitmq startup failed", "error", err)
		os.Exit(1)
	}
	w := &worker{db: db, rabbit: rabbit, protector: protector, log: log}
	log.Info("Eino worker ready")
	if err = rabbit.ConsumeEinoCommands(ctx, w.handle); err != nil && !errors.Is(err, context.Canceled) {
		log.Error("Eino worker stopped", "error", err)
		os.Exit(1)
	}
}

func (w *worker) handle(ctx context.Context, command model.Command) error {
	var payload struct {
		ExecutionRuntime string                `json:"execution_runtime"`
		Description      string                `json:"description"`
		MaxIterations    int                   `json:"max_iterations"`
		RuntimeSnapshot  model.RuntimeSnapshot `json:"runtime_snapshot"`
	}
	if command.Type != "run" || json.Unmarshal(command.Payload, &payload) != nil || payload.ExecutionRuntime != "go_eino" || payload.RuntimeSnapshot.ID == "" {
		return errors.New("invalid Eino command")
	}
	if payload.RuntimeSnapshot.TenantID != command.TenantID {
		return errors.New("runtime snapshot tenant mismatch")
	}
	prompt, err := w.db.GetPromptVersion(ctx, command.TenantID, payload.RuntimeSnapshot.PromptVersionID)
	if err != nil {
		return err
	}
	profile, err := w.db.GetModelProfileVersion(ctx, command.TenantID, payload.RuntimeSnapshot.ModelProfileVersionID)
	if err != nil {
		return err
	}
	account, encrypted, err := w.db.ProviderAccountSecret(ctx, command.TenantID, profile.ProviderAccountID)
	if err != nil {
		return err
	}
	key, err := w.protector.Decrypt(encrypted)
	if err != nil {
		return err
	}
	defer clear(key)
	chatModel, err := runtime.NewOpenAIModel(ctx, runtime.OpenAIProfile{Provider: account.Provider, ModelName: profile.ModelName, Parameters: profile.Parameters}, key)
	if err != nil {
		return err
	}
	agent, err := runtime.NewReAct(ctx, runtime.ReActConfig{SnapshotID: payload.RuntimeSnapshot.ID, Model: chatModel, MaxSteps: payload.MaxIterations})
	if err != nil {
		return err
	}
	if err = w.publish(ctx, command, "task_started", map[string]any{"type": "task_started", "task_id": command.TaskID, "runtime": "go_eino"}); err != nil {
		return err
	}
	startedAt := time.Now()
	result, err := agent.Run(ctx, runtime.Request{SnapshotID: payload.RuntimeSnapshot.ID, System: prompt.Content, Input: payload.Description})
	if err != nil {
		_ = w.publish(ctx, command, "runtime_invocation", map[string]any{"runtime": "go_eino", "invocation_kind": "model", "status": "failed", "runtime_snapshot_id": payload.RuntimeSnapshot.ID, "prompt_version_id": payload.RuntimeSnapshot.PromptVersionID, "model_profile_version_id": payload.RuntimeSnapshot.ModelProfileVersionID, "latency_ms": time.Since(startedAt).Milliseconds()})
		_ = w.publish(ctx, command, "task_error", map[string]any{"type": "task_error", "task_id": command.TaskID, "error": "Eino runtime execution failed"})
		return err
	}
	invocation := map[string]any{"runtime": "go_eino", "invocation_kind": "model", "status": "succeeded", "runtime_snapshot_id": payload.RuntimeSnapshot.ID, "prompt_version_id": payload.RuntimeSnapshot.PromptVersionID, "model_profile_version_id": payload.RuntimeSnapshot.ModelProfileVersionID, "input_tokens": result.InputTokens, "output_tokens": result.OutputTokens, "latency_ms": time.Since(startedAt).Milliseconds()}
	if cost := estimateCostUSD(profile.Parameters, result.InputTokens, result.OutputTokens); cost != nil {
		invocation["estimated_cost_usd"] = *cost
	}
	if err = w.publish(ctx, command, "runtime_invocation", invocation); err != nil {
		return err
	}
	return w.publish(ctx, command, "task_finished", map[string]any{"type": "task_finished", "task_id": command.TaskID, "result": result.Content, "runtime_snapshot_id": result.SnapshotID})
}

func estimateCostUSD(parameters json.RawMessage, inputTokens, outputTokens int) *float64 {
	var pricing struct {
		PricePer1KInput  *float64 `json:"price_per_1k_input"`
		PricePer1KOutput *float64 `json:"price_per_1k_output"`
	}
	if len(parameters) == 0 || json.Unmarshal(parameters, &pricing) != nil || (pricing.PricePer1KInput == nil && pricing.PricePer1KOutput == nil) {
		return nil
	}
	cost := float64(inputTokens) / 1000 * valueOrZero(pricing.PricePer1KInput)
	cost += float64(outputTokens) / 1000 * valueOrZero(pricing.PricePer1KOutput)
	cost = float64(int64(cost*1e8+0.5)) / 1e8
	return &cost
}

func valueOrZero(value *float64) float64 {
	if value == nil {
		return 0
	}
	return *value
}

func (w *worker) publish(ctx context.Context, command model.Command, eventType string, data any) error {
	event := model.Event{MessageID: uuid.NewString(), TaskID: command.TaskID, TenantID: command.TenantID, Type: eventType, CreatedAt: time.Now().UTC()}
	body, err := json.Marshal(struct {
		model.Event
		Data any `json:"data"`
	}{Event: event, Data: data})
	if err != nil {
		return err
	}
	return w.rabbit.PublishEvent(ctx, event.MessageID, "task."+command.TenantID+"."+command.TaskID+"."+eventType, body)
}

func clear(bytes []byte) {
	for i := range bytes {
		bytes[i] = 0
	}
}
