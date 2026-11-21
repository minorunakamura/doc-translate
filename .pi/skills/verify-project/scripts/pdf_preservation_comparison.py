"""#101: freeze the input population, replay once, then compare actual output."""

import html
import itertools
import json
import re
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

from pdf_adoption_replay import (
    BOOK,
    ROOT,
    SEED,
    checked,
    deny_network,
    replay_chunk,
)
from pdf_connection_comparison import capture_output
from pdf_source_feasibility import finish, new_directory, sha, write_json

from doc_translate.pdf_correction import load_pdf_correction

SURVEY = SEED.parent
SEEDS = (61, 76, 81, 111, 181, 211, 321, 346)
SURVEY_HASH = "3c362961813f40d509249755c34bcef42f32cd6a45162148e7d4666d84de19c2"


def capture(workspace, ids):
    assert workspace.name == "new" and workspace == workspace.resolve()
    tmp = workspace.parent
    assert tmp.name.startswith("isolated-") and tmp.parents[2] == ROOT / "work"
    assert (
        not tmp.stat().st_mode & 0o077
        and not (workspace / "source-snapshot.json").exists()
    )
    digests = list(ids.values())
    chunks = [load_pdf_correction(workspace, h) for h in digests]
    pages = [page for chunk in chunks for page in chunk.owned_pages]
    assert pages == (list(range(81, 86)) if len(ids) == 1 else list(range(1, 347)))
    for chunk in chunks:
        assert chunk.schema_version == 16
        record = json.loads(checked(workspace / chunk.run.artifact, chunk.run.sha256))
        assert record["request"]["context"]["adoption_rule"] == "drawn-enclosure-v15"
    return capture_output(
        workspace, digests, list(ids), chunks, "preservation-101", assets=True
    )


def edges(source, original_ids):
    # Source IDs get renumbered. Identify both fragment endpoints in the old chunk namespace.
    pairs = {}
    for block in source["blocks"]:
        endpoints = [
            (original_ids[f["chunk_sha256"]], f["block"]["block_id"])
            for f in block["structure"]["fragments"]
        ]
        for pair in itertools.pairwise(endpoints):
            assert pair not in pairs
            pairs[pair] = block["block_id"]
    return pairs


