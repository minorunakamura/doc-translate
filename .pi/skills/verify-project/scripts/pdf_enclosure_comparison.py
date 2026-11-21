"""#109: split an `adoption-diff` into enclosure/schema-only and other differences.

Usage: python pdf_enclosure_comparison.py <adoption-diff parent>
Reads `<parent>/proof/observations.json` and both proofs' chunks, writes
`<parent>/enclosure.json` (private: refs and pages only, no text).
"""

import json
import sys
from pathlib import Path


def split(parent):
    diff = json.loads((parent / "proof/observations.json").read_bytes())
    earlier, later = Path(diff["earlier"]), Path(diff["later"])
    status = json.loads((later / "population-status.json").read_bytes())
    only, other, enclosures = [], [], []
    for h, chunk in diff["chunks"].items():
        old, new = (
            json.loads((p / f"chunks/{h}.json").read_bytes()) for p in (earlier, later)
        )
        assert sorted(chunk["differences"].get("other_fields", [])) == [
            "schema_version"
        ]
        assert (old["schema_version"], new["schema_version"]) == (15, 16)
        assert set(chunk["differences"]) <= {"blocks", "other_fields"}
        old_ids = [b["block_id"] for b in old["blocks"]]
        new_ids = [b["block_id"] for b in new["blocks"]]
        olds = {b["block_id"]: b for b in old["blocks"]}
        stripped = {
            b["block_id"]: {k: v for k, v in b.items() if k != "enclosure"}
            for b in new["blocks"]
        }
        assert all("enclosure" not in b for b in old["blocks"])
        rows = [
            {"block_id": i, "change": "content"}
            for i in new_ids
            if olds.get(i) != stripped[i]
        ]
        if old_ids != new_ids:
            rows.append({"change": "order", "old": old_ids, "new": new_ids})
        owned = status[h]["owned"]
        (other if rows else only).append({"chunk": h, "owned": owned, "rows": rows})
        for n, b in enumerate(new["blocks"]):
            if "enclosure" in b:
                enclosures.append(
                    {
                        "owned_start": owned[0],
                        "enclosure": b["enclosure"],
                        "block_id": b["block_id"],
                        "kind": b["kind"],
                        "pages": sorted({e["page"] for e in b["evidence"]}),
                        "refs": [e["ref"] for e in b["evidence"]],
                        "old_index": old_ids.index(b["block_id"]),
                        "new_index": n,
                        "moved": old_ids.index(b["block_id"]) != n,
                    }
                )
    return {
        "enclosure_or_schema_only": only,
        "other": other,
        "enclosures": enclosures,
    }


if __name__ == "__main__":
    parent = Path(sys.argv[1]).absolute()
    result = split(parent)
    out = parent / "enclosure.json"
    out.write_text(json.dumps(result, indent=1))
    assert json.loads(out.read_bytes()) == result
