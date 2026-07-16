package broker

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"sync"
	"time"

	"github.com/agent-room/agent-room/gateway/internal/model"
	amqp "github.com/rabbitmq/amqp091-go"
)

const (
	CommandsExchange   = "agent-room.commands"
	EventsExchange     = "agent-room.events"
	DeadExchange       = "agent-room.dlx"
	JobsQueue          = "agent-room.jobs"
	EinoJobsQueue      = "agent-room.jobs.eino"
	KnowledgeJobsQueue = "agent-room.jobs.knowledge"
	EventsQueue        = "agent-room.events.projector"
)

type Rabbit struct {
	url      string
	log      *slog.Logger
	mu       sync.Mutex
	conn     *amqp.Connection
	channel  *amqp.Channel
	confirms <-chan amqp.Confirmation
	returns  <-chan amqp.Return
}

func New(url string, log *slog.Logger) *Rabbit { return &Rabbit{url: url, log: log} }
func (r *Rabbit) Ping() error                  { r.mu.Lock(); defer r.mu.Unlock(); return r.ensurePublisher() }

func declare(ch *amqp.Channel) error {
	for _, ex := range []struct{ name, kind string }{{CommandsExchange, "direct"}, {EventsExchange, "topic"}, {DeadExchange, "direct"}} {
		if err := ch.ExchangeDeclare(ex.name, ex.kind, true, false, false, false, nil); err != nil {
			return err
		}
	}
	qargs := amqp.Table{"x-queue-type": "quorum", "x-dead-letter-exchange": DeadExchange, "x-dead-letter-routing-key": "dead.command", "x-delivery-limit": int32(5)}
	if _, err := ch.QueueDeclare(JobsQueue, true, false, false, false, qargs); err != nil {
		return err
	}
	// The legacy bindings keep already queued pre-dual-runtime commands
	// executable. New tasks are routed to a runtime-specific queue.
	for _, key := range []string{"run", "resume", "run.python_langgraph"} {
		if err := ch.QueueBind(JobsQueue, key, CommandsExchange, false, nil); err != nil {
			return err
		}
	}
	if _, err := ch.QueueDeclare(EinoJobsQueue, true, false, false, false, qargs); err != nil {
		return err
	}
	if err := ch.QueueBind(EinoJobsQueue, "run.go_eino", CommandsExchange, false, nil); err != nil {
		return err
	}
	if _, err := ch.QueueDeclare(KnowledgeJobsQueue, true, false, false, false, qargs); err != nil {
		return err
	}
	if err := ch.QueueBind(KnowledgeJobsQueue, "knowledge.ingest", CommandsExchange, false, nil); err != nil {
		return err
	}
	eargs := amqp.Table{"x-queue-type": "quorum", "x-dead-letter-exchange": DeadExchange, "x-dead-letter-routing-key": "dead.event", "x-delivery-limit": int32(5)}
	if _, err := ch.QueueDeclare(EventsQueue, true, false, false, false, eargs); err != nil {
		return err
	}
	if err := ch.QueueBind(EventsQueue, "#", EventsExchange, false, nil); err != nil {
		return err
	}
	for _, q := range []string{"agent-room.commands.dlq", "agent-room.events.dlq"} {
		if _, err := ch.QueueDeclare(q, true, false, false, false, nil); err != nil {
			return err
		}
	}
	if err := ch.QueueBind("agent-room.commands.dlq", "dead.command", DeadExchange, false, nil); err != nil {
		return err
	}
	return ch.QueueBind("agent-room.events.dlq", "dead.event", DeadExchange, false, nil)
}

func (r *Rabbit) ensurePublisher() error {
	if r.conn != nil && !r.conn.IsClosed() && r.channel != nil && !r.channel.IsClosed() {
		return nil
	}
	conn, err := amqp.DialConfig(r.url, amqp.Config{Heartbeat: 10 * time.Second, Dial: amqp.DefaultDial(5 * time.Second)})
	if err != nil {
		return err
	}
	ch, err := conn.Channel()
	if err != nil {
		conn.Close()
		return err
	}
	if err = declare(ch); err != nil {
		ch.Close()
		conn.Close()
		return err
	}
	if err = ch.Confirm(false); err != nil {
		ch.Close()
		conn.Close()
		return err
	}
	r.conn, r.channel = conn, ch
	r.confirms = ch.NotifyPublish(make(chan amqp.Confirmation, 1))
	r.returns = ch.NotifyReturn(make(chan amqp.Return, 1))
	return nil
}

func (r *Rabbit) PublishCommand(ctx context.Context, messageID, routingKey string, body []byte) error {
	return r.publish(ctx, CommandsExchange, messageID, routingKey, body, routingKey != "cancel")
}

// PublishEvent publishes a durable event envelope for the Gateway projector.
func (r *Rabbit) PublishEvent(ctx context.Context, messageID, routingKey string, body []byte) error {
	return r.publish(ctx, EventsExchange, messageID, routingKey, body, true)
}