def replay(output, mode):
    assert mode in ("local", "all") and output.absolute().parent.parent == ROOT / "work"
    output = new_directory(output, BOOK)
    stamps = {}
    inventory = {}

    def read(path, digest=None):
        assert path == path.resolve()
        if path.is_relative_to(BOOK):
            expected, mtime = inventory[str(path.relative_to(BOOK))]
            assert digest in (None, expected) and path.stat().st_mtime_ns == mtime
            digest = expected
        else:
            assert path.is_relative_to(SURVEY)
            if path != SURVEY / "hashes.json":
                expected = hashes[str(path.relative_to(SURVEY))]
                assert digest in (None, expected)
                digest = expected
        t = path.stat().st_mtime_ns
        data = checked(path, digest) if digest else path.read_bytes()
        assert (
            stamps.setdefault(str(path), [sha(data), t]) == [sha(data), t]
            and path.stat().st_mtime_ns == t
        )
        return data

    ids = {}
    status = {}
    phase = "population"
    try:
        hashes = json.loads(read(SURVEY / "hashes.json", SURVEY_HASH))

        def survey(name):
            return json.loads(read(SURVEY / name, hashes[name]))

        inventory = survey("original-inventory.json")
        population = survey("population.json")
        rows = sorted(population["chunks"], key=lambda r: r["owned_pages"][0])
        all_ids = {r["sha256"] for r in rows}
        assert len(rows) == len(all_ids) == 70 and [
            p for r in rows for p in r["owned_pages"]
        ] == list(range(1, 347))
        assert {BOOK / f"corrections/pdf/{h}.json" for h in all_ids} == {
            BOOK / p
            for p in inventory
            if re.fullmatch(r"corrections/pdf/[0-9a-f]{64}\.json", p)
        }
        bindings = {n: survey(f"seed-{n}/correspondence.json") for n in SEEDS}
        for mapping in bindings.values():
            assert len({b["original_block_id"] for b in mapping}) == len(
                mapping
            ) and all(h in all_ids for b in mapping for h, k in b["fragments"])
        if mode == "local":
            rows = [r for r in rows if r["owned_pages"][0] == 81]
            assert len(rows) == 1 and {
                h for b in bindings[81] for h, k in b["fragments"]
            } == {rows[0]["sha256"]}
        status = {
            r["sha256"]: {"owned": r["owned_pages"], "result": "not attempted"}
            for r in rows
        }
        phase = "replay"
        with tempfile.TemporaryDirectory(dir=output, prefix="isolated-") as tmp:
            ws = Path(tmp) / "new"
            ws.mkdir()
            for row in rows:
                h = row["sha256"]
                c = json.loads(read(BOOK / f"corrections/pdf/{h}.json", h))
                assert all(
                    row[k] == c[k]
                    for k in ("draft_sha256", "owned_pages", "reference_pages")
                )
                with tempfile.TemporaryDirectory(dir=output, prefix="isolated-") as w:
                    status[h]["result"] = "failed"
                    attempts = ws / "attempts/pdf-correction"
                    before = set(attempts.glob("*.json"))
                    rejected = None
                    try:
                        ids[h] = replay_chunk(
                            ws, 0, h, c, read, verify_record=True, wire_dir=Path(w)
                        )
                    except ValueError as error:
                        # Only an adoption refusal is a per-chunk result; anything else stops.
                        frames = [
                            f.name for f in traceback.extract_tb(error.__traceback__)
                        ]
                        if mode != "all" or "adopt_owned_ranges" not in frames:
                            raise
                        rejected = type(error).__name__
                        diagnosis = output / "rejections"
                        diagnosis.mkdir(exist_ok=True)
                        (diagnosis / f"{row['owned_pages'][0]}.txt").write_text(
                            "".join(traceback.format_exception(error))
                        )
                    finally:
                        saved = set(attempts.glob("*.json")) - before
                        if saved:
                            (attempt_path,) = saved
                            record = json.loads(
                                checked(attempt_path, attempt_path.stem)
                            )
                            prior = json.loads(
                                read(BOOK / c["run"]["artifact"], c["run"]["sha256"])
                            )
                            status[h].update(
                                status=record["status"],
                                attempt_sha256=attempt_path.stem,
                                attempts=len(record["attempts"]),
                                same_final=record["attempts"][-1]["final"]
                                == prior["attempts"][-1]["final"],
                            )
                        status[h].update(
                            sent=(Path(w) / "0.sent").is_file(),
                            wire_used=(Path(w) / "0.used").is_file(),
                        )
                    assert (
                        status[h]["sent"]
                        and status[h]["wire_used"]
                        and status[h]["attempts"] == 1
                        and status[h]["same_final"]
                    )
                    if rejected:
                        status[h].update(result="rejected", exception=rejected)
                    else:
                        status[h].update(result="saved", new=ids[h])
                        kept = output / "chunks"
                        kept.mkdir(exist_ok=True)
                        (kept / f"{h}.json").write_bytes(
                            checked(ws / f"corrections/pdf/{ids[h]}.json", ids[h])
                        )
                    write_json(output / "population-status.json", status)
            # Never compare a partial cohort: every chunk was tried once, all must save.
            assert len(ids) == len(set(ids.values())) == len(rows), "rejected chunks"
            phase = "fresh load, assemble, units and render"
            subprocess.run(
                [sys.executable, __file__, "capture", str(ws)],
                input=json.dumps(ids).encode(),
                check=True,
            )
        data = json.loads((output / "capture.json").read_bytes())
        assert data["digests"] == list(ids.values()) and data["old_ids"] == list(ids)
        phase = "post-render comparison"
        local = check_local(data, bindings[81], read)
        result = {"local": local}
        if mode == "all":
            ptr = json.loads(read(BOOK / "source-snapshot.json"))["sha256"]
            old = json.loads(read(BOOK / f"snapshots/source/{ptr}.json", ptr))
            assert [(b["block_id"], b["kind"]) for b in old["blocks"]] == [
                (b["block_id"], b["kind"]) for b in population["blocks"]
            ]
            baseline = edges(old, {h: h for h in ids})
            current = edges(data["source"], {v: k for k, v in ids.items()})
            result["connections"] = {
                name: [
                    {"endpoints": pair, "block_id": side[pair]}
                    for pair in sorted(side.keys() - other.keys())
                ]
                for name, side, other in [
                    ("added", current, baseline),
                    ("removed", baseline, current),
                ]
            }
            result["connections"]["changed_partners"] = [
                {"old": a, "new": b}
                for a in sorted(baseline.keys() - current.keys())
                for b in sorted(current.keys() - baseline.keys())
                if a[0] == b[0] or a[1] == b[1]
            ]
            result["old_source_sha256"] = ptr
        phase = "capture complete; manual comparison required"
    finally:
        write_json(output / "population-status.json", status)
        write_json(output / "phase.json", phase)
        write_json(output / "inputs.json", stamps)
        assert not list(output.glob("isolated-*"))
        for p, (h, t) in stamps.items():
            checked(Path(p), h)
            assert Path(p).stat().st_mtime_ns == t
    finish(output, result)


