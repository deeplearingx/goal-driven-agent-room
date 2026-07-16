package rag

import (
	"sort"

	"github.com/agent-room/agent-room/gateway/internal/model"
)

// FuseChunks applies reciprocal-rank fusion to lexical and semantic
// candidates. It returns the original chunks (rather than only IDs), assigns
// the fused score, and uses IDs as a stable tie breaker for reproducible runs.
func FuseChunks(k int, lexical, semantic []model.KnowledgeChunk, limit int) []model.KnowledgeChunk {
	byID := make(map[string]model.KnowledgeChunk, len(lexical)+len(semantic))
	lexicalRanks := make([]RankedID, 0, len(lexical))
	semanticRanks := make([]RankedID, 0, len(semantic))
	for _, chunk := range lexical {
		if chunk.ID == "" {
			continue
		}
		byID[chunk.ID] = chunk
		lexicalRanks = append(lexicalRanks, RankedID{ID: chunk.ID})
	}
	for _, chunk := range semantic {
		if chunk.ID == "" {
			continue
		}
		if _, exists := byID[chunk.ID]; !exists {
			byID[chunk.ID] = chunk
		}
		semanticRanks = append(semanticRanks, RankedID{ID: chunk.ID})
	}
	fused := FuseRRF(k, lexicalRanks, semanticRanks)
	if limit > 0 && len(fused) > limit {
		fused = fused[:limit]
	}
	result := make([]model.KnowledgeChunk, 0, len(fused))
	for _, item := range fused {
		chunk := byID[item.ID]
		chunk.Score = item.Score
		result = append(result, chunk)
	}
	// FuseRRF already orders this way; retaining the sort makes this boundary
	// robust should the internal implementation change.
	sort.SliceStable(result, func(i, j int) bool {
		if result[i].Score == result[j].Score {
			return result[i].ID < result[j].ID
		}
		return result[i].Score > result[j].Score
	})
	return result
}
