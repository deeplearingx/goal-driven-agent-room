package httpapi

import (
	"context"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"
	"unicode/utf8"

	"github.com/agent-room/agent-room/gateway/internal/auth"
	"github.com/agent-room/agent-room/gateway/internal/cache"
	"github.com/agent-room/agent-room/gateway/internal/embeddings"
	"github.com/agent-room/agent-room/gateway/internal/guard"
	"github.com/agent-room/agent-room/gateway/internal/model"
	"github.com/agent-room/agent-room/gateway/internal/rag"
	"github.com/agent-room/agent-room/gateway/internal/secrets"
	"github.com/agent-room/agent-room/gateway/internal/store"
	"github.com/agent-room/agent-room/gateway/internal/webhook"
	"github.com/google/uuid"
	"golang.org/x/time/rate"
)

type Repository interface {
	CreateTask(context.Context, model.Task, model.Command, model.AuditEvent) (model.Task, bool, error)
	GetTask(context.Context, string, string) (model.Task, error)
	ListTasks(context.Context, string, int) ([]model.Task, error)
	ListAuditEvents(context.Context, string, int) ([]model.AuditEvent, error)
	CreateEvalRun(context.Context, model.EvalRun, model.AuditEvent) (model.EvalRun, error)
	ListEvalRuns(context.Context, string, string, string, int) ([]model.EvalRun, error)
	EvalRunPassed(context.Context, string, string, string, string) (bool, error)
	ListInvocations(context.Context, string, string, int) ([]store.Invocation, error)
	RecordAudit(context.Context, model.AuditEvent) error
	RolesForPrincipal(context.Context, string, string) ([]string, error)
	ListRoleBindings(context.Context, string) ([]model.RoleBinding, error)
	ReplaceRoleBindings(context.Context, string, string, string, string, []string, model.AuditEvent) error
	CreatePromptVersion(context.Context, string, string, string, string, string, string, json.RawMessage, model.AuditEvent) (model.PromptVersion, error)
	ListPromptVersions(context.Context, string, int) ([]model.PromptVersion, error)
	UpsertPromptRelease(context.Context, model.PromptRelease, model.AuditEvent) (model.PromptRelease, error)
	ListPromptReleases(context.Context, string, string) ([]model.PromptRelease, error)
	CreateModelProfileVersion(context.Context, string, string, string, string, string, string, string, json.RawMessage, model.AuditEvent) (model.ModelProfileVersion, error)
	ListModelProfileVersions(context.Context, string, int) ([]model.ModelProfileVersion, error)
	CreateProviderAccount(context.Context, model.ProviderAccount, []byte, model.AuditEvent) (model.ProviderAccount, error)
	ListProviderAccounts(context.Context, string, int) ([]model.ProviderAccount, error)
	CreateAgentVersion(context.Context, string, string, string, string, string, json.RawMessage, model.AuditEvent) (model.AgentVersion, error)
	ListAgentVersions(context.Context, string, int) ([]model.AgentVersion, error)
	CreateToolVersion(context.Context, string, string, string, string, string, string, json.RawMessage, model.AuditEvent) (model.ToolVersion, error)
	ListToolVersions(context.Context, string, int) ([]model.ToolVersion, error)
	ListToolApprovals(context.Context, string, int) ([]model.ToolApproval, error)
	DecideToolApproval(context.Context, string, string, string, string, string, model.AuditEvent) (model.ToolApproval, error)
	SearchKnowledge(context.Context, string, string, int) ([]model.KnowledgeChunk, error)
	SearchKnowledgeVector(context.Context, string, string, []float32, int) ([]model.KnowledgeChunk, error)
	EnqueueKnowledgeIngest(context.Context, string, model.Command, model.AuditEvent) error
	GetModelProfileVersion(context.Context, string, string) (model.ModelProfileVersion, error)
	ProviderAccountSecret(context.Context, string, string) (model.ProviderAccount, []byte, error)
	CreateSkillVersion(context.Context, string, string, string, string, string, json.RawMessage, model.AuditEvent) (model.SkillVersion, error)
	ListSkillVersions(context.Context, string, int) ([]model.SkillVersion, error)
	CreateRuntimeSnapshot(context.Context, model.RuntimeSnapshot, model.AuditEvent) (model.RuntimeSnapshot, error)
	GetRuntimeSnapshot(context.Context, string, string) (model.RuntimeSnapshot, error)
	EnqueueResume(context.Context, string, string, string, json.RawMessage, model.AuditEvent) error
	EnqueueCancel(context.Context, string, string, string, model.AuditEvent) error
	EnqueueRetry(context.Context, string, string, string, model.AuditEvent) error
	EventsAfter(context.Context, string, string, int64, int) ([]model.Event, error)
	Ping(context.Context) error
}

type operationalRepository interface {
	OperationalStats(context.Context) (store.OperationalStats, error)
}

type webhookRepository interface {
	CreateWebhookSubscription(context.Context, model.WebhookSubscription, []byte, []byte, model.AuditEvent) (model.WebhookSubscription, error)
	ListWebhookSubscriptions(context.Context, string, int) ([]model.WebhookSubscription, error)
	SetWebhookSubscriptionEnabled(context.Context, string, string, bool, model.AuditEvent) (model.WebhookSubscription, error)
}

type Config struct {
	APIKey, AllowedOrigins string
	RatePerSecond          float64
	RateBurst              int
	MaxTenantLimiters      int
	MaxRequestBytes        int64
	SSEHeartbeat           time.Duration
	WorkspaceRoot          string
	DefaultTenant          string
	TrustTenantHeader      bool
	MetricsPublic          bool
	Authenticator          auth.Authenticator
	SecretProtector        secrets.Protector
	SecretDecryptor        secrets.Decryptor
	EmbeddingClient        EmbeddingClient
	GuardMode              guard.Mode
	SecretKeyReference     string
	EnableEinoRuntime      bool
	WebhookAllowedHosts    []string
	ConfigCache            cache.ConfigCache
	Readiness              func(context.Context) error
}

// EmbeddingClient is implemented by the trusted provider adapter. It is an
// injected dependency so the public HTTP layer never owns provider transport
// construction or serializes decrypted credentials.
type EmbeddingClient interface {
	Embed(context.Context, embeddings.Profile, []byte, []string) ([][]float32, error)
}

type Server struct {
	repo         Repository
	hub          *Hub
	log          *slog.Logger
	cfg          Config
	mux          *http.ServeMux
	limiters     sync.Map
	limiterCount atomic.Int64
	requests     atomic.Uint64
	rejected     atomic.Uint64
	sseClients   atomic.Int64
	admission    durationHistogram
}

var admissionDurationBounds = [...]time.Duration{25 * time.Millisecond, 50 * time.Millisecond, 100 * time.Millisecond, 250 * time.Millisecond, 500 * time.Millisecond, time.Second, 2500 * time.Millisecond, 5 * time.Second}

// durationHistogram is a deliberately label-free Prometheus histogram. Keeping
// tenant, task and URL values out of metric labels prevents cardinality growth
// while still making the admission-path p95/p99 computable for the SLO.
type durationHistogram struct {
	buckets [len(admissionDurationBounds)]atomic.Uint64
	count   atomic.Uint64
	sumUS   atomic.Uint64
}

func (h *durationHistogram) Observe(d time.Duration) {
	if d < 0 {
		return
	}
	for i, bound := range admissionDurationBounds {
		if d <= bound {
			h.buckets[i].Add(1)
		}
	}
	h.count.Add(1)
	h.sumUS.Add(uint64(d.Microseconds()))
}

func (h *durationHistogram) WritePrometheus(w io.Writer, name string) {
	fmt.Fprintf(w, "# TYPE %s histogram\n", name)
	for i, bound := range admissionDurationBounds {
		fmt.Fprintf(w, "%s_bucket{le=\"%.3f\"} %d\n", name, bound.Seconds(), h.buckets[i].Load())
	}
	fmt.Fprintf(w, "%s_bucket{le=\"+Inf\"} %d\n%s_sum %.6f\n%s_count %d\n", name, h.count.Load(), name, float64(h.sumUS.Load())/1_000_000, name, h.count.Load())
}

var safeID = regexp.MustCompile(`^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$`)

func New(repo Repository, hub *Hub, cfg Config, log *slog.Logger) *Server {
	if cfg.DefaultTenant == "" {
		cfg.DefaultTenant = "default"
	}
	if cfg.GuardMode == "" {
		cfg.GuardMode = guard.ModeWarn
	}
	if cfg.MaxTenantLimiters <= 0 {
		cfg.MaxTenantLimiters = 10000
	}
	s := &Server{repo: repo, hub: hub, cfg: cfg, log: log, mux: http.NewServeMux()}
	s.routes()
	return s
}
func (s *Server) Handler() http.Handler { return s.middleware(s.mux) }

func (s *Server) runtimeSnapshot(ctx context.Context, tenantID, snapshotID string) (model.RuntimeSnapshot, error) {
	cacheKey := runtimeSnapshotCacheKey(tenantID, snapshotID)
	if s.cfg.ConfigCache != nil {
		var cached model.RuntimeSnapshot
		if found, err := s.cfg.ConfigCache.Get(ctx, cacheKey, &cached); err != nil {
			s.log.Warn("runtime snapshot cache get failed", "error", err)
		} else if found {
			return cached, nil
		}
	}
	snapshot, err := s.repo.GetRuntimeSnapshot(ctx, tenantID, snapshotID)
	if err != nil {
		return model.RuntimeSnapshot{}, err
	}
	if s.cfg.ConfigCache != nil {
		if err := s.cfg.ConfigCache.Put(ctx, cacheKey, snapshot); err != nil {
			s.log.Warn("runtime snapshot cache put failed", "error", err)
		}
	}
	return snapshot, nil
}

func promptReleaseCacheKey(tenantID, environment string) string {
	return "prompt-release:" + tenantID + ":" + environment
}

func runtimeSnapshotCacheKey(tenantID, snapshotID string) string {
	return "runtime-snapshot:" + tenantID + ":" + snapshotID
}

