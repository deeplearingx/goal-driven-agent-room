package main

import (
	"context"
	"errors"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/agent-room/agent-room/gateway/internal/auth"
	"github.com/agent-room/agent-room/gateway/internal/broker"
	"github.com/agent-room/agent-room/gateway/internal/cache"
	"github.com/agent-room/agent-room/gateway/internal/config"
	"github.com/agent-room/agent-room/gateway/internal/embeddings"
	"github.com/agent-room/agent-room/gateway/internal/httpapi"
	"github.com/agent-room/agent-room/gateway/internal/model"
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
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()
	db, err := store.Open(ctx, cfg.DatabaseURL, cfg.DBMaxConnections)
	if err != nil {
		log.Error("database startup failed", "error", err)
		os.Exit(1)
	}
	defer db.Close()
	if cfg.AutoMigrate {
		if err = db.Migrate(ctx); err != nil {
			log.Error("database migration failed", "error", err)
			os.Exit(1)
		}
	}
	if cfg.MigrateOnly {
		log.Info("database migrations complete")
		return
	}
	rabbit := broker.New(cfg.RabbitMQURL, log)
	defer rabbit.Close()
	if err = rabbit.Ping(); err != nil {
		log.Error("rabbitmq startup failed", "error", err)
		os.Exit(1)
	}
	hub := httpapi.NewHub()
	var authenticator auth.Authenticator
	if cfg.OIDCIssuer != "" {
		authenticator, err = auth.NewOIDC(ctx, cfg.OIDCIssuer, cfg.OIDCAudience, cfg.OIDCTenantClaim, cfg.OIDCRolesClaim)
		if err != nil {
			log.Error("OIDC startup failed", "error", err)
			os.Exit(1)
		}
	}
	var protector *secrets.AESGCM
	if cfg.ConfigEncryptionKey != "" {
		protector, err = secrets.NewAESGCM(cfg.ConfigEncryptionKey)
		if err != nil {
			log.Error("configuration encryption startup failed", "error", err)
			os.Exit(2)
		}
	}
	var configCache *cache.RedisConfigCache
	if cfg.RedisURL != "" {
		configCache, err = cache.NewRedisConfigCache(cfg.RedisURL, cfg.ConfigCacheTTL, cfg.ConfigCacheLocalLimit)
		if err == nil {
			pingCtx, cancel := context.WithTimeout(ctx, 2*time.Second)
			err = configCache.Ping(pingCtx)
			cancel()
		}
		if err != nil {
			log.Error("Redis configuration cache startup failed", "error", err)
			if cfg.Environment == "production" {
				os.Exit(2)
			}
			configCache = nil
		} else {
			defer configCache.Close()
			go listenConfigInvalidations(ctx, configCache, log)
		}
	}
	api := httpapi.New(db, hub, httpapi.Config{APIKey: cfg.APIKey, AllowedOrigins: cfg.AllowedOrigins, RatePerSecond: cfg.RatePerSecond, RateBurst: cfg.RateBurst, MaxTenantLimiters: cfg.MaxTenantLimiters, MaxRequestBytes: cfg.MaxRequestBytes, SSEHeartbeat: cfg.SSEHeartbeat, WorkspaceRoot: cfg.WorkspaceRoot, DefaultTenant: cfg.DefaultTenant, TrustTenantHeader: cfg.TrustTenantHeader, MetricsPublic: cfg.MetricsPublic, Authenticator: authenticator, SecretProtector: protector, SecretDecryptor: protector, EmbeddingClient: embeddings.NewOpenAICompatibleWithAllowedHosts(nil, cfg.EmbeddingAllowedHosts), GuardMode: cfg.GuardMode, SecretKeyReference: cfg.ConfigEncryptionKeyID, EnableEinoRuntime: cfg.EnableEinoRuntime, WebhookAllowedHosts: cfg.WebhookAllowedHosts, ConfigCache: configCache, Readiness: func(context.Context) error { return rabbit.Ping() }}, log)
	server := &http.Server{Addr: cfg.HTTPAddr, Handler: api.Handler(), ReadHeaderTimeout: 5 * time.Second, ReadTimeout: 30 * time.Second, WriteTimeout: 0, IdleTimeout: 75 * time.Second, MaxHeaderBytes: 1 << 20}
	errCh := make(chan error, 4)
	go func() { errCh <- broker.RunOutbox(ctx, db, rabbit, cfg.OutboxPoll, log) }()
	go maintain(ctx, db, cfg.Retention, cfg.CleanupInterval, log)
	go func() {
		errCh <- rabbit.ConsumeEvents(ctx, func(ctx context.Context, e model.Event) error {
			projected, inserted, err := db.ProjectEvent(ctx, e)
			if inserted {
				hub.Notify(projected.TaskID)
			}
			return err
		})
	}()
	go listen(ctx, db, hub, log, errCh)
	go func() { log.Info("gateway listening", "address", cfg.HTTPAddr); errCh <- server.ListenAndServe() }()
	select {
	case <-ctx.Done():
	case err = <-errCh:
		if err != nil && !errors.Is(err, http.ErrServerClosed) && !errors.Is(err, context.Canceled) {
			log.Error("service component stopped", "error", err)
		}
	}
	shutdownCtx, cancel := context.WithTimeout(context.Background(), cfg.ShutdownTimeout)
	defer cancel()
	_ = server.Shutdown(shutdownCtx)
}

type cleaner interface {
	Cleanup(context.Context, time.Duration) (store.CleanupStats, error)
}

func maintain(ctx context.Context, db cleaner, retention, interval time.Duration, log *slog.Logger) {
	if retention <= 0 || interval <= 0 {
		return
	}
	ticker := time.NewTicker(interval)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			stats, err := db.Cleanup(ctx, retention)
			if err != nil {
				log.Error("retention cleanup failed", "error", err)
			} else if stats.Tasks+stats.Outbox+stats.Inbox > 0 {
				log.Info("retention cleanup complete", "tasks", stats.Tasks, "outbox", stats.Outbox, "inbox", stats.Inbox)
			}
		}
	}
}

type listener interface {
	Listen(context.Context, func(string)) error
}

func listen(ctx context.Context, db listener, hub *httpapi.Hub, log *slog.Logger, errCh chan<- error) {
	for ctx.Err() == nil {
		err := db.Listen(ctx, hub.Notify)
		if ctx.Err() != nil {
			errCh <- ctx.Err()
			return
		}
		log.Error("postgres event listener disconnected", "error", err)
		select {
		case <-ctx.Done():
			errCh <- ctx.Err()
			return
		case <-time.After(time.Second):
		}
	}
}

func listenConfigInvalidations(ctx context.Context, configCache *cache.RedisConfigCache, log *slog.Logger) {
	for ctx.Err() == nil {
		err := configCache.Listen(ctx)
		if ctx.Err() != nil {
			return
		}
		log.Error("Redis invalidation subscriber disconnected", "error", err)
		select {
		case <-ctx.Done():
			return
		case <-time.After(time.Second):
		}
	}
}
