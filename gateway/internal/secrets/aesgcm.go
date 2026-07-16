package secrets

import (
	"crypto/aes"
	"crypto/cipher"
	"crypto/rand"
	"encoding/base64"
	"fmt"
	"io"
)

type Protector interface {
	Encrypt([]byte) ([]byte, error)
}

// Decryptor is intentionally separate from Protector: the Gateway's HTTP
// control plane only needs write-only encryption, whereas a trusted runtime
// worker receives the additional authority to decrypt provider credentials.
type Decryptor interface {
	Decrypt([]byte) ([]byte, error)
}

type AESGCM struct {
	aead cipher.AEAD
}

func NewAESGCM(encodedKey string) (*AESGCM, error) {
	key, err := base64.StdEncoding.DecodeString(encodedKey)
	if err != nil {
		return nil, fmt.Errorf("decode encryption key: %w", err)
	}
	if len(key) != 32 {
		return nil, fmt.Errorf("encryption key must decode to 32 bytes")
	}
	block, err := aes.NewCipher(key)
	if err != nil {
		return nil, err
	}
	aead, err := cipher.NewGCM(block)
	if err != nil {
		return nil, err
	}
	return &AESGCM{aead: aead}, nil
}

func (a *AESGCM) Encrypt(plaintext []byte) ([]byte, error) {
	nonce := make([]byte, a.aead.NonceSize())
	if _, err := io.ReadFull(rand.Reader, nonce); err != nil {
		return nil, err
	}
	return a.aead.Seal(nonce, nonce, plaintext, nil), nil
}

func (a *AESGCM) Decrypt(ciphertext []byte) ([]byte, error) {
	nonceSize := a.aead.NonceSize()
	if len(ciphertext) < nonceSize {
		return nil, fmt.Errorf("ciphertext is too short")
	}
	return a.aead.Open(nil, ciphertext[:nonceSize], ciphertext[nonceSize:], nil)
}