func (s *Server) routes() {
	s.mux.HandleFunc("GET /healthz", s.health)
	s.mux.HandleFunc("GET /readyz", s.ready)
	s.mux.HandleFunc("GET /metrics", s.metrics)
	s.mux.HandleFunc("GET /api/agent-room/sessions", s.sessions)
	s.mux.HandleFunc("GET /api/v1/audit-events", s.auditEvents)
	s.mux.HandleFunc("GET /api/v1/admin/invocations", s.invocations)
	s.mux.HandleFunc("GET /api/v1/admin/eval-runs", s.evalRuns)
	s.mux.HandleFunc("POST /api/v1/admin/eval-runs", s.createEvalRun)
	s.mux.HandleFunc("GET /api/v1/admin/role-bindings", s.roleBindings)
	s.mux.HandleFunc("PUT /api/v1/admin/role-bindings/{subject}", s.replaceRoleBindings)
	s.mux.HandleFunc("GET /api/v1/admin/prompts", s.promptVersions)
	s.mux.HandleFunc("POST /api/v1/admin/prompts", s.createPromptVersion)
	s.mux.HandleFunc("GET /api/v1/admin/prompt-releases", s.promptReleases)
	s.mux.HandleFunc("PUT /api/v1/admin/prompt-releases/{name}", s.upsertPromptRelease)
	s.mux.HandleFunc("GET /api/v1/admin/model-profiles", s.modelProfileVersions)
	s.mux.HandleFunc("POST /api/v1/admin/model-profiles", s.createModelProfileVersion)
	s.mux.HandleFunc("GET /api/v1/admin/provider-accounts", s.providerAccounts)
	s.mux.HandleFunc("POST /api/v1/admin/provider-accounts", s.createProviderAccount)
	s.mux.HandleFunc("GET /api/v1/admin/agents", s.agentVersions)
	s.mux.HandleFunc("POST /api/v1/admin/agents", s.createAgentVersion)
	s.mux.HandleFunc("GET /api/v1/admin/tools", s.toolVersions)
	s.mux.HandleFunc("POST /api/v1/admin/tools", s.createToolVersion)
	s.mux.HandleFunc("GET /api/v1/admin/tool-approvals", s.toolApprovals)
	s.mux.HandleFunc("POST /api/v1/admin/tool-approvals/{approval_id}/decision", s.decideToolApproval)
	s.mux.HandleFunc("GET /api/v1/knowledge/search", s.searchKnowledge)
	s.mux.HandleFunc("POST /api/v1/knowledge/search", s.searchKnowledgeHybrid)
	s.mux.HandleFunc("POST /api/v1/admin/knowledge/documents", s.enqueueKnowledgeIngest)
	s.mux.HandleFunc("GET /api/v1/admin/skills", s.skillVersions)
	s.mux.HandleFunc("POST /api/v1/admin/skills", s.createSkillVersion)
	s.mux.HandleFunc("POST /api/v1/admin/runtime-snapshots", s.createRuntimeSnapshot)
	s.mux.HandleFunc("GET /api/v1/admin/webhook-subscriptions", s.webhookSubscriptions)
	s.mux.HandleFunc("POST /api/v1/admin/webhook-subscriptions", s.createWebhookSubscription)
	s.mux.HandleFunc("PUT /api/v1/admin/webhook-subscriptions/{subscription_id}", s.setWebhookSubscriptionEnabled)
	s.mux.HandleFunc("GET /workspace/files", s.workspaceFiles)
	s.mux.HandleFunc("GET /workspace/file", s.workspaceFile)
	s.mux.HandleFunc("GET /workspace/preview/{task_id}/{path...}", s.workspacePreview)
	s.mux.HandleFunc("POST /api/v1/tasks", s.create)
	s.mux.HandleFunc("GET /api/v1/tasks/{task_id}", s.get)
	s.mux.HandleFunc("GET /api/v1/tasks/{task_id}/events", s.events)
	s.mux.HandleFunc("POST /api/v1/tasks/{task_id}/resume", s.resume)
	s.mux.HandleFunc("POST /api/v1/tasks/{task_id}/cancel", s.cancel)
	s.mux.HandleFunc("POST /api/v1/tasks/{task_id}/retry", s.retry)
	// Compatibility routes used by the existing React client.
	s.mux.HandleFunc("POST /tasks/stream", s.createAndStream)
	s.mux.HandleFunc("GET /tasks/{task_id}", s.get)
	s.mux.HandleFunc("GET /tasks/{task_id}/events", s.events)
	s.mux.HandleFunc("POST /tasks/{task_id}/resume", s.resume)
	s.mux.HandleFunc("POST /tasks/{task_id}/cancel", s.cancel)
}

func (s *Server) middleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		observeAdmission := r.Method == http.MethodPost && r.URL.Path == "/api/v1/tasks"
		startedAt := time.Now()
		if observeAdmission {
			defer func() { s.admission.Observe(time.Since(startedAt)) }()
		}
		requestID := r.Header.Get("X-Request-ID")
		if requestID == "" {
			requestID = uuid.NewString()
		}
		w.Header().Set("X-Request-ID", requestID)
		w.Header().Set("X-Content-Type-Options", "nosniff")
		w.Header().Set("Referrer-Policy", "no-referrer")
		if origin := r.Header.Get("Origin"); origin != "" && originAllowed(origin, s.cfg.AllowedOrigins) {
			w.Header().Set("Access-Control-Allow-Origin", origin)
			w.Header().Set("Vary", "Origin")
			w.Header().Set("Access-Control-Allow-Headers", "Authorization,Content-Type,Idempotency-Key,X-Tenant-ID,X-Request-ID")
			w.Header().Set("Access-Control-Allow-Methods", "GET,POST,PUT,OPTIONS")
		}
		if r.Method == http.MethodOptions {
			w.WriteHeader(http.StatusNoContent)
			return
		}
		if strings.HasPrefix(r.URL.Path, "/healthz") || strings.HasPrefix(r.URL.Path, "/readyz") || (s.cfg.MetricsPublic && strings.HasPrefix(r.URL.Path, "/metrics")) {
			next.ServeHTTP(w, r)
			return
		}
		provided := strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer ")
		identity := auth.Identity{}
		if s.cfg.Authenticator != nil {
			var err error
			identity, err = s.cfg.Authenticator.Authenticate(r.Context(), provided)
			if err != nil {
				s.rejected.Add(1)
				problem(w, http.StatusUnauthorized, "unauthorized", "missing or invalid bearer token")
				return
			}
		} else if s.cfg.APIKey != "" {
			if subtle.ConstantTimeCompare([]byte(provided), []byte(s.cfg.APIKey)) != 1 {
				s.rejected.Add(1)
				problem(w, http.StatusUnauthorized, "unauthorized", "missing or invalid bearer token")
				return
			}
		}
		tenant := s.cfg.DefaultTenant
		if identity.TenantID != "" {
			tenant = identity.TenantID
		} else if s.cfg.TrustTenantHeader && r.Header.Get("X-Tenant-ID") != "" {
			tenant = r.Header.Get("X-Tenant-ID")
		}
		if !safeID.MatchString(tenant) {
			problem(w, http.StatusBadRequest, "invalid_tenant", "invalid X-Tenant-ID")
			return
		}
		limiterValue, loaded := s.limiters.LoadOrStore(tenant, rate.NewLimiter(rate.Limit(s.cfg.RatePerSecond), s.cfg.RateBurst))
		if !loaded && s.limiterCount.Add(1) > int64(s.cfg.MaxTenantLimiters) {
			s.limiters.Delete(tenant)
			s.limiterCount.Add(-1)
			s.rejected.Add(1)
			problem(w, http.StatusServiceUnavailable, "rate_limiter_capacity", "tenant limiter capacity reached")
			return
		}
		if !limiterValue.(*rate.Limiter).Allow() {
			s.rejected.Add(1)
			w.Header().Set("Retry-After", "1")
			problem(w, http.StatusTooManyRequests, "rate_limited", "tenant request rate exceeded")
			return
		}
		actorID := "anonymous:development"
		if identity.Subject != "" {
			storedRoles, err := s.repo.RolesForPrincipal(r.Context(), tenant, identity.Subject)
			if err != nil {
				s.log.Error("resolve principal roles failed", "error", err)
				problem(w, http.StatusInternalServerError, "internal_error", "could not resolve principal roles")
				return
			}
			identity.Roles = mergeRoles(identity.Roles, storedRoles)
			actorID = identity.Subject
			if !allowed(identity.Roles, r.Method) {
				s.rejected.Add(1)
				problem(w, http.StatusForbidden, "forbidden", "principal lacks required role")
				return
			}
		} else if s.cfg.APIKey != "" {
			actorID = "service:api-key"
		}
		ctx := context.WithValue(r.Context(), tenantKey{}, tenant)
		ctx = context.WithValue(ctx, requestIDKey{}, requestID)
		ctx = context.WithValue(ctx, actorKey{}, actorID)
		ctx = context.WithValue(ctx, rolesKey{}, identity.Roles)
		s.requests.Add(1)
		next.ServeHTTP(w, r.WithContext(ctx))
	})
}

func allowed(roles []string, method string) bool {
	for _, role := range roles {
		if role == "tenant_admin" || role == "service" || (method == http.MethodGet && (role == "operator" || role == "viewer")) || (method != http.MethodGet && role == "operator") {
			return true
		}
	}
	return false
}

func mergeRoles(primary, secondary []string) []string {
	seen := make(map[string]struct{}, len(primary)+len(secondary))
	merged := make([]string, 0, len(primary)+len(secondary))
	for _, role := range append(primary, secondary...) {
		if _, exists := seen[role]; !exists {
			seen[role] = struct{}{}
			merged = append(merged, role)
		}
	}
	return merged
}

func hasRole(roles []string, expected string) bool {
	for _, role := range roles {
		if role == expected {
			return true
		}
	}
	return false
}

type tenantKey struct{}
type requestIDKey struct{}
type actorKey struct{}
type rolesKey struct{}

func tenant(r *http.Request) string {
	v, _ := r.Context().Value(tenantKey{}).(string)
	if v == "" {
		return "default"
	}
	return v
}

func requestID(r *http.Request) string {
	v, _ := r.Context().Value(requestIDKey{}).(string)
	return v
}

func actorID(r *http.Request) string {
	v, _ := r.Context().Value(actorKey{}).(string)
	return v
}

func roles(r *http.Request) []string {
	v, _ := r.Context().Value(rolesKey{}).([]string)
	return v
}

func auditEvent(r *http.Request, action, resourceType, resourceID string) model.AuditEvent {
	return model.AuditEvent{
		ID:           uuid.NewString(),
		TenantID:     tenant(r),
		ActorID:      actorID(r),
		Action:       action,
		ResourceType: resourceType,
		ResourceID:   resourceID,
		RequestID:    requestID(r),
		Data:         json.RawMessage(`{}`),
		OccurredAt:   time.Now().UTC(),
	}
}

