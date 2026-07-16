package auth

import (
	"context"
	"encoding/json"
	"fmt"

	"github.com/coreos/go-oidc/v3/oidc"
)

type Identity struct {
	Subject  string
	TenantID string
	Roles    []string
}

type Authenticator interface {
	Authenticate(context.Context, string) (Identity, error)
}

type OIDCVerifier struct {
	verifier    *oidc.IDTokenVerifier
	tenantClaim string
	rolesClaim  string
}

func NewOIDC(ctx context.Context, issuer, audience, tenantClaim, rolesClaim string) (*OIDCVerifier, error) {
	provider, err := oidc.NewProvider(ctx, issuer)
	if err != nil {
		return nil, fmt.Errorf("discover OIDC provider: %w", err)
	}
	return &OIDCVerifier{
		verifier:    provider.Verifier(&oidc.Config{ClientID: audience}),
		tenantClaim: tenantClaim,
		rolesClaim:  rolesClaim,
	}, nil
}

func (v *OIDCVerifier) Authenticate(ctx context.Context, rawToken string) (Identity, error) {
	token, err := v.verifier.Verify(ctx, rawToken)
	if err != nil {
		return Identity{}, fmt.Errorf("verify OIDC token: %w", err)
	}
	claims := map[string]json.RawMessage{}
	if err = token.Claims(&claims); err != nil {
		return Identity{}, fmt.Errorf("decode OIDC claims: %w", err)
	}
	var subject, tenantID string
	if err = json.Unmarshal(claims["sub"], &subject); err != nil || subject == "" {
		return Identity{}, fmt.Errorf("OIDC token is missing subject")
	}
	if err = json.Unmarshal(claims[v.tenantClaim], &tenantID); err != nil || tenantID == "" {
		return Identity{}, fmt.Errorf("OIDC token is missing %s claim", v.tenantClaim)
	}
	roles, err := parseRoles(claims[v.rolesClaim])
	if err != nil {
		return Identity{}, fmt.Errorf("decode OIDC %s claim: %w", v.rolesClaim, err)
	}
	return Identity{Subject: subject, TenantID: tenantID, Roles: roles}, nil
}

func parseRoles(raw json.RawMessage) ([]string, error) {
	if len(raw) == 0 {
		return nil, nil
	}
	var roles []string
	if err := json.Unmarshal(raw, &roles); err == nil {
		return roles, nil
	}
	var role string
	if err := json.Unmarshal(raw, &role); err != nil {
		return nil, err
	}
	return []string{role}, nil
}
