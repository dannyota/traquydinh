import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from trachuan_agenteval.scoring import Evidence, Matcher


@pytest.mark.parametrize("paginated", [False, True])
@pytest.mark.parametrize("name", ["verified_later_article", "verified_first_clause"])
def test_cross_article_range_uses_real_matcher_recordings(monkeypatch, paginated, name):
    from trachuan_agenteval import scoring

    fixture = json.loads((Path(__file__).parent / "fixtures/matcher_ranges.json").read_text())
    expected = next(r for r in fixture["records"] if r["name"] == name)

    def recorded_matcher(args, **kwargs):
        request = json.loads(kwargs["input"])
        matches = [r for r in fixture["records"] if r["request"] == request]
        assert len(matches) == 1
        return SimpleNamespace(returncode=0, stdout=json.dumps(matches[0]["score"]))

    monkeypatch.setattr(scoring.subprocess, "run", recorded_matcher)
    arguments = fixture["range"]["arguments"]
    result = deepcopy(fixture["range"]["result"])
    evidence = Evidence()
    if paginated:
        first = deepcopy(result)
        first["items"][0].update(complete=False, next="second-page")
        first["items"][0]["units"] = first["items"][0]["units"][:2]
        evidence.add("read", arguments, first)
        result["items"][0]["units"] = result["items"][0]["units"][2:]
        evidence.add("read", {"items": [{"next": "second-page"}]}, result)
    else:
        evidence.add("read", arguments, result)
    score = Matcher(Path("/recorded/agentmatch")).score(
        expected["request"]["case_id"], expected["request"]["citations"], evidence
    )
    assert score == expected["score"]


@pytest.mark.parametrize("same_release", [False, True])
def test_read_units_reuse_only_verified_paths_for_the_same_release(same_release):
    doc = "vn:vbpl:fixture"
    path = [{"anchor": "chuong-2"}, {"anchor": "dieu-4"}]
    evidence = Evidence()
    evidence.add(
        "search",
        {},
        {
            "release": "vn/20990101_1",
            "hits": [
                {
                    "handle": doc + "#dieu-4",
                    "doc": doc,
                    "path": path,
                }
            ],
        },
    )
    evidence.add(
        "read",
        {"items": [{"handle": doc, "from": "dieu-3/khoan-1", "to": "dieu-4"}]},
        {
            "release": "vn/20990101_1" if same_release else "vn/20990102_1",
            "items": [
                {
                    "doc": doc,
                    "complete": True,
                    "units": [
                        {"kind": "lead", "anchor": "dieu-3"},
                        {"kind": "provision", "anchor": "dieu-3/khoan-1"},
                        {"kind": "provision", "anchor": "dieu-4"},
                    ],
                }
            ],
        },
    )
    later = evidence.hits[-1]
    assert [p["identity"]["anchor"] for p in later["path"]] == (
        [p["anchor"] for p in path] if same_release else []
    )