func (s *Server) create(w http.ResponseWriter, r *http.Request) {
	task, _, ok := s.createTask(w, r)
	if ok {
		writeTask(w, http.StatusAccepted, task)
	}
}
func (s *Server) createTask(w http.ResponseWriter, r *http.Request) (model.Task, bool, bool) {
	var req model.CreateTask
	if !decode(w, r, &req, s.cfg.MaxRequestBytes) {
		return model.Task{}, false, false
	}
	req.Title = strings.TrimSpace(req.Title)
	req.Description = strings.TrimSpace(req.Description)
	if req.Title == "" || len(req.Title) > 200 || req.Description == "" || len(req.Description) > 100000 {
		problem(w, http.StatusBadRequest, "invalid_request", "title (1..200) and description (1..100000) are required")
		return model.Task{}, false, false
	}
	var guardFinding *guard.Finding
	if guardFinding = guard.Check(s.cfg.GuardMode, req.Title+"\n"+req.Description); guardFinding != nil && s.cfg.GuardMode == guard.ModeBlock {
		audit := auditEvent(r, "guardrail.blocked", "task_input", "new")
		audit.Data, _ = json.Marshal(map[string]any{"guardrail": guardFinding, "mode": s.cfg.GuardMode})
		if err := s.repo.RecordAudit(r.Context(), audit); err != nil {
			s.log.Error("record guardrail rejection audit failed", "error", err)
		}
		problem(w, http.StatusUnprocessableEntity, "guardrail_blocked", "task input matched a security guardrail")
		return model.Task{}, false, false
	}
	maxRevisions := 2
	if req.MaxRevisions != nil {
		maxRevisions = *req.MaxRevisions
	}
	if maxRevisions < 0 || maxRevisions > 10 {
		problem(w, http.StatusBadRequest, "invalid_request", "max_revisions must be between 0 and 10")
		return model.Task{}, false, false
	}
	maxIterations := 10
	if req.MaxIterations != nil {
		maxIterations = *req.MaxIterations
	}
	if maxIterations < 1 || maxIterations > 50 {
		problem(w, http.StatusBadRequest, "invalid_request", "max_iterations must be between 1 and 50")
		return model.Task{}, false, false
	}
	if req.Graph != "" && req.Graph != "full_react" && req.Graph != "goal" {
		problem(w, http.StatusBadRequest, "invalid_request", "graph must be full_react, goal, or omitted")
		return model.Task{}, false, false
	}
	req.ExecutionRuntime = strings.TrimSpace(req.ExecutionRuntime)
	if req.ExecutionRuntime == "" {
		req.ExecutionRuntime = "python_langgraph"
	}
	if req.ExecutionRuntime != "python_langgraph" && req.ExecutionRuntime != "go_eino" {
		problem(w, http.StatusBadRequest, "invalid_request", "execution_runtime must be python_langgraph or go_eino")
		return model.Task{}, false, false
	}
	if req.ExecutionRuntime == "go_eino" && !s.cfg.EnableEinoRuntime {
		problem(w, http.StatusConflict, "runtime_disabled", "go_eino runtime is not enabled")
		return model.Task{}, false, false
	}
	var snapshot model.RuntimeSnapshot
	if req.RuntimeSnapshotID != "" {
		if !safeID.MatchString(req.RuntimeSnapshotID) {
			problem(w, http.StatusBadRequest, "invalid_request", "runtime_snapshot_id must be a safe identifier")
			return model.Task{}, false, false
		}
		var err error
		snapshot, err = s.runtimeSnapshot(r.Context(), tenant(r), req.RuntimeSnapshotID)
		if errors.Is(err, store.ErrNotFound) {
			problem(w, http.StatusNotFound, "not_found", "runtime snapshot not found")
			return model.Task{}, false, false
		}
		if err != nil {
			s.log.Error("load runtime snapshot failed", "error", err)
			problem(w, http.StatusInternalServerError, "internal_error", "could not load runtime snapshot")
			return model.Task{}, false, false
		}
	}
	if key := strings.TrimSpace(r.Header.Get("Idempotency-Key")); len(key) > 128 {
		problem(w, http.StatusBadRequest, "invalid_request", "Idempotency-Key must be at most 128 characters")
		return model.Task{}, false, false
	}
	tenantID := tenant(r)
	requestJSON, _ := json.Marshal(req)
	taskID := "task-" + strings.ReplaceAll(uuid.NewString(), "-", "")[:12]
	messageID := uuid.NewString()
	payload := map[string]any{"title": req.Title, "description": req.Description, "max_revisions": maxRevisions, "metadata": req.Metadata, "tenant_id": tenantID, "max_iterations": maxIterations, "verify_files": req.VerifyFiles, "execution_runtime": req.ExecutionRuntime}
	if req.Graph != "" {
		payload["graph"] = req.Graph
	}
	if req.VerifyCommand != "" {
		payload["verify_command"] = req.VerifyCommand
	}
	if snapshot.ID != "" {
		payload["runtime_snapshot"] = snapshot
	}
	payloadJSON, _ := json.Marshal(payload)
	t := model.Task{ID: taskID, TenantID: tenantID, Status: model.StatusQueued, Request: requestJSON, IdempotencyKey: strings.TrimSpace(r.Header.Get("Idempotency-Key"))}
	cmd := model.Command{MessageID: messageID, Type: "run", TaskID: taskID, TenantID: tenantID, Payload: payloadJSON, CreatedAt: time.Now().UTC()}
	audit := auditEvent(r, "task.create", "task", taskID)
	if guardFinding != nil {
		audit.Data, _ = json.Marshal(map[string]any{"guardrail": guardFinding, "mode": s.cfg.GuardMode})
	}
	created, wasCreated, err := s.repo.CreateTask(r.Context(), t, cmd, audit)
	if err != nil {
		s.log.Error("create task failed", "error", err)
		problem(w, http.StatusInternalServerError, "internal_error", "could not create task")
		return model.Task{}, false, false
	}
	return created, wasCreated, true
}

func (s *Server) createAndStream(w http.ResponseWriter, r *http.Request) {
	task, _, ok := s.createTask(w, r)
	if !ok {
		return
	}
	s.stream(w, r, task.ID, 0)
}
func (s *Server) get(w http.ResponseWriter, r *http.Request) {
	t, err := s.repo.GetTask(r.Context(), tenant(r), r.PathValue("task_id"))
	if errors.Is(err, store.ErrNotFound) {
		problem(w, http.StatusNotFound, "not_found", "task not found")
		return
	}
	if err != nil {
		problem(w, http.StatusInternalServerError, "internal_error", "could not load task")
		return
	}
	writeTask(w, http.StatusOK, t)
}

func (s *Server) sessions(w http.ResponseWriter, r *http.Request) {
	tasks, err := s.repo.ListTasks(r.Context(), tenant(r), 100)
	if err != nil {
		problem(w, http.StatusInternalServerError, "internal_error", "could not list tasks")
		return
	}
	items := make([]map[string]any, 0, len(tasks))
	for _, task := range tasks {
		var request struct {
			Title string `json:"title"`
		}
		_ = json.Unmarshal(task.Request, &request)
		items = append(items, map[string]any{"id": task.ID, "name": request.Title, "status": task.Status, "updated_at": task.UpdatedAt})
	}
	jsonResponse(w, http.StatusOK, map[string]any{"sessions": items})
}

func (s *Server) auditEvents(w http.ResponseWriter, r *http.Request) {
	limit := 100
	if raw := r.URL.Query().Get("limit"); raw != "" {
		parsed, err := strconv.Atoi(raw)
		if err != nil || parsed < 1 || parsed > 500 {
			problem(w, http.StatusBadRequest, "invalid_request", "limit must be between 1 and 500")
			return
		}
		limit = parsed
	}
	events, err := s.repo.ListAuditEvents(r.Context(), tenant(r), limit)
	if err != nil {
		s.log.Error("list audit events failed", "error", err)
		problem(w, http.StatusInternalServerError, "internal_error", "could not load audit events")
		return
	}
	jsonResponse(w, http.StatusOK, map[string]any{"events": events})
}

func (s *Server) invocations(w http.ResponseWriter, r *http.Request) {
	if !hasRole(roles(r), "tenant_admin") {
		problem(w, http.StatusForbidden, "forbidden", "tenant_admin role is required")
		return
	}
	limit := 100
	if raw := r.URL.Query().Get("limit"); raw != "" {
		parsed, err := strconv.Atoi(raw)
		if err != nil || parsed < 1 || parsed > 500 {
			problem(w, http.StatusBadRequest, "invalid_request", "limit must be between 1 and 500")
			return
		}
		limit = parsed
	}
	taskID := strings.TrimSpace(r.URL.Query().Get("task_id"))
	if taskID != "" && !safeID.MatchString(taskID) {
		problem(w, http.StatusBadRequest, "invalid_request", "task_id must be a safe identifier")
		return
	}
	items, err := s.repo.ListInvocations(r.Context(), tenant(r), taskID, limit)
	if err != nil {
		s.log.Error("list runtime invocations failed", "error", err)
		problem(w, http.StatusInternalServerError, "internal_error", "could not load runtime invocations")
		return
	}
	jsonResponse(w, http.StatusOK, map[string]any{"invocations": items})
}

func (s *Server) evalRuns(w http.ResponseWriter, r *http.Request) {
	if !hasRole(roles(r), "tenant_admin") {
		problem(w, http.StatusForbidden, "forbidden", "tenant_admin role is required")
		return
	}
	limit := 100
	if raw := r.URL.Query().Get("limit"); raw != "" {
		parsed, err := strconv.Atoi(raw)
		if err != nil || parsed < 1 || parsed > 500 {
			problem(w, http.StatusBadRequest, "invalid_request", "limit must be between 1 and 500")
			return
		}
		limit = parsed
	}
	targetKind := strings.TrimSpace(r.URL.Query().Get("target_kind"))
	targetVersionID := strings.TrimSpace(r.URL.Query().Get("target_version_id"))
	if targetKind != "" && targetKind != "prompt" && targetKind != "agent" && targetKind != "runtime" {
		problem(w, http.StatusBadRequest, "invalid_request", "target_kind must be prompt, agent, or runtime")
		return
	}
	if targetVersionID != "" && !safeID.MatchString(targetVersionID) {
		problem(w, http.StatusBadRequest, "invalid_request", "target_version_id must be a safe identifier")
		return
	}
	items, err := s.repo.ListEvalRuns(r.Context(), tenant(r), targetKind, targetVersionID, limit)
	if err != nil {
		s.log.Error("list eval runs failed", "error", err)
		problem(w, http.StatusInternalServerError, "internal_error", "could not load eval runs")
		return
	}
	jsonResponse(w, http.StatusOK, map[string]any{"eval_runs": items})
}

