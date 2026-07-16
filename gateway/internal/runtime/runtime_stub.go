//go:build !eino

// Package runtime defines the stable execution contract. The actual Eino
// implementation is built in the 64-bit production image with -tags=eino.
package runtime

import (
	"context"
	"errors"
)

var (
	ErrInvalidRequest = errors.New("invalid Eino runtime request")
	ErrUnavailable    = errors.New("Eino runtime is unavailable in this build")
)

type ReActConfig struct {
	SnapshotID string
	Model      any
	Tools      any
	MaxSteps   int
}

type Request struct{ SnapshotID, System, Input string }
type Result struct{ SnapshotID, Content string }
type ReActRuntime struct{}

func NewReAct(context.Context, ReActConfig) (*ReActRuntime, error)   { return nil, ErrUnavailable }
func (r *ReActRuntime) Run(context.Context, Request) (Result, error) { return Result{}, ErrUnavailable }
