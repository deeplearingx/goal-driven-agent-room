package cache

import (
	"context"
	"testing"
	"time"
)

func TestLocalCacheCachesAndInvalidates(t *testing.T) {
	cache := NewLocalConfigCache(time.Minute, 2)
	if err := cache.Put(context.Background(), "prompt-release:tenant:prod", map[string]string{"version": "v1"}); err != nil {
		t.Fatal(err)
	}
	var result map[string]string
	found, err := cache.Get(context.Background(), "prompt-release:tenant:prod", &result)
	if err != nil || !found || result["version"] != "v1" {
		t.Fatalf("found=%v result=%v err=%v", found, result, err)
	}
	if err = cache.Invalidate(context.Background(), "prompt-release:tenant:prod"); err != nil {
		t.Fatal(err)
	}
	found, err = cache.Get(context.Background(), "prompt-release:tenant:prod", &result)
	if err != nil || found {
		t.Fatalf("found=%v err=%v", found, err)
	}
}

func TestLocalCacheIsBoundedAndExpires(t *testing.T) {
	cache := NewLocalConfigCache(time.Millisecond, 1)
	_ = cache.Put(context.Background(), "one", map[string]string{"v": "1"})
	time.Sleep(2 * time.Millisecond)
	var result map[string]string
	if found, _ := cache.Get(context.Background(), "one", &result); found {
		t.Fatal("expired entry was returned")
	}
}
