package store

import (
	"context"
	"crypto/sha256"
	"embed"
	"encoding/json"
	"errors"
	"fmt"
	"time"

	"github.com/agent-room/agent-room/gateway/internal/model"
	"github.com/agent-room/agent-room/gateway/internal/rag"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

//go:embed migrations/*.sql
var migrationFiles embed.FS

var migrationNames = []string{
	"migrations/001_initial.sql",
	"migrations/002_control_plane.sql",
	"migrations/003_configuration_registry.sql",
	"migrations/004_agent_tool_registry.sql",
	"migrations/005_skill_registry.sql",
	"migrations/006_prompt_releases.sql",
	"migrations/007_tool_approvals.sql",
	"migrations/008_knowledge_fts.sql",
	"migrations/009_knowledge_vectors.sql",
	"migrations/010_runtime_invocations.sql",
	"migrations/011_eval_runs.sql",
	"migrations/012_outbox_retry_backoff.sql",
	"migrations/013_webhook_delivery.sql",
}

var (
	ErrNotFound = errors.New("task not found")
	ErrConflict = errors.New("task state conflict")
)

type Store struct{ pool *pgxpool.Pool }

type OperationalStats struct {
	PendingOutbox       int64
	OldestOutboxSeconds float64
	Events              int64
	TasksQueued         int64
	TasksRunning        int64
	TasksAwaitingUser   int64
	TasksFailed         int64
}

type Invocation struct {
	ID                    string          `json:"invocation_id"`
	TenantID              string          `json:"tenant_id"`
	TaskID                string          `json:"task_id"`
	Runtime               string          `json:"runtime"`
	Kind                  string          `json:"invocation_kind"`
	Status                string          `json:"status"`
	RuntimeSnapshotID     string          `json:"runtime_snapshot_id,omitempty"`
	PromptVersionID       string          `json:"prompt_version_id,omitempty"`
	ModelProfileVersionID string          `json:"model_profile_version_id,omitempty"`
	ToolVersionID         string          `json:"tool_version_id,omitempty"`
	InputTokens           *int            `json:"input_tokens,omitempty"`
	OutputTokens          *int            `json:"output_tokens,omitempty"`
	EstimatedCostUSD      *float64        `json:"estimated_cost_usd,omitempty"`
	LatencyMS             *int            `json:"latency_ms,omitempty"`
	Data                  json.RawMessage `json:"data"`
	OccurredAt            time.Time       `json:"occurred_at"`
}

type CleanupStats struct {
	Tasks  int64
	Outbox int64
	Inbox  int64
}

func Open(ctx context.Context, databaseURL string, maxConnections int32) (*Store, error) {
	cfg, err := pgxpool.ParseConfig(databaseURL)
	if err != nil {
		return nil, fmt.Errorf("parse database URL: %w", err)
	}
	cfg.MaxConns = maxConnections
	cfg.MinConns = min(3, maxConnections)
	cfg.MaxConnLifetime = time.Hour
	cfg.MaxConnIdleTime = 10 * time.Minute
	cfg.HealthCheckPeriod = 30 * time.Second
	pool, err := pgxpool.NewWithConfig(ctx, cfg)
	if err != nil {
		return nil, fmt.Errorf("open postgres: %w", err)
	}
	if err := pool.Ping(ctx); err != nil {
		pool.Close()
		return nil, fmt.Errorf("ping postgres: %w", err)
	}
	return &Store{pool: pool}, nil
}

func (s *Store) Close()                         { s.pool.Close() }
func (s *Store) Ping(ctx context.Context) error { return s.pool.Ping(ctx) }

func (s *Store) Migrate(ctx context.Context) error {
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx)
	// Multiple rollout replicas may start together. This transaction-scoped
	// advisory lock makes schema setup a single-writer operation.
	if _, err = tx.Exec(ctx, `SELECT pg_advisory_xact_lock(hashtext('agent_room_gateway_migrations'))`); err != nil {
		return err
	}
	if _, err = tx.Exec(ctx, `
		CREATE TABLE IF NOT EXISTS schema_migrations (
			name text PRIMARY KEY,
			applied_at timestamptz NOT NULL DEFAULT now()
		)`); err != nil {
		return err
	}
	for _, name := range migrationNames {
		var applied bool
		if err = tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM schema_migrations WHERE name=$1)`, name).Scan(&applied); err != nil {
			return err
		}
		if applied {
			continue
		}
		body, readErr := migrationFiles.ReadFile(name)
		if readErr != nil {
			return readErr
		}
		if _, err = tx.Exec(ctx, string(body)); err != nil {
			return fmt.Errorf("apply %s: %w", name, err)
		}
		if _, err = tx.Exec(ctx, `INSERT INTO schema_migrations(name) VALUES($1)`, name); err != nil {
			return err
		}
	}
	return tx.Commit(ctx)
}

func (s *Store) CreateTask(ctx context.Context, task model.Task, command model.Command, audit model.AuditEvent) (model.Task, bool, error) {
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return model.Task{}, false, err
	}
	defer tx.Rollback(ctx)
	var key any
	if task.IdempotencyKey != "" {
		key = task.IdempotencyKey
	}
	row := tx.QueryRow(ctx, `
		INSERT INTO tasks(task_id, tenant_id, status, request, idempotency_key)
		VALUES($1,$2,$3,$4,$5)
		ON CONFLICT (tenant_id, idempotency_key) DO NOTHING
		RETURNING task_id, tenant_id, status, request, COALESCE(result,'null'), COALESCE(error,''), created_at, updated_at`,
		task.ID, task.TenantID, task.Status, task.Request, key)
	created := true
	if err := scanTask(row, &task); errors.Is(err, pgx.ErrNoRows) {
		created = false
		if task.IdempotencyKey == "" {
			return model.Task{}, false, errors.New("task id collision")
		}
		err = scanTask(tx.QueryRow(ctx, `SELECT task_id,tenant_id,status,request,COALESCE(result,'null'),COALESCE(error,''),created_at,updated_at FROM tasks WHERE tenant_id=$1 AND idempotency_key=$2`, task.TenantID, task.IdempotencyKey), &task)
		if err != nil {
			return model.Task{}, false, err
		}
	} else if err != nil {
		return model.Task{}, false, err
	}
	if created {
		payload, err := json.Marshal(command)
		if err != nil {
			return model.Task{}, false, err
		}
		if _, err = tx.Exec(ctx, `INSERT INTO outbox(message_id,routing_key,payload) VALUES($1,$2,$3)`, command.MessageID, commandRoutingKey(command), payload); err != nil {
			return model.Task{}, false, err
		}
		if err = insertAudit(ctx, tx, audit); err != nil {
			return model.Task{}, false, err
		}
	}
	if err := tx.Commit(ctx); err != nil {
		return model.Task{}, false, err
	}
	return task, created, nil
}

// EnqueueKnowledgeIngest commits the audit record and durable outbox message
// in one transaction. HTTP handlers can therefore return as soon as RabbitMQ
// work is durable without doing document parsing on an online request.
func (s *Store) EnqueueKnowledgeIngest(ctx context.Context, tenantID string, command model.Command, audit model.AuditEvent) error {
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx)
	if _, err = tx.Exec(ctx, `INSERT INTO tenants(tenant_id,display_name) VALUES($1,$1) ON CONFLICT (tenant_id) DO NOTHING`, tenantID); err != nil {
		return err
	}
	payload, err := json.Marshal(command)
	if err != nil {
		return err
	}
	if _, err = tx.Exec(ctx, `INSERT INTO outbox(message_id,routing_key,payload) VALUES($1,'knowledge.ingest',$2)`, command.MessageID, payload); err != nil {
		return err
	}
	if err = insertAudit(ctx, tx, audit); err != nil {
		return err
	}
	return tx.Commit(ctx)
}

func commandRoutingKey(command model.Command) string {
	if command.Type != "run" {
		return command.Type
	}
	var payload struct {
		ExecutionRuntime string `json:"execution_runtime"`
	}
	if json.Unmarshal(command.Payload, &payload) == nil && (payload.ExecutionRuntime == "python_langgraph" || payload.ExecutionRuntime == "go_eino") {
		return "run." + payload.ExecutionRuntime
	}
	return "run"
}

func (s *Store) GetTask(ctx context.Context, tenantID, taskID string) (model.Task, error) {
	var t model.Task
	err := scanTask(s.pool.QueryRow(ctx, `SELECT task_id,tenant_id,status,request,COALESCE(result,'null'),COALESCE(error,''),created_at,updated_at FROM tasks WHERE tenant_id=$1 AND task_id=$2`, tenantID, taskID), &t)
	if errors.Is(err, pgx.ErrNoRows) {
		return model.Task{}, ErrNotFound
	}
	return t, err
}

func (s *Store) ListTasks(ctx context.Context, tenantID string, limit int) ([]model.Task, error) {
	rows, err := s.pool.Query(ctx, `SELECT task_id,tenant_id,status,request,COALESCE(result,'null'),COALESCE(error,''),created_at,updated_at FROM tasks WHERE tenant_id=$1 ORDER BY updated_at DESC LIMIT $2`, tenantID, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	tasks := make([]model.Task, 0)
	for rows.Next() {
		var task model.Task
		if err := scanTask(rows, &task); err != nil {
			return nil, err
		}
		tasks = append(tasks, task)
	}
	return tasks, rows.Err()
}

func (s *Store) ListAuditEvents(ctx context.Context, tenantID string, limit int) ([]model.AuditEvent, error) {
	rows, err := s.pool.Query(ctx, `
		SELECT event_id,tenant_id,actor_id,action,resource_type,resource_id,request_id,data,occurred_at
		FROM audit_events WHERE tenant_id=$1 ORDER BY occurred_at DESC LIMIT $2`, tenantID, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	events := make([]model.AuditEvent, 0)
	for rows.Next() {
		var event model.AuditEvent
		if err := rows.Scan(&event.ID, &event.TenantID, &event.ActorID, &event.Action, &event.ResourceType, &event.ResourceID, &event.RequestID, &event.Data, &event.OccurredAt); err != nil {
			return nil, err
		}
		events = append(events, event)
	}
	return events, rows.Err()
}

// RecordAudit is used for security decisions that intentionally do not create
// another domain object, such as rejecting a guarded task before it enters the
// outbox. It records structured metadata, never the untrusted original text.
func (s *Store) RecordAudit(ctx context.Context, event model.AuditEvent) error {
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx)
	if err = insertAudit(ctx, tx, event); err != nil {
		return err
	}
	return tx.Commit(ctx)
}

func (s *Store) CreateEvalRun(ctx context.Context, run model.EvalRun, audit model.AuditEvent) (model.EvalRun, error) {
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return model.EvalRun{}, err
	}
	defer tx.Rollback(ctx)
	if _, err = tx.Exec(ctx, `INSERT INTO tenants(tenant_id,display_name) VALUES($1,$1) ON CONFLICT (tenant_id) DO NOTHING`, run.TenantID); err != nil {
		return model.EvalRun{}, err
	}
	err = tx.QueryRow(ctx, `INSERT INTO eval_runs(eval_run_id,tenant_id,suite_name,suite_version,target_kind,target_version_id,status,score,threshold,summary,source_ref,created_by) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12) RETURNING created_at`, run.ID, run.TenantID, run.SuiteName, run.SuiteVersion, run.TargetKind, run.TargetVersionID, run.Status, run.Score, run.Threshold, run.Summary, run.SourceRef, run.CreatedBy).Scan(&run.CreatedAt)
	if err != nil {
		return model.EvalRun{}, err
	}
	if err = insertAudit(ctx, tx, audit); err != nil {
		return model.EvalRun{}, err
	}
	if err = tx.Commit(ctx); err != nil {
		return model.EvalRun{}, err
	}
	return run, nil
}

func (s *Store) ListEvalRuns(ctx context.Context, tenantID, targetKind, targetVersionID string, limit int) ([]model.EvalRun, error) {
	query := `SELECT eval_run_id,tenant_id,suite_name,suite_version,target_kind,target_version_id,status,score::float8,threshold::float8,summary,source_ref,created_by,created_at FROM eval_runs WHERE tenant_id=$1`
	args := []any{tenantID}
	if targetKind != "" {
		query += fmt.Sprintf(" AND target_kind=$%d", len(args)+1)
		args = append(args, targetKind)
	}
	if targetVersionID != "" {
		query += fmt.Sprintf(" AND target_version_id=$%d", len(args)+1)
		args = append(args, targetVersionID)
	}
	query += fmt.Sprintf(" ORDER BY created_at DESC LIMIT $%d", len(args)+1)
	args = append(args, limit)
	rows, err := s.pool.Query(ctx, query, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	items := make([]model.EvalRun, 0)
	for rows.Next() {
		var item model.EvalRun
		if err = rows.Scan(&item.ID, &item.TenantID, &item.SuiteName, &item.SuiteVersion, &item.TargetKind, &item.TargetVersionID, &item.Status, &item.Score, &item.Threshold, &item.Summary, &item.SourceRef, &item.CreatedBy, &item.CreatedAt); err != nil {
			return nil, err
		}
		items = append(items, item)
	}
	return items, rows.Err()
}

func (s *Store) EvalRunPassed(ctx context.Context, tenantID, runID, targetKind, targetVersionID string) (bool, error) {
	var passed bool
	err := s.pool.QueryRow(ctx, `SELECT status='passed' FROM eval_runs WHERE tenant_id=$1 AND eval_run_id=$2 AND target_kind=$3 AND target_version_id=$4`, tenantID, runID, targetKind, targetVersionID).Scan(&passed)
	if errors.Is(err, pgx.ErrNoRows) {
		return false, nil
	}
	return passed, err
}

func (s *Store) RolesForPrincipal(ctx context.Context, tenantID, subject string) ([]string, error) {
	rows, err := s.pool.Query(ctx, `
		SELECT rb.role FROM role_bindings rb
		JOIN principals p ON p.principal_id=rb.principal_id
		WHERE rb.tenant_id=$1 AND p.subject=$2`, tenantID, subject)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	roles := make([]string, 0)
	for rows.Next() {
		var role string
		if err := rows.Scan(&role); err != nil {
			return nil, err
		}
		roles = append(roles, role)
	}
	return roles, rows.Err()
}

func (s *Store) ListRoleBindings(ctx context.Context, tenantID string) ([]model.RoleBinding, error) {
	rows, err := s.pool.Query(ctx, `
		SELECT p.subject,p.kind,array_agg(rb.role ORDER BY rb.role)
		FROM principals p JOIN role_bindings rb ON rb.principal_id=p.principal_id
		WHERE p.tenant_id=$1 GROUP BY p.subject,p.kind ORDER BY p.subject`, tenantID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	bindings := make([]model.RoleBinding, 0)
	for rows.Next() {
		var binding model.RoleBinding
		if err := rows.Scan(&binding.Subject, &binding.Kind, &binding.Roles); err != nil {
			return nil, err
		}
		bindings = append(bindings, binding)
	}
	return bindings, rows.Err()
}

func (s *Store) ReplaceRoleBindings(ctx context.Context, tenantID, principalID, subject, kind string, roles []string, audit model.AuditEvent) error {
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx)
	if _, err = tx.Exec(ctx, `INSERT INTO tenants(tenant_id,display_name) VALUES($1,$1) ON CONFLICT (tenant_id) DO NOTHING`, tenantID); err != nil {
		return err
	}
	var storedID string
	if err = tx.QueryRow(ctx, `
		INSERT INTO principals(principal_id,tenant_id,subject,kind) VALUES($1,$2,$3,$4)
		ON CONFLICT (tenant_id,subject) DO UPDATE SET kind=EXCLUDED.kind
		RETURNING principal_id`, principalID, tenantID, subject, kind).Scan(&storedID); err != nil {
		return err
	}
	if _, err = tx.Exec(ctx, `DELETE FROM role_bindings WHERE tenant_id=$1 AND principal_id=$2`, tenantID, storedID); err != nil {
		return err
	}
	for _, role := range roles {
		if _, err = tx.Exec(ctx, `INSERT INTO role_bindings(tenant_id,principal_id,role) VALUES($1,$2,$3)`, tenantID, storedID, role); err != nil {
			return err
		}
	}
	if err = insertAudit(ctx, tx, audit); err != nil {
		return err
	}
	return tx.Commit(ctx)
}

func (s *Store) CreatePromptVersion(ctx context.Context, tenantID, promptID, versionID, name, content, actorID string, variables json.RawMessage, audit model.AuditEvent) (model.PromptVersion, error) {
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return model.PromptVersion{}, err
	}
	defer tx.Rollback(ctx)
	if _, err = tx.Exec(ctx, `INSERT INTO tenants(tenant_id,display_name) VALUES($1,$1) ON CONFLICT (tenant_id) DO NOTHING`, tenantID); err != nil {
		return model.PromptVersion{}, err
	}
	if _, err = tx.Exec(ctx, `INSERT INTO prompts(prompt_id,tenant_id,name) VALUES($1,$2,$3) ON CONFLICT (tenant_id,name) DO NOTHING`, promptID, tenantID, name); err != nil {
		return model.PromptVersion{}, err
	}
	var storedPromptID string
	if err = tx.QueryRow(ctx, `SELECT prompt_id FROM prompts WHERE tenant_id=$1 AND name=$2 FOR UPDATE`, tenantID, name).Scan(&storedPromptID); err != nil {
		return model.PromptVersion{}, err
	}
	if len(variables) == 0 {
		variables = json.RawMessage(`{}`)
	}
	hash := fmt.Sprintf("%x", sha256.Sum256(append(append([]byte(content), '\n'), variables...)))
	var version int
	if err = tx.QueryRow(ctx, `SELECT COALESCE(MAX(version),0)+1 FROM prompt_versions WHERE prompt_id=$1`, storedPromptID).Scan(&version); err != nil {
		return model.PromptVersion{}, err
	}
	var result model.PromptVersion
	err = tx.QueryRow(ctx, `
		INSERT INTO prompt_versions(prompt_version_id,prompt_id,tenant_id,version,content,variables,content_hash,created_by)
		VALUES($1,$2,$3,$4,$5,$6,$7,$8)
		RETURNING prompt_version_id,prompt_id,tenant_id,version,content,variables,content_hash,created_by,created_at`,
		versionID, storedPromptID, tenantID, version, content, variables, hash, actorID).Scan(
		&result.ID, &result.PromptID, &result.TenantID, &result.Version, &result.Content, &result.Variables, &result.Hash, &result.CreatedBy, &result.CreatedAt)
	if err != nil {
		return model.PromptVersion{}, err
	}
	result.Name = name
	if err = insertAudit(ctx, tx, audit); err != nil {
		return model.PromptVersion{}, err
	}
	if err = tx.Commit(ctx); err != nil {
		return model.PromptVersion{}, err
	}
	return result, nil
}

func (s *Store) ListPromptVersions(ctx context.Context, tenantID string, limit int) ([]model.PromptVersion, error) {
	rows, err := s.pool.Query(ctx, `
		SELECT pv.prompt_version_id,pv.prompt_id,pv.tenant_id,p.name,pv.version,pv.content,pv.variables,pv.content_hash,pv.created_by,pv.created_at
		FROM prompt_versions pv JOIN prompts p ON p.prompt_id=pv.prompt_id
		WHERE pv.tenant_id=$1 ORDER BY pv.created_at DESC LIMIT $2`, tenantID, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	versions := make([]model.PromptVersion, 0)
	for rows.Next() {
		var version model.PromptVersion
		if err := rows.Scan(&version.ID, &version.PromptID, &version.TenantID, &version.Name, &version.Version, &version.Content, &version.Variables, &version.Hash, &version.CreatedBy, &version.CreatedAt); err != nil {
			return nil, err
		}
		versions = append(versions, version)
	}
	return versions, rows.Err()
}

func (s *Store) GetPromptVersion(ctx context.Context, tenantID, versionID string) (model.PromptVersion, error) {
	var version model.PromptVersion
	err := s.pool.QueryRow(ctx, `
		SELECT pv.prompt_version_id,pv.prompt_id,pv.tenant_id,p.name,pv.version,pv.content,pv.variables,pv.content_hash,pv.created_by,pv.created_at
		FROM prompt_versions pv JOIN prompts p ON p.prompt_id=pv.prompt_id
		WHERE pv.tenant_id=$1 AND pv.prompt_version_id=$2`, tenantID, versionID).Scan(&version.ID, &version.PromptID, &version.TenantID, &version.Name, &version.Version, &version.Content, &version.Variables, &version.Hash, &version.CreatedBy, &version.CreatedAt)
	if errors.Is(err, pgx.ErrNoRows) {
		return model.PromptVersion{}, ErrNotFound
	}
	if err != nil {
		return model.PromptVersion{}, err
	}
	return version, nil
}

func (s *Store) UpsertPromptRelease(ctx context.Context, release model.PromptRelease, audit model.AuditEvent) (model.PromptRelease, error) {
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return model.PromptRelease{}, err
	}
	defer tx.Rollback(ctx)
	var promptID string
	if err = tx.QueryRow(ctx, `SELECT prompt_id FROM prompts WHERE tenant_id=$1 AND name=$2`, release.TenantID, release.PromptName).Scan(&promptID); err != nil {
		return model.PromptRelease{}, ErrNotFound
	}
	var baselinePromptID string
	if err = tx.QueryRow(ctx, `SELECT prompt_id FROM prompt_versions WHERE tenant_id=$1 AND prompt_version_id=$2`, release.TenantID, release.BaselineVersionID).Scan(&baselinePromptID); err != nil || baselinePromptID != promptID {
		return model.PromptRelease{}, ErrNotFound
	}
	if release.CandidateVersionID != "" {
		var candidatePromptID string
		if err = tx.QueryRow(ctx, `SELECT prompt_id FROM prompt_versions WHERE tenant_id=$1 AND prompt_version_id=$2`, release.TenantID, release.CandidateVersionID).Scan(&candidatePromptID); err != nil || candidatePromptID != promptID {
			return model.PromptRelease{}, ErrNotFound
		}
	}
	release.PromptID = promptID
	err = tx.QueryRow(ctx, `
		INSERT INTO prompt_releases(tenant_id,prompt_id,environment,baseline_version_id,candidate_version_id,candidate_weight,updated_by)
		VALUES($1,$2,$3,$4,NULLIF($5,''),$6,$7)
		ON CONFLICT (tenant_id,prompt_id,environment) DO UPDATE SET baseline_version_id=EXCLUDED.baseline_version_id,candidate_version_id=EXCLUDED.candidate_version_id,candidate_weight=EXCLUDED.candidate_weight,updated_by=EXCLUDED.updated_by,updated_at=now()
		RETURNING updated_at`, release.TenantID, release.PromptID, release.Environment, release.BaselineVersionID, release.CandidateVersionID, release.CandidateWeight, release.UpdatedBy).Scan(&release.UpdatedAt)
	if err != nil {
		return model.PromptRelease{}, err
	}
	if err = insertAudit(ctx, tx, audit); err != nil {
		return model.PromptRelease{}, err
	}
	if err = tx.Commit(ctx); err != nil {
		return model.PromptRelease{}, err
	}
	return release, nil
}

func (s *Store) ListPromptReleases(ctx context.Context, tenantID, environment string) ([]model.PromptRelease, error) {
	rows, err := s.pool.Query(ctx, `
		SELECT pr.tenant_id,pr.prompt_id,p.name,pr.environment,pr.baseline_version_id,COALESCE(pr.candidate_version_id,''),pr.candidate_weight,pr.updated_by,pr.updated_at
		FROM prompt_releases pr JOIN prompts p ON p.prompt_id=pr.prompt_id
		WHERE pr.tenant_id=$1 AND ($2='' OR pr.environment=$2) ORDER BY p.name,pr.environment`, tenantID, environment)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	items := make([]model.PromptRelease, 0)
	for rows.Next() {
		var item model.PromptRelease
		if err := rows.Scan(&item.TenantID, &item.PromptID, &item.PromptName, &item.Environment, &item.BaselineVersionID, &item.CandidateVersionID, &item.CandidateWeight, &item.UpdatedBy, &item.UpdatedAt); err != nil {
			return nil, err
		}
		items = append(items, item)
	}
	return items, rows.Err()
}

func (s *Store) CreateModelProfileVersion(ctx context.Context, tenantID, profileID, versionID, name, providerAccountID, modelName, actorID string, parameters json.RawMessage, audit model.AuditEvent) (model.ModelProfileVersion, error) {
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return model.ModelProfileVersion{}, err
	}
	defer tx.Rollback(ctx)
	if _, err = tx.Exec(ctx, `INSERT INTO tenants(tenant_id,display_name) VALUES($1,$1) ON CONFLICT (tenant_id) DO NOTHING`, tenantID); err != nil {
		return model.ModelProfileVersion{}, err
	}
	if _, err = tx.Exec(ctx, `INSERT INTO model_profiles(model_profile_id,tenant_id,name) VALUES($1,$2,$3) ON CONFLICT (tenant_id,name) DO NOTHING`, profileID, tenantID, name); err != nil {
		return model.ModelProfileVersion{}, err
	}
	var storedProfileID string
	if err = tx.QueryRow(ctx, `SELECT model_profile_id FROM model_profiles WHERE tenant_id=$1 AND name=$2 FOR UPDATE`, tenantID, name).Scan(&storedProfileID); err != nil {
		return model.ModelProfileVersion{}, err
	}
	if len(parameters) == 0 {
		parameters = json.RawMessage(`{}`)
	}
	var version int
	if err = tx.QueryRow(ctx, `SELECT COALESCE(MAX(version),0)+1 FROM model_profile_versions WHERE model_profile_id=$1`, storedProfileID).Scan(&version); err != nil {
		return model.ModelProfileVersion{}, err
	}
	var accountID any
	if providerAccountID != "" {
		accountID = providerAccountID
	}
	var result model.ModelProfileVersion
	err = tx.QueryRow(ctx, `
		INSERT INTO model_profile_versions(model_profile_version_id,model_profile_id,tenant_id,version,provider_account_id,model_name,parameters,created_by)
		VALUES($1,$2,$3,$4,$5,$6,$7,$8)
		RETURNING model_profile_version_id,model_profile_id,tenant_id,version,COALESCE(provider_account_id,''),model_name,parameters,created_by,created_at`,
		versionID, storedProfileID, tenantID, version, accountID, modelName, parameters, actorID).Scan(
		&result.ID, &result.ModelProfileID, &result.TenantID, &result.Version, &result.ProviderAccountID, &result.ModelName, &result.Parameters, &result.CreatedBy, &result.CreatedAt)
	if err != nil {
		return model.ModelProfileVersion{}, err
	}
	result.Name = name
	if err = insertAudit(ctx, tx, audit); err != nil {
		return model.ModelProfileVersion{}, err
	}
	if err = tx.Commit(ctx); err != nil {
		return model.ModelProfileVersion{}, err
	}
	return result, nil
}

func (s *Store) ListModelProfileVersions(ctx context.Context, tenantID string, limit int) ([]model.ModelProfileVersion, error) {
	rows, err := s.pool.Query(ctx, `
		SELECT mpv.model_profile_version_id,mpv.model_profile_id,mpv.tenant_id,mp.name,mpv.version,COALESCE(mpv.provider_account_id,''),mpv.model_name,mpv.parameters,mpv.created_by,mpv.created_at
		FROM model_profile_versions mpv JOIN model_profiles mp ON mp.model_profile_id=mpv.model_profile_id
		WHERE mpv.tenant_id=$1 ORDER BY mpv.created_at DESC LIMIT $2`, tenantID, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	versions := make([]model.ModelProfileVersion, 0)
	for rows.Next() {
		var version model.ModelProfileVersion
		if err := rows.Scan(&version.ID, &version.ModelProfileID, &version.TenantID, &version.Name, &version.Version, &version.ProviderAccountID, &version.ModelName, &version.Parameters, &version.CreatedBy, &version.CreatedAt); err != nil {
			return nil, err
		}
		versions = append(versions, version)
	}
	return versions, rows.Err()
}

func (s *Store) GetModelProfileVersion(ctx context.Context, tenantID, versionID string) (model.ModelProfileVersion, error) {
	var version model.ModelProfileVersion
	err := s.pool.QueryRow(ctx, `
		SELECT mpv.model_profile_version_id,mpv.model_profile_id,mpv.tenant_id,mp.name,mpv.version,COALESCE(mpv.provider_account_id,''),mpv.model_name,mpv.parameters,mpv.created_by,mpv.created_at
		FROM model_profile_versions mpv JOIN model_profiles mp ON mp.model_profile_id=mpv.model_profile_id
		WHERE mpv.tenant_id=$1 AND mpv.model_profile_version_id=$2`, tenantID, versionID).Scan(&version.ID, &version.ModelProfileID, &version.TenantID, &version.Name, &version.Version, &version.ProviderAccountID, &version.ModelName, &version.Parameters, &version.CreatedBy, &version.CreatedAt)
	if errors.Is(err, pgx.ErrNoRows) {
		return model.ModelProfileVersion{}, ErrNotFound
	}
	if err != nil {
		return model.ModelProfileVersion{}, err
	}
	return version, nil
}

func (s *Store) CreateProviderAccount(ctx context.Context, account model.ProviderAccount, encryptedSecret []byte, audit model.AuditEvent) (model.ProviderAccount, error) {
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return model.ProviderAccount{}, err
	}
	defer tx.Rollback(ctx)
	if _, err = tx.Exec(ctx, `INSERT INTO tenants(tenant_id,display_name) VALUES($1,$1) ON CONFLICT (tenant_id) DO NOTHING`, account.TenantID); err != nil {
		return model.ProviderAccount{}, err
	}
	err = tx.QueryRow(ctx, `
		INSERT INTO provider_accounts(provider_account_id,tenant_id,provider,name,encrypted_secret,key_reference)
		VALUES($1,$2,$3,$4,$5,$6)
		RETURNING provider_account_id,tenant_id,provider,name,key_reference,created_at,COALESCE(rotated_at,'epoch'::timestamptz)`,
		account.ID, account.TenantID, account.Provider, account.Name, encryptedSecret, account.KeyRef).Scan(
		&account.ID, &account.TenantID, &account.Provider, &account.Name, &account.KeyRef, &account.CreatedAt, &account.RotatedAt)
	if err != nil {
		return model.ProviderAccount{}, err
	}
	if account.RotatedAt.Equal(time.Unix(0, 0)) {
		account.RotatedAt = time.Time{}
	}
	if err = insertAudit(ctx, tx, audit); err != nil {
		return model.ProviderAccount{}, err
	}
	if err = tx.Commit(ctx); err != nil {
		return model.ProviderAccount{}, err
	}
	return account, nil
}

func (s *Store) ListProviderAccounts(ctx context.Context, tenantID string, limit int) ([]model.ProviderAccount, error) {
	rows, err := s.pool.Query(ctx, `
		SELECT provider_account_id,tenant_id,provider,name,key_reference,created_at,rotated_at
		FROM provider_accounts WHERE tenant_id=$1 ORDER BY created_at DESC LIMIT $2`, tenantID, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	accounts := make([]model.ProviderAccount, 0)
	for rows.Next() {
		var account model.ProviderAccount
		if err := rows.Scan(&account.ID, &account.TenantID, &account.Provider, &account.Name, &account.KeyRef, &account.CreatedAt, &account.RotatedAt); err != nil {
			return nil, err
		}
		accounts = append(accounts, account)
	}
	return accounts, rows.Err()
}

// ProviderAccountSecret is for trusted runtime processes only. It is not
// exposed through the HTTP repository or serialized in an audit/event payload.
func (s *Store) ProviderAccountSecret(ctx context.Context, tenantID, accountID string) (model.ProviderAccount, []byte, error) {
	var account model.ProviderAccount
	var encrypted []byte
	err := s.pool.QueryRow(ctx, `
		SELECT provider_account_id,tenant_id,provider,name,key_reference,created_at,rotated_at,encrypted_secret
		FROM provider_accounts WHERE tenant_id=$1 AND provider_account_id=$2`, tenantID, accountID).Scan(
		&account.ID, &account.TenantID, &account.Provider, &account.Name, &account.KeyRef, &account.CreatedAt, &account.RotatedAt, &encrypted)
	if errors.Is(err, pgx.ErrNoRows) {
		return model.ProviderAccount{}, nil, ErrNotFound
	}
	if err != nil {
		return model.ProviderAccount{}, nil, err
	}
	return account, encrypted, nil
}

func (s *Store) CreateToolApproval(ctx context.Context, approval model.ToolApproval, audit model.AuditEvent) (model.ToolApproval, error) {
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return model.ToolApproval{}, err
	}
	defer tx.Rollback(ctx)
	err = tx.QueryRow(ctx, `
		INSERT INTO tool_approvals(approval_id,tenant_id,task_id,tool_version_id,operation_hash,request,status,requested_by,expires_at)
		VALUES($1,$2,$3,$4,$5,$6,'pending',$7,$8)
		ON CONFLICT (tenant_id,operation_hash) DO UPDATE SET approval_id=tool_approvals.approval_id
		RETURNING approval_id,tenant_id,task_id,tool_version_id,operation_hash,request,status,requested_by,requested_at,expires_at,COALESCE(decided_by,''),decided_at,COALESCE(decision_note,'')`,
		approval.ID, approval.TenantID, approval.TaskID, approval.ToolVersionID, approval.OperationHash, approval.Request, approval.RequestedBy, approval.ExpiresAt).Scan(&approval.ID, &approval.TenantID, &approval.TaskID, &approval.ToolVersionID, &approval.OperationHash, &approval.Request, &approval.Status, &approval.RequestedBy, &approval.RequestedAt, &approval.ExpiresAt, &approval.DecidedBy, &approval.DecidedAt, &approval.DecisionNote)
	if err != nil {
		return model.ToolApproval{}, err
	}
	if err = insertAudit(ctx, tx, audit); err != nil {
		return model.ToolApproval{}, err
	}
	if err = tx.Commit(ctx); err != nil {
		return model.ToolApproval{}, err
	}
	return approval, nil
}

func (s *Store) ListToolApprovals(ctx context.Context, tenantID string, limit int) ([]model.ToolApproval, error) {
	if _, err := s.pool.Exec(ctx, `UPDATE tool_approvals SET status='expired' WHERE tenant_id=$1 AND status='pending' AND expires_at<=now()`, tenantID); err != nil {
		return nil, err
	}
	rows, err := s.pool.Query(ctx, `SELECT approval_id,tenant_id,task_id,tool_version_id,operation_hash,request,status,requested_by,requested_at,expires_at,COALESCE(decided_by,''),decided_at,COALESCE(decision_note,'') FROM tool_approvals WHERE tenant_id=$1 ORDER BY requested_at DESC LIMIT $2`, tenantID, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	items := make([]model.ToolApproval, 0)
	for rows.Next() {
		var a model.ToolApproval
		if err := rows.Scan(&a.ID, &a.TenantID, &a.TaskID, &a.ToolVersionID, &a.OperationHash, &a.Request, &a.Status, &a.RequestedBy, &a.RequestedAt, &a.ExpiresAt, &a.DecidedBy, &a.DecidedAt, &a.DecisionNote); err != nil {
			return nil, err
		}
		items = append(items, a)
	}
	return items, rows.Err()
}

func (s *Store) DecideToolApproval(ctx context.Context, tenantID, approvalID, status, actor, note string, audit model.AuditEvent) (model.ToolApproval, error) {
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return model.ToolApproval{}, err
	}
	defer tx.Rollback(ctx)
	var approval model.ToolApproval
	err = tx.QueryRow(ctx, `UPDATE tool_approvals SET status=$3,decided_by=$4,decided_at=now(),decision_note=$5 WHERE tenant_id=$1 AND approval_id=$2 AND status='pending' AND expires_at>now() RETURNING approval_id,tenant_id,task_id,tool_version_id,operation_hash,request,status,requested_by,requested_at,expires_at,COALESCE(decided_by,''),decided_at,COALESCE(decision_note,'')`, tenantID, approvalID, status, actor, note).Scan(&approval.ID, &approval.TenantID, &approval.TaskID, &approval.ToolVersionID, &approval.OperationHash, &approval.Request, &approval.Status, &approval.RequestedBy, &approval.RequestedAt, &approval.ExpiresAt, &approval.DecidedBy, &approval.DecidedAt, &approval.DecisionNote)
	if errors.Is(err, pgx.ErrNoRows) {
		return model.ToolApproval{}, ErrConflict
	}
	if err != nil {
		return model.ToolApproval{}, err
	}
	if err = insertAudit(ctx, tx, audit); err != nil {
		return model.ToolApproval{}, err
	}
	if err = tx.Commit(ctx); err != nil {
		return model.ToolApproval{}, err
	}
	return approval, nil
}

// SearchKnowledge is the low-cost lexical retrieval baseline. It is tenant
// scoped in SQL and uses websearch_to_tsquery so user text is parsed rather
// than concatenated into a tsquery expression.
func (s *Store) SearchKnowledge(ctx context.Context, tenantID, query string, limit int) ([]model.KnowledgeChunk, error) {
	rows, err := s.pool.Query(ctx, `
		SELECT kc.chunk_id,kc.document_id,kc.tenant_id,kd.title,kd.source_uri,kc.ordinal,
		       ts_headline('simple',kc.content,websearch_to_tsquery('simple',$2),'MaxWords=40,MinWords=15'),kc.metadata,
		       ts_rank_cd(kc.fts,websearch_to_tsquery('simple',$2))
		FROM knowledge_chunks kc JOIN knowledge_documents kd ON kd.document_id=kc.document_id
		WHERE kc.tenant_id=$1 AND kd.status='indexed' AND kc.fts @@ websearch_to_tsquery('simple',$2)
		ORDER BY ts_rank_cd(kc.fts,websearch_to_tsquery('simple',$2)) DESC,kc.chunk_id LIMIT $3`, tenantID, query, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	items := make([]model.KnowledgeChunk, 0)
	for rows.Next() {
		var chunk model.KnowledgeChunk
		if err := rows.Scan(&chunk.ID, &chunk.DocumentID, &chunk.TenantID, &chunk.Title, &chunk.SourceURI, &chunk.Ordinal, &chunk.Content, &chunk.Metadata, &chunk.Score); err != nil {
			return nil, err
		}
		items = append(items, chunk)
	}
	return items, rows.Err()
}

func (s *Store) SearchKnowledgeVector(ctx context.Context, tenantID, modelName string, embedding []float32, limit int) ([]model.KnowledgeChunk, error) {
	vector, err := rag.VectorLiteral(embedding)
	if err != nil {
		return nil, err
	}
	rows, err := s.pool.Query(ctx, `
		SELECT kc.chunk_id,kc.document_id,kc.tenant_id,kd.title,kd.source_uri,kc.ordinal,kc.content,kc.metadata,
		       1-(ke.embedding <=> $3::vector)
		FROM knowledge_embeddings ke JOIN knowledge_chunks kc ON kc.chunk_id=ke.chunk_id JOIN knowledge_documents kd ON kd.document_id=kc.document_id
		WHERE ke.tenant_id=$1 AND kc.tenant_id=$1 AND kd.status='indexed' AND ke.model_name=$2
		ORDER BY ke.embedding <=> $3::vector,kc.chunk_id LIMIT $4`, tenantID, modelName, vector, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	items := make([]model.KnowledgeChunk, 0)
	for rows.Next() {
		var chunk model.KnowledgeChunk
		if err := rows.Scan(&chunk.ID, &chunk.DocumentID, &chunk.TenantID, &chunk.Title, &chunk.SourceURI, &chunk.Ordinal, &chunk.Content, &chunk.Metadata, &chunk.Score); err != nil {
			return nil, err
		}
		items = append(items, chunk)
	}
	return items, rows.Err()
}

// UpsertKnowledgeEmbedding stores one model-specific vector for a chunk. The
// INSERT ... SELECT shape makes the tenant check part of the write itself, so
// callers cannot attach an embedding to another tenant's chunk.
func (s *Store) UpsertKnowledgeEmbedding(ctx context.Context, tenantID, chunkID, modelName string, embedding []float32) error {
	vector, err := rag.VectorLiteral(embedding)
	if err != nil {
		return err
	}
	result, err := s.pool.Exec(ctx, `
		INSERT INTO knowledge_embeddings(chunk_id,tenant_id,model_name,dimensions,embedding)
		SELECT kc.chunk_id,kc.tenant_id,$3,$4,$5::vector
		FROM knowledge_chunks kc
		WHERE kc.chunk_id=$1 AND kc.tenant_id=$2
		ON CONFLICT (chunk_id,model_name) DO UPDATE
		SET embedding=EXCLUDED.embedding,dimensions=EXCLUDED.dimensions,created_at=now()`,
		chunkID, tenantID, modelName, len(embedding), vector)
	if err != nil {
		return err
	}
	if result.RowsAffected() == 0 {
		return ErrNotFound
	}
	return nil
}

// IngestKnowledge replaces every chunk of one stable document ID in one
// transaction. Search queries only see `indexed` documents, so a failed or
// interrupted ingest never leaks a partial set of chunks.
func (s *Store) IngestKnowledge(ctx context.Context, tenantID, knowledgeBaseID, knowledgeBaseName, documentID, sourceURI, title, contentHash, actor string, chunks []rag.Chunk, audit model.AuditEvent) error {
	return s.ingestKnowledge(ctx, tenantID, knowledgeBaseID, knowledgeBaseName, documentID, sourceURI, title, contentHash, actor, chunks, "", nil, audit)
}

// IngestKnowledgeWithEmbeddings commits chunks and their frozen-profile
// vectors together. A failed vector insert rolls back the document update.
func (s *Store) IngestKnowledgeWithEmbeddings(ctx context.Context, tenantID, knowledgeBaseID, knowledgeBaseName, documentID, sourceURI, title, contentHash, actor, embeddingProfileVersionID string, chunks []rag.Chunk, embeddings [][]float32, audit model.AuditEvent) error {
	if embeddingProfileVersionID == "" || len(chunks) != len(embeddings) {
		return errors.New("embedding profile and chunk vectors must align")
	}
	return s.ingestKnowledge(ctx, tenantID, knowledgeBaseID, knowledgeBaseName, documentID, sourceURI, title, contentHash, actor, chunks, embeddingProfileVersionID, embeddings, audit)
}

func (s *Store) ingestKnowledge(ctx context.Context, tenantID, knowledgeBaseID, knowledgeBaseName, documentID, sourceURI, title, contentHash, actor string, chunks []rag.Chunk, embeddingProfileVersionID string, embeddings [][]float32, audit model.AuditEvent) error {
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx)
	if _, err = tx.Exec(ctx, `INSERT INTO tenants(tenant_id,display_name) VALUES($1,$1) ON CONFLICT (tenant_id) DO NOTHING`, tenantID); err != nil {
		return err
	}
	if _, err = tx.Exec(ctx, `INSERT INTO knowledge_bases(knowledge_base_id,tenant_id,name) VALUES($1,$2,$3) ON CONFLICT (tenant_id,name) DO NOTHING`, knowledgeBaseID, tenantID, knowledgeBaseName); err != nil {
		return err
	}
	var kbID string
	if err = tx.QueryRow(ctx, `SELECT knowledge_base_id FROM knowledge_bases WHERE tenant_id=$1 AND name=$2 FOR UPDATE`, tenantID, knowledgeBaseName).Scan(&kbID); err != nil {
		return err
	}
	if _, err = tx.Exec(ctx, `INSERT INTO knowledge_documents(document_id,knowledge_base_id,tenant_id,source_uri,title,content_hash,status) VALUES($1,$2,$3,$4,$5,$6,'pending') ON CONFLICT (document_id) DO UPDATE SET source_uri=EXCLUDED.source_uri,title=EXCLUDED.title,content_hash=EXCLUDED.content_hash,status='pending',updated_at=now()`, documentID, kbID, tenantID, sourceURI, title, contentHash); err != nil {
		return err
	}
	if _, err = tx.Exec(ctx, `DELETE FROM knowledge_chunks WHERE document_id=$1`, documentID); err != nil {
		return err
	}
	for index, chunk := range chunks {
		if _, err = tx.Exec(ctx, `INSERT INTO knowledge_chunks(chunk_id,document_id,tenant_id,ordinal,content,content_hash) VALUES($1,$2,$3,$4,$5,$6)`, fmt.Sprintf("%s-%d", documentID, chunk.Ordinal), documentID, tenantID, chunk.Ordinal, chunk.Content, chunk.Hash); err != nil {
			return err
		}
		if embeddingProfileVersionID != "" {
			vector, vectorErr := rag.VectorLiteral(embeddings[index])
			if vectorErr != nil {
				return vectorErr
			}
			if _, err = tx.Exec(ctx, `INSERT INTO knowledge_embeddings(chunk_id,tenant_id,model_name,dimensions,embedding) VALUES($1,$2,$3,$4,$5::vector)`, fmt.Sprintf("%s-%d", documentID, chunk.Ordinal), tenantID, embeddingProfileVersionID, len(embeddings[index]), vector); err != nil {
				return err
			}
		}
	}
	if _, err = tx.Exec(ctx, `UPDATE knowledge_documents SET status='indexed',updated_at=now() WHERE document_id=$1 AND tenant_id=$2`, documentID, tenantID); err != nil {
		return err
	}
	if err = insertAudit(ctx, tx, audit); err != nil {
		return err
	}
	return tx.Commit(ctx)
}

func (s *Store) CreateAgentVersion(ctx context.Context, tenantID, agentID, versionID, name, actorID string, specification json.RawMessage, audit model.AuditEvent) (model.AgentVersion, error) {
	canonical, err := canonicalJSON(specification)
	if err != nil {
		return model.AgentVersion{}, err
	}
	hash := fmt.Sprintf("%x", sha256.Sum256(canonical))
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return model.AgentVersion{}, err
	}
	defer tx.Rollback(ctx)
	if _, err = tx.Exec(ctx, `INSERT INTO tenants(tenant_id,display_name) VALUES($1,$1) ON CONFLICT (tenant_id) DO NOTHING`, tenantID); err != nil {
		return model.AgentVersion{}, err
	}
	if _, err = tx.Exec(ctx, `INSERT INTO agents(agent_id,tenant_id,name) VALUES($1,$2,$3) ON CONFLICT (tenant_id,name) DO NOTHING`, agentID, tenantID, name); err != nil {
		return model.AgentVersion{}, err
	}
	var storedID string
	if err = tx.QueryRow(ctx, `SELECT agent_id FROM agents WHERE tenant_id=$1 AND name=$2 FOR UPDATE`, tenantID, name).Scan(&storedID); err != nil {
		return model.AgentVersion{}, err
	}
	var version int
	if err = tx.QueryRow(ctx, `SELECT COALESCE(MAX(version),0)+1 FROM agent_versions WHERE agent_id=$1`, storedID).Scan(&version); err != nil {
		return model.AgentVersion{}, err
	}
	result := model.AgentVersion{Name: name}
	err = tx.QueryRow(ctx, `INSERT INTO agent_versions(agent_version_id,agent_id,tenant_id,version,specification,content_hash,created_by) VALUES($1,$2,$3,$4,$5,$6,$7) RETURNING agent_version_id,agent_id,tenant_id,version,specification,content_hash,created_by,created_at`, versionID, storedID, tenantID, version, canonical, hash, actorID).Scan(&result.ID, &result.AgentID, &result.TenantID, &result.Version, &result.Specification, &result.Hash, &result.CreatedBy, &result.CreatedAt)
	if err != nil {
		return model.AgentVersion{}, err
	}
	if err = insertAudit(ctx, tx, audit); err != nil {
		return model.AgentVersion{}, err
	}
	if err = tx.Commit(ctx); err != nil {
		return model.AgentVersion{}, err
	}
	return result, nil
}

func (s *Store) ListAgentVersions(ctx context.Context, tenantID string, limit int) ([]model.AgentVersion, error) {
	rows, err := s.pool.Query(ctx, `SELECT av.agent_version_id,av.agent_id,av.tenant_id,a.name,av.version,av.specification,av.content_hash,av.created_by,av.created_at FROM agent_versions av JOIN agents a ON a.agent_id=av.agent_id WHERE av.tenant_id=$1 ORDER BY av.created_at DESC LIMIT $2`, tenantID, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	items := make([]model.AgentVersion, 0)
	for rows.Next() {
		var v model.AgentVersion
		if err := rows.Scan(&v.ID, &v.AgentID, &v.TenantID, &v.Name, &v.Version, &v.Specification, &v.Hash, &v.CreatedBy, &v.CreatedAt); err != nil {
			return nil, err
		}
		items = append(items, v)
	}
	return items, rows.Err()
}

func (s *Store) CreateToolVersion(ctx context.Context, tenantID, toolID, versionID, name, kind, actorID string, specification json.RawMessage, audit model.AuditEvent) (model.ToolVersion, error) {
	canonical, err := canonicalJSON(specification)
	if err != nil {
		return model.ToolVersion{}, err
	}
	hash := fmt.Sprintf("%x", sha256.Sum256(canonical))
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return model.ToolVersion{}, err
	}
	defer tx.Rollback(ctx)
	if _, err = tx.Exec(ctx, `INSERT INTO tenants(tenant_id,display_name) VALUES($1,$1) ON CONFLICT (tenant_id) DO NOTHING`, tenantID); err != nil {
		return model.ToolVersion{}, err
	}
	if _, err = tx.Exec(ctx, `INSERT INTO tools(tool_id,tenant_id,name) VALUES($1,$2,$3) ON CONFLICT (tenant_id,name) DO NOTHING`, toolID, tenantID, name); err != nil {
		return model.ToolVersion{}, err
	}
	var storedID string
	if err = tx.QueryRow(ctx, `SELECT tool_id FROM tools WHERE tenant_id=$1 AND name=$2 FOR UPDATE`, tenantID, name).Scan(&storedID); err != nil {
		return model.ToolVersion{}, err
	}
	var version int
	if err = tx.QueryRow(ctx, `SELECT COALESCE(MAX(version),0)+1 FROM tool_versions WHERE tool_id=$1`, storedID).Scan(&version); err != nil {
		return model.ToolVersion{}, err
	}
	result := model.ToolVersion{Name: name, Kind: kind}
	err = tx.QueryRow(ctx, `INSERT INTO tool_versions(tool_version_id,tool_id,tenant_id,version,kind,specification,content_hash,created_by) VALUES($1,$2,$3,$4,$5,$6,$7,$8) RETURNING tool_version_id,tool_id,tenant_id,version,kind,specification,content_hash,created_by,created_at`, versionID, storedID, tenantID, version, kind, canonical, hash, actorID).Scan(&result.ID, &result.ToolID, &result.TenantID, &result.Version, &result.Kind, &result.Specification, &result.Hash, &result.CreatedBy, &result.CreatedAt)
	if err != nil {
		return model.ToolVersion{}, err
	}
	if err = insertAudit(ctx, tx, audit); err != nil {
		return model.ToolVersion{}, err
	}
	if err = tx.Commit(ctx); err != nil {
		return model.ToolVersion{}, err
	}
	return result, nil
}

func (s *Store) ListToolVersions(ctx context.Context, tenantID string, limit int) ([]model.ToolVersion, error) {
	rows, err := s.pool.Query(ctx, `SELECT tv.tool_version_id,tv.tool_id,tv.tenant_id,t.name,tv.version,tv.kind,tv.specification,tv.content_hash,tv.created_by,tv.created_at FROM tool_versions tv JOIN tools t ON t.tool_id=tv.tool_id WHERE tv.tenant_id=$1 ORDER BY tv.created_at DESC LIMIT $2`, tenantID, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	items := make([]model.ToolVersion, 0)
	for rows.Next() {
		var v model.ToolVersion
		if err := rows.Scan(&v.ID, &v.ToolID, &v.TenantID, &v.Name, &v.Version, &v.Kind, &v.Specification, &v.Hash, &v.CreatedBy, &v.CreatedAt); err != nil {
			return nil, err
		}
		items = append(items, v)
	}
	return items, rows.Err()
}

func (s *Store) CreateSkillVersion(ctx context.Context, tenantID, skillID, versionID, name, actorID string, specification json.RawMessage, audit model.AuditEvent) (model.SkillVersion, error) {
	canonical, err := canonicalJSON(specification)
	if err != nil {
		return model.SkillVersion{}, err
	}
	hash := fmt.Sprintf("%x", sha256.Sum256(canonical))
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return model.SkillVersion{}, err
	}
	defer tx.Rollback(ctx)
	if _, err = tx.Exec(ctx, `INSERT INTO tenants(tenant_id,display_name) VALUES($1,$1) ON CONFLICT (tenant_id) DO NOTHING`, tenantID); err != nil {
		return model.SkillVersion{}, err
	}
	if _, err = tx.Exec(ctx, `INSERT INTO skills(skill_id,tenant_id,name) VALUES($1,$2,$3) ON CONFLICT (tenant_id,name) DO NOTHING`, skillID, tenantID, name); err != nil {
		return model.SkillVersion{}, err
	}
	var storedID string
	if err = tx.QueryRow(ctx, `SELECT skill_id FROM skills WHERE tenant_id=$1 AND name=$2 FOR UPDATE`, tenantID, name).Scan(&storedID); err != nil {
		return model.SkillVersion{}, err
	}
	var version int
	if err = tx.QueryRow(ctx, `SELECT COALESCE(MAX(version),0)+1 FROM skill_versions WHERE skill_id=$1`, storedID).Scan(&version); err != nil {
		return model.SkillVersion{}, err
	}
	result := model.SkillVersion{Name: name}
	err = tx.QueryRow(ctx, `INSERT INTO skill_versions(skill_version_id,skill_id,tenant_id,version,specification,content_hash,created_by) VALUES($1,$2,$3,$4,$5,$6,$7) RETURNING skill_version_id,skill_id,tenant_id,version,specification,content_hash,created_by,created_at`, versionID, storedID, tenantID, version, canonical, hash, actorID).Scan(&result.ID, &result.SkillID, &result.TenantID, &result.Version, &result.Specification, &result.Hash, &result.CreatedBy, &result.CreatedAt)
	if err != nil {
		return model.SkillVersion{}, err
	}
	if err = insertAudit(ctx, tx, audit); err != nil {
		return model.SkillVersion{}, err
	}
	if err = tx.Commit(ctx); err != nil {
		return model.SkillVersion{}, err
	}
	return result, nil
}

func (s *Store) ListSkillVersions(ctx context.Context, tenantID string, limit int) ([]model.SkillVersion, error) {
	rows, err := s.pool.Query(ctx, `SELECT sv.skill_version_id,sv.skill_id,sv.tenant_id,s.name,sv.version,sv.specification,sv.content_hash,sv.created_by,sv.created_at FROM skill_versions sv JOIN skills s ON s.skill_id=sv.skill_id WHERE sv.tenant_id=$1 ORDER BY sv.created_at DESC LIMIT $2`, tenantID, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	items := make([]model.SkillVersion, 0)
	for rows.Next() {
		var v model.SkillVersion
		if err := rows.Scan(&v.ID, &v.SkillID, &v.TenantID, &v.Name, &v.Version, &v.Specification, &v.Hash, &v.CreatedBy, &v.CreatedAt); err != nil {
			return nil, err
		}
		items = append(items, v)
	}
	return items, rows.Err()
}

func (s *Store) CreateRuntimeSnapshot(ctx context.Context, snapshot model.RuntimeSnapshot, audit model.AuditEvent) (model.RuntimeSnapshot, error) {
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return model.RuntimeSnapshot{}, err
	}
	defer tx.Rollback(ctx)
	if _, err = tx.Exec(ctx, `INSERT INTO tenants(tenant_id,display_name) VALUES($1,$1) ON CONFLICT (tenant_id) DO NOTHING`, snapshot.TenantID); err != nil {
		return model.RuntimeSnapshot{}, err
	}
	if snapshot.PromptVersionID != "" {
		var found bool
		if err = tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM prompt_versions WHERE tenant_id=$1 AND prompt_version_id=$2)`, snapshot.TenantID, snapshot.PromptVersionID).Scan(&found); err != nil || !found {
			return model.RuntimeSnapshot{}, ErrNotFound
		}
	}
	if snapshot.ModelProfileVersionID != "" {
		var found bool
		if err = tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM model_profile_versions WHERE tenant_id=$1 AND model_profile_version_id=$2)`, snapshot.TenantID, snapshot.ModelProfileVersionID).Scan(&found); err != nil || !found {
			return model.RuntimeSnapshot{}, ErrNotFound
		}
	}
	if len(snapshot.AgentVersion) == 0 {
		snapshot.AgentVersion = json.RawMessage(`{}`)
	}
	if len(snapshot.ToolVersions) == 0 {
		snapshot.ToolVersions = json.RawMessage(`[]`)
	}
	canonicalAgent, err := canonicalJSON(snapshot.AgentVersion)
	if err != nil {
		return model.RuntimeSnapshot{}, err
	}
	canonicalTools, err := canonicalJSON(snapshot.ToolVersions)
	if err != nil {
		return model.RuntimeSnapshot{}, err
	}
	snapshot.Hash = fmt.Sprintf("%x", sha256.Sum256([]byte(snapshot.PromptVersionID+"\n"+snapshot.ModelProfileVersionID+"\n"+string(canonicalAgent)+"\n"+string(canonicalTools))))
	err = tx.QueryRow(ctx, `
		INSERT INTO runtime_snapshots(runtime_snapshot_id,tenant_id,prompt_version_id,model_profile_version_id,agent_version,tool_versions,content_hash,created_by)
		VALUES($1,$2,NULLIF($3,''),NULLIF($4,''),$5,$6,$7,$8)
		ON CONFLICT (tenant_id,content_hash) DO UPDATE SET runtime_snapshot_id=runtime_snapshots.runtime_snapshot_id
		RETURNING runtime_snapshot_id,tenant_id,COALESCE(prompt_version_id,''),COALESCE(model_profile_version_id,''),agent_version,tool_versions,content_hash,created_by,created_at`,
		snapshot.ID, snapshot.TenantID, snapshot.PromptVersionID, snapshot.ModelProfileVersionID, canonicalAgent, canonicalTools, snapshot.Hash, snapshot.CreatedBy).Scan(
		&snapshot.ID, &snapshot.TenantID, &snapshot.PromptVersionID, &snapshot.ModelProfileVersionID, &snapshot.AgentVersion, &snapshot.ToolVersions, &snapshot.Hash, &snapshot.CreatedBy, &snapshot.CreatedAt)
	if err != nil {
		return model.RuntimeSnapshot{}, err
	}
	if err = insertAudit(ctx, tx, audit); err != nil {
		return model.RuntimeSnapshot{}, err
	}
	if err = tx.Commit(ctx); err != nil {
		return model.RuntimeSnapshot{}, err
	}
	return snapshot, nil
}

func (s *Store) GetRuntimeSnapshot(ctx context.Context, tenantID, snapshotID string) (model.RuntimeSnapshot, error) {
	var snapshot model.RuntimeSnapshot
	err := s.pool.QueryRow(ctx, `
		SELECT runtime_snapshot_id,tenant_id,COALESCE(prompt_version_id,''),COALESCE(model_profile_version_id,''),agent_version,tool_versions,content_hash,created_by,created_at
		FROM runtime_snapshots WHERE tenant_id=$1 AND runtime_snapshot_id=$2`, tenantID, snapshotID).Scan(
		&snapshot.ID, &snapshot.TenantID, &snapshot.PromptVersionID, &snapshot.ModelProfileVersionID, &snapshot.AgentVersion, &snapshot.ToolVersions, &snapshot.Hash, &snapshot.CreatedBy, &snapshot.CreatedAt)
	if errors.Is(err, pgx.ErrNoRows) {
		return model.RuntimeSnapshot{}, ErrNotFound
	}
	return snapshot, err
}

func canonicalJSON(raw json.RawMessage) ([]byte, error) {
	var value any
	if err := json.Unmarshal(raw, &value); err != nil {
		return nil, err
	}
	return json.Marshal(value)
}

func (s *Store) EnqueueResume(ctx context.Context, tenantID, taskID, messageID string, payload json.RawMessage, audit model.AuditEvent) error {
	return s.enqueueTransition(ctx, tenantID, taskID, messageID, "resume", payload, []string{model.StatusAwaitingUser}, audit)
}

func (s *Store) EnqueueCancel(ctx context.Context, tenantID, taskID, messageID string, audit model.AuditEvent) error {
	return s.enqueueTransition(ctx, tenantID, taskID, messageID, "cancel", json.RawMessage(`{}`), []string{model.StatusQueued, model.StatusRunning, model.StatusAwaitingUser}, audit)
}

func (s *Store) EnqueueRetry(ctx context.Context, tenantID, taskID, messageID string, audit model.AuditEvent) error {
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx)
	var status string
	var request json.RawMessage
	if err = tx.QueryRow(ctx, `SELECT status,request FROM tasks WHERE tenant_id=$1 AND task_id=$2 FOR UPDATE`, tenantID, taskID).Scan(&status, &request); errors.Is(err, pgx.ErrNoRows) {
		return ErrNotFound
	} else if err != nil {
		return err
	}
	if status != model.StatusFailed {
		return ErrConflict
	}
	var payload map[string]any
	if err = json.Unmarshal(request, &payload); err != nil {
		return fmt.Errorf("decode stored task request: %w", err)
	}
	payload["tenant_id"] = tenantID
	payloadJSON, err := json.Marshal(payload)
	if err != nil {
		return err
	}
	body, err := json.Marshal(model.Command{MessageID: messageID, Type: "run", TaskID: taskID, TenantID: tenantID, Payload: payloadJSON, CreatedAt: time.Now().UTC()})
	if err != nil {
		return err
	}
	if _, err = tx.Exec(ctx, `INSERT INTO outbox(message_id,routing_key,payload) VALUES($1,'run',$2)`, messageID, body); err != nil {
		return err
	}
	if _, err = tx.Exec(ctx, `UPDATE tasks SET status='queued',error=NULL,result=NULL,updated_at=now() WHERE tenant_id=$1 AND task_id=$2`, tenantID, taskID); err != nil {
		return err
	}
	if err = insertAudit(ctx, tx, audit); err != nil {
		return err
	}
	return tx.Commit(ctx)
}

func (s *Store) enqueueTransition(ctx context.Context, tenantID, taskID, messageID, kind string, payload json.RawMessage, allowed []string, audit model.AuditEvent) error {
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx)
	var current string
	if err = tx.QueryRow(ctx, `SELECT status FROM tasks WHERE tenant_id=$1 AND task_id=$2 FOR UPDATE`, tenantID, taskID).Scan(&current); errors.Is(err, pgx.ErrNoRows) {
		return ErrNotFound
	} else if err != nil {
		return err
	}
	ok := false
	for _, status := range allowed {
		if current == status {
			ok = true
			break
		}
	}
	if !ok {
		return ErrConflict
	}
	command := model.Command{MessageID: messageID, Type: kind, TaskID: taskID, TenantID: tenantID, Payload: payload, CreatedAt: time.Now().UTC()}
	body, _ := json.Marshal(command)
	if _, err = tx.Exec(ctx, `INSERT INTO outbox(message_id,routing_key,payload) VALUES($1,$2,$3)`, messageID, kind, body); err != nil {
		return err
	}
	next := model.StatusQueued
	if kind == "cancel" {
		next = model.StatusCancelled
	}
	if _, err = tx.Exec(ctx, `UPDATE tasks SET status=$3,updated_at=now() WHERE tenant_id=$1 AND task_id=$2`, tenantID, taskID, next); err != nil {
		return err
	}
	if err = insertAudit(ctx, tx, audit); err != nil {
		return err
	}
	return tx.Commit(ctx)
}

func insertAudit(ctx context.Context, tx pgx.Tx, event model.AuditEvent) error {
	if len(event.Data) == 0 {
		event.Data = json.RawMessage(`{}`)
	}
	_, err := tx.Exec(ctx, `
		INSERT INTO audit_events(event_id,tenant_id,actor_id,action,resource_type,resource_id,request_id,data,occurred_at)
		VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9)
		ON CONFLICT (event_id) DO NOTHING`,
		event.ID, event.TenantID, event.ActorID, event.Action, event.ResourceType, event.ResourceID, event.RequestID, event.Data, event.OccurredAt)
	return err
}

func (s *Store) EventsAfter(ctx context.Context, tenantID, taskID string, after int64, limit int) ([]model.Event, error) {
	rows, err := s.pool.Query(ctx, `SELECT message_id,task_id,tenant_id,sequence,event_type,data,created_at FROM task_events WHERE tenant_id=$1 AND task_id=$2 AND sequence>$3 ORDER BY sequence LIMIT $4`, tenantID, taskID, after, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	events := make([]model.Event, 0)
	for rows.Next() {
		var e model.Event
		if err := rows.Scan(&e.MessageID, &e.TaskID, &e.TenantID, &e.Sequence, &e.Type, &e.Data, &e.CreatedAt); err != nil {
			return nil, err
		}
		events = append(events, e)
	}
	return events, rows.Err()
}

func (s *Store) ListInvocations(ctx context.Context, tenantID, taskID string, limit int) ([]Invocation, error) {
	query := `SELECT invocation_id,tenant_id,task_id,runtime,invocation_kind,status,COALESCE(runtime_snapshot_id,''),COALESCE(prompt_version_id,''),COALESCE(model_profile_version_id,''),COALESCE(tool_version_id,''),input_tokens,output_tokens,estimated_cost_usd::float8,latency_ms,data,occurred_at FROM runtime_invocations WHERE tenant_id=$1`
	args := []any{tenantID}
	if taskID != "" {
		query += " AND task_id=$2"
		args = append(args, taskID)
	}
	query += fmt.Sprintf(" ORDER BY occurred_at DESC LIMIT $%d", len(args)+1)
	args = append(args, limit)
	rows, err := s.pool.Query(ctx, query, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	items := make([]Invocation, 0)
	for rows.Next() {
		var item Invocation
		if err = rows.Scan(&item.ID, &item.TenantID, &item.TaskID, &item.Runtime, &item.Kind, &item.Status, &item.RuntimeSnapshotID, &item.PromptVersionID, &item.ModelProfileVersionID, &item.ToolVersionID, &item.InputTokens, &item.OutputTokens, &item.EstimatedCostUSD, &item.LatencyMS, &item.Data, &item.OccurredAt); err != nil {
			return nil, err
		}
		items = append(items, item)
	}
	return items, rows.Err()
}

func (s *Store) CreateWebhookSubscription(ctx context.Context, subscription model.WebhookSubscription, endpointEncrypted, signingSecretEncrypted []byte, audit model.AuditEvent) (model.WebhookSubscription, error) {
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return model.WebhookSubscription{}, err
	}
	defer tx.Rollback(ctx)
	if _, err = tx.Exec(ctx, `INSERT INTO tenants(tenant_id,display_name) VALUES($1,$1) ON CONFLICT (tenant_id) DO NOTHING`, subscription.TenantID); err != nil {
		return model.WebhookSubscription{}, err
	}
	err = tx.QueryRow(ctx, `INSERT INTO webhook_subscriptions(subscription_id,tenant_id,name,kind,event_types,endpoint_host,endpoint_encrypted,signing_secret_encrypted,enabled,created_by) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10) RETURNING created_at,updated_at`, subscription.ID, subscription.TenantID, subscription.Name, subscription.Kind, subscription.EventTypes, subscription.EndpointHost, endpointEncrypted, signingSecretEncrypted, subscription.Enabled, subscription.CreatedBy).Scan(&subscription.CreatedAt, &subscription.UpdatedAt)
	if err != nil {
		return model.WebhookSubscription{}, err
	}
	if err = insertAudit(ctx, tx, audit); err != nil {
		return model.WebhookSubscription{}, err
	}
	if err = tx.Commit(ctx); err != nil {
		return model.WebhookSubscription{}, err
	}
	return subscription, nil
}

func (s *Store) ListWebhookSubscriptions(ctx context.Context, tenantID string, limit int) ([]model.WebhookSubscription, error) {
	rows, err := s.pool.Query(ctx, `SELECT subscription_id,tenant_id,name,kind,event_types,endpoint_host,enabled,created_by,created_at,updated_at FROM webhook_subscriptions WHERE tenant_id=$1 ORDER BY created_at DESC LIMIT $2`, tenantID, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	items := make([]model.WebhookSubscription, 0)
	for rows.Next() {
		var item model.WebhookSubscription
		if err = rows.Scan(&item.ID, &item.TenantID, &item.Name, &item.Kind, &item.EventTypes, &item.EndpointHost, &item.Enabled, &item.CreatedBy, &item.CreatedAt, &item.UpdatedAt); err != nil {
			return nil, err
		}
		items = append(items, item)
	}
	return items, rows.Err()
}

func (s *Store) SetWebhookSubscriptionEnabled(ctx context.Context, tenantID, subscriptionID string, enabled bool, audit model.AuditEvent) (model.WebhookSubscription, error) {
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return model.WebhookSubscription{}, err
	}
	defer tx.Rollback(ctx)
	var item model.WebhookSubscription
	err = tx.QueryRow(ctx, `UPDATE webhook_subscriptions SET enabled=$3,updated_at=now() WHERE tenant_id=$1 AND subscription_id=$2 RETURNING subscription_id,tenant_id,name,kind,event_types,endpoint_host,enabled,created_by,created_at,updated_at`, tenantID, subscriptionID, enabled).Scan(&item.ID, &item.TenantID, &item.Name, &item.Kind, &item.EventTypes, &item.EndpointHost, &item.Enabled, &item.CreatedBy, &item.CreatedAt, &item.UpdatedAt)
	if errors.Is(err, pgx.ErrNoRows) {
		return model.WebhookSubscription{}, ErrNotFound
	}
	if err != nil {
		return model.WebhookSubscription{}, err
	}
	if err = insertAudit(ctx, tx, audit); err != nil {
		return model.WebhookSubscription{}, err
	}
	if err = tx.Commit(ctx); err != nil {
		return model.WebhookSubscription{}, err
	}
	return item, nil
}

func (s *Store) ClaimWebhookDeliveries(ctx context.Context, limit int) ([]model.WebhookDelivery, error) {
	rows, err := s.pool.Query(ctx, `WITH picked AS (
		SELECT delivery_id FROM webhook_deliveries
		WHERE status IN ('pending','processing') AND next_attempt_at<=now() AND (locked_until IS NULL OR locked_until<now()) AND attempts<12
		ORDER BY next_attempt_at,delivery_id FOR UPDATE SKIP LOCKED LIMIT $1
	) UPDATE webhook_deliveries d SET status='processing',locked_until=now()+interval '30 seconds',attempts=attempts+1,updated_at=now()
	FROM picked,webhook_subscriptions s WHERE d.delivery_id=picked.delivery_id AND s.subscription_id=d.subscription_id
	RETURNING d.delivery_id,d.subscription_id,d.tenant_id,s.kind,s.endpoint_host,s.endpoint_encrypted,COALESCE(s.signing_secret_encrypted,''::bytea),d.event_message_id,d.event_type,d.payload,d.attempts`, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	items := make([]model.WebhookDelivery, 0)
	for rows.Next() {
		var item model.WebhookDelivery
		if err = rows.Scan(&item.ID, &item.SubscriptionID, &item.TenantID, &item.Kind, &item.EndpointHost, &item.EndpointEncrypted, &item.SigningSecretEncrypted, &item.EventMessageID, &item.EventType, &item.Payload, &item.Attempts); err != nil {
			return nil, err
		}
		items = append(items, item)
	}
	return items, rows.Err()
}

func (s *Store) CompleteWebhookDelivery(ctx context.Context, deliveryID int64, responseStatus int) error {
	_, err := s.pool.Exec(ctx, `UPDATE webhook_deliveries SET status='delivered',response_status=$2,delivered_at=now(),locked_until=NULL,last_error=NULL,updated_at=now() WHERE delivery_id=$1`, deliveryID, responseStatus)
	return err
}

func (s *Store) FailWebhookDelivery(ctx context.Context, deliveryID int64, attempts, responseStatus int, cause error) error {
	status := "pending"
	if attempts >= 12 {
		status = "dead"
	}
	message := "delivery failed"
	if cause != nil {
		message = cause.Error()
	}
	_, err := s.pool.Exec(ctx, `UPDATE webhook_deliveries SET status=$2,response_status=NULLIF($3,0),last_error=left($4,1000),locked_until=NULL,next_attempt_at=now()+(power(2,LEAST($5,8))*interval '1 second'),updated_at=now() WHERE delivery_id=$1`, deliveryID, status, responseStatus, message, attempts)
	return err
}

func (s *Store) ProjectEvent(ctx context.Context, e model.Event) (model.Event, bool, error) {
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return e, false, err
	}
	defer tx.Rollback(ctx)
	if _, err = tx.Exec(ctx, `SELECT pg_advisory_xact_lock(hashtext($1))`, e.TaskID); err != nil {
		return e, false, err
	}
	if err = tx.QueryRow(ctx, `SELECT COALESCE(MAX(sequence),0)+1 FROM task_events WHERE task_id=$1`, e.TaskID).Scan(&e.Sequence); err != nil {
		return e, false, err
	}
	ct, err := tx.Exec(ctx, `INSERT INTO task_events(task_id,sequence,message_id,tenant_id,event_type,data,created_at) VALUES($1,$2,$3,$4,$5,$6,$7) ON CONFLICT (message_id) DO NOTHING`, e.TaskID, e.Sequence, e.MessageID, e.TenantID, e.Type, e.Data, e.CreatedAt)
	if err != nil {
		return e, false, err
	}
	if ct.RowsAffected() == 0 {
		return e, false, tx.Commit(ctx)
	}
	if _, err = tx.Exec(ctx, `INSERT INTO webhook_deliveries(subscription_id,tenant_id,event_message_id,event_type,payload)
		SELECT subscription_id,$1,$2,$3,jsonb_build_object('message_id',$2,'task_id',$4,'tenant_id',$1,'type',$3,'data',$5::jsonb,'created_at',$6)
		FROM webhook_subscriptions WHERE tenant_id=$1 AND enabled AND $3=ANY(event_types)
		ON CONFLICT (subscription_id,event_message_id) DO NOTHING`, e.TenantID, e.MessageID, e.Type, e.TaskID, e.Data, e.CreatedAt); err != nil {
		return e, false, err
	}
	if err = projectInvocation(ctx, tx, e); err != nil {
		return e, false, err
	}
	if err = applyEventStatus(ctx, tx, e); err != nil {
		return e, false, err
	}
	if _, err = tx.Exec(ctx, `SELECT pg_notify('agent_room_events',$1)`, e.TaskID); err != nil {
		return e, false, err
	}
	if err = tx.Commit(ctx); err != nil {
		return e, false, err
	}
	return e, true, nil
}

type invocationEventData struct {
	Runtime               string   `json:"runtime"`
	InvocationKind        string   `json:"invocation_kind"`
	Status                string   `json:"status"`
	RuntimeSnapshotID     string   `json:"runtime_snapshot_id"`
	PromptVersionID       string   `json:"prompt_version_id"`
	ModelProfileVersionID string   `json:"model_profile_version_id"`
	ToolVersionID         string   `json:"tool_version_id"`
	InputTokens           *int     `json:"input_tokens"`
	OutputTokens          *int     `json:"output_tokens"`
	EstimatedCostUSD      *float64 `json:"estimated_cost_usd"`
	LatencyMS             *int     `json:"latency_ms"`
}

func invocationFromEvent(e model.Event) (invocationEventData, bool, error) {
	var data invocationEventData
	switch e.Type {
	case "runtime_invocation":
		if err := json.Unmarshal(e.Data, &data); err != nil || (data.InvocationKind != "model" && data.InvocationKind != "tool") || (data.Status != "succeeded" && data.Status != "failed") || data.Runtime == "" {
			return data, false, errors.New("invalid runtime invocation event")
		}
		return data, true, nil
	case "usage":
		if err := json.Unmarshal(e.Data, &data); err != nil {
			return data, false, errors.New("invalid usage event")
		}
		data.Runtime = "python_langgraph"
		data.InvocationKind = "model"
		data.Status = "succeeded"
		if data.InputTokens == nil || data.OutputTokens == nil || *data.InputTokens < 0 || *data.OutputTokens < 0 {
			return data, false, errors.New("invalid usage event")
		}
		return data, true, nil
	default:
		return data, false, nil
	}
}

func projectInvocation(ctx context.Context, tx pgx.Tx, e model.Event) error {
	data, project, err := invocationFromEvent(e)
	if err != nil {
		return err
	}
	if !project {
		return nil
	}
	if (data.InvocationKind != "model" && data.InvocationKind != "tool") || (data.Status != "succeeded" && data.Status != "failed") || data.Runtime == "" {
		return errors.New("invalid runtime invocation event")
	}
	_, err = tx.Exec(ctx, `INSERT INTO runtime_invocations(invocation_id,tenant_id,task_id,runtime,invocation_kind,status,runtime_snapshot_id,prompt_version_id,model_profile_version_id,tool_version_id,input_tokens,output_tokens,estimated_cost_usd,latency_ms,data,occurred_at) VALUES($1,$2,$3,$4,$5,$6,NULLIF($7,''),NULLIF($8,''),NULLIF($9,''),NULLIF($10,''),$11,$12,$13,$14,$15,$16) ON CONFLICT (invocation_id) DO NOTHING`, e.MessageID, e.TenantID, e.TaskID, data.Runtime, data.InvocationKind, data.Status, data.RuntimeSnapshotID, data.PromptVersionID, data.ModelProfileVersionID, data.ToolVersionID, data.InputTokens, data.OutputTokens, data.EstimatedCostUSD, data.LatencyMS, e.Data, e.CreatedAt)
	return err
}

func applyEventStatus(ctx context.Context, tx pgx.Tx, e model.Event) error {
	status := ""
	switch e.Type {
	case "task_started":
		status = model.StatusRunning
	case "task_finished":
		var result struct {
			Status string `json:"status"`
		}
		_ = json.Unmarshal(e.Data, &result)
		status = result.Status
		if status == "" {
			status = model.StatusCompleted
		}
		_, err := tx.Exec(ctx, `UPDATE tasks SET status=$3,result=$4,error=NULL,updated_at=now() WHERE tenant_id=$1 AND task_id=$2 AND status<>'cancelled'`, e.TenantID, e.TaskID, status, e.Data)
		return err
	case "task_error":
		var failure struct {
			Error     string `json:"error"`
			Cancelled bool   `json:"cancelled"`
		}
		_ = json.Unmarshal(e.Data, &failure)
		status = model.StatusFailed
		if failure.Cancelled {
			status = model.StatusCancelled
		}
		_, err := tx.Exec(ctx, `UPDATE tasks SET status=$3,error=$4,updated_at=now() WHERE tenant_id=$1 AND task_id=$2`, e.TenantID, e.TaskID, status, failure.Error)
		return err
	}
	if status != "" {
		_, err := tx.Exec(ctx, `UPDATE tasks SET status=$3,updated_at=now() WHERE tenant_id=$1 AND task_id=$2 AND status<>'cancelled'`, e.TenantID, e.TaskID, status)
		return err
	}
	return nil
}

func (s *Store) ClaimOutbox(ctx context.Context, limit int) ([]model.OutboxMessage, error) {
	rows, err := s.pool.Query(ctx, `WITH picked AS (SELECT id FROM outbox WHERE published_at IS NULL AND (locked_until IS NULL OR locked_until<now()) AND (next_attempt_at IS NULL OR next_attempt_at<=now()) ORDER BY id FOR UPDATE SKIP LOCKED LIMIT $1) UPDATE outbox o SET locked_until=now()+interval '30 seconds',attempts=attempts+1 FROM picked WHERE o.id=picked.id RETURNING o.id,o.message_id,o.routing_key,o.payload`, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []model.OutboxMessage
	for rows.Next() {
		var m model.OutboxMessage
		if err := rows.Scan(&m.ID, &m.MessageID, &m.RoutingKey, &m.Payload); err != nil {
			return nil, err
		}
		out = append(out, m)
	}
	return out, rows.Err()
}

func (s *Store) MarkOutboxPublished(ctx context.Context, id int64) error {
	_, err := s.pool.Exec(ctx, `UPDATE outbox SET published_at=now(),locked_until=NULL,next_attempt_at=NULL,last_error=NULL WHERE id=$1`, id)
	return err
}
func (s *Store) MarkOutboxFailed(ctx context.Context, id int64, cause error) error {
	_, err := s.pool.Exec(ctx, `UPDATE outbox SET locked_until=NULL,last_error=$2,next_attempt_at=now() + (power(2, LEAST(attempts,8)) * interval '1 second') WHERE id=$1`, id, cause.Error())
	return err
}

func (s *Store) OperationalStats(ctx context.Context) (OperationalStats, error) {
	var stats OperationalStats
	err := s.pool.QueryRow(ctx, `
		SELECT
			count(*) FILTER (WHERE published_at IS NULL),
			COALESCE(EXTRACT(EPOCH FROM now() - min(created_at) FILTER (WHERE published_at IS NULL)), 0)
		FROM outbox`).Scan(&stats.PendingOutbox, &stats.OldestOutboxSeconds)
	if err != nil {
		return OperationalStats{}, err
	}
	if err = s.pool.QueryRow(ctx, `SELECT count(*) FROM task_events`).Scan(&stats.Events); err != nil {
		return OperationalStats{}, err
	}
	if err = s.pool.QueryRow(ctx, `
		SELECT
			count(*) FILTER (WHERE status='queued'),
			count(*) FILTER (WHERE status='running'),
			count(*) FILTER (WHERE status='awaiting_user'),
			count(*) FILTER (WHERE status='failed')
		FROM tasks`).Scan(&stats.TasksQueued, &stats.TasksRunning, &stats.TasksAwaitingUser, &stats.TasksFailed); err != nil {
		return OperationalStats{}, err
	}
	return stats, nil
}

func (s *Store) Cleanup(ctx context.Context, retention time.Duration) (CleanupStats, error) {
	if retention <= 0 {
		return CleanupStats{}, nil
	}
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return CleanupStats{}, err
	}
	defer tx.Rollback(ctx)
	interval := fmt.Sprintf("%.3f seconds", retention.Seconds())
	var stats CleanupStats
	if tag, err := tx.Exec(ctx, `DELETE FROM worker_inbox WHERE status='done' AND updated_at < now() - $1::interval`, interval); err != nil {
		return CleanupStats{}, err
	} else {
		stats.Inbox = tag.RowsAffected()
	}
	if tag, err := tx.Exec(ctx, `DELETE FROM outbox WHERE published_at IS NOT NULL AND published_at < now() - $1::interval`, interval); err != nil {
		return CleanupStats{}, err
	} else {
		stats.Outbox = tag.RowsAffected()
	}
	if tag, err := tx.Exec(ctx, `DELETE FROM tasks WHERE status IN ('completed','failed','cancelled') AND updated_at < now() - $1::interval`, interval); err != nil {
		return CleanupStats{}, err
	} else {
		stats.Tasks = tag.RowsAffected()
	}
	if err := tx.Commit(ctx); err != nil {
		return CleanupStats{}, err
	}
	return stats, nil
}

func (s *Store) Listen(ctx context.Context, onTask func(string)) error {
	conn, err := s.pool.Acquire(ctx)
	if err != nil {
		return err
	}
	defer conn.Release()
	if _, err = conn.Exec(ctx, `LISTEN agent_room_events`); err != nil {
		return err
	}
	for {
		n, err := conn.Conn().WaitForNotification(ctx)
		if err != nil {
			return err
		}
		onTask(n.Payload)
	}
}

type scanner interface{ Scan(...any) error }

func scanTask(row scanner, t *model.Task) error {
	return row.Scan(&t.ID, &t.TenantID, &t.Status, &t.Request, &t.Result, &t.Error, &t.CreatedAt, &t.UpdatedAt)
}
