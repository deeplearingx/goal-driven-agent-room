package rag

import "testing"

func TestVectorLiteralValidatesDimensionsAndValues(t *testing.T) {
	value, err := VectorLiteral([]float32{1, 0.25, -3})
	if err != nil || value != "[1,0.25,-3]" {
		t.Fatalf("value=%q err=%v", value, err)
	}
	if _, err := VectorLiteral(nil); err == nil {
		t.Fatal("empty vector accepted")
	}
}