func (s *Server) createEvalRun(w http.ResponseWriter, r *http.Request) {
	if !hasRole(roles(r), "tenant_admin") {
		problem(w, http.StatusForbidden, "forbidden", "tenant_admin role is required")
		return
	}
	var body struct {
		SuiteName       string          `json:"suite_name"`
		SuiteVersion    string          `json:"suite_version"`
		TargetKind      string          `json:"target_kind"`
		TargetVersionID string          `json:"target_version_id"`
		Status          string          `json:"status"`
		Score           *float64        `json:"score"`
		Threshold       *float64        `json:"threshold"`
		Summary         json.RawMessage `json:"summary"`
		SourceRef       string          `json:"source_ref"`
	}
	if !decode(w, r, &body, s.cfg.MaxRequestBytes) {
		return
	}
	body.SuiteName, body.SuiteVersion, body.TargetKind, body.TargetVersionID, body.Status, body.SourceRef = strings.TrimSpace(body.SuiteName), strings.TrimSpace(body.SuiteVersion), strings.TrimSpace(body.TargetKind), strings.TrimSpace(body.TargetVersionID), strings.TrimSpace(body.Status), strings.TrimSpace(body.SourceRef)
	if !safeID.MatchString(body.SuiteName) || !safeID.MatchString(body.SuiteVersion) || !safeID.MatchString(body.TargetVersionID) || (body.TargetKind != "prompt" && body.TargetKind != "agent" && body.TargetKind != "runtime") || (body.Status != "passed" && body.Status != "failed") || len(body.SourceRef) > 500 || (len(body.Summary) > 0 && !json.Valid(body.Summary)) {
		problem(w, http.StatusBadRequest, "invalid_request", "invalid eval run fields")
		return
	}
	if body.Score != nil && (*body.Score < 0 || *body.Score > 1) || body.Threshold != nil && (*body.Threshold < 0 || *body.Threshold > 1) {
		problem(w, http.StatusBadRequest, "invalid_request", "score and threshold must be 0..1")
		return
	}
	if len(body.Summary) == 0 {
		body.Summary = json.RawMessage(`{}`)
	}
	run := model.EvalRun{ID: uuid.NewString(), TenantID: tenant(r), SuiteName: body.SuiteName, SuiteVersion: body.SuiteVersion, TargetKind: body.TargetKind, TargetVersionID: body.TargetVersionID, Status: body.Status, Score: body.Score, Threshold: body.Threshold, Summary: body.Summary, SourceRef: body.SourceRef, CreatedBy: actorID(r)}
	created, err := s.repo.CreateEvalRun(r.Context(), run, auditEvent(r, "eval.run.create", "eval_run", run.ID))
	if err != nil {
		s.log.Error("create eval run failed", "error", err)
		problem(w, http.StatusInternalServerError, "internal_error", "could not create eval run")
		return
	}
	jsonResponse(w, http.StatusCreated, created)
}

func (s *Server) webhookSubscriptions(w http.ResponseWriter, r *http.Request) {
	if !hasRole(roles(r), "tenant_admin") {
		problem(w, http.StatusForbidden, "forbidden", "tenant_admin role is required")
		return
	}
	repo, ok := s.repo.(webhookRepository)
	if !ok {
		problem(w, http.StatusNotImplemented, "not_implemented", "webhook subscriptions are unavailable")
		return
	}
	limit := 100
	if raw := r.URL.Query().Get("limit"); raw != "" {
		parsed, err := strconv.Atoi(raw)
		if err != nil || parsed < 1 || parsed > 500 {
			problem(w, http.StatusBadRequest, "invalid_request", "limit must be between 1 and 500")
			return
		}
		limit = parsed
	}
	items, err := repo.ListWebhookSubscriptions(r.Context(), tenant(r), limit)
	if err != nil {
		s.log.Error("list webhook subscriptions failed", "error", err)
		problem(w, http.StatusInternalServerError, "internal_error", "could not load webhook subscriptions")
		return
	}
	jsonResponse(w, http.StatusOK, map[string]any{"webhook_subscriptions": items})
}

func (s *Server) createWebhookSubscription(w http.ResponseWriter, r *http.Request) {
	if !hasRole(roles(r), "tenant_admin") {
		problem(w, http.StatusForbidden, "forbidden", "tenant_admin role is required")
		return
	}
	repo, ok := s.repo.(webhookRepository)
	if !ok {
		problem(w, http.StatusNotImplemented, "not_implemented", "webhook subscriptions are unavailable")
		return
	}
	if s.cfg.SecretProtector == nil {
		problem(w, http.StatusConflict, "encryption_unavailable", "configuration encryption must be configured for webhook subscriptions")
		return
	}
	var body struct {
		Name          string   `json:"name"`
		Kind          string   `json:"kind"`
		EventTypes    []string `json:"event_types"`
		EndpointURL   string   `json:"endpoint_url"`
		SigningSecret string   `json:"signing_secret"`
		Enabled       *bool    `json:"enabled"`
	}
	if !decode(w, r, &body, s.cfg.MaxRequestBytes) {
		return
	}
	body.Name, body.Kind, body.EndpointURL = strings.TrimSpace(body.Name), strings.TrimSpace(body.Kind), strings.TrimSpace(body.EndpointURL)
	if utf8.RuneCountInString(body.Name) < 1 || utf8.RuneCountInString(body.Name) > 100 || len(body.EventTypes) == 0 || len(body.EventTypes) > 32 {
		problem(w, http.StatusBadRequest, "invalid_request", "name must be 1..100 characters and event_types must contain 1..32 values")
		return
	}
	seen := make(map[string]struct{}, len(body.EventTypes))
	for i, eventType := range body.EventTypes {
		eventType = strings.TrimSpace(eventType)
		if !webhookEventTypeAllowed(eventType) {
			problem(w, http.StatusBadRequest, "invalid_request", "event_types contains an unsupported event")
			return
		}
		if _, exists := seen[eventType]; exists {
			problem(w, http.StatusBadRequest, "invalid_request", "event_types must not contain duplicates")
			return
		}
		seen[eventType] = struct{}{}
		body.EventTypes[i] = eventType
	}
	endpoint, err := webhook.ValidateEndpoint(body.EndpointURL, body.Kind, s.cfg.WebhookAllowedHosts)
	if err != nil {
		problem(w, http.StatusBadRequest, "invalid_request", err.Error())
		return
	}
	endpointEncrypted, err := s.cfg.SecretProtector.Encrypt([]byte(endpoint.String()))
	if err != nil {
		s.log.Error("encrypt webhook endpoint failed", "error", err)
		problem(w, http.StatusInternalServerError, "internal_error", "could not store webhook subscription")
		return
	}
	secret := []byte(strings.TrimSpace(body.SigningSecret))
	secretEncrypted, err := s.cfg.SecretProtector.Encrypt(secret)
	clear(secret)
	if err != nil {
		s.log.Error("encrypt webhook signing secret failed", "error", err)
		problem(w, http.StatusInternalServerError, "internal_error", "could not store webhook subscription")
		return
	}
	enabled := true
	if body.Enabled != nil {
		enabled = *body.Enabled
	}
	subscription := model.WebhookSubscription{ID: uuid.NewString(), TenantID: tenant(r), Name: body.Name, Kind: body.Kind, EventTypes: body.EventTypes, EndpointHost: endpoint.Hostname(), Enabled: enabled, CreatedBy: actorID(r)}
	created, err := repo.CreateWebhookSubscription(r.Context(), subscription, endpointEncrypted, secretEncrypted, auditEvent(r, "webhook_subscription.create", "webhook_subscription", subscription.ID))
	if err != nil {
		s.log.Error("create webhook subscription failed", "error", err)
		problem(w, http.StatusInternalServerError, "internal_error", "could not create webhook subscription")
		return
	}
	jsonResponse(w, http.StatusCreated, created)
}

func (s *Server) setWebhookSubscriptionEnabled(w http.ResponseWriter, r *http.Request) {
	if !hasRole(roles(r), "tenant_admin") {
		problem(w, http.StatusForbidden, "forbidden", "tenant_admin role is required")
		return
	}
	repo, ok := s.repo.(webhookRepository)
	if !ok {
		problem(w, http.StatusNotImplemented, "not_implemented", "webhook subscriptions are unavailable")
		return
	}
	subscriptionID := r.PathValue("subscription_id")
	if !safeID.MatchString(subscriptionID) {
		problem(w, http.StatusBadRequest, "invalid_request", "subscription_id must be a safe identifier")
		return
	}
	var body struct {
		Enabled *bool `json:"enabled"`
	}
	if !decode(w, r, &body, s.cfg.MaxRequestBytes) {
		return
	}
	if body.Enabled == nil {
		problem(w, http.StatusBadRequest, "invalid_request", "enabled is required")
		return
	}
	updated, err := repo.SetWebhookSubscriptionEnabled(r.Context(), tenant(r), subscriptionID, *body.Enabled, auditEvent(r, "webhook_subscription.set_enabled", "webhook_subscription", subscriptionID))
	if errors.Is(err, store.ErrNotFound) {
		problem(w, http.StatusNotFound, "not_found", "webhook subscription not found")
		return
	}
	if err != nil {
		s.log.Error("update webhook subscription failed", "error", err)
		problem(w, http.StatusInternalServerError, "internal_error", "could not update webhook subscription")
		return
	}
	jsonResponse(w, http.StatusOK, updated)
}

func webhookEventTypeAllowed(eventType string) bool {
	switch eventType {
	case "task_started", "task_finished", "task_error", "usage", "runtime_invocation", "tool_call", "tool_result", "verification_completed", "supervisor_decided":
		return true
	default:
		return false
	}
}

func clear(bytes []byte) {
	for i := range bytes {
		bytes[i] = 0
	}
}

func (s *Server) roleBindings(w http.ResponseWriter, r *http.Request) {
	if !hasRole(roles(r), "tenant_admin") {
		problem(w, http.StatusForbidden, "forbidden", "tenant_admin role is required")
		return
	}
	bindings, err := s.repo.ListRoleBindings(r.Context(), tenant(r))
	if err != nil {
		problem(w, http.StatusInternalServerError, "internal_error", "could not load role bindings")
		return
	}
	jsonResponse(w, http.StatusOK, map[string]any{"role_bindings": bindings})
}

func (s *Server) replaceRoleBindings(w http.ResponseWriter, r *http.Request) {
	if !hasRole(roles(r), "tenant_admin") {
		problem(w, http.StatusForbidden, "forbidden", "tenant_admin role is required")
		return
	}
	var body struct {
		Kind  string   `json:"kind"`
		Roles []string `json:"roles"`
	}
	if !decode(w, r, &body, s.cfg.MaxRequestBytes) {
		return
	}
	subject := strings.TrimSpace(r.PathValue("subject"))
	body.Kind = strings.TrimSpace(body.Kind)
	if subject == "" || (body.Kind != "user" && body.Kind != "service") || len(body.Roles) == 0 || len(body.Roles) > 4 {
		problem(w, http.StatusBadRequest, "invalid_request", "subject, kind and roles are required")
		return
	}
	for _, role := range body.Roles {
		if !hasRole([]string{"tenant_admin", "operator", "viewer", "service"}, role) {
			problem(w, http.StatusBadRequest, "invalid_request", "unknown role")
			return
		}
	}
	audit := auditEvent(r, "role_binding.replace", "principal", subject)
	if err := s.repo.ReplaceRoleBindings(r.Context(), tenant(r), uuid.NewString(), subject, body.Kind, body.Roles, audit); err != nil {
		s.log.Error("replace role bindings failed", "error", err)
		problem(w, http.StatusInternalServerError, "internal_error", "could not replace role bindings")
		return
	}
	jsonResponse(w, http.StatusOK, map[string]any{"subject": subject, "roles": body.Roles})
}

func (s *Server) promptVersions(w http.ResponseWriter, r *http.Request) {
	if !hasRole(roles(r), "tenant_admin") {
		problem(w, http.StatusForbidden, "forbidden", "tenant_admin role is required")
		return
	}
	versions, err := s.repo.ListPromptVersions(r.Context(), tenant(r), 100)
	if err != nil {
		problem(w, http.StatusInternalServerError, "internal_error", "could not load prompt versions")
		return
	}
	jsonResponse(w, http.StatusOK, map[string]any{"prompt_versions": versions})
}

