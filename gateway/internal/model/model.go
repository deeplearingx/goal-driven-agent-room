package model

import (
	"encoding/json"
	"time"
)

const (
	StatusQueued       = "queued"
	StatusRunning      = "running"
	StatusAwaitingUser = "awaiting_user"
	StatusCompleted    = "completed"
	StatusFailed       = "failed"
	StatusCancelled    = "cancelled"
)

type CreateTask struct {
	Title             string            `json:"title"`
	Description       string            `json:"description"`
	MaxRevisions      *int              `json:"max_revisions,omitempty"`
	Metadata          map[string]any    `json:"metadata"`
	Graph             string            `json:"graph,omitempty"`
	VerifyCommand     string            `json:"verify_command,omitempty"`
	VerifyFiles       map[string]string `json:"verify_files,omitempty"`
	MaxIterations     *int              `json:"max_iterations,omitempty"`
	RuntimeSnapshotID string            `json:"runtime_snapshot_id,omitempty"`
	ExecutionRuntime  string            `json:"execution_runtime,omitempty"`
}

type Task struct {
	ID             string          `json:"task_id"`
	TenantID       string          `json:"tenant_id,omitempty"`
	Status         string          `json:"status"`
	Request        json.RawMessage `json:"request,omitempty"`
	Result         json.RawMessage `json:"result,omitempty"`
	Error          string          `json:"error,omitempty"`
	IdempotencyKey string          `json:"-"`
	CreatedAt      time.Time       `json:"created_at"`
	UpdatedAt      time.Time       `json:"updated_at"`
}

type Command struct {
	MessageID string          `json:"message_id"`
	Type      string          `json:"type"`
	TaskID    string          `json:"task_id"`
	TenantID  string          `json:"tenant_id"`
	Payload   json.RawMessage `json:"payload"`
	CreatedAt time.Time       `json:"created_at"`
}

type Event struct {
	MessageID string          `json:"message_id"`
	TaskID    string          `json:"task_id"`
	TenantID  string          `json:"tenant_id"`
	Sequence  int64           `json:"sequence"`
	Type      string          `json:"type"`
	Data      json.RawMessage `json:"data"`
	CreatedAt time.Time       `json:"created_at"`
}

type WebhookSubscription struct {
	ID           string    `json:"subscription_id"`
	TenantID     string    `json:"tenant_id,omitempty"`
	Name         string    `json:"name"`
	Kind         string    `json:"kind"`
	EventTypes   []string  `json:"event_types"`
	EndpointHost string    `json:"endpoint_host"`
	Enabled      bool      `json:"enabled"`
	CreatedBy    string    `json:"created_by"`
	CreatedAt    time.Time `json:"created_at"`
	UpdatedAt    time.Time `json:"updated_at"`
}

type WebhookDelivery struct {
	ID                     int64           `json:"delivery_id"`
	SubscriptionID         string          `json:"subscription_id"`
	TenantID               string          `json:"tenant_id"`
	Kind                   string          `json:"kind"`
	EndpointHost           string          `json:"endpoint_host"`
	EndpointEncrypted      []byte          `json:"-"`
	SigningSecretEncrypted []byte          `json:"-"`
	EventMessageID         string          `json:"event_message_id"`
	EventType              string          `json:"event_type"`
	Payload                json.RawMessage `json:"payload"`
	Attempts               int             `json:"attempts"`
}

type OutboxMessage struct {
	ID         int64
	MessageID  string
	RoutingKey string
	Payload    []byte
}

type AuditEvent struct {
	ID           string          `json:"event_id"`
	TenantID     string          `json:"tenant_id"`
	ActorID      string          `json:"actor_id"`
	Action       string          `json:"action"`
	ResourceType string          `json:"resource_type"`
	ResourceID   string          `json:"resource_id"`
	RequestID    string          `json:"request_id"`
	Data         json.RawMessage `json:"data"`
	OccurredAt   time.Time       `json:"occurred_at"`
}

type RoleBinding struct {
	Subject string   `json:"subject"`
	Kind    string   `json:"kind"`
	Roles   []string `json:"roles"`
}

type PromptVersion struct {
	ID        string          `json:"prompt_version_id"`
	PromptID  string          `json:"prompt_id"`
	TenantID  string          `json:"tenant_id"`
	Name      string          `json:"name"`
	Version   int             `json:"version"`
	Content   string          `json:"content"`
	Variables json.RawMessage `json:"variables"`
	Hash      string          `json:"content_hash"`
	CreatedBy string          `json:"created_by"`
	CreatedAt time.Time       `json:"created_at"`
}

type PromptRelease struct {
	TenantID           string    `json:"tenant_id"`
	PromptID           string    `json:"prompt_id"`
	PromptName         string    `json:"prompt_name"`
	Environment        string    `json:"environment"`
	BaselineVersionID  string    `json:"baseline_version_id"`
	CandidateVersionID string    `json:"candidate_version_id,omitempty"`
	CandidateWeight    int       `json:"candidate_weight"`
	UpdatedBy          string    `json:"updated_by"`
	UpdatedAt          time.Time `json:"updated_at"`
}

type ModelProfileVersion struct {
	ID                string          `json:"model_profile_version_id"`
	ModelProfileID    string          `json:"model_profile_id"`
	TenantID          string          `json:"tenant_id"`
	Name              string          `json:"name"`
	Version           int             `json:"version"`
	ProviderAccountID string          `json:"provider_account_id,omitempty"`
	ModelName         string          `json:"model_name"`
	Parameters        json.RawMessage `json:"parameters"`
	CreatedBy         string          `json:"created_by"`
	CreatedAt         time.Time       `json:"created_at"`
}

