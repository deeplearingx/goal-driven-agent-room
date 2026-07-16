package rag

import "testing"

func TestRRFRewardsCrossRetrieverAgreement(t *testing.T) {
	result := FuseRRF(60, []RankedID{{ID: "lexical-only"}, {ID: "shared"}}, []RankedID{{ID: "vector-only"}, {ID: "shared"}})
	if len(result) != 3 || result[0].ID != "shared" {
		t.Fatalf("result=%+v", result)
	}
}
