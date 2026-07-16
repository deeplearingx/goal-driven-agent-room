// Package rag contains deterministic ingestion and retrieval primitives.
package rag

import (
	"crypto/sha256"
	"fmt"
	"strings"
	"unicode"
)

type Chunk struct {
	Ordinal int
	Content string
	Hash    string
}

// ChunkText splits on rune boundaries and prefers whitespace near the target
// limit. The overlap makes neighboring chunks preserve context, while the hash
// makes duplicate detection and retry idempotency straightforward.
func ChunkText(text string, size, overlap int) ([]Chunk, error) {
	if size < 64 || size > 8000 || overlap < 0 || overlap >= size {
		return nil, fmt.Errorf("invalid chunk size or overlap")
	}
	runes := []rune(strings.TrimSpace(text))
	if len(runes) == 0 {
		return []Chunk{}, nil
	}
	chunks := make([]Chunk, 0)
	start := 0
	for start < len(runes) {
		end := start + size
		if end > len(runes) {
			end = len(runes)
		}
		if end < len(runes) {
			for i := end; i > start+size/2; i-- {
				if unicode.IsSpace(runes[i-1]) {
					end = i
					break
				}
			}
		}
		content := strings.TrimSpace(string(runes[start:end]))
		if content != "" {
			chunks = append(chunks, Chunk{Ordinal: len(chunks), Content: content, Hash: fmt.Sprintf("%x", sha256.Sum256([]byte(content)))})
		}
		if end == len(runes) {
			break
		}
		next := end - overlap
		if next <= start {
			next = end
		}
		start = next
	}
	return chunks, nil
}