def adoption_diff(output, earlier, later):
    """Per chunk saved by both proofs: every block, exclusion and QA difference."""
    output = new_directory(output, BOOK)
    statuses = [
        json.loads((p / "population-status.json").read_bytes())
        for p in (earlier, later)
    ]
    # A rejected cohort has no final hashes.json; each kept chunk is its own new digest.
    for proof, status in zip((earlier, later), statuses, strict=True):
        assert all(
            sha((proof / f"chunks/{h}.json").read_bytes()) == row["new"]
            for h, row in status.items()
            if row["result"] == "saved"
        )
    shared = [
        h
        for h, row in statuses[0].items()
        if row["result"] == "saved" and statuses[1][h]["result"] == "saved"
    ]

    def keyed(values, key):
        return {key(v, i): v for i, v in enumerate(values)}

    fields = {
        "blocks": lambda v, i: v["block_id"],
        "retained_blocks": lambda v, i: v["block_id"],
        "exclusions": lambda v, i: json.dumps(v, sort_keys=True),
        "uncertainties": lambda v, i: json.dumps(v, sort_keys=True),
    }
    chunks = {}
    for h in shared:
        old, new = (
            json.loads((p / f"chunks/{h}.json").read_bytes()) for p in (earlier, later)
        )
        differences = {}
        for name, key in fields.items():
            a, b = keyed(old[name], key), keyed(new[name], key)
            changed = {
                "removed": [a[k] for k in a.keys() - b.keys()],
                "added": [b[k] for k in b.keys() - a.keys()],
                "changed": [
                    {"old": a[k], "new": b[k]}
                    for k in a.keys() & b.keys()
                    if a[k] != b[k]
                ],
            }
            if list(a) != list(b) and a.keys() == b.keys():
                changed["order"] = {"old": list(a), "new": list(b)}
            if any(changed.values()):
                differences[name] = changed
        others = sorted(
            k for k in old.keys() - {"run", *fields} if old.get(k) != new.get(k)
        )
        if others:
            differences["other_fields"] = others
        chunks[h] = {"owned": statuses[1][h]["owned"], "differences": differences}
    finish(
        output,
        {
            "earlier": str(earlier),
            "later": str(later),
            "compared": len(shared),
            "changed": sorted(h for h, v in chunks.items() if v["differences"]),
            "chunks": chunks,
        },
    )


