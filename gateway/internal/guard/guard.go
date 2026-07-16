// Package guard implements the lightweight, deterministic first line of
// defence for content entering Go-managed runtime paths. It complements—not
// replaces—provider moderation and the Python runtime's tool/output checks.
package guard

import (
	"regexp"
	"strings"
)

type Mode string

const (
	ModeOff   Mode = "off"
	ModeWarn  Mode = "warn"
	ModeBlock Mode = "block"
)

type Finding struct {
	Category string `json:"category"`
	Rule     string `json:"rule"`
}

var rules = []struct {
	category string
	rule     string
	pattern  *regexp.Regexp
}{
	{"prompt_injection", "ignore_previous_instructions", regexp.MustCompile(`(?i)\bignore\s+(all\s+)?previous\s+instructions\b`)},
	{"prompt_injection", "disregard_prior_instructions", regexp.MustCompile(`(?i)\bdisregard\s+(all\s+)?prior\s+(rules|instructions)\b`)},
	{"secret", "aws_access_key", regexp.MustCompile(`\bAKIA[0-9A-Z]{16}\b`)},
	{"secret", "api_key", regexp.MustCompile(`(?i)\b(sk|pk|api[_-]?key)[_-][A-Za-z0-9]{16,}\b`)},
}

func ParseMode(value string) (Mode, bool) {
	mode := Mode(strings.ToLower(strings.TrimSpace(value)))
	return mode, mode == ModeOff || mode == ModeWarn || mode == ModeBlock
}

func Scan(text string) *Finding {
	for _, rule := range rules {
		if rule.pattern.MatchString(text) {
			return &Finding{Category: rule.category, Rule: rule.rule}
		}
	}
	return nil
}

func Check(mode Mode, text string) *Finding {
	if mode == ModeOff || text == "" {
		return nil
	}
	return Scan(text)
}
