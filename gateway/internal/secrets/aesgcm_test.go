package secrets

import (
	"bytes"
	"encoding/base64"
	"testing"
)

func TestAESGCMEncryptsWithRandomNonce(t *testing.T) {
	key := base64.StdEncoding.EncodeToString(bytes.Repeat([]byte{7}, 32))
	protector, err := NewAESGCM(key)
	if err != nil {
		t.Fatal(err)
	}
	first, err := protector.Encrypt([]byte("secret"))
	if err != nil {
		t.Fatal(err)
	}
	second, err := protector.Encrypt([]byte("secret"))
	if err != nil {
		t.Fatal(err)
	}
	if bytes.Equal(first, second) || bytes.Contains(first, []byte("secret")) {
		t.Fatalf("ciphertexts are unsafe: %x %x", first, second)
	}
}

func TestAESGCMDecryptRoundTrip(t *testing.T) {
	protector, err := NewAESGCM("MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=")
	if err != nil {
		t.Fatal(err)
	}
	ciphertext, err := protector.Encrypt([]byte("secret-value"))
	if err != nil {
		t.Fatal(err)
	}
	plaintext, err := protector.Decrypt(ciphertext)
	if err != nil || string(plaintext) != "secret-value" {
		t.Fatalf("plaintext=%q err=%v", plaintext, err)
	}
}
