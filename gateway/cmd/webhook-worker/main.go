package main

import (
	"context"
	"errors"
	"log/slog"
	"os"
	"os/signal"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/agent-room/agent-room/gateway/internal/secrets"
	"github.com/agent-room/agent-room/gateway/internal/store"
	"github.com/agent-room/agent-room/gateway/internal/webhook"
)

func main() {
	log := slog.New(slog.NewJSONHandler(os.Stdout, nil))
	databaseURL := os.Getenv("DATABASE_URL")
	key := os.Getenv("GATEWAY_CONFIG_ENCRYPTION_KEY")
	if databaseURL == "" || key == "" {
		log.Error("DATABASE_URL and GATEWAY_CONFIG_ENCRYPTION_KEY are required")
		os.Exit(2)
	}
	protector, err := secrets.NewAESGCM(key)
	if err != nil {
		log.Error("invalid configuration encryption key", "error", err)
		os.Exit(2)
	}
	db, err := store.Open(context.Background(), databaseURL, int32(envInt("GATEWAY_DB_MAX_CONNECTIONS", 10)))
	if err != nil {
		log.Error("open database failed", "error", err)
		os.Exit(2)
	}
	defer db.Close()
	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()
	worker := webhook.NewWorker(db, protector, splitCSV(os.Getenv("GATEWAY_WEBHOOK_ALLOWED_HOSTS")), envDuration("WEBHOOK_WORKER_POLL", 500*time.Millisecond), envInt("WEBHOOK_WORKER_BATCH", 25))
	log.Info("webhook worker started")
	if err = worker.Run(ctx); err != nil && !errors.Is(err, context.Canceled) {
		log.Error("webhook worker stopped", "error", err)
		os.Exit(1)
	}
}

func envInt(key string, fallback int) int {
	value, err := strconv.Atoi(os.Getenv(key))
	if err != nil || value < 1 {
		return fallback
	}
	return value
}

func envDuration(key string, fallback time.Duration) time.Duration {
	value, err := time.ParseDuration(os.Getenv(key))
	if err != nil || value <= 0 {
		return fallback
	}
	return value
}

func splitCSV(value string) []string {
	parts := strings.Split(value, ",")
	items := make([]string, 0, len(parts))
	for _, part := range parts {
		if part = strings.TrimSpace(part); part != "" {
			items = append(items, part)
		}
	}
	return items
}
