package httpapi

import "sync"

type Hub struct {
	mu   sync.Mutex
	next uint64
	subs map[string]map[uint64]chan struct{}
}

func NewHub() *Hub { return &Hub{subs: make(map[string]map[uint64]chan struct{})} }
func (h *Hub) Subscribe(taskID string) (<-chan struct{}, func()) {
	h.mu.Lock()
	defer h.mu.Unlock()
	h.next++
	id := h.next
	ch := make(chan struct{}, 1)
	if h.subs[taskID] == nil {
		h.subs[taskID] = make(map[uint64]chan struct{})
	}
	h.subs[taskID][id] = ch
	return ch, func() {
		h.mu.Lock()
		defer h.mu.Unlock()
		if m := h.subs[taskID]; m != nil {
			delete(m, id)
			if len(m) == 0 {
				delete(h.subs, taskID)
			}
		}
	}
}
func (h *Hub) Notify(taskID string) {
	h.mu.Lock()
	defer h.mu.Unlock()
	for _, ch := range h.subs[taskID] {
		select {
		case ch <- struct{}{}:
		default:
		}
	}
}
