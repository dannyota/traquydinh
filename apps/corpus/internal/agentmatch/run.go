package agentmatch

import (
	"context"
	"encoding/json"
	"fmt"
	"io"

	"danny.vn/trachuan/apps/corpus/internal/evaluation"
	"danny.vn/trachuan/internal/retrieval"
)

type request struct {
	CaseID    string          `json:"case_id"`
	Citations []string        `json:"citations"`
	Hits      []retrieval.Hit `json:"hits"`
}

// Run scores cited evidence with the corpus product matcher without opening a database.
func Run(ctx context.Context, input io.Reader, output io.Writer) error {
	if err := ctx.Err(); err != nil {
		return fmt.Errorf("match context: %w", err)
	}
	var req request
	decoder := json.NewDecoder(io.LimitReader(input, 32*1024*1024))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&req); err != nil {
		return fmt.Errorf("decode match request: %w", err)
	}
	var extra any
	if err := decoder.Decode(&extra); err != io.EOF {
		return fmt.Errorf("match request has trailing data")
	}
	suite, err := evaluation.VNSuite()
	if err != nil {
		return fmt.Errorf("load matcher fixtures: %w", err)
	}
	var selected *evaluation.Case
	for _, c := range suite.Development {
		if c.ID == req.CaseID {
			selected = &c
			break
		}
	}
	if selected == nil {
		return fmt.Errorf("unknown development case")
	}
	cited := make(map[string]bool, len(req.Citations))
	for _, h := range req.Citations {
		cited[h] = true
	}
	hits := make([]retrieval.Hit, 0, len(req.Hits))
	for _, h := range req.Hits {
		if cited[h.Identity.DocumentKey] {
			hits = append(hits, retrieval.Hit{Identity: retrieval.Identity{Country: h.Identity.Country, DocumentKey: h.Identity.DocumentKey}})
		}
		if cited[handle(h.Identity)] {
			hits = append(hits, h)
			continue
		}
		for _, f := range h.Fragments {
			if f.Complete && cited[handle(f.Identity)] {
				hits = append(hits, retrieval.Hit{Identity: f.Identity, Path: h.Path})
			}
		}
	}
	matched := 0
	for i := range selected.ExpectedCitations {
		found := false
		for _, e := range suite.Map.Entries {
			if e.CaseID == selected.ID && e.CitationIndex == i {
				found = true
				if evaluation.MatchProduct(e, hits) > 0 {
					matched++
				}
				break
			}
		}
		if !found {
			return fmt.Errorf("development adjudication missing")
		}
	}
	if err := json.NewEncoder(output).Encode(struct {
		Matched  int `json:"matched"`
		Expected int `json:"expected"`
	}{matched, len(selected.ExpectedCitations)}); err != nil {
		return fmt.Errorf("encode match score: %w", err)
	}
	return nil
}

func handle(i retrieval.Identity) string {
	if i.Anchor == "" {
		return i.DocumentKey
	}
	return i.DocumentKey + "#" + i.Anchor
}