type RuntimeSnapshot struct {
	ID                    string          `json:"runtime_snapshot_id"`
	TenantID              string          `json:"tenant_id"`
	PromptVersionID       string          `json:"prompt_version_id,omitempty"`
	ModelProfileVersionID string          `json:"model_profile_version_id,omitempty"`
	AgentVersion          json.RawMessage `json:"agent_version"`
	ToolVersions          json.RawMessage `json:"tool_versions"`
	Hash                  string          `json:"content_hash"`
	CreatedBy             string          `json:"created_by"`
	CreatedAt             time.Time       `json:"created_at"`
}

type ProviderAccount struct {
	ID        string    `json:"provider_account_id"`
	TenantID  string    `json:"tenant_id"`
	Provider  string    `json:"provider"`
	Name      string    `json:"name"`
	KeyRef    string    `json:"key_reference"`
	CreatedAt time.Time `json:"created_at"`
	RotatedAt time.Time `json:"rotated_at,omitempty"`
}

type AgentVersion struct {
	ID            string          `json:"agent_version_id"`
	AgentID       string          `json:"agent_id"`
	TenantID      string          `json:"tenant_id"`
	Name          string          `json:"name"`
	Version       int             `json:"version"`
	Specification json.RawMessage `json:"specification"`
	Hash          string          `json:"content_hash"`
	CreatedBy     string          `json:"created_by"`
	CreatedAt     time.Time       `json:"created_at"`
}

type ToolVersion struct {
	ID            string          `json:"tool_version_id"`
	ToolID        string          `json:"tool_id"`
	TenantID      string          `json:"tenant_id"`
	Name          string          `json:"name"`
	Version       int             `json:"version"`
	Kind          string          `json:"kind"`
	Specification json.RawMessage `json:"specification"`
	Hash          string          `json:"content_hash"`
	CreatedBy     string          `json:"created_by"`
	CreatedAt     time.Time       `json:"created_at"`
}

type SkillVersion struct {
	ID            string          `json:"skill_version_id"`
	SkillID       string          `json:"skill_id"`
	TenantID      string          `json:"tenant_id"`
	Name          string          `json:"name"`
	Version       int             `json:"version"`
	Specification json.RawMessage `json:"specification"`
	Hash          string          `json:"content_hash"`
	CreatedBy     string          `json:"created_by"`
	CreatedAt     time.Time       `json:"created_at"`
}

type ToolApproval struct {
	ID            string          `json:"approval_id"`
	TenantID      string          `json:"tenant_id"`
	TaskID        string          `json:"task_id"`
	ToolVersionID string          `json:"tool_version_id"`
	OperationHash string          `json:"operation_hash"`
	Request       json.RawMessage `json:"request"`
	Status        string          `json:"status"`
	RequestedBy   string          `json:"requested_by"`
	RequestedAt   time.Time       `json:"requested_at"`
	ExpiresAt     time.Time       `json:"expires_at"`
	DecidedBy     string          `json:"decided_by,omitempty"`
	DecidedAt     *time.Time      `json:"decided_at,omitempty"`
	DecisionNote  string          `json:"decision_note,omitempty"`
}

type KnowledgeChunk struct {
	ID         string          `json:"chunk_id"`
	DocumentID string          `json:"document_id"`
	TenantID   string          `json:"tenant_id"`
	Title      string          `json:"title"`
	SourceURI  string          `json:"source_uri"`
	Ordinal    int             `json:"ordinal"`
	Content    string          `json:"content"`
	Metadata   json.RawMessage `json:"metadata"`
	Score      float64         `json:"score"`
}

// KnowledgeIngestPayload is the durable command body consumed by the
// knowledge worker. The worker persists derived chunks, not the original
// document blob; production RabbitMQ transport must be configured with TLS.
type KnowledgeIngestPayload struct {
	KnowledgeBaseID           string `json:"knowledge_base_id"`
	KnowledgeBaseName         string `json:"knowledge_base_name"`
	DocumentID                string `json:"document_id"`
	SourceURI                 string `json:"source_uri"`
	Title                     string `json:"title"`
	Content                   string `json:"content"`
	ContentHash               string `json:"content_hash"`
	EmbeddingProfileVersionID string `json:"embedding_profile_version_id,omitempty"`
	ActorID                   string `json:"actor_id"`
	ChunkSize                 int    `json:"chunk_size"`
	ChunkOverlap              int    `json:"chunk_overlap"`
}

type EvalRun struct {
	ID              string          `json:"eval_run_id"`
	TenantID        string          `json:"tenant_id"`
	SuiteName       string          `json:"suite_name"`
	SuiteVersion    string          `json:"suite_version"`
	TargetKind      string          `json:"target_kind"`
	TargetVersionID string          `json:"target_version_id"`
	Status          string          `json:"status"`
	Score           *float64        `json:"score,omitempty"`
	Threshold       *float64        `json:"threshold,omitempty"`
	Summary         json.RawMessage `json:"summary"`
	SourceRef       string          `json:"source_ref,omitempty"`
	CreatedBy       string          `json:"created_by"`
	CreatedAt       time.Time       `json:"created_at"`
}
