package agentmatch

import (
	"bytes"
	"context"
	"encoding/json"
	"strings"
	"testing"

	"danny.vn/trachuan/apps/corpus/internal/evaluation"
	"danny.vn/trachuan/internal/retrieval"
)

func TestRun_ProductMatcher(t *testing.T) {
	suite, err := evaluation.VNSuite()
	if err != nil {
		t.Fatal(err)
	}
	var entry evaluation.MapEntry
	for _, e := range suite.Map.Entries {
		if e.CaseID == "dev-001" && e.CitationIndex == 0 {
			entry = e
			break
		}
	}
	a := entry.Alternatives[0]
	handle := a.DocumentKey + "#" + *a.Anchor
	for _, tc := range []struct {
		name      string
		citations []string
		hits      []retrieval.Hit
		want      int
	}{
		{"exact", []string{handle}, []retrieval.Hit{{Identity: retrieval.Identity{Country: a.Country, DocumentKey: a.DocumentKey, Anchor: *a.Anchor}}}, 1},
		{"uncited evidence", nil, []retrieval.Hit{{Identity: retrieval.Identity{Country: a.Country, DocumentKey: a.DocumentKey, Anchor: *a.Anchor}}}, 0},
		{"unseen citation", []string{handle}, nil, 0},
		{"document citation cannot earn child credit", []string{a.DocumentKey}, []retrieval.Hit{{Identity: retrieval.Identity{Country: a.Country, DocumentKey: a.DocumentKey, Anchor: *a.Anchor}}}, 0},
	} {
		t.Run(tc.name, func(t *testing.T) {
			input, err := json.Marshal(map[string]any{"case_id": "dev-001", "citations": tc.citations, "hits": tc.hits})
			if err != nil {
				t.Fatal(err)
			}
			var output bytes.Buffer
			if err := Run(context.Background(), strings.NewReader(string(input)), &output); err != nil {
				t.Fatal(err)
			}
			var got struct {
				Matched  int `json:"matched"`
				Expected int `json:"expected"`
			}
			if err := json.Unmarshal(output.Bytes(), &got); err != nil {
				t.Fatal(err)
			}
			if got.Matched != tc.want || got.Expected != 1 {
				t.Fatalf("got %+v, want matched %d expected 1", got, tc.want)
			}
		})
	}
}
