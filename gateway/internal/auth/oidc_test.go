package auth

import "testing"

func TestParseRoles(t *testing.T) {
	roles, err := parseRoles([]byte(`["operator","viewer"]`))
	if err != nil || len(roles) != 2 || roles[0] != "operator" {
		t.Fatalf("roles=%v err=%v", roles, err)
	}
	roles, err = parseRoles([]byte(`"service"`))
	if err != nil || len(roles) != 1 || roles[0] != "service" {
		t.Fatalf("roles=%v err=%v", roles, err)
	}
	if _, err = parseRoles([]byte(`42`)); err == nil {
		t.Fatal("non-string roles claim was accepted")
	}
}
