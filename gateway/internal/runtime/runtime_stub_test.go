//go:build !eino

package runtime

import "testing"

func TestDefaultBuildDoesNotAccidentallyActivateEino(t *testing.T) {
	if _, err := NewReAct(t.Context(), ReActConfig{SnapshotID: "frozen-v1"}); err != ErrUnavailable {
		t.Fatalf("err=%v", err)
	}
}