func (s *Server) createPromptVersion(w http.ResponseWriter, r *http.Request) {
	if !hasRole(roles(r), "tenant_admin") {
		problem(w, http.StatusForbidden, "forbidden", "tenant_admin role is required")
		return
	}
	var body struct {
		Name      string          `json:"name"`
		Content   string          `json:"content"`
		Variables json.RawMessage `json:"variables"`
	}
	if !decode(w, r, &body, s.cfg.MaxRequestBytes) {
		return
	}
	body.Name = strings.TrimSpace(body.Name)
	if !safeID.MatchString(body.Name) || strings.TrimSpace(body.Content) == "" || len(body.Content) > 100000 {
		problem(w, http.StatusBadRequest, "invalid_request", "name must be a safe identifier and content must be 1..100000 characters")
		return
	}
	if len(body.Variables) > 0 && !json.Valid(body.Variables) {
		problem(w, http.StatusBadRequest, "invalid_request", "variables must be valid JSON")
		return
	}
	audit := auditEvent(r, "prompt.version.create", "prompt", body.Name)
	version, err := s.repo.CreatePromptVersion(r.Context(), tenant(r), uuid.NewString(), uuid.NewString(), body.Name, body.Content, actorID(r), body.Variables, audit)
	if err != nil {
		if strings.Contains(err.Error(), "duplicate key") {
			problem(w, http.StatusConflict, "duplicate_version", "an identical prompt version already exists")
			return
		}
		s.log.Error("create prompt version failed", "error", err)
		problem(w, http.StatusInternalServerError, "internal_error", "could not create prompt version")
		return
	}
	jsonResponse(w, http.StatusCreated, version)
}

func (s *Server) promptReleases(w http.ResponseWriter, r *http.Request) {
	if !hasRole(roles(r), "tenant_admin") {
		problem(w, http.StatusForbidden, "forbidden", "tenant_admin role is required")
		return
	}
	environment := strings.TrimSpace(r.URL.Query().Get("environment"))
	if environment != "" && !safeID.MatchString(environment) {
		problem(w, http.StatusBadRequest, "invalid_request", "environment must be a safe identifier")
		return
	}
	cacheKey := promptReleaseCacheKey(tenant(r), environment)
	if s.cfg.ConfigCache != nil {
		var cached []model.PromptRelease
		if found, cacheErr := s.cfg.ConfigCache.Get(r.Context(), cacheKey, &cached); cacheErr != nil {
			s.log.Warn("prompt release cache get failed", "error", cacheErr)
		} else if found {
			jsonResponse(w, http.StatusOK, map[string]any{"prompt_releases": cached})
			return
		}
	}
	items, err := s.repo.ListPromptReleases(r.Context(), tenant(r), environment)
	if err != nil {
		s.log.Error("load prompt releases failed", "error", err)
		problem(w, http.StatusInternalServerError, "internal_error", "could not load prompt releases")
		return
	}
	if s.cfg.ConfigCache != nil {
		if cacheErr := s.cfg.ConfigCache.Put(r.Context(), cacheKey, items); cacheErr != nil {
			s.log.Warn("prompt release cache put failed", "error", cacheErr)
		}
	}
	jsonResponse(w, http.StatusOK, map[string]any{"prompt_releases": items})
}

func (s *Server) upsertPromptRelease(w http.ResponseWriter, r *http.Request) {
	if !hasRole(roles(r), "tenant_admin") {
		problem(w, http.StatusForbidden, "forbidden", "tenant_admin role is required")
		return
	}
	name := strings.TrimSpace(r.PathValue("name"))
	var body struct {
		Environment        string `json:"environment"`
		BaselineVersionID  string `json:"baseline_version_id"`
		CandidateVersionID string `json:"candidate_version_id,omitempty"`
		CandidateWeight    int    `json:"candidate_weight"`
		EvalRunID          string `json:"eval_run_id,omitempty"`
	}
	if !decode(w, r, &body, s.cfg.MaxRequestBytes) {
		return
	}
	body.Environment, body.BaselineVersionID, body.CandidateVersionID, body.EvalRunID = strings.TrimSpace(body.Environment), strings.TrimSpace(body.BaselineVersionID), strings.TrimSpace(body.CandidateVersionID), strings.TrimSpace(body.EvalRunID)
	if !safeID.MatchString(name) || !safeID.MatchString(body.Environment) || !safeID.MatchString(body.BaselineVersionID) || (body.CandidateVersionID != "" && !safeID.MatchString(body.CandidateVersionID)) || (body.EvalRunID != "" && !safeID.MatchString(body.EvalRunID)) || body.CandidateWeight < 0 || body.CandidateWeight > 100 || (body.CandidateVersionID == "" && body.CandidateWeight != 0) {
		problem(w, http.StatusBadRequest, "invalid_request", "invalid prompt release configuration")
		return
	}
	if body.EvalRunID != "" {
		passed, err := s.repo.EvalRunPassed(r.Context(), tenant(r), body.EvalRunID, "prompt", body.BaselineVersionID)
		if err != nil {
			s.log.Error("validate eval gate failed", "error", err)
			problem(w, http.StatusInternalServerError, "internal_error", "could not validate eval gate")
			return
		}
		if !passed {
			problem(w, http.StatusConflict, "eval_gate_failed", "eval run is missing, failed, or targets a different prompt version")
			return
		}
	}
	release := model.PromptRelease{TenantID: tenant(r), PromptName: name, Environment: body.Environment, BaselineVersionID: body.BaselineVersionID, CandidateVersionID: body.CandidateVersionID, CandidateWeight: body.CandidateWeight, UpdatedBy: actorID(r)}
	created, err := s.repo.UpsertPromptRelease(r.Context(), release, auditEvent(r, "prompt.release.upsert", "prompt", name))
	if errors.Is(err, store.ErrNotFound) {
		problem(w, http.StatusNotFound, "not_found", "prompt or version does not belong to this tenant")
		return
	}
	if err != nil {
		s.log.Error("upsert prompt release failed", "error", err)
		problem(w, http.StatusInternalServerError, "internal_error", "could not update prompt release")
		return
	}
	if s.cfg.ConfigCache != nil {
		if cacheErr := s.cfg.ConfigCache.Invalidate(r.Context(), promptReleaseCacheKey(tenant(r), created.Environment)); cacheErr != nil {
			s.log.Warn("prompt release cache invalidation failed", "error", cacheErr)
		}
	}
	jsonResponse(w, http.StatusOK, created)
}

func (s *Server) modelProfileVersions(w http.ResponseWriter, r *http.Request) {
	if !hasRole(roles(r), "tenant_admin") {
		problem(w, http.StatusForbidden, "forbidden", "tenant_admin role is required")
		return
	}
	versions, err := s.repo.ListModelProfileVersions(r.Context(), tenant(r), 100)
	if err != nil {
		problem(w, http.StatusInternalServerError, "internal_error", "could not load model profile versions")
		return
	}
	jsonResponse(w, http.StatusOK, map[string]any{"model_profile_versions": versions})
}

func (s *Server) createModelProfileVersion(w http.ResponseWriter, r *http.Request) {
	if !hasRole(roles(r), "tenant_admin") {
		problem(w, http.StatusForbidden, "forbidden", "tenant_admin role is required")
		return
	}
	var body struct {
		Name              string          `json:"name"`
		ProviderAccountID string          `json:"provider_account_id,omitempty"`
		ModelName         string          `json:"model_name"`
		Parameters        json.RawMessage `json:"parameters"`
	}
	if !decode(w, r, &body, s.cfg.MaxRequestBytes) {
		return
	}
	body.Name = strings.TrimSpace(body.Name)
	body.ProviderAccountID = strings.TrimSpace(body.ProviderAccountID)
	body.ModelName = strings.TrimSpace(body.ModelName)
	if !safeID.MatchString(body.Name) || body.ModelName == "" || len(body.ModelName) > 200 {
		problem(w, http.StatusBadRequest, "invalid_request", "name and model_name are required")
		return
	}
	if body.ProviderAccountID != "" && !safeID.MatchString(body.ProviderAccountID) {
		problem(w, http.StatusBadRequest, "invalid_request", "provider_account_id must be a safe identifier")
		return
	}
	if len(body.Parameters) > 0 && !json.Valid(body.Parameters) {
		problem(w, http.StatusBadRequest, "invalid_request", "parameters must be valid JSON")
		return
	}
	audit := auditEvent(r, "model_profile.version.create", "model_profile", body.Name)
	version, err := s.repo.CreateModelProfileVersion(r.Context(), tenant(r), uuid.NewString(), uuid.NewString(), body.Name, body.ProviderAccountID, body.ModelName, actorID(r), body.Parameters, audit)
	if err != nil {
		s.log.Error("create model profile version failed", "error", err)
		problem(w, http.StatusInternalServerError, "internal_error", "could not create model profile version")
		return
	}
	jsonResponse(w, http.StatusCreated, version)
}

func (s *Server) providerAccounts(w http.ResponseWriter, r *http.Request) {
	if !hasRole(roles(r), "tenant_admin") {
		problem(w, http.StatusForbidden, "forbidden", "tenant_admin role is required")
		return
	}
	accounts, err := s.repo.ListProviderAccounts(r.Context(), tenant(r), 100)
	if err != nil {
		s.log.Error("load provider accounts failed", "error", err)
		problem(w, http.StatusInternalServerError, "internal_error", "could not load provider accounts")
		return
	}
	jsonResponse(w, http.StatusOK, map[string]any{"provider_accounts": accounts})
}

func (s *Server) createProviderAccount(w http.ResponseWriter, r *http.Request) {
	if !hasRole(roles(r), "tenant_admin") {
		problem(w, http.StatusForbidden, "forbidden", "tenant_admin role is required")
		return
	}
	if s.cfg.SecretProtector == nil {
		problem(w, http.StatusServiceUnavailable, "configuration_encryption_unavailable", "provider account storage is not configured")
		return
	}
	var body struct {
		Provider string `json:"provider"`
		Name     string `json:"name"`
		Secret   string `json:"secret"`
	}
	if !decode(w, r, &body, s.cfg.MaxRequestBytes) {
		return
	}
	body.Provider = strings.TrimSpace(body.Provider)
	body.Name = strings.TrimSpace(body.Name)
	if !safeID.MatchString(body.Provider) || !safeID.MatchString(body.Name) || len(body.Secret) == 0 || len(body.Secret) > 16384 {
		problem(w, http.StatusBadRequest, "invalid_request", "provider and name must be safe identifiers and secret must be 1..16384 bytes")
		return
	}
	encrypted, err := s.cfg.SecretProtector.Encrypt([]byte(body.Secret))
	if err != nil {
		s.log.Error("provider account encryption failed", "error", err)
		problem(w, http.StatusInternalServerError, "internal_error", "could not protect provider account")
		return
	}
	account := model.ProviderAccount{ID: uuid.NewString(), TenantID: tenant(r), Provider: body.Provider, Name: body.Name, KeyRef: s.cfg.SecretKeyReference}
	audit := auditEvent(r, "provider_account.create", "provider_account", account.ID)
	created, err := s.repo.CreateProviderAccount(r.Context(), account, encrypted, audit)
	if err != nil {
		if strings.Contains(err.Error(), "duplicate key") {
			problem(w, http.StatusConflict, "already_exists", "provider account name already exists")
			return
		}
		s.log.Error("create provider account failed", "error", err)
		problem(w, http.StatusInternalServerError, "internal_error", "could not create provider account")
		return
	}
	jsonResponse(w, http.StatusCreated, created)
}

