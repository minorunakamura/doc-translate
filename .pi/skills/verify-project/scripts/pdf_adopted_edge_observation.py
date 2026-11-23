"""#111: observe the 73->74 code pair in a finished `all` replay, read-only.

usage: python pdf_adopted_edge_observation.py <replay parent>/proof <new 0700 dir>
Writes private JSON only; prints pass/fail and counts.
"""

import json
import sys
from pathlib import Path

from pdf_source_feasibility import finish, new_directory

PAIR = ("p73-code", "p74-code1")
LINES = (5, 10)  # non-empty printed rows on pages 73 and 74
JOINED_QA = (
    "Unresolved outgoing code continuation",
    "Unresolved incoming code continuation",
)
BLANK_QA = "Additional blank-line count not evidenced by the original"


def observe(proof):
    data = json.loads((proof / "capture.json").read_bytes())
    (block,) = [
        b
        for b in data["source"]["blocks"]
        if any(f["block"]["block_id"] in PAIR for f in b["structure"]["fragments"])
    ]
    fragments = [f["block"] for f in block["structure"]["fragments"]]
    key = block["block_id"]
    lines = block["text"].split("\n")
    pages = sorted({loc["page"] for loc in block["locations"]})
    qa = [u for u in data["qa"]["uncertainties"] if key in u.get("target_ids", [])]
    failed = [
        c
        for c in data["qa"]["checks"]
        if key in c.get("target_ids", [])
        and c["name"] == "pdf_connection"
        and not c["passed"]
    ]
    unit_hits = sum(key == u.get("block_id") for u in data["units"])
    checks = {
        "one_block_two_fragments": [f["block_id"] for f in fragments] == list(PAIR),
        "rows_in_order_once": lines
        == [line for f in fragments for line in f["text"].split("\n")]
        and [len(f["text"].split("\n")) for f in fragments] == list(LINES),
        "both_pages_located": pages == [73, 74],
        "english_once": data["english"].count(block["text"]) == 1,
        "code_not_in_units": unit_hits == 0,
        "connection_qa_gone": not failed
        and not any(q["reason"] in JOINED_QA for q in qa),
        "blank_line_qa_kept": sum(q["reason"].startswith(BLANK_QA) for q in qa) == 1,
    }
    return checks, {
        "block_id": key,
        "lines": lines,
        "pages": pages,
        "qa": qa,
        "failed_checks": failed,
    }


if __name__ == "__main__":
    proof, output = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
    output = new_directory(output)
    checks, detail = observe(proof)
    finish(output, {"checks": checks, "detail": detail})
    print(json.dumps({k: "pass" if v else "fail" for k, v in checks.items()}))
    sys.exit(0 if all(checks.values()) else 1)
