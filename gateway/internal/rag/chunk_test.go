package rag

import "testing"

func TestChunkingIsDeterministicAndOverlapping(t *testing.T) {
	text := "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu nu xi omicron"
	first, err := ChunkText(text, 64, 12)
	if err != nil || len(first) < 2 {
		t.Fatalf("chunks=%+v err=%v", first, err)
	}
	second, _ := ChunkText(text, 64, 12)
	if first[0].Hash != second[0].Hash || first[1].Ordinal != 1 {
		t.Fatalf("not deterministic: %+v", first)
	}
	if _, err := ChunkText(text, 32, 0); err == nil {
		t.Fatal("unsafe chunk size accepted")
	}
}
