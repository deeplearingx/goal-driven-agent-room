//go:build eino

package main

import (
	"encoding/json"
	"testing"
)

func TestEstimateCostUSD(t *testing.T) {
	cost := estimateCostUSD(json.RawMessage(`{"price_per_1k_input":0.01,"price_per_1k_output":0.03}`), 1250, 400)
	if cost == nil || *cost != 0.0245 {
		t.Fatalf("cost=%v", cost)
	}
}

func TestEstimateCostUSDRequiresPricing(t *testing.T) {
	if cost := estimateCostUSD(json.RawMessage(`{"temperature":0.2}`), 100, 20); cost != nil {
		t.Fatalf("cost=%v", *cost)
	}
}