func (s *Server) agentVersions(w http.ResponseWriter, r *http.Request) {
	if !hasRole(roles(r), "tenant_admin") {
		problem(w, http.StatusForbidden, "forbidden", "tenant_admin role is required")
		return
	}
	items, err := s.repo.ListAgentVersions(r.Context(), tenant(r), 100)
	if err != nil {
		s.log.Error("load agent versions failed", "error", err)
		problem(w, http.StatusInternalServerError, "internal_error", "could not load agent versions")
		return
	}
	jsonResponse(w, http.StatusOK, map[string]any{"agent_versions": items})
}

func (s *Server) createAgentVersion(w http.ResponseWriter, r *http.Request) {
	if !hasRole(roles(r), "tenant_admin") {
		problem(w, http.StatusForbidden, "forbidden", "tenant_admin role is required")
		return
	}
	var body struct {
		Name          string          `json:"name"`
		Specification json.RawMessage `json:"specification"`
	}
	if !decode(w, r, &body, s.cfg.MaxRequestBytes) {
		return
	}
	body.Name = strings.TrimSpace(body.Name)
	if !safeID.MatchString(body.Name) || !json.Valid(body.Specification) || !strings.HasPrefix(strings.TrimSpace(string(body.Specification)), "{") {
		problem(w, http.StatusBadRequest, "invalid_request", "name must be a safe identifier and specification must be a JSON object")
		return
	}
	created, err := s.repo.CreateAgentVersion(r.Context(), tenant(r), uuid.NewString(), uuid.NewString(), body.Name, actorID(r), body.Specification, auditEvent(r, "agent.version.create", "agent", body.Name))
	if err != nil {
		if strings.Contains(err.Error(), "duplicate key") {
			problem(w, http.StatusConflict, "duplicate_version", "an identical agent version already exists")
			return
		}
		s.log.Error("create agent version failed", "error", err)
		problem(w, http.StatusInternalServerError, "internal_error", "could not create agent version")
		return
	}
	jsonResponse(w, http.StatusCreated, created)
}

func (s *Server) toolVersions(w http.ResponseWriter, r *http.Request) {
	if !hasRole(roles(r), "tenant_admin") {
		problem(w, http.StatusForbidden, "forbidden", "tenant_admin role is required")
		return
	}
	items, err := s.repo.ListToolVersions(r.Context(), tenant(r), 100)
	if err != nil {
		s.log.Error("load tool versions failed", "error", err)
		problem(w, http.StatusInternalServerError, "internal_error", "could not load tool versions")
		return
	}
	jsonResponse(w, http.StatusOK, map[string]any{"tool_versions": items})
}

func (s *Server) createToolVersion(w http.ResponseWriter, r *http.Request) {
	if !hasRole(roles(r), "tenant_admin") {
		problem(w, http.StatusForbidden, "forbidden", "tenant_admin role is required")
		return
	}
	var body struct {
		Name          string          `json:"name"`
		Kind          string          `json:"kind"`
		Specification json.RawMessage `json:"specification"`
	}
	if !decode(w, r, &body, s.cfg.MaxRequestBytes) {
		return
	}
	body.Name, body.Kind = strings.TrimSpace(body.Name), strings.TrimSpace(body.Kind)
	if !safeID.MatchString(body.Name) || !safeID.MatchString(body.Kind) || !json.Valid(body.Specification) || !strings.HasPrefix(strings.TrimSpace(string(body.Specification)), "{") {
		problem(w, http.StatusBadRequest, "invalid_request", "name and kind must be safe identifiers and specification must be a JSON object")
		return
	}
	created, err := s.repo.CreateToolVersion(r.Context(), tenant(r), uuid.NewString(), uuid.NewString(), body.Name, body.Kind, actorID(r), body.Specification, auditEvent(r, "tool.version.create", "tool", body.Name))
	if err != nil {
		if strings.Contains(err.Error(), "duplicate key") {
			problem(w, http.StatusConflict, "duplicate_version", "an identical tool version already exists")
			return
		}
		s.log.Error("create tool version failed", "error", err)
		problem(w, http.StatusInternalServerError, "internal_error", "could not create tool version")
		return
	}
	jsonResponse(w, http.StatusCreated, created)
}

func (s *Server) toolApprovals(w http.ResponseWriter, r *http.Request) {
	if !hasRole(roles(r), "tenant_admin") && !hasRole(roles(r), "operator") {
		problem(w, http.StatusForbidden, "forbidden", "tenant_admin or operator role is required")
		return
	}
	items, err := s.repo.ListToolApprovals(r.Context(), tenant(r), 100)
	if err != nil {
		s.log.Error("load tool approvals failed", "error", err)
		problem(w, http.StatusInternalServerError, "internal_error", "could not load tool approvals")
		return
	}
	jsonResponse(w, http.StatusOK, map[string]any{"tool_approvals": items})
}

func (s *Server) decideToolApproval(w http.ResponseWriter, r *http.Request) {
	if !hasRole(roles(r), "tenant_admin") && !hasRole(roles(r), "operator") {
		problem(w, http.StatusForbidden, "forbidden", "tenant_admin or operator role is required")
		return
	}
	id := strings.TrimSpace(r.PathValue("approval_id"))
	var body struct {
		Decision string `json:"decision"`
		Note     string `json:"note"`
	}
	if !decode(w, r, &body, s.cfg.MaxRequestBytes) {
		return
	}
	body.Decision = strings.TrimSpace(body.Decision)
	body.Note = strings.TrimSpace(body.Note)
	if !safeID.MatchString(id) || (body.Decision != "approved" && body.Decision != "denied") || len(body.Note) > 2000 {
		problem(w, http.StatusBadRequest, "invalid_request", "approval_id, decision, or note is invalid")
		return
	}
	approval, err := s.repo.DecideToolApproval(r.Context(), tenant(r), id, body.Decision, actorID(r), body.Note, auditEvent(r, "tool_approval."+body.Decision, "tool_approval", id))
	if errors.Is(err, store.ErrConflict) {
		problem(w, http.StatusConflict, "approval_unavailable", "approval is expired or already decided")
		return
	}
	if err != nil {
		s.log.Error("decide tool approval failed", "error", err)
		problem(w, http.StatusInternalServerError, "internal_error", "could not decide tool approval")
		return
	}
	jsonResponse(w, http.StatusOK, approval)
}

func (s *Server) searchKnowledge(w http.ResponseWriter, r *http.Request) {
	query := strings.TrimSpace(r.URL.Query().Get("q"))
	if utf8.RuneCountInString(query) < 2 || utf8.RuneCountInString(query) > 500 {
		problem(w, http.StatusBadRequest, "invalid_request", "q must be 2..500 characters")
		return
	}
	limit := 10
	if raw := r.URL.Query().Get("limit"); raw != "" {
		parsed, err := strconv.Atoi(raw)
		if err != nil || parsed < 1 || parsed > 50 {
			problem(w, http.StatusBadRequest, "invalid_request", "limit must be 1..50")
			return
		}
		limit = parsed
	}
	chunks, err := s.repo.SearchKnowledge(r.Context(), tenant(r), query, limit)
	if err != nil {
		s.log.Error("knowledge search failed", "error", err)
		problem(w, http.StatusInternalServerError, "internal_error", "could not search knowledge")
		return
	}
	jsonResponse(w, http.StatusOK, map[string]any{"chunks": chunks})
}

func (s *Server) searchKnowledgeHybrid(w http.ResponseWriter, r *http.Request) {
	var body struct {
		Query                     string `json:"q"`
		EmbeddingProfileVersionID string `json:"embedding_profile_version_id"`
		Limit                     int    `json:"limit"`
	}
	if !decode(w, r, &body, s.cfg.MaxRequestBytes) {
		return
	}
	body.Query = strings.TrimSpace(body.Query)
	body.EmbeddingProfileVersionID = strings.TrimSpace(body.EmbeddingProfileVersionID)
	if utf8.RuneCountInString(body.Query) < 2 || utf8.RuneCountInString(body.Query) > 500 || (body.EmbeddingProfileVersionID != "" && !safeID.MatchString(body.EmbeddingProfileVersionID)) {
		problem(w, http.StatusBadRequest, "invalid_request", "q must be 2..500 characters and profile ID must be valid")
		return
	}
	if body.Limit == 0 {
		body.Limit = 10
	}
	if body.Limit < 1 || body.Limit > 50 {
		problem(w, http.StatusBadRequest, "invalid_request", "limit must be 1..50")
		return
	}
	lexical, err := s.repo.SearchKnowledge(r.Context(), tenant(r), body.Query, body.Limit*4)
	if err != nil {
		s.log.Error("knowledge lexical search failed", "error", err)
		problem(w, http.StatusInternalServerError, "internal_error", "could not search knowledge")
		return
	}
	if body.EmbeddingProfileVersionID == "" {
		jsonResponse(w, http.StatusOK, map[string]any{"mode": "lexical", "chunks": lexical})
		return
	}
	if s.cfg.SecretDecryptor == nil || s.cfg.EmbeddingClient == nil {
		problem(w, http.StatusServiceUnavailable, "semantic_search_unavailable", "semantic search is not configured")
		return
	}
	profile, err := s.repo.GetModelProfileVersion(r.Context(), tenant(r), body.EmbeddingProfileVersionID)
	if err != nil {
		if errors.Is(err, store.ErrNotFound) {
			problem(w, http.StatusNotFound, "not_found", "embedding model profile was not found")
			return
		}
		s.log.Error("load embedding model profile failed", "error", err)
		problem(w, http.StatusInternalServerError, "internal_error", "could not load embedding model profile")
		return
	}
	account, encrypted, err := s.repo.ProviderAccountSecret(r.Context(), tenant(r), profile.ProviderAccountID)
	if err != nil {
		s.log.Error("load embedding provider account failed", "error", err)
		problem(w, http.StatusInternalServerError, "internal_error", "could not load embedding provider account")
		return
	}
	key, err := s.cfg.SecretDecryptor.Decrypt(encrypted)
	if err != nil {
		s.log.Error("decrypt embedding provider key failed", "error", err)
		problem(w, http.StatusInternalServerError, "internal_error", "could not prepare semantic search")
		return
	}
	defer clearSecret(key)
	vectors, err := s.cfg.EmbeddingClient.Embed(r.Context(), embeddings.Profile{Provider: account.Provider, ModelName: profile.ModelName, Parameters: profile.Parameters}, key, []string{body.Query})
	if err != nil || len(vectors) != 1 {
		s.log.Error("embed search query failed", "error", err)
		problem(w, http.StatusBadGateway, "semantic_search_failed", "could not create semantic search query")
		return
	}
	semantic, err := s.repo.SearchKnowledgeVector(r.Context(), tenant(r), body.EmbeddingProfileVersionID, vectors[0], body.Limit*4)
	if err != nil {
		s.log.Error("knowledge vector search failed", "error", err)
		problem(w, http.StatusInternalServerError, "internal_error", "could not search knowledge")
		return
	}
	jsonResponse(w, http.StatusOK, map[string]any{"mode": "hybrid_rrf", "chunks": rag.FuseChunks(60, lexical, semantic, body.Limit)})
}

