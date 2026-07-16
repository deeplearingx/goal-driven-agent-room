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
	"github.com/agent-room/agent-room/gateway/internal/embeddings"
	"github.com/agent-room/agent-room/gateway/internal/model"
	"github.com/agent-room/agent-room/gateway/internal/rag"
	"github.com/agent-room/agent-room/gateway/internal/secrets"
	"github.com/agent-room/agent-room/gateway/internal/store"
)

func main() {
	log := slog.New(slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{Level: slog.LevelInfo}))
	cfg, err := config.Load()
	if err != nil {
		log.Error("invalid configuration", "error", err)
		os.Exit(2)
	}
	var protector secrets.Decryptor
	if cfg.ConfigEncryptionKey != "" {
		protector, err = secrets.NewAESGCM(cfg.ConfigEncryptionKey)
		if err != nil {
			log.Error("invalid configuration encryption key", "error", err)
			os.Exit(2)
		}
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
	log.Info("knowledge worker ready")
	embeddingClient := embeddings.NewOpenAICompatibleWithAllowedHosts(nil, cfg.EmbeddingAllowedHosts)
	if err = rabbit.ConsumeKnowledgeCommands(ctx, func(ctx context.Context, command model.Command) error {
		return ingest(ctx, db, protector, embeddingClient, command)
	}); err != nil && !errors.Is(err, context.Canceled) {
		log.Error("knowledge worker stopped", "error", err)
		os.Exit(1)
	}
}

type embedder interface {
	Embed(context.Context, embeddings.Profile, []byte, []string) ([][]float32, error)
}

func ingest(ctx context.Context, db *store.Store, protector secrets.Decryptor, embeddingClient embedder, command model.Command) error {
	var payload model.KnowledgeIngestPayload
	if command.Type != "knowledge.ingest" || json.Unmarshal(command.Payload, &payload) != nil || command.TenantID == "" || payload.DocumentID == "" {
		return errors.New("invalid knowledge ingest command")
	}
	chunks, err := rag.ChunkText(payload.Content, payload.ChunkSize, payload.ChunkOverlap)
	if err != nil {
		return err
	}
	var vectors [][]float32
	if payload.EmbeddingProfileVersionID != "" {
		if protector == nil {
			return errors.New("knowledge embedding requires GATEWAY_CONFIG_ENCRYPTION_KEY")
		}
		profile, profileErr := db.GetModelProfileVersion(ctx, command.TenantID, payload.EmbeddingProfileVersionID)
		if profileErr != nil {
			return profileErr
		}
		account, encrypted, accountErr := db.ProviderAccountSecret(ctx, command.TenantID, profile.ProviderAccountID)
		if accountErr != nil {
			return accountErr
		}
		key, decryptErr := protector.Decrypt(encrypted)
		if decryptErr != nil {
			return decryptErr
		}
		defer clear(key)
		vectors, err = embedChunks(ctx, embeddingClient, embeddings.Profile{Provider: account.Provider, ModelName: profile.ModelName, Parameters: profile.Parameters}, key, chunks)
		if err != nil {
			return err
		}
	}
	auditData, err := json.Marshal(map[string]int{"chunks": len(chunks)})
	if err != nil {
		return err
	}
	audit := model.AuditEvent{ID: command.MessageID + ".indexed", TenantID: command.TenantID, ActorID: payload.ActorID, Action: "knowledge.ingest.indexed", ResourceType: "knowledge_document", ResourceID: payload.DocumentID, RequestID: command.MessageID, Data: auditData, OccurredAt: time.Now().UTC()}
	if payload.EmbeddingProfileVersionID == "" {
		return db.IngestKnowledge(ctx, command.TenantID, payload.KnowledgeBaseID, payload.KnowledgeBaseName, payload.DocumentID, payload.SourceURI, payload.Title, payload.ContentHash, payload.ActorID, chunks, audit)
	}
	return db.IngestKnowledgeWithEmbeddings(ctx, command.TenantID, payload.KnowledgeBaseID, payload.KnowledgeBaseName, payload.DocumentID, payload.SourceURI, payload.Title, payload.ContentHash, payload.ActorID, payload.EmbeddingProfileVersionID, chunks, vectors, audit)
}

// embedChunks keeps each provider request below the 128-input batch limit while
// preserving the chunk order used later by the transactional database write.
func embedChunks(ctx context.Context, client embedder, profile embeddings.Profile, key []byte, chunks []rag.Chunk) ([][]float32, error) {
	result := make([][]float32, 0, len(chunks))
	for start := 0; start < len(chunks); start += 128 {
		end := start + 128
		if end > len(chunks) {
			end = len(chunks)
		}
		inputs := make([]string, end-start)
		for index, chunk := range chunks[start:end] {
			inputs[index] = chunk.Content
		}
		vectors, err := client.Embed(ctx, profile, key, inputs)
		if err != nil || len(vectors) != len(inputs) {
			if err != nil {
				return nil, err
			}
			return nil, errors.New("embedding provider returned wrong batch length")
		}
		result = append(result, vectors...)
	}
	return result, nil
}

func clear(bytes []byte) {
	for i := range bytes {
		bytes[i] = 0
	}
}
