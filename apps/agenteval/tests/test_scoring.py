from trachuan_agenteval.scoring import Evidence

DOC = "vn:vbpl:fixture"


def test_read_continuation_earns_evidence_only_after_complete_chain():
    evidence = Evidence()
    evidence.add(
        "read",
        {"items": [{"handle": DOC + "#dieu-1"}]},
        {
            "items": [
                {
                    "doc": DOC,
                    "complete": False,
                    "next": "page2",
                    "units": [{"kind": "provision", "anchor": "dieu-1/khoan-1", "text": "fixture"}],
                }
            ]
        },
    )
    assert not evidence.hits
    evidence.add(
        "read",
        {"items": [{"next": "page2"}]},
        {
            "items": [
                {
                    "doc": DOC,
                    "complete": True,
                    "units": [{"kind": "provision", "anchor": "dieu-1/khoan-2", "text": "fixture"}],
                }
            ]
        },
    )
    assert len(evidence.hits) == 2
    assert not evidence.pending


def test_a_final_page_without_a_seen_start_cannot_earn_whole_chain_credit():
    evidence = Evidence()
    evidence.add(
        "read",
        {"items": [{"next": "unseen"}]},
        {
            "items": [
                {
                    "doc": DOC,
                    "complete": True,
                    "units": [{"kind": "provision", "anchor": "dieu-1"}],
                }
            ]
        },
    )
    assert not evidence.hits


def test_text_is_not_retained_in_matcher_input():
    evidence = Evidence()
    evidence.add(
        "search",
        {},
        {
            "hits": [
                {
                    "handle": DOC + "#dieu-1",
                    "doc": DOC,
                    "text": "legal body",
                    "relations": [
                        {
                            "source_key": DOC,
                            "evidence": "legal body",
                            "evidence_sha256": "fixture",
                            "kind": "cites",
                            "complete": True,
                        }
                    ],
                }
            ]
        },
    )
    assert "legal body" not in str(evidence.hits)


def test_range_units_do_not_invent_complete_child_fragments_for_parent():
    evidence = Evidence()
    evidence.add(
        "read",
        {"items": [{"handle": DOC + "#dieu-1"}]},
        {
            "items": [
                {
                    "doc": DOC,
                    "complete": True,
                    "units": [
                        {"kind": "provision", "anchor": "dieu-1"},
                        {"kind": "provision", "anchor": "dieu-1/khoan-1"},
                    ],
                }
            ]
        },
    )
    parent = next(h for h in evidence.hits if h["identity"]["anchor"] == "dieu-1")
    assert not parent.get("fragments")


def test_document_outline_is_observed_document_evidence():
    evidence = Evidence()
    evidence.add(
        "read",
        {"items": [{"number": "fixture-number"}]},
        {
            "items": [
                {
                    "doc": DOC,
                    "document": {"title": "fixture"},
                    "complete": True,
                    "units": [{"kind": "outline", "anchor": "dieu-1"}],
                }
            ]
        },
    )
    assert evidence.hits == [{"identity": {"country": "vn", "document_key": DOC, "anchor": ""}}]


def test_read_error_without_structured_items_adds_no_evidence():
    evidence = Evidence()
    evidence.add("read", {"items": [{"handle": "invalid"}]}, {})
    assert not evidence.hits
