package config

import "testing"

func TestProductionAllowsOIDCWithoutAPIKey(t *testing.T) {
	t.Setenv("DATABASE_URL", "postgres://example")
	t.Setenv("RABBITMQ_URL", "amqp://example")
	t.Setenv("GATEWAY_ENV", "production")
	t.Setenv("GATEWAY_API_KEY", "")
	t.Setenv("GATEWAY_OIDC_ISSUER", "https://issuer.example")
	t.Setenv("GATEWAY_OIDC_AUDIENCE", "agent-room")
	if _, err := Load(); err != nil {
		t.Fatal(err)
	}
}

func TestOIDCIssuerRequiresAudience(t *testing.T) {
	t.Setenv("DATABASE_URL", "postgres://example")
	t.Setenv("RABBITMQ_URL", "amqp://example")
	t.Setenv("GATEWAY_OIDC_ISSUER", "https://issuer.example")
	t.Setenv("GATEWAY_OIDC_AUDIENCE", "")
	if _, err := Load(); err == nil {
		t.Fatal("missing OIDC audience was accepted")
	}
}