func clearSecret(bytes []byte) {
	for i := range bytes {
		bytes[i] = 0
	}
}

func (s *Server) enqueueKnowledgeIngest(w http.ResponseWriter, r *http.Request) {
	if !hasRole(roles(r), "tenant_admin") {
		problem(w, http.StatusForbidden, "forbidden", "tenant_admin role is required")
		return
	}
	var body struct {
		KnowledgeBaseID           string `json:"knowledge_base_id"`
		KnowledgeBaseName         string `json:"knowledge_base_name"`
		DocumentID                string `json:"document_id"`
		SourceURI                 string `json:"source_uri"`
		Title                     string `json:"title"`
		Content                   string `json:"content"`
		EmbeddingProfileVersionID string `json:"embedding_profile_version_id"`
		ChunkSize                 int    `json:"chunk_size"`
		ChunkOverlap              int    `json:"chunk_overlap"`
	}
	if !decode(w, r, &body, s.cfg.MaxRequestBytes) {
		return
	}
	body.KnowledgeBaseID = strings.TrimSpace(body.KnowledgeBaseID)
	body.KnowledgeBaseName = strings.TrimSpace(body.KnowledgeBaseName)
	body.DocumentID = strings.TrimSpace(body.DocumentID)
	body.SourceURI = strings.TrimSpace(body.SourceURI)
	body.Title = strings.TrimSpace(body.Title)
	body.Content = strings.TrimSpace(body.Content)
	body.EmbeddingProfileVersionID = strings.TrimSpace(body.EmbeddingProfileVersionID)
	if !safeID.MatchString(body.KnowledgeBaseID) || !safeID.MatchString(body.KnowledgeBaseName) || !safeID.MatchString(body.DocumentID) || body.SourceURI == "" || body.Title == "" || body.Content == "" {
		problem(w, http.StatusBadRequest, "invalid_request", "knowledge base, document, source_uri, title, and content are required")
		return
	}
	if body.ChunkSize == 0 {
		body.ChunkSize = 1200
	}
	if body.ChunkOverlap == 0 {
		body.ChunkOverlap = 200
	}
	if body.ChunkSize < 64 || body.ChunkSize > 8000 || body.ChunkOverlap < 0 || body.ChunkOverlap >= body.ChunkSize {
		problem(w, http.StatusBadRequest, "invalid_request", "chunk_size must be 64..8000 and chunk_overlap smaller than chunk_size")
		return
	}
	digest := fmt.Sprintf("%x", sha256.Sum256([]byte(body.Content)))
	if body.EmbeddingProfileVersionID != "" && !safeID.MatchString(body.EmbeddingProfileVersionID) {
		problem(w, http.StatusBadRequest, "invalid_request", "embedding_profile_version_id is invalid")
		return
	}
	ingest := model.KnowledgeIngestPayload{KnowledgeBaseID: body.KnowledgeBaseID, KnowledgeBaseName: body.KnowledgeBaseName, DocumentID: body.DocumentID, SourceURI: body.SourceURI, Title: body.Title, Content: body.Content, ContentHash: digest, EmbeddingProfileVersionID: body.EmbeddingProfileVersionID, ActorID: actorID(r), ChunkSize: body.ChunkSize, ChunkOverlap: body.ChunkOverlap}
	payload, err := json.Marshal(ingest)
	if err != nil {
		problem(w, http.StatusInternalServerError, "internal_error", "could not encode ingest command")
		return
	}
	command := model.Command{MessageID: uuid.NewString(), Type: "knowledge.ingest", TaskID: body.DocumentID, TenantID: tenant(r), Payload: payload, CreatedAt: time.Now().UTC()}
	if err = s.repo.EnqueueKnowledgeIngest(r.Context(), tenant(r), command, auditEvent(r, "knowledge.ingest.enqueue", "knowledge_document", body.DocumentID)); err != nil {
		s.log.Error("enqueue knowledge ingest failed", "error", err)
		problem(w, http.StatusInternalServerError, "internal_error", "could not enqueue knowledge ingest")
		return
	}
	jsonResponse(w, http.StatusAccepted, map[string]any{"document_id": body.DocumentID, "message_id": command.MessageID, "status": "queued"})
}

func (s *Server) skillVersions(w http.ResponseWriter, r *http.Request) {
	if !hasRole(roles(r), "tenant_admin") {
		problem(w, http.StatusForbidden, "forbidden", "tenant_admin role is required")
		return
	}
	items, err := s.repo.ListSkillVersions(r.Context(), tenant(r), 100)
	if err != nil {
		s.log.Error("load skill versions failed", "error", err)
		problem(w, http.StatusInternalServerError, "internal_error", "could not load skill versions")
		return
	}
	jsonResponse(w, http.StatusOK, map[string]any{"skill_versions": items})
}

func (s *Server) createSkillVersion(w http.ResponseWriter, r *http.Request) {
	if !hasRole(roles(r), "tenant_admin") {
		problem(w, http.StatusForbidden, "forbidden", "tenant_admin role is required")
		return
	}
	var body struct {
		Name          string          `json:"name"`
		Specification json.RawMessage `json:"specification"`
	}
	if !decode(w, r, &body, s.cfg.MaxRequestBytes) {
		return
	}
	body.Name = strings.TrimSpace(body.Name)
	if !safeID.MatchString(body.Name) || !json.Valid(body.Specification) || !strings.HasPrefix(strings.TrimSpace(string(body.Specification)), "{") {
		problem(w, http.StatusBadRequest, "invalid_request", "name must be a safe identifier and specification must be a JSON object")
		return
	}
	created, err := s.repo.CreateSkillVersion(r.Context(), tenant(r), uuid.NewString(), uuid.NewString(), body.Name, actorID(r), body.Specification, auditEvent(r, "skill.version.create", "skill", body.Name))
	if err != nil {
		if strings.Contains(err.Error(), "duplicate key") {
			problem(w, http.StatusConflict, "duplicate_version", "an identical skill version already exists")
			return
		}
		s.log.Error("create skill version failed", "error", err)
		problem(w, http.StatusInternalServerError, "internal_error", "could not create skill version")
		return
	}
	jsonResponse(w, http.StatusCreated, created)
}

func (s *Server) createRuntimeSnapshot(w http.ResponseWriter, r *http.Request) {
	if !hasRole(roles(r), "tenant_admin") {
		problem(w, http.StatusForbidden, "forbidden", "tenant_admin role is required")
		return
	}
	var body struct {
		PromptVersionID       string          `json:"prompt_version_id,omitempty"`
		ModelProfileVersionID string          `json:"model_profile_version_id,omitempty"`
		AgentVersion          json.RawMessage `json:"agent_version"`
		ToolVersions          json.RawMessage `json:"tool_versions"`
	}
	if !decode(w, r, &body, s.cfg.MaxRequestBytes) {
		return
	}
	if len(body.AgentVersion) > 0 && (!json.Valid(body.AgentVersion) || !strings.HasPrefix(strings.TrimSpace(string(body.AgentVersion)), "{")) {
		problem(w, http.StatusBadRequest, "invalid_request", "agent_version must be a JSON object")
		return
	}
	if len(body.ToolVersions) > 0 && (!json.Valid(body.ToolVersions) || !strings.HasPrefix(strings.TrimSpace(string(body.ToolVersions)), "[")) {
		problem(w, http.StatusBadRequest, "invalid_request", "tool_versions must be a JSON array")
		return
	}
	snapshot := model.RuntimeSnapshot{ID: uuid.NewString(), TenantID: tenant(r), PromptVersionID: strings.TrimSpace(body.PromptVersionID), ModelProfileVersionID: strings.TrimSpace(body.ModelProfileVersionID), AgentVersion: body.AgentVersion, ToolVersions: body.ToolVersions, CreatedBy: actorID(r)}
	audit := auditEvent(r, "runtime_snapshot.create", "runtime_snapshot", snapshot.ID)
	created, err := s.repo.CreateRuntimeSnapshot(r.Context(), snapshot, audit)
	if errors.Is(err, store.ErrNotFound) {
		problem(w, http.StatusNotFound, "not_found", "referenced configuration version was not found")
		return
	}
	if err != nil {
		s.log.Error("create runtime snapshot failed", "error", err)
		problem(w, http.StatusInternalServerError, "internal_error", "could not create runtime snapshot")
		return
	}
	if s.cfg.ConfigCache != nil {
		if cacheErr := s.cfg.ConfigCache.Invalidate(r.Context(), runtimeSnapshotCacheKey(tenant(r), created.ID)); cacheErr != nil {
			s.log.Warn("runtime snapshot cache invalidation failed", "error", cacheErr)
		}
	}
	jsonResponse(w, http.StatusCreated, created)
}

func (s *Server) workspaceFiles(w http.ResponseWriter, r *http.Request) {
	root, ok := s.workspaceForRequest(w, r, r.URL.Query().Get("task_id"))
	if !ok {
		return
	}
	files := make([]map[string]any, 0)
	_ = filepath.WalkDir(root, func(path string, entry os.DirEntry, walkErr error) error {
		if walkErr != nil {
			return nil
		}
		if entry.IsDir() {
			if path != root && (strings.HasPrefix(entry.Name(), ".") || entry.Name() == "node_modules" || entry.Name() == "__pycache__") {
				return filepath.SkipDir
			}
			return nil
		}
		if len(files) >= 1000 {
			return filepath.SkipAll
		}
		info, err := entry.Info()
		if err != nil {
			return nil
		}
		relative, err := filepath.Rel(root, path)
		if err == nil {
			files = append(files, map[string]any{"path": filepath.ToSlash(relative), "size": info.Size()})
		}
		return nil
	})
	jsonResponse(w, http.StatusOK, map[string]any{"files": files})
}

func (s *Server) workspaceFile(w http.ResponseWriter, r *http.Request) {
	root, ok := s.workspaceForRequest(w, r, r.URL.Query().Get("task_id"))
	if !ok {
		return
	}
	target, ok := safeWorkspacePath(root, r.URL.Query().Get("path"))
	if !ok {
		problem(w, http.StatusBadRequest, "invalid_path", "workspace path is invalid")
		return
	}
	file, err := os.Open(target)
	if errors.Is(err, os.ErrNotExist) {
		problem(w, http.StatusNotFound, "not_found", "file not found")
		return
	}
	if err != nil {
		problem(w, http.StatusInternalServerError, "internal_error", "could not read file")
		return
	}
	defer file.Close()
	content, err := io.ReadAll(io.LimitReader(file, 256*1024+1))
	if err != nil {
		problem(w, http.StatusInternalServerError, "internal_error", "could not read file")
		return
	}
	truncated := len(content) > 256*1024
	if truncated {
		content = content[:256*1024]
	}
	jsonResponse(w, http.StatusOK, map[string]any{"path": r.URL.Query().Get("path"), "content": string(content), "truncated": truncated, "binary": !utf8.Valid(content)})
}

