package tools

import (
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"
)

var digestImagePattern = regexp.MustCompile(`^[^\s@]+@sha256:[a-fA-F0-9]{64}$`)

type SandboxConfig struct {
	Image   string
	Command []string
	// Root is the operator-approved parent for all task workspaces.
	Root string
	// Workspace is an ephemeral per-task directory mounted read/write at /work.
	Workspace string
}

// DockerArgs produces a hermetic Docker command. It intentionally accepts a
// command vector rather than a shell string, and rejects unpinned images.
func DockerArgs(config SandboxConfig) ([]string, error) {
	if ValidateSandboxImage(config.Image) != nil || strings.TrimSpace(config.Root) == "" || strings.TrimSpace(config.Workspace) == "" || len(config.Command) == 0 {
		return nil, fmt.Errorf("sandbox requires a fully digest-pinned image, approved root, workspace, and command")
	}
	for _, item := range config.Command {
		if strings.TrimSpace(item) == "" {
			return nil, fmt.Errorf("sandbox command contains an empty argument")
		}
	}
	workspace, err := containedWorkspace(config.Root, config.Workspace)
	if err != nil {
		return nil, err
	}
	args := []string{
		"run", "--rm", "--network", "none", "--read-only", "--user", "65532:65532",
		"--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--pids-limit", "64",
		"--memory", "256m", "--cpus", "0.5", "--ulimit", "nofile=256:256",
		"--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=64m",
		"--mount", "type=bind,src=" + workspace + ",dst=/work,rw", "--workdir", "/work", config.Image,
	}
	return append(args, config.Command...), nil
}

func ValidateSandboxImage(image string) error {
	if !digestImagePattern.MatchString(image) {
		return fmt.Errorf("sandbox image must use a full sha256 digest")
	}
	return nil
}

func containedWorkspace(root, workspace string) (string, error) {
	rootPath, err := filepath.EvalSymlinks(root)
	if err != nil {
		return "", fmt.Errorf("resolve sandbox root: %w", err)
	}
	workspacePath, err := filepath.EvalSymlinks(workspace)
	if err != nil {
		return "", fmt.Errorf("resolve sandbox workspace: %w", err)
	}
	info, err := os.Stat(workspacePath)
	if err != nil || !info.IsDir() {
		return "", fmt.Errorf("sandbox workspace must be an existing directory")
	}
	rootPath, _ = filepath.Abs(rootPath)
	workspacePath, _ = filepath.Abs(workspacePath)
	rel, err := filepath.Rel(rootPath, workspacePath)
	if err != nil || rel == ".." || strings.HasPrefix(rel, ".."+string(filepath.Separator)) {
		return "", fmt.Errorf("sandbox workspace escapes approved root")
	}
	if strings.ContainsAny(workspacePath, ",\r\n\x00") {
		return "", fmt.Errorf("sandbox workspace contains unsupported mount characters")
	}
	return workspacePath, nil
}
