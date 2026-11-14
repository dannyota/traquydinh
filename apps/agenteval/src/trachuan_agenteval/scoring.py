import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .processes import child_environment


def parse_citations(answer: str) -> list[str]:
    return list(
        dict.fromkeys(
            re.findall(
                r"\[\[cite:([a-z]{2}:[a-z][a-z0-9_-]*:[^\s\[\]#\x00]+"
                r"(?:#[^\s\[\]#\x00]+)?)\]\]",
                answer,
            )
        )
    )


def identity(handle: str) -> dict:
    doc, _, anchor = handle.partition("#")
    return {"country": doc.split(":")[0], "document_key": doc, "anchor": anchor}


@dataclass
class Evidence:
    hits: list[dict] = field(default_factory=list)
    pending: dict[str, list[dict]] = field(default_factory=dict)
    paths: dict[tuple[str, str, str], list[dict]] = field(default_factory=dict)

    def add(self, name: str, arguments: dict, result: dict) -> None:
        release = result.get("release", "")
        if name == "search":
            for hit in result.get("hits", []) + result.get("non_current", []):
                if not hit.get("handle"):
                    continue
                path = [
                    {"identity": identity(hit["doc"] + "#" + p["anchor"])}
                    for p in hit.get("path", [])
                    if p.get("anchor")
                ]
                for i, node in enumerate(path):
                    self.paths[(release, hit["doc"], node["identity"]["anchor"])] = path[: i + 1]
                self.paths[(release, hit["doc"], identity(hit["handle"])["anchor"])] = path
                self.hits.append(
                    {
                        "identity": identity(hit["handle"]),
                        "path": path,
                        "fragments": hit.get("fragments", []),
                        "relations": [
                            {
                                k: v
                                for k, v in r.items()
                                if k
                                in {
                                    "source_key",
                                    "target_key",
                                    "source_anchor",
                                    "target_anchor",
                                    "kind",
                                    "evidence_sha256",
                                    "complete",
                                }
                            }
                            for r in hit.get("relations", [])
                        ],
                    }
                )
        elif name == "read" and "items" in result:
            for request, item in zip(
                arguments.get("items", []), result.get("items", []), strict=True
            ):
                if item.get("doc") and item.get("document") and not item.get("error"):
                    self.hits.append({"identity": identity(item["doc"])})
                if request.get("next") and request["next"] not in self.pending:
                    continue
                previous = self.pending.pop(request.get("next", ""), [])
                units = previous + item.get("units", [])
                if item.get("error") or item.get("not_read"):
                    if item.get("next"):
                        self.pending[item["next"]] = units
                    continue
                if item.get("next"):
                    self.pending[item["next"]] = units
                elif item.get("complete") and item.get("doc"):
                    paths = [
                        {"identity": identity(item["doc"] + "#" + u["anchor"])}
                        for u in units
                        if u.get("kind") == "lead" and u.get("anchor")
                    ]
                    first = next(
                        (u.get("anchor") for u in units if u.get("kind") == "provision"), None
                    )
                    for unit in units:
                        if unit.get("kind") == "provision" and unit.get("anchor"):
                            verified = self.paths.get((release, item["doc"], unit["anchor"]), [])
                            self.hits.append(
                                {
                                    "identity": identity(item["doc"] + "#" + unit["anchor"]),
                                    "path": verified or (paths if unit["anchor"] == first else []),
                                }
                            )


class Matcher:
    def __init__(self, binary: Path) -> None:
        self.binary = binary

    def score(self, case_id: str, citations: list[str], evidence: Evidence) -> dict:
        request = {"case_id": case_id, "citations": citations, "hits": evidence.hits}
        result = subprocess.run(
            [str(self.binary)],
            input=json.dumps(request),
            capture_output=True,
            text=True,
            check=False,
            env=child_environment(os.environ, "system"),
        )
        if result.returncode:
            raise RuntimeError("product matcher failed")
        return json.loads(result.stdout)
