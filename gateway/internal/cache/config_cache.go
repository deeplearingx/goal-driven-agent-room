// Package cache supplies bounded local configuration caching backed by Redis
// and cross-process invalidation through Redis Pub/Sub.
package cache

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"sync"
	"time"

	"github.com/redis/go-redis/v9"
)

const invalidationChannel = "agent_room:config:invalidate:v1"

type ConfigCache interface {
	Get(context.Context, string, any) (bool, error)
	Put(context.Context, string, any) error
	Invalidate(context.Context, string) error
}

type entry struct {
	payload   []byte
	expiresAt time.Time
}

type RedisConfigCache struct {
	client       redis.UniversalClient
	ttl          time.Duration
	localLimit   int
	mu           sync.Mutex
	local        map[string]entry
	invalidation chan string
}

func NewRedisConfigCache(redisURL string, ttl time.Duration, localLimit int) (*RedisConfigCache, error) {
	options, err := redis.ParseURL(redisURL)
	if err != nil {
		return nil, fmt.Errorf("parse Redis URL: %w", err)
	}
	if ttl <= 0 {
		ttl = 30 * time.Second
	}
	if localLimit <= 0 {
		localLimit = 2048
	}
	return newCache(redis.NewClient(options), ttl, localLimit), nil
}

func NewLocalConfigCache(ttl time.Duration, localLimit int) *RedisConfigCache {
	if ttl <= 0 {
		ttl = 30 * time.Second
	}
	if localLimit <= 0 {
		localLimit = 2048
	}
	return newCache(nil, ttl, localLimit)
}

func newCache(client redis.UniversalClient, ttl time.Duration, localLimit int) *RedisConfigCache {
	return &RedisConfigCache{client: client, ttl: ttl, localLimit: localLimit, local: make(map[string]entry), invalidation: make(chan string, 128)}
}

func (c *RedisConfigCache) Get(ctx context.Context, key string, target any) (bool, error) {
	if payload, found := c.localGet(key); found {
		return true, json.Unmarshal(payload, target)
	}
	if c.client == nil {
		return false, nil
	}
	payload, err := c.client.Get(ctx, redisKey(key)).Bytes()
	if errors.Is(err, redis.Nil) {
		return false, nil
	}
	if err != nil {
		return false, fmt.Errorf("redis get: %w", err)
	}
	c.localPut(key, payload)
	if err = json.Unmarshal(payload, target); err != nil {
		c.localDelete(key)
		return false, fmt.Errorf("decode cached configuration: %w", err)
	}
	return true, nil
}

func (c *RedisConfigCache) Put(ctx context.Context, key string, value any) error {
	payload, err := json.Marshal(value)
	if err != nil {
		return fmt.Errorf("encode cached configuration: %w", err)
	}
	c.localPut(key, payload)
	if c.client == nil {
		return nil
	}
	if err = c.client.Set(ctx, redisKey(key), payload, c.ttl).Err(); err != nil {
		return fmt.Errorf("redis set: %w", err)
	}
	return nil
}

func (c *RedisConfigCache) Invalidate(ctx context.Context, key string) error {
	c.localDelete(key)
	if c.client == nil {
		return nil
	}
	if err := c.client.Del(ctx, redisKey(key)).Err(); err != nil {
		return fmt.Errorf("redis delete: %w", err)
	}
	payload, _ := json.Marshal(map[string]string{"key": key})
	if err := c.client.Publish(ctx, invalidationChannel, payload).Err(); err != nil {
		return fmt.Errorf("redis publish invalidation: %w", err)
	}
	return nil
}

func (c *RedisConfigCache) Ping(ctx context.Context) error {
	if c.client == nil {
		return nil
	}
	return c.client.Ping(ctx).Err()
}

func (c *RedisConfigCache) Close() error {
	if c.client != nil {
		return c.client.Close()
	}
	return nil
}

// Listen applies invalidations received from every Gateway instance. A caller
// should restart it after a Redis disconnect; cache expiry bounds staleness if
// the subscriber is unavailable.
func (c *RedisConfigCache) Listen(ctx context.Context) error {
	if c.client == nil {
		<-ctx.Done()
		return ctx.Err()
	}
	pubsub := c.client.Subscribe(ctx, invalidationChannel)
	defer pubsub.Close()
	if _, err := pubsub.Receive(ctx); err != nil {
		return err
	}
	channel := pubsub.Channel()
	for {
		select {
		case <-ctx.Done():
			return ctx.Err()
		case message, ok := <-channel:
			if !ok {
				return errors.New("Redis invalidation subscription closed")
			}
			var event struct {
				Key string `json:"key"`
			}
			if json.Unmarshal([]byte(message.Payload), &event) == nil && event.Key != "" {
				c.localDelete(event.Key)
			}
		}
	}
}

func (c *RedisConfigCache) localGet(key string) ([]byte, bool) {
	c.mu.Lock()
	defer c.mu.Unlock()
	value, ok := c.local[key]
	if !ok || time.Now().After(value.expiresAt) {
		delete(c.local, key)
		return nil, false
	}
	return append([]byte(nil), value.payload...), true
}

func (c *RedisConfigCache) localPut(key string, payload []byte) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if len(c.local) >= c.localLimit {
		for victim := range c.local {
			delete(c.local, victim)
			break
		}
	}
	c.local[key] = entry{payload: append([]byte(nil), payload...), expiresAt: time.Now().Add(c.ttl)}
}

func (c *RedisConfigCache) localDelete(key string) {
	c.mu.Lock()
	delete(c.local, key)
	c.mu.Unlock()
}

func redisKey(key string) string { return "agent_room:config:v1:" + key }