func (r *Rabbit) publish(ctx context.Context, exchange, messageID, routingKey string, body []byte, mandatory bool) error {
	r.mu.Lock()
	defer r.mu.Unlock()
	if err := r.ensurePublisher(); err != nil {
		return fmt.Errorf("connect rabbitmq: %w", err)
	}
	err := r.channel.PublishWithContext(ctx, exchange, routingKey, mandatory, false, amqp.Publishing{Headers: amqp.Table{"x-schema-version": "1"}, ContentType: "application/json", DeliveryMode: amqp.Persistent, MessageId: messageID, Timestamp: time.Now().UTC(), Body: body})
	if err != nil {
		r.reset()
		return err
	}
	select {
	case returned := <-r.returns:
		return fmt.Errorf("unroutable message %s: %s", messageID, returned.ReplyText)
	case confirmation := <-r.confirms:
		if !confirmation.Ack {
			return errors.New("rabbitmq rejected publish")
		}
		return nil
	case <-ctx.Done():
		return ctx.Err()
	}
}

// ConsumeEinoCommands isolates Go runtime deliveries from the Python queue.
func (r *Rabbit) ConsumeEinoCommands(ctx context.Context, handle func(context.Context, model.Command) error) error {
	return r.consumeCommands(ctx, EinoJobsQueue, "Eino", handle)
}

// ConsumeKnowledgeCommands handles the long-running document ingestion path
// on an isolated quorum queue, so it can never starve interactive task runs.
func (r *Rabbit) ConsumeKnowledgeCommands(ctx context.Context, handle func(context.Context, model.Command) error) error {
	return r.consumeCommands(ctx, KnowledgeJobsQueue, "knowledge", handle)
}

func (r *Rabbit) consumeCommands(ctx context.Context, queue, label string, handle func(context.Context, model.Command) error) error {
	conn, err := amqp.Dial(r.url)
	if err != nil {
		return err
	}
	defer conn.Close()
	ch, err := conn.Channel()
	if err != nil {
		return err
	}
	defer ch.Close()
	if err = declare(ch); err != nil {
		return err
	}
	if err = ch.Qos(1, 0, false); err != nil {
		return err
	}
	deliveries, err := ch.Consume(queue, "", false, false, false, false, nil)
	if err != nil {
		return err
	}
	for {
		select {
		case <-ctx.Done():
			return ctx.Err()
		case delivery, ok := <-deliveries:
			if !ok {
				return fmt.Errorf("%s command delivery channel closed", label)
			}
			var command model.Command
			if err := json.Unmarshal(delivery.Body, &command); err != nil {
				_ = delivery.Nack(false, false)
				continue
			}
			if command.MessageID == "" {
				command.MessageID = delivery.MessageId
			}
			if err := handle(ctx, command); err != nil {
				_ = delivery.Nack(false, true)
				continue
			}
			_ = delivery.Ack(false)
		}
	}
}

func (r *Rabbit) ConsumeEvents(ctx context.Context, handle func(context.Context, model.Event) error) error {
	backoff := time.Second
	for ctx.Err() == nil {
		err := r.consumeEventsOnce(ctx, handle)
		if ctx.Err() != nil {
			return ctx.Err()
		}
		r.log.Error("event consumer disconnected", "error", err, "retry_in", backoff)
		select {
		case <-time.After(backoff):
		case <-ctx.Done():
			return ctx.Err()
		}
		if backoff < 30*time.Second {
			backoff *= 2
		}
	}
	return ctx.Err()
}

func (r *Rabbit) consumeEventsOnce(ctx context.Context, handle func(context.Context, model.Event) error) error {
	conn, err := amqp.Dial(r.url)
	if err != nil {
		return err
	}
	defer conn.Close()
	ch, err := conn.Channel()
	if err != nil {
		return err
	}
	defer ch.Close()
	if err = declare(ch); err != nil {
		return err
	}
	if err = ch.Qos(100, 0, false); err != nil {
		return err
	}
	deliveries, err := ch.Consume(EventsQueue, "", false, false, false, false, nil)
	if err != nil {
		return err
	}
	for {
		select {
		case <-ctx.Done():
			return ctx.Err()
		case d, ok := <-deliveries:
			if !ok {
				return errors.New("event delivery channel closed")
			}
			var e model.Event
			if err := json.Unmarshal(d.Body, &e); err != nil {
				r.log.Error("invalid event", "error", err)
				_ = d.Nack(false, false)
				continue
			}
			if e.MessageID == "" {
				e.MessageID = d.MessageId
			}
			if e.CreatedAt.IsZero() {
				e.CreatedAt = time.Now().UTC()
			}
			if err := handle(ctx, e); err != nil {
				r.log.Error("project event failed", "message_id", e.MessageID, "error", err)
				_ = d.Nack(false, true)
				continue
			}
			_ = d.Ack(false)
		}
	}
}

func (r *Rabbit) reset() {
	if r.channel != nil {
		_ = r.channel.Close()
	}
	if r.conn != nil {
		_ = r.conn.Close()
	}
	r.channel = nil
	r.conn = nil
}
func (r *Rabbit) Close() { r.mu.Lock(); defer r.mu.Unlock(); r.reset() }
