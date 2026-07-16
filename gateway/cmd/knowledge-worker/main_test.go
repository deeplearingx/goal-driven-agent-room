package main

import (
	"context"
	"testing"

	"github.com/agent-room/agent-room/gateway/internal/embeddings"
	"github.com/agent-room/agent-room/gateway/internal/rag"
)

type recordingEmbedder struct{ calls []int }

func (f *recordingEmbedder) Embed(_ context.Context, _ embeddings.Profile, _ []byte, inputs []string) ([][]float32, error) {
	f.calls = append(f.calls, len(inputs))
	result := make([][]float32, len(inputs))
	for i := range result {
		result[i] = []float32{float32(i + 1)}
	}
	return result, nil
}

func TestEmbedChunksBatchesWithoutReordering(t *testing.T) {
	chunks := make([]rag.Chunk, 129)
	for i := range chunks {
		chunks[i].Content = "chunk"
	}
	client := &recordingEmbedder{}
	vectors, err := embedChunks(context.Background(), client, embeddings.Profile{}, []byte("key"), chunks)
	if err != nil || len(vectors) != 129 || len(client.calls) != 2 || client.calls[0] != 128 || client.calls[1] != 1 {
		t.Fatalf("vectors=%d calls=%v err=%v", len(vectors), client.calls, err)
	}
	if vectors[0][0] != 1 || vectors[128][0] != 1 {
		t.Fatalf("unexpected batch ordering: first=%v last=%v", vectors[0], vectors[128])
	}
}
