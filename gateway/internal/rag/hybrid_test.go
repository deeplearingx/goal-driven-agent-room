package rag

import (
	"testing"

	"github.com/agent-room/agent-room/gateway/internal/model"
)

func TestFuseChunksPreservesCandidateAndUsesRRFScore(t *testing.T) {
	lexical := []model.KnowledgeChunk{{ID: "lexical", Content: "lexical content"}, {ID: "shared", Content: "old copy"}}
	semantic := []model.KnowledgeChunk{{ID: "semantic", Content: "semantic content"}, {ID: "shared", Content: "semantic copy"}}

	got := FuseChunks(60, lexical, semantic, 10)
	if len(got) != 3 || got[0].ID != "shared" {
		t.Fatalf("unexpected fusion result: %+v", got)
	}
	if got[0].Content != "old copy" || got[0].Score <= got[1].Score {
		t.Fatalf("shared chunk did not preserve lexical candidate or score: %+v", got)
	}
}

func TestFuseChunksAppliesLimit(t *testing.T) {
	got := FuseChunks(60, []model.KnowledgeChunk{{ID: "a"}, {ID: "b"}}, nil, 1)
	if len(got) != 1 || got[0].ID != "a" {
		t.Fatalf("unexpected limited result: %+v", got)
	}
}
