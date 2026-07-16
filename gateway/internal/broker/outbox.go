package broker

import (
	"context"
	"log/slog"
	"time"

	"github.com/agent-room/agent-room/gateway/internal/model"
)

type OutboxStore interface {
	ClaimOutbox(context.Context, int) ([]model.OutboxMessage, error)
	MarkOutboxPublished(context.Context, int64) error
	MarkOutboxFailed(context.Context, int64, error) error
}

// commandPublisher is deliberately smaller than Rabbit so the persistence
// boundary can be exercised without a live broker.  A failed publication must
// be recorded before the loop can retry it after a process restart.
type commandPublisher interface {
	PublishCommand(context.Context, string, string, []byte) error
}

func RunOutbox(ctx context.Context, store OutboxStore, publisher commandPublisher, poll time.Duration, log *slog.Logger) error {
	ticker := time.NewTicker(poll)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-ticker.C:
			messages, err := store.ClaimOutbox(ctx, 100)
			if err != nil {
				log.Error("claim outbox failed", "error", err)
				continue
			}
			for _, m := range messages {
				publishCtx, cancel := context.WithTimeout(ctx, 10*time.Second)
				err = publisher.PublishCommand(publishCtx, m.MessageID, m.RoutingKey, m.Payload)
				cancel()
				if err != nil {
					_ = store.MarkOutboxFailed(ctx, m.ID, err)
					log.Error("publish outbox failed", "message_id", m.MessageID, "error", err)
					break
				}
				if err = store.MarkOutboxPublished(ctx, m.ID); err != nil {
					log.Error("mark outbox published failed", "message_id", m.MessageID, "error", err)
				}
			}
		}
	}
}
