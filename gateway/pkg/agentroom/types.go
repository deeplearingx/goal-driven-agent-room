package agentroom

import (
	"encoding/json"
	"time"
)

type CreateTaskInput struct {
	Title             string            `json:"title"`
	Description       string            `json:"description"`
	MaxRevisions      *int              `json:"max_revisions,omitempty"`
	Metadata          map[string]any    `json:"metadata,omitempty"`
	Graph             string            `json:"graph,omitempty"`
	VerifyCommand     string            `json:"verify_command,omitempty"`
	VerifyFiles       map[string]string `json:"verify_files,omitempty"`
	MaxIterations     *int              `json:"max_iterations,omitempty"`
	RuntimeSnapshotID string            `json:"runtime_snapshot_id,omitempty"`
	ExecutionRuntime  string            `json:"execution_runtime,omitempty"`
}

type Task struct {
	ID        string          `json:"task_id"`
	TenantID  string          `json:"tenant_id,omitempty"`
	Status    string          `json:"status"`
	Request   json.RawMessage `json:"request,omitempty"`
	Result    json.RawMessage `json:"result,omitempty"`
	Error     string          `json:"error,omitempty"`
	CreatedAt time.Time       `json:"created_at"`
	UpdatedAt time.Time       `json:"updated_at"`
}

type Event struct {
	Sequence int64           `json:"sequence"`
	Type     string          `json:"type"`
	Data     json.RawMessage `json:"data"`
}

type KnowledgeSearchInput struct {
	Query                     string `json:"q"`
	EmbeddingProfileVersionID string `json:"embedding_profile_version_id,omitempty"`
	Limit                     int    `json:"limit,omitempty"`
}

type KnowledgeChunk struct {
	ID                        string  `json:"chunk_id"`
	DocumentID                string  `json:"document_id"`
	Content                   string  `json:"content"`
	Score                     float64 `json:"score"`
	EmbeddingProfileVersionID string  `json:"embedding_profile_version_id,omitempty"`
}
