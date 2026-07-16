package config

import (
	"fmt"
	"os"
	"strconv"
	"strings"
	"time"

	"github.com/agent-room/agent-room/gateway/internal/guard"
)

type Config struct {
	HTTPAddr              string
	Environment           string
	DatabaseURL           string
	RabbitMQURL           string
	APIKey                string
	AllowedOrigins        string
	RatePerSecond         float64
	RateBurst             int
	MaxTenantLimiters     int
	DBMaxConnections      int32
	ShutdownTimeout       time.Duration
	OutboxPoll            time.Duration
	SSEHeartbeat          time.Duration
	MaxRequestBytes       int64
	WorkspaceRoot         string
	DefaultTenant         string
	TrustTenantHeader     bool
	MetricsPublic         bool
	AutoMigrate           bool
	MigrateOnly           bool
	Retention             time.Duration
	CleanupInterval       time.Duration
	OIDCIssuer            string
	OIDCAudience          string
	OIDCTenantClaim       string
	OIDCRolesClaim        string
	ConfigEncryptionKey   string
	ConfigEncryptionKeyID string
	EnableEinoRuntime     bool
	EmbeddingAllowedHosts []string
	WebhookAllowedHosts   []string
	RedisURL              string
	ConfigCacheTTL        time.Duration
	ConfigCacheLocalLimit int
	GuardMode             guard.Mode
}