func (s *Server) workspacePreview(w http.ResponseWriter, r *http.Request) {
	root, ok := s.workspaceForRequest(w, r, r.PathValue("task_id"))
	if !ok {
		return
	}
	target, ok := safeWorkspacePath(root, r.PathValue("path"))
	if !ok {
		problem(w, http.StatusBadRequest, "invalid_path", "workspace path is invalid")
		return
	}
	w.Header().Set("Content-Security-Policy", "sandbox; default-src 'self' data: blob: 'unsafe-inline'")
	http.ServeFile(w, r, target)
}

func (s *Server) workspaceForRequest(w http.ResponseWriter, r *http.Request, taskID string) (string, bool) {
	if s.cfg.WorkspaceRoot == "" {
		jsonResponse(w, http.StatusOK, map[string]any{"files": []any{}})
		return "", false
	}
	if !safeID.MatchString(taskID) {
		problem(w, http.StatusBadRequest, "invalid_task", "task_id is required")
		return "", false
	}
	if _, err := s.repo.GetTask(r.Context(), tenant(r), taskID); err != nil {
		problem(w, http.StatusNotFound, "not_found", "task not found")
		return "", false
	}
	return filepath.Join(s.cfg.WorkspaceRoot, taskID), true
}

func safeWorkspacePath(root, relative string) (string, bool) {
	if relative == "" || filepath.IsAbs(relative) {
		return "", false
	}
	clean := filepath.Clean(filepath.FromSlash(relative))
	if clean == ".." || strings.HasPrefix(clean, ".."+string(filepath.Separator)) {
		return "", false
	}
	target := filepath.Join(root, clean)
	rel, err := filepath.Rel(root, target)
	return target, err == nil && rel != ".." && !strings.HasPrefix(rel, ".."+string(filepath.Separator))
}

func (s *Server) resume(w http.ResponseWriter, r *http.Request) {
	var body struct {
		Decision string `json:"decision"`
		AtNode   string `json:"at_node,omitempty"`
	}
	if !decode(w, r, &body, s.cfg.MaxRequestBytes) {
		return
	}
	body.Decision = strings.TrimSpace(body.Decision)
	if body.Decision == "" || len(body.Decision) > 20000 {
		problem(w, http.StatusBadRequest, "invalid_request", "decision is required and must be at most 20000 characters")
		return
	}
	payload, _ := json.Marshal(body)
	taskID := r.PathValue("task_id")
	err := s.repo.EnqueueResume(r.Context(), tenant(r), taskID, uuid.NewString(), payload, auditEvent(r, "task.resume", "task", taskID))
	if transitionError(w, err) {
		return
	}
	t, err := s.repo.GetTask(r.Context(), tenant(r), taskID)
	if err != nil {
		problem(w, http.StatusInternalServerError, "internal_error", "could not load task")
		return
	}
	writeTask(w, http.StatusAccepted, t)
}
func (s *Server) cancel(w http.ResponseWriter, r *http.Request) {
	taskID := r.PathValue("task_id")
	err := s.repo.EnqueueCancel(r.Context(), tenant(r), taskID, uuid.NewString(), auditEvent(r, "task.cancel", "task", taskID))
	if transitionError(w, err) {
		return
	}
	w.WriteHeader(http.StatusAccepted)
}
func (s *Server) retry(w http.ResponseWriter, r *http.Request) {
	taskID := r.PathValue("task_id")
	err := s.repo.EnqueueRetry(r.Context(), tenant(r), taskID, uuid.NewString(), auditEvent(r, "task.retry", "task", taskID))
	if transitionError(w, err) {
		return
	}
	t, err := s.repo.GetTask(r.Context(), tenant(r), taskID)
	if err != nil {
		problem(w, http.StatusInternalServerError, "internal_error", "could not load task")
		return
	}
	writeTask(w, http.StatusAccepted, t)
}
func transitionError(w http.ResponseWriter, err error) bool {
	if err == nil {
		return false
	}
	if errors.Is(err, store.ErrNotFound) {
		problem(w, http.StatusNotFound, "not_found", "task not found")
	} else if errors.Is(err, store.ErrConflict) {
		problem(w, http.StatusConflict, "invalid_state", "task cannot perform this transition")
	} else {
		problem(w, http.StatusInternalServerError, "internal_error", "could not update task")
	}
	return true
}

func (s *Server) events(w http.ResponseWriter, r *http.Request) {
	after := int64(-1)
	if value := r.URL.Query().Get("from"); value != "" {
		after, _ = strconv.ParseInt(value, 10, 64)
	}
	if value := r.Header.Get("Last-Event-ID"); value != "" {
		if n, err := strconv.ParseInt(value, 10, 64); err == nil {
			after = n
		}
	}
	s.stream(w, r, r.PathValue("task_id"), after)
}
func (s *Server) stream(w http.ResponseWriter, r *http.Request, taskID string, after int64) {
	if _, err := s.repo.GetTask(r.Context(), tenant(r), taskID); err != nil {
		if errors.Is(err, store.ErrNotFound) {
			problem(w, http.StatusNotFound, "not_found", "task not found")
		} else {
			problem(w, http.StatusInternalServerError, "internal_error", "could not load task")
		}
		return
	}
	flusher, ok := w.(http.Flusher)
	if !ok {
		problem(w, http.StatusInternalServerError, "stream_unsupported", "streaming unsupported")
		return
	}
	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache, no-transform")
	w.Header().Set("Connection", "keep-alive")
	w.Header().Set("X-Accel-Buffering", "no")
	notify, unsubscribe := s.hub.Subscribe(taskID)
	defer unsubscribe()
	s.sseClients.Add(1)
	defer s.sseClients.Add(-1)
	heartbeat := time.NewTicker(s.cfg.SSEHeartbeat)
	defer heartbeat.Stop()
	cursor := after
	for {
		events, err := s.repo.EventsAfter(r.Context(), tenant(r), taskID, cursor, 500)
		if err != nil {
			return
		}
		for _, e := range events {
			if e.Sequence <= cursor {
				continue
			}
			fmt.Fprintf(w, "id: %d\nevent: %s\ndata: %s\n\n", e.Sequence, e.Type, e.Data)
			cursor = e.Sequence
		}
		if len(events) > 0 {
			flusher.Flush()
			continue
		}
		select {
		case <-r.Context().Done():
			return
		case <-notify:
			continue
		case <-heartbeat.C:
			fmt.Fprint(w, ": ping\n\n")
			flusher.Flush()
		}
	}
}

func (s *Server) health(w http.ResponseWriter, _ *http.Request) {
	jsonResponse(w, http.StatusOK, map[string]any{"status": "ok"})
}
func (s *Server) ready(w http.ResponseWriter, r *http.Request) {
	ctx, cancel := context.WithTimeout(r.Context(), 2*time.Second)
	defer cancel()
	if err := s.repo.Ping(ctx); err != nil {
		problem(w, http.StatusServiceUnavailable, "not_ready", "database unavailable")
		return
	}
	if s.cfg.Readiness != nil && s.cfg.Readiness(ctx) != nil {
		problem(w, http.StatusServiceUnavailable, "not_ready", "message broker unavailable")
		return
	}
	jsonResponse(w, http.StatusOK, map[string]any{"status": "ready"})
}
func (s *Server) metrics(w http.ResponseWriter, _ *http.Request) {
	w.Header().Set("Content-Type", "text/plain; version=0.0.4")
	fmt.Fprintf(w, "# TYPE agent_room_gateway_requests_total counter\nagent_room_gateway_requests_total %d\n# TYPE agent_room_gateway_rejected_total counter\nagent_room_gateway_rejected_total %d\n# TYPE agent_room_gateway_sse_clients gauge\nagent_room_gateway_sse_clients %d\n", s.requests.Load(), s.rejected.Load(), s.sseClients.Load())
	s.admission.WritePrometheus(w, "agent_room_admission_duration_seconds")
	if repo, ok := s.repo.(operationalRepository); ok {
		ctx, cancel := context.WithTimeout(context.Background(), time.Second)
		defer cancel()
		if stats, err := repo.OperationalStats(ctx); err == nil {
			fmt.Fprintf(w, "# TYPE agent_room_outbox_pending gauge\nagent_room_outbox_pending %d\n# TYPE agent_room_outbox_oldest_seconds gauge\nagent_room_outbox_oldest_seconds %.3f\n# TYPE agent_room_task_events_total gauge\nagent_room_task_events_total %d\n# TYPE agent_room_tasks_queued gauge\nagent_room_tasks_queued %d\n# TYPE agent_room_tasks_running gauge\nagent_room_tasks_running %d\n# TYPE agent_room_tasks_awaiting_user gauge\nagent_room_tasks_awaiting_user %d\n# TYPE agent_room_tasks_failed gauge\nagent_room_tasks_failed %d\n", stats.PendingOutbox, stats.OldestOutboxSeconds, stats.Events, stats.TasksQueued, stats.TasksRunning, stats.TasksAwaitingUser, stats.TasksFailed)
		}
	}
}

func decode(w http.ResponseWriter, r *http.Request, dst any, max int64) bool {
	r.Body = http.MaxBytesReader(w, r.Body, max)
	dec := json.NewDecoder(r.Body)
	dec.DisallowUnknownFields()
	if err := dec.Decode(dst); err != nil {
		problem(w, http.StatusBadRequest, "invalid_json", err.Error())
		return false
	}
	if dec.Decode(&struct{}{}) != io.EOF {
		problem(w, http.StatusBadRequest, "invalid_json", "request body must contain exactly one JSON value")
		return false
	}
	return true
}
func writeTask(w http.ResponseWriter, status int, t model.Task) {
	if len(t.Result) > 0 && string(t.Result) != "null" {
		var result map[string]any
		if json.Unmarshal(t.Result, &result) == nil {
			result["task_id"] = t.ID
			result["status"] = t.Status
			jsonResponse(w, status, result)
			return
		}
	}
	jsonResponse(w, status, map[string]any{"task_id": t.ID, "status": t.Status, "error": t.Error, "created_at": t.CreatedAt, "updated_at": t.UpdatedAt})
}
func jsonResponse(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(v)
}
func problem(w http.ResponseWriter, status int, code, detail string) {
	jsonResponse(w, status, map[string]any{"type": "about:blank", "title": http.StatusText(status), "status": status, "code": code, "detail": detail})
}
func originAllowed(origin, allowed string) bool {
	for _, item := range strings.Split(allowed, ",") {
		if strings.TrimSpace(item) == origin || strings.TrimSpace(item) == "*" {
			return true
		}
	}
	return false
}