def check_local(data, correspondence, read):
    import math
    from contextlib import closing
    from io import BytesIO

    import pypdfium2 as pdfium
    from pdf_listing_comparison import decoded
    from PIL import Image

    from doc_translate.units import TranslationUnit

    (i,) = [i for i, c in enumerate(data["chunks"]) if 81 in c["owned_pages"]]
    c, old, new = data["chunks"][i], data["old_ids"][i], data["digests"][i]
    assert c["owned_pages"] == list(range(81, 86))
    seed = SURVEY / "seed-81"
    d = json.loads(
        read(BOOK / f"drafts/pdf/{c['draft_sha256']}.json", c["draft_sha256"])
    )
    raw = json.loads(read(BOOK / d["raw"]["artifact"], d["raw"]["sha256"]))

    def node(ev):
        _, col, index = ev["ref"].split("/")
        v = raw[col][int(index)]
        assert v["self_ref"] == ev["ref"]
        return v

    s = data["source"]
    qa = data["qa"]["uncertainties"]
    (cap,) = [
        b
        for b in c["blocks"]
        if b["kind"] == "caption" and b["evidence"][0]["page"] == 81
    ]
    idx = c["blocks"].index(cap)
    before, _, code, after = c["blocks"][idx - 1 : idx + 3]
    assert (
        before["kind"] == after["kind"] == "body"
        and code["kind"] == "code"
        and cap["target_id"] == code["block_id"]
    )

    def source(b):
        (bl,) = [
            v
            for v in s["blocks"]
            if any(
                f["block"] == b and f["chunk_sha256"] == new
                for f in v["structure"]["fragments"]
            )
        ]
        assert [json.loads(l["evidence"]) for l in bl["locations"]] == b["evidence"]
        return bl

    near = [before, cap, code, after]
    bs = [source(b) for b in near]
    keys = [b["block_id"] for b in bs]
    j = s["blocks"].index(bs[0])
    assert bs == s["blocks"][j : j + 4] and bs[1]["structure"]["target_id"] == keys[2]
    assert [
        row["fragments"]
        for row in correspondence
        if any([old, b["block_id"]] in row["fragments"] for b in near)
    ] == [[[old, b["block_id"]]] for b in near]
    anchors = {m["block_id"]: m["output_anchor"] for m in data["source_map"]["blocks"]}
    eng = html.unescape(data["english"])
    ordered = list(re.finditer(r'<a id="([^"]+)"></a>', eng))
    sections = {
        m[1]: eng[m.end() : end]
        for m, end in zip(
            ordered, [m.start() for m in ordered[1:]] + [len(eng)], strict=True
        )
    }

    def output(b):
        return sections[anchors[b["block_id"]]]

    us = [TranslationUnit.model_validate(u) for u in data["units"]]
    assert not any(u.block_id == keys[2] for u in us)
    for b in [bs[0], bs[1], bs[3]]:
        assert (
            "".join(decoded(u) for u in us if u.block_id == b["block_id"]) == b["text"]
            and output(b).count(b["text"]) == 1
        )
    assert [decoded(u) for u in us if u.block_id == keys[1]] == [cap["text"]]
    positions = [eng.index(f'<a id="{anchors[k]}"></a>') for k in keys]
    assert positions == sorted(positions)
    evs = cap["evidence"] + code["evidence"]
    assert [e["charspan"] for e in evs] == [[0, 21], [0, 39], [22, 42]] and evs[0][
        "ref"
    ] == evs[2]["ref"] != evs[1]["ref"]
    expected = [node(e)["text"][slice(*e["charspan"])] for e in evs]
    assert (
        expected == [cap["text"], *code["text"].splitlines()]
        and len(set(expected)) == 3
    )
    assert re.search(
        r"^(`{3,})[^\n]*\n" + re.escape(code["text"]) + r"\n\1$",
        output(bs[2]),
        re.MULTILINE,
    )
    assert cap["text"] not in output(bs[2]) and all(
        output(bs[2]).count(t) == 1 for t in expected[1:]
    )
    (gap,) = [
        q for q in qa if keys[2] in q["block_ids"] and "blank-line count" in q["reason"]
    ]
    assert gap["output_anchors"] == [anchors[keys[2]]] and [
        (l["page"], l["extraction_ref"], json.loads(l["evidence"])["charspan"])
        for l in gap["locations"]
    ] == [(e["page"], e["ref"], e["charspan"]) for e in code["evidence"]]
    boxes = [node(e)["prov"][0]["bbox"] for e in evs]
    assert all(b["coord_origin"] == "BOTTOMLEFT" for b in boxes)
    bounds = [
        op(b[k] for b in boxes)
        for k, op in [("l", min), ("b", min), ("r", max), ("t", max)]
    ]
    page = next(p for p in d["pages"] if p["page"] == 81)
    image = Image.open(
        BytesIO(read(seed / "page-81.png", page["image"]["sha256"]))
    ).convert("L")
    assert image.size == (page["width_pixels"], page["height_pixels"])
    with (
        pdfium.PdfDocument(
            read(BOOK / d["document"]["artifact"], d["document"]["sha256"])
        ) as pdf,
        closing(pdf[80].get_textpage()) as tp,
    ):
        assert tp.get_text_bounded(*bounds).splitlines() == expected
        rows = []
        for line in expected:
            hits = []
            with closing(tp.search(line, match_case=True)) as search:
                while hit := search.get_next():
                    l, b, r, t = tp.get_charbox(hit[0])
                    if (
                        bounds[0] <= (l + r) / 2 <= bounds[2]
                        and bounds[1] <= (b + t) / 2 <= bounds[3]
                    ):
                        hits.append(hit[0])
            (start,) = hits
            glyphs = [
                tp.get_charbox(start + i)
                for i, ch in enumerate(line)
                if not ch.isspace()
            ]
            rows.append(glyphs)
            sx, sy, h = page["scale_x"], page["scale_y"], page["height_points"]
            assert all(
                min(
                    image.crop(
                        (
                            math.floor(l * sx),
                            math.floor((h - t) * sy),
                            math.ceil(r * sx),
                            math.ceil((h - b) * sy),
                        )
                    ).tobytes()
                )
                < 128
                for l, b, r, t in glyphs
            )
        assert all(
            max(g[3] for g in lower) < min(g[1] for g in upper)
            for upper, lower in itertools.pairwise(rows)
        )
    warnings = []
    for n in (81, 83):
        prior = json.loads(read(seed / f"page-{n}.json"))
        (w,) = prior["warnings"]
        (q,) = [
            v
            for v in qa
            if all(w[k] == v[k] for k in ("reason", "locations", "origin", "stage"))
        ]
        (k,) = q["block_ids"]
        (b,) = [b for b in s["blocks"] if b["block_id"] == k]
        (frag,) = b["structure"]["fragments"]
        assert (
            frag["chunk_sha256"] == new
            and b["kind"] == "code"
            and b["text"]
            == " ".join(node(e)["text"] for e in frag["block"]["evidence"])
        )
        assert (
            q["output_anchors"] == [anchors[k]]
            and "※" in output(b)
            and b["text"] in output(b)
        )
        idx = s["blocks"].index(b)
        assert all(
            x["text"] in output(x) for x in (s["blocks"][idx - 1], s["blocks"][idx + 1])
        )
        warnings.append(q)
    return {
        "role_blocks": keys,
        "chunk": new,
        "anchors": [anchors[k] for k in keys],
        "expected_rows": expected,
        "evidence": evs,
        "blank_line_qa": gap,
        "unreadable_qa": warnings,
    }


if __name__ == "__main__":
    import os

    if not __debug__ or len(sys.argv) != 3:
        raise SystemExit("Expected MODE and private output path; assertions required")
    target = Path(sys.argv[2]).absolute()
    parent = target.parents[2] if sys.argv[1] == "capture" else target.parent
    if (
        parent.parent != ROOT / "work"
        or parent != parent.resolve()
        or parent.stat().st_mode & 0o077
        or any(
            not p.is_file()
            or p != p.resolve()
            or not os.path.samestat(p.stat(), os.fstat(stream.fileno()))
            for p, stream in (
                (parent / "stderr", sys.stderr),
                (parent / "stdout", sys.stdout),
            )
        )
    ):
        raise SystemExit("Private parent stdout/stderr files required")
    sys.addaudithook(deny_network)
    if sys.argv[1] == "adoption-diff":
        earlier, later = (Path(p).absolute() for p in os.environ["PROOFS"].split(":"))
        assert all(p.parent.parent == ROOT / "work" for p in (earlier, later))
        adoption_diff(target, earlier, later)
    elif sys.argv[1] == "capture":
        write_json(
            target.parent.parent / "capture.json", capture(target, json.load(sys.stdin))
        )
    else:
        replay(target, sys.argv[1])
