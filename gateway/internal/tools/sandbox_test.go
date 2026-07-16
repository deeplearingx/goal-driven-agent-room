package tools

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestDockerSandboxRequiresPinnedImageAndHardening(t *testing.T) {
	root := t.TempDir()
	workspace := filepath.Join(root, "task-1")
	if err := os.Mkdir(workspace, 0o700); err != nil {
		t.Fatal(err)
	}
	if _, err := DockerArgs(SandboxConfig{Image: "python:3.13", Root: root, Workspace: workspace, Command: []string{"python", "-V"}}); err == nil {
		t.Fatal("mutable image accepted")
	}
	args, err := DockerArgs(SandboxConfig{Image: "python@sha256:" + strings.Repeat("a", 64), Root: root, Workspace: workspace, Command: []string{"python", "-V"}})
	if err != nil {
		t.Fatal(err)
	}
	joined := strings.Join(args, " ")
	for _, required := range []string{"--network none", "--read-only", "--cap-drop ALL", "--security-opt no-new-privileges", "--pids-limit 64", "--ulimit nofile=256:256"} {
		if !strings.Contains(joined, required) {
			t.Fatalf("missing %q in %s", required, joined)
		}
	}
}

func TestDockerSandboxRejectsWorkspaceOutsideApprovedRoot(t *testing.T) {
	root := t.TempDir()
	outside := t.TempDir()
	_, err := DockerArgs(SandboxConfig{
		Image: "python@sha256:" + strings.Repeat("b", 64),
		Root:  root, Workspace: outside, Command: []string{"python", "-V"},
	})
	if err == nil || !strings.Contains(err.Error(), "escapes approved root") {
		t.Fatalf("expected workspace escape rejection, got %v", err)
	}
}
