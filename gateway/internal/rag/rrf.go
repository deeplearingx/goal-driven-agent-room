package rag

import "sort"

type RankedID struct {
	ID    string
	Score float64
}

// FuseRRF combines independently ranked candidate lists without assuming that
// their raw scores share a scale. Stable ID tie-breaking keeps replay results
// deterministic.
func FuseRRF(k int, lists ...[]RankedID) []RankedID {
	if k <= 0 {
		k = 60
	}
	scores := make(map[string]float64)
	for _, list := range lists {
		seen := make(map[string]bool)
		for rank, item := range list {
			if item.ID == "" || seen[item.ID] {
				continue
			}
			seen[item.ID] = true
			scores[item.ID] += 1.0 / float64(k+rank+1)
		}
	}
	result := make([]RankedID, 0, len(scores))
	for id, score := range scores {
		result = append(result, RankedID{ID: id, Score: score})
	}
	sort.Slice(result, func(i, j int) bool {
		if result[i].Score == result[j].Score {
			return result[i].ID < result[j].ID
		}
		return result[i].Score > result[j].Score
	})
	return result
}
