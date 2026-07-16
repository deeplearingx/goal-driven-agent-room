package rag

import (
	"fmt"
	"math"
	"strconv"
	"strings"
)

// VectorLiteral returns pgvector's text representation after validating every
// coordinate. The returned value is always passed as a SQL parameter, never
// concatenated into a query.
func VectorLiteral(values []float32) (string, error) {
	if len(values) == 0 || len(values) > 8192 {
		return "", fmt.Errorf("invalid embedding dimension")
	}
	parts := make([]string, len(values))
	for i, value := range values {
		if math.IsNaN(float64(value)) || math.IsInf(float64(value), 0) {
			return "", fmt.Errorf("embedding contains non-finite value")
		}
		parts[i] = strconv.FormatFloat(float64(value), 'g', 8, 32)
	}
	return "[" + strings.Join(parts, ",") + "]", nil
}