func Load() (Config, error) {
	c := Config{
		HTTPAddr:              env("GATEWAY_HTTP_ADDR", ":8080"),
		Environment:           env("GATEWAY_ENV", "development"),
		DatabaseURL:           os.Getenv("DATABASE_URL"),
		RabbitMQURL:           os.Getenv("RABBITMQ_URL"),
		APIKey:                os.Getenv("GATEWAY_API_KEY"),
		AllowedOrigins:        env("GATEWAY_ALLOWED_ORIGINS", "http://localhost:5173"),
		RatePerSecond:         envFloat("GATEWAY_RATE_PER_SECOND", 20),
		RateBurst:             envInt("GATEWAY_RATE_BURST", 40),
		MaxTenantLimiters:     envInt("GATEWAY_MAX_TENANT_LIMITERS", 10000),
		DBMaxConnections:      int32(envInt("GATEWAY_DB_MAX_CONNECTIONS", 30)),
		ShutdownTimeout:       envDuration("GATEWAY_SHUTDOWN_TIMEOUT", 20*time.Second),
		OutboxPoll:            envDuration("GATEWAY_OUTBOX_POLL", 200*time.Millisecond),
		SSEHeartbeat:          envDuration("GATEWAY_SSE_HEARTBEAT", 15*time.Second),
		MaxRequestBytes:       int64(envInt("GATEWAY_MAX_REQUEST_BYTES", 1<<20)),
		WorkspaceRoot:         os.Getenv("GATEWAY_WORKSPACE"),
		DefaultTenant:         env("GATEWAY_DEFAULT_TENANT", "default"),
		TrustTenantHeader:     envBool("GATEWAY_TRUST_TENANT_HEADER", false),
		MetricsPublic:         envBool("GATEWAY_METRICS_PUBLIC", false),
		AutoMigrate:           envBool("GATEWAY_AUTO_MIGRATE", false),
		MigrateOnly:           envBool("GATEWAY_MIGRATE_ONLY", false),
		Retention:             envDuration("GATEWAY_RETENTION", 30*24*time.Hour),
		CleanupInterval:       envDuration("GATEWAY_CLEANUP_INTERVAL", time.Hour),
		OIDCIssuer:            os.Getenv("GATEWAY_OIDC_ISSUER"),
		OIDCAudience:          os.Getenv("GATEWAY_OIDC_AUDIENCE"),
		OIDCTenantClaim:       env("GATEWAY_OIDC_TENANT_CLAIM", "tenant_id"),
		OIDCRolesClaim:        env("GATEWAY_OIDC_ROLES_CLAIM", "roles"),
		ConfigEncryptionKey:   os.Getenv("GATEWAY_CONFIG_ENCRYPTION_KEY"),
		ConfigEncryptionKeyID: env("GATEWAY_CONFIG_ENCRYPTION_KEY_ID", "local-v1"),
		EnableEinoRuntime:     envBool("GATEWAY_ENABLE_EINO_RUNTIME", false),
		EmbeddingAllowedHosts: splitCSV(env("GATEWAY_EMBEDDING_ALLOWED_HOSTS", "api.openai.com")),
		WebhookAllowedHosts:   splitCSV(env("GATEWAY_WEBHOOK_ALLOWED_HOSTS", "open.feishu.cn")),
		RedisURL:              os.Getenv("REDIS_URL"),
		ConfigCacheTTL:        envDuration("GATEWAY_CONFIG_CACHE_TTL", 30*time.Second),
		ConfigCacheLocalLimit: envInt("GATEWAY_CONFIG_CACHE_LOCAL_LIMIT", 2048),
		GuardMode:             guard.Mode(env("GATEWAY_GUARD_MODE", "warn")),
	}
	if c.DatabaseURL == "" || c.RabbitMQURL == "" {
		return Config{}, fmt.Errorf("DATABASE_URL and RABBITMQ_URL are required")
	}
	if c.DefaultTenant == "" {
		return Config{}, fmt.Errorf("GATEWAY_DEFAULT_TENANT must not be empty")
	}
	if c.MaxTenantLimiters < 1 || c.MaxTenantLimiters > 1000000 {
		return Config{}, fmt.Errorf("GATEWAY_MAX_TENANT_LIMITERS must be 1..1000000")
	}
	if c.ConfigCacheTTL < time.Second || c.ConfigCacheTTL > time.Hour || c.ConfigCacheLocalLimit < 1 || c.ConfigCacheLocalLimit > 1000000 {
		return Config{}, fmt.Errorf("GATEWAY_CONFIG_CACHE_TTL must be 1s..1h and GATEWAY_CONFIG_CACHE_LOCAL_LIMIT must be 1..1000000")
	}
	if mode, ok := guard.ParseMode(string(c.GuardMode)); !ok {
		return Config{}, fmt.Errorf("GATEWAY_GUARD_MODE must be off, warn, or block")
	} else {
		c.GuardMode = mode
	}
	if c.OIDCIssuer != "" && c.OIDCAudience == "" {
		return Config{}, fmt.Errorf("GATEWAY_OIDC_AUDIENCE is required when GATEWAY_OIDC_ISSUER is set")
	}
	if c.Environment == "production" && c.APIKey == "" && c.OIDCIssuer == "" {
		return Config{}, fmt.Errorf("GATEWAY_API_KEY or GATEWAY_OIDC_ISSUER is required when GATEWAY_ENV=production")
	}
	return c, nil
}

func splitCSV(value string) []string {
	parts := strings.Split(value, ",")
	result := make([]string, 0, len(parts))
	for _, part := range parts {
		if trimmed := strings.TrimSpace(part); trimmed != "" {
			result = append(result, trimmed)
		}
	}
	return result
}

func env(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

func envInt(key string, fallback int) int {
	v, err := strconv.Atoi(os.Getenv(key))
	if err != nil {
		return fallback
	}
	return v
}

func envFloat(key string, fallback float64) float64 {
	v, err := strconv.ParseFloat(os.Getenv(key), 64)
	if err != nil {
		return fallback
	}
	return v
}

func envDuration(key string, fallback time.Duration) time.Duration {
	v, err := time.ParseDuration(os.Getenv(key))
	if err != nil {
		return fallback
	}
	return v
}

func envBool(key string, fallback bool) bool {
	v := os.Getenv(key)
	if v == "" {
		return fallback
	}
	parsed, err := strconv.ParseBool(v)
	if err != nil {
		return fallback
	}
	return parsed
}
