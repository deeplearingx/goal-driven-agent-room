package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"os/signal"
	"strings"

	"github.com/agent-room/agent-room/gateway/pkg/agentroom"
)

type globalOptions struct {
	URL    string
	Token  string
	Tenant string
}

func main() {
	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt)
	defer cancel()
	if err := run(ctx, os.Args[1:], os.Stdout, os.Stderr); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

func run(ctx context.Context, args []string, stdout, stderr io.Writer) error {
	global := flag.NewFlagSet("kbctl", flag.ContinueOnError)
	global.SetOutput(stderr)
	options := globalOptions{}
	global.StringVar(&options.URL, "url", envOr("AGENT_ROOM_URL", "http://127.0.0.1:8080"), "gateway base URL")
	global.StringVar(&options.Token, "token", os.Getenv("AGENT_ROOM_API_KEY"), "bearer token (or AGENT_ROOM_API_KEY)")
	global.StringVar(&options.Tenant, "tenant", os.Getenv("AGENT_ROOM_TENANT"), "tenant ID (or AGENT_ROOM_TENANT)")
	if err := global.Parse(args); err != nil {
		return err
	}
	remaining := global.Args()
	if len(remaining) == 0 {
		usage(stderr)
		return errors.New("kbctl: command is required")
	}
	client, err := agentroom.New(agentroom.Config{BaseURL: options.URL, Token: options.Token, TenantID: options.Tenant})
	if err != nil {
		return err
	}
	switch remaining[0] {
	case "run":
		return runTask(ctx, client, remaining[1:], stdout, stderr)
	case "get":
		return getTask(ctx, client, remaining[1:], stdout, stderr)
	case "cancel":
		return transitionTask(ctx, client.CancelTask, "cancelled", remaining[1:], stdout)
	case "retry":
		return transitionTask(ctx, client.RetryTask, "queued", remaining[1:], stdout)
	case "events":
		return streamEvents(ctx, client, remaining[1:], stdout, stderr)
	case "search":
		return search(ctx, client, remaining[1:], stdout, stderr)
	case "help", "-h", "--help":
		usage(stdout)
		return nil
	default:
		usage(stderr)
		return fmt.Errorf("kbctl: unknown command %q", remaining[0])
	}
}

func runTask(ctx context.Context, client *agentroom.Client, args []string, stdout, stderr io.Writer) error {
	flags := flag.NewFlagSet("run", flag.ContinueOnError)
	flags.SetOutput(stderr)
	title := flags.String("title", "", "task title")
	description := flags.String("description", "", "task description")
	runtime := flags.String("runtime", "python_langgraph", "python_langgraph or go_eino")
	snapshot := flags.String("snapshot", "", "frozen runtime snapshot ID")
	graph := flags.String("graph", "", "full_react or goal")
	idempotency := flags.String("idempotency-key", "", "request idempotency key")
	follow := flags.Bool("follow", false, "stream events after creation")
	if err := flags.Parse(args); err != nil {
		return err
	}
	if strings.TrimSpace(*title) == "" || strings.TrimSpace(*description) == "" {
		return errors.New("kbctl run: --title and --description are required")
	}
	task, err := client.CreateTask(ctx, agentroom.CreateTaskInput{Title: *title, Description: *description, ExecutionRuntime: *runtime, RuntimeSnapshotID: *snapshot, Graph: *graph}, *idempotency)
	if err != nil {
		return err
	}
	if err := writeJSON(stdout, task); err != nil || !*follow {
		return err
	}
	return client.StreamEvents(ctx, task.ID, -1, func(event agentroom.Event) error { return writeJSON(stdout, event) })
}

func getTask(ctx context.Context, client *agentroom.Client, args []string, stdout, stderr io.Writer) error {
	flags := flag.NewFlagSet("get", flag.ContinueOnError)
	flags.SetOutput(stderr)
	if err := flags.Parse(args); err != nil {
		return err
	}
	if flags.NArg() != 1 {
		return errors.New("kbctl get: TASK_ID is required")
	}
	task, err := client.GetTask(ctx, flags.Arg(0))
	if err != nil {
		return err
	}
	return writeJSON(stdout, task)
}

func transitionTask(ctx context.Context, transition func(context.Context, string) error, status string, args []string, stdout io.Writer) error {
	if len(args) != 1 {
		return errors.New("kbctl: TASK_ID is required")
	}
	if err := transition(ctx, args[0]); err != nil {
		return err
	}
	return writeJSON(stdout, map[string]string{"task_id": args[0], "status": status})
}

func streamEvents(ctx context.Context, client *agentroom.Client, args []string, stdout, stderr io.Writer) error {
	flags := flag.NewFlagSet("events", flag.ContinueOnError)
	flags.SetOutput(stderr)
	after := flags.Int64("after", -1, "resume after event sequence")
	taskID := ""
	if len(args) > 0 && !strings.HasPrefix(args[0], "-") {
		taskID, args = args[0], args[1:]
	}
	if err := flags.Parse(args); err != nil {
		return err
	}
	if taskID == "" {
		if flags.NArg() != 1 {
			return errors.New("kbctl events: TASK_ID is required")
		}
		taskID = flags.Arg(0)
	} else if flags.NArg() != 0 {
		return errors.New("kbctl events: exactly one TASK_ID is required")
	}
	return client.StreamEvents(ctx, taskID, *after, func(event agentroom.Event) error { return writeJSON(stdout, event) })
}

func search(ctx context.Context, client *agentroom.Client, args []string, stdout, stderr io.Writer) error {
	flags := flag.NewFlagSet("search", flag.ContinueOnError)
	flags.SetOutput(stderr)
	query := flags.String("query", "", "search query")
	profile := flags.String("profile", "", "embedding profile version ID")
	limit := flags.Int("limit", 10, "result count (1..50)")
	if err := flags.Parse(args); err != nil {
		return err
	}
	if strings.TrimSpace(*query) == "" {
		return errors.New("kbctl search: --query is required")
	}
	chunks, err := client.SearchKnowledge(ctx, agentroom.KnowledgeSearchInput{Query: *query, EmbeddingProfileVersionID: *profile, Limit: *limit})
	if err != nil {
		return err
	}
	return writeJSON(stdout, map[string]any{"chunks": chunks})
}

func writeJSON(writer io.Writer, value any) error {
	encoder := json.NewEncoder(writer)
	encoder.SetEscapeHTML(false)
	return encoder.Encode(value)
}

func envOr(key, fallback string) string {
	if value := strings.TrimSpace(os.Getenv(key)); value != "" {
		return value
	}
	return fallback
}

func usage(writer io.Writer) {
	fmt.Fprintln(writer, `kbctl [global flags] COMMAND

Global flags:
  --url URL       gateway URL (AGENT_ROOM_URL)
  --token TOKEN   bearer token (AGENT_ROOM_API_KEY)
  --tenant ID     tenant ID (AGENT_ROOM_TENANT)

Commands:
  run      --title TEXT --description TEXT [--runtime NAME] [--snapshot ID] [--follow]
  get      TASK_ID
  cancel   TASK_ID
  retry    TASK_ID
  events   TASK_ID [--after SEQUENCE]
  search   --query TEXT [--profile ID] [--limit N]`)
}
