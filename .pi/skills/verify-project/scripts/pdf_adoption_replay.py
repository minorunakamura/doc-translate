"""#97, #98 or #108: the selected saved successes into a new workspace, without a provider."""

import base64
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

from pdf_source_feasibility import ROOT, finish, new_directory, sha, write_json

BOOK = ROOT / "work/design-and-build-great-web-apis"
FINAL = ROOT / "work/issue83-survey-xcP1KLJ7/root-final"
SEED = FINAL.parent / "root-survey/seed-76"
SCRIPT = str(Path(__file__).resolve())


def checked(path, digest):
    if path != path.resolve() or not path.is_relative_to(ROOT / "work"):
        raise ValueError("Unapproved path")
    data = path.read_bytes()
    if sha(data) != digest:
        raise ValueError("Changed input")
    return data


def deny_network(event, args):
    if event.startswith("socket."):
        raise RuntimeError("Offline transport only")


def wire(path, digest):
    """Only private pipes; no runtime/provider/auth, no second invocation."""
    from doc_translate.pdf_correction import ADOPTION_RULE

    assert path.name in {"0.json", "1.json", "2.json", "3.json"} and all(
        stat.S_ISFIFO(os.fstat(stream.fileno()).st_mode)
        for stream in (sys.stdin, sys.stdout)
    )
    record = json.loads(checked(path, digest))
    attempt = record["attempts"][-1]
    path.with_suffix(".used").touch(exist_ok=False)
    for kind in ("preflight", "generate"):
        msg = json.loads(sys.stdin.buffer.readline())
        assert msg["type"] == kind
        payload = msg["payload"]
        if kind == "preflight":
            config = record["config"]
            assert payload == {
                **config,
                "options": {
                    k: v for k, v in config["options"].items() if v is not None
                },
                "image_input": True,
            }
            response = attempt["preflight"]
        else:
            images = payload.pop("images")
            ctx = payload["context"].copy()
            assert ctx.pop("adoption_rule") == ADOPTION_RULE and ctx.pop("page_results")
            assert {**payload, "context": ctx, "stage": "correction"} == record[
                "request"
            ]
            for image, old in zip(images, attempt["images"], strict=True):
                data = base64.b64decode(image.pop("data"), validate=True)
                assert sha(data) == old["sha256"] and len(data) == old["byte_size"]
                assert image == {
                    k: old[k] for k in ("width", "height", "mimeType", "coordinates")
                }
            response = attempt["final"]
        print(
            json.dumps(
                {**msg, "type": "result", "payload": response}, separators=(",", ":")
            ),
            flush=True,
        )


def listing_evidence(
    block,
    prior,
    source_block,
    source,
    raw,
    workspace,
    manifest,
    *,
    caption=None,
    footnote=None,
    relative=False,
):
    """Compare each printed line and each separator, independently of adoption."""
    import ctypes
    from math import isclose

    import pypdfium2 as pdfium

    page = prior.evidence[0].page
    doc = manifest["document"]
    expected = re.findall(r"([^\n]+)(\n*)", prior.text)
    actual = re.findall(r"([^\n]+)(\n*)", block.text)
    assert (
        "".join(a + b for a, b in actual) == block.text
        and "".join(a + b for a, b in expected) == prior.text
    )
    rows = []
    with pdfium.PdfDocument(checked(workspace / doc["artifact"], doc["sha256"])) as pdf:
        tp = pdf[page - 1].get_textpage()
        for e in prior.evidence:
            n = raw["texts"][int(e.ref.split("/")[-1])]
            (prov,) = n["prov"]
            box = prov["bbox"]
            assert prov["page_no"] == page and box["coord_origin"] == "BOTTOMLEFT"
            local = tp.get_text_bounded(box["l"], box["b"], box["r"], box["t"])
            lines = local.splitlines(keepends=True)
            search = tp.search(lines[0].rstrip("\r\n"), match_case=True)
            indices = []
            while hit := search.get_next():
                i, _ = hit
                l, b, r, t = tp.get_charbox(i)
                if (
                    box["l"] < r
                    and l < box["r"]
                    and box["b"] < t
                    and b < box["t"]
                    and tp.get_text_range(i, len(local)) == local
                ):
                    indices.append(i)
            (index,) = indices
            search.close()
            cursor = prov["charspan"][0]
            if caption is not None:
                first = lines.pop(0)
                assert first.rstrip("\r\n") == caption == n["text"][: len(caption)]
                cursor += len(caption)
                index += len(first)
            end = prov["charspan"][1]
            if footnote is not None:
                assert lines.pop().rstrip("\r\n") == footnote and n["text"][
                    :end
                ].endswith(" " + footnote)
                end -= len(footnote) + 1
            for physical in lines:
                line = physical.rstrip("\r\n")
                start = n["text"].index(line, cursor)
                gap = n["text"][cursor:start]
                assert not gap.strip()
                cursor = start + len(line)

                def origin(i):
                    x, y = ctypes.c_double(), ctypes.c_double()
                    assert pdfium.raw.FPDFText_GetCharOrigin(tp, i, x, y)
                    return (x.value, y.value)

                x, y = origin(index)
                step = (
                    (origin(index + len(line) - 1)[0] - x) / (len(line) - 1)
                    if len(line) > 1
                    else None
                )
                for j, char in enumerate(line):
                    i = index + j
                    assert pdfium.raw.FPDFText_GetUnicode(tp, i) == ord(char)
                    assert pdfium.raw.FPDFText_IsGenerated(tp, i) == 0 or char == " "
                    cx, cy = origin(i)
                    assert isclose(cy, y, abs_tol=0.02) and isclose(
                        cx, x + j * (step or 0), abs_tol=0.02
                    )
                    assert box["l"] - 0.02 <= cx <= box["r"] + 0.02
                assert not rows or rows[-1][5] > y
                rows.append((line, e.ref, [start, cursor], gap, x, y, step))
                index += len(physical)
            assert cursor == end
    margin = min(r[4] for r in rows)
    pitch = next(r[-1] for r in rows if r[-1])
    comparisons = []
    for i, (
        (line, ref, span, gap, x, y, step),
        (want, sep),
        (got, actual_sep),
    ) in enumerate(zip(rows, expected, actual, strict=True)):
        indent = len(got) - len(line)
        assert (
            indent >= 0
            and got == " " * indent + line
            and (relative or want == got)
            and isclose(x, margin + indent * pitch, abs_tol=0.02)
            and (step is None or isclose(step, pitch, abs_tol=0.02))
        )
        expected_count = max(1, rows[i + 1][3].count("\n")) if i + 1 < len(rows) else 0
        assert actual_sep == "\n" * expected_count and len(sep) >= expected_count
        qa = []
        if sep != actual_sep:
            adjacent = [(ref, span), (rows[i + 1][1], rows[i + 1][2])]
            qa = [
                q
                for q in source.uncertainties
                if source_block.block_id in q.target_ids
                and "blank-line count" in q.reason
                and f"proposed {sep!r}, adopted {actual_sep!r}" in q.reason
                and [
                    (l.extraction_ref, json.loads(l.evidence)["charspan"])
                    for l in q.locations
                    if l.format == "pdf" and l.page == page
                ]
                == adjacent
            ]
            assert len(qa) == 1
        comparisons.append(
            {
                "line": i + 1,
                "ref": ref,
                "span": span,
                "original_xy": [x, y],
                "printed_line_sha256": sha(line.encode()),
                "printed_line_and_indent_match": True,
                "pitch": step,
                "indent": indent,
                "expected_line_sha256": sha(want.encode()),
                "output_line_sha256": sha(got.encode()),
                "expected_newlines": len(sep),
                "actual_newlines": len(actual_sep),
                "located_blank_count_uncertainty": bool(qa),
                "qa": [q.model_dump(mode="json") for q in qa],
            }
        )
    return comparisons


def word_evidence(tp, box, left, right):
    """Inspect actual glyph advances on both sides, not PDFium's inserted SPACE."""
    import ctypes
    from contextlib import closing

    import pypdfium2.raw as native

    phrase = left + " " + right
    positions = []
    with closing(tp.search(phrase, match_case=True)) as search:
        while hit := search.get_next():
            index, length = hit
            l, b, r, t = tp.get_charbox(index)
            if box["l"] < r and l < box["r"] and box["b"] < t and b < box["t"]:
                assert (
                    length == len(phrase) and tp.get_text_range(index, length) == phrase
                )
                positions.append(index)
    (index,) = positions
    gap = index + len(left)
    explicit = native.FPDFText_IsGenerated(tp, gap) == 0
    points = {}
    fonts = set()
    draws = {}
    for i in range(index, index + len(phrase)):
        if not explicit and i == gap:
            continue
        obj = native.FPDFText_GetTextObject(tp, i)
        assert obj and native.FPDFText_IsGenerated(tp, i) == 0
        mat = native.FS_MATRIX()
        assert (
            native.FPDFText_GetMatrix(tp, i, mat)
            and mat.b == mat.c == 0
            and min(mat.a, mat.d) > 0
        )
        fonts.add(
            (
                ctypes.cast(native.FPDFTextObj_GetFont(obj), ctypes.c_void_p).value,
                native.FPDFText_GetFontSize(tp, i),
                mat.a,
                mat.d,
            )
        )
        x, y = ctypes.c_double(), ctypes.c_double()
        assert native.FPDFText_GetCharOrigin(tp, i, x, y)
        assert box["l"] <= x.value <= box["r"] and box["b"] <= y.value <= box["t"]
        points[i] = (x.value, y.value, tp.get_charbox(i, loose=True)[2])
        draws[i] = ctypes.cast(obj, ctypes.c_void_p).value
    assert (
        max(p[1] for p in points.values()) - min(p[1] for p in points.values()) < 0.01
    )
    excess = points[gap + 1][0] - points[gap - 1][2]
    jitter = max(
        abs(p[0] - points[i - 1][2])
        for i, p in points.items()
        if i - 1 in points and i not in (gap, gap + 1)
    )
    separate = draws[gap - 1] != draws[gap + 1]
    assert explicit or (separate and len(fonts) == 1 and excess > jitter + 0.02)
    return {
        "explicit": explicit,
        "separate_draws": separate,
        "extra_pt": excess,
        "local_jitter_pt": jitter,
        "boundary_xy": points[gap + 1][:2],
    }


def compare(workspace, digests, historical, gold):
    import pypdfium2 as pdfium

    from doc_translate.pdf_correction import load_pdf_correction
    from doc_translate.pdf_source import assemble_pdf_source
    from doc_translate.render import render_document
    from doc_translate.snapshot import save_source_snapshot
    from doc_translate.units import generate_translation_units

    chunks = [load_pdf_correction(workspace, h) for h in digests]
    source = assemble_pdf_source(
        workspace=workspace, chunk_sha256s=digests, snapshot_id="adoption-replay"
    )
    save_source_snapshot(workspace, source)
    units = generate_translation_units(source)
    output = render_document(workspace)
    english = (workspace / output["artifacts"]["english"]).read_text()

    def reaches_output(block):
        selected = [u for u in units if u.block_id == block.block_id]
        if block.kind == "code":
            fenced = re.compile(
                r"^(`{3,})[^`\n]*\n"
                + re.escape(block.text.removesuffix("\n"))
                + r"\n\1$",
                re.MULTILINE,
            )
            return not selected and len(fenced.findall(english)) == 1
        decoded = ""
        for unit in selected:
            text = unit.source_text
            for token in unit.tokens:
                value = token.value["text"]
                assert isinstance(value, str)
                text = text.replace(token.start_marker, value)
                if token.end_marker:
                    text = text.replace(token.end_marker, "")
            decoded += text
        return decoded == block.text

    draft = chunks[-1].draft_sha256
    manifest = json.loads(checked(workspace / f"drafts/pdf/{draft}.json", draft))
    raw = json.loads(
        checked(workspace / manifest["raw"]["artifact"], manifest["raw"]["sha256"])
    )
    found = []
    gold_results = []
    control_results = []
    blank_count_uncertain = 0
    roles = {
        "text": "body",
        "section_header": "heading",
        "footnote": "footnote",
        "code": "code",
    }
    for g in gold:
        ref = g["self_ref"]
        node = raw["texts"][int(ref.split("/")[-1])]
        assert node["self_ref"] == ref and node["text"] == g["text"]
        assert (
            node["label"] == g["label"]
            and [p["page_no"] for p in node["prov"]] == g["pages"]
        )
        selected = [
            (i, b)
            for i, b in enumerate(source.blocks)
            if any(e.format == "pdf" and e.extraction_ref == ref for e in b.locations)
        ]
        spans = []
        for index, b in selected:
            assert reaches_output(b) and b.kind == roles[g["label"]]
            loc = [
                e for e in b.locations if e.format == "pdf" and e.extraction_ref == ref
            ]
            assert len(loc) == len(b.locations)
            bounds = []
            for e in loc:
                match = [p for p in node["prov"] if p["page_no"] == e.page]
                assert len(match) == 1
                assert e.evidence is not None
                span = json.loads(e.evidence)["charspan"] or match[0]["charspan"]
                assert span == match[0]["charspan"]
                bounds.append(span)
                spans.append((e.page, span))
            assert b.text == g["text"][bounds[0][0] : bounds[-1][1]]
            found.append(index)
        gold_results.append(
            {
                "pages": g["pages"],
                "text_sha256": sha(g["text"].encode()),
                "kind": roles[g["label"]],
                "source_indices": [i for i, _ in selected],
            }
        )
        assert spans == [(p["page_no"], p["charspan"]) for p in node["prov"]]
    assert len(gold) == 50 and found == sorted(set(found))
    old = historical[-1]
    chunk = chunks[-1]
    assert all(
        c.exclusions == h.exclusions for c, h in zip(chunks, historical, strict=True)
    )
    assert (
        chunk.exclusions == old.exclusions
        and chunk.retained_blocks == old.retained_blocks
    )
    ids = [b.block_id for b in old.blocks]
    controls = [b for b in chunk.blocks if b.block_id in ids]
    assert len(ids) == 9 and [b.block_id for b in controls] == ids
    correspondence = json.loads((SEED / "correspondence.json").read_bytes())
    baseline = json.loads((SEED / "current-source.json").read_bytes())
    fixed = json.loads(
        checked(
            FINAL / "fixed-expectations.json",
            json.loads((FINAL / "hashes.json").read_bytes())["fixed-expectations.json"],
        )
    )
    (case,) = [c for c in fixed["cases"] if c["owned_pages"] == list(range(71, 81))]
    old_digest = case["chunk_sha256s"][-1]
    original_digest = fixed["source_sha256"]
    full = json.loads(
        checked(BOOK / f"snapshots/source/{original_digest}.json", original_digest)
    )
    originals = {entry["block_id"]: entry for entry in full["blocks"]}
    differences = []
    for b, prior in zip(controls, old.blocks, strict=True):
        assert b.model_dump(exclude={"text"}) == prior.model_dump(exclude={"text"})
        (block,) = [
            s
            for s in source.blocks
            if any(
                f["block"]["block_id"] == b.block_id
                and f["chunk_sha256"] == digests[-1]
                for f in s.model_dump()["structure"]["fragments"]
            )
        ]
        (row,) = [
            row
            for row in correspondence
            if [old_digest, b.block_id] in row["fragments"]
        ]
        (fixed_control,) = [
            item
            for item in case["normal_or_local_uncertainty_blocks"]
            if item["original_block_id"] == row["original_block_id"]
        ]
        (base,) = [
            s for s in baseline["blocks"] if s["block_id"] == row["selected_block_id"]
        ]
        assert (
            base["text"] == prior.text
            and base["kind"] == prior.kind
            and base["locations"]
            == [l.model_dump(mode="json") for l in block.locations]
        )
        original_block = originals[row["original_block_id"]]
        # #83 fingerprints the complete original block, before selected-scope IDs.
        fingerprint = sha(
            json.dumps(original_block, ensure_ascii=False, sort_keys=True).encode()
        )
        assert fingerprint == fixed_control["content_structure_location_sha256"]
        assert (
            original_block["text"] == base["text"]
            and original_block["kind"] == base["kind"]
            and original_block["locations"] == base["locations"]
        )
        assert [
            [f["chunk_sha256"], f["block"]["block_id"]]
            for f in original_block["structure"]["fragments"]
        ] == row["fragments"]
        assert reaches_output(block)
        control_results.append(
            {
                "page": b.evidence[0].page,
                "kind": b.kind,
                "source_index": source.blocks.index(block),
                "verified_original_block_fingerprint": fingerprint,
                "old_sha256": sha(prior.text.encode()),
                "adopted_sha256": sha(b.text.encode()),
            }
        )
        if b.kind == "body":
            (e,) = b.evidence
            assert e.ref
            n = raw["texts"][int(e.ref.split("/")[-1])]
            original = n["text"]
            diff = "-" if e.page == 76 else " "
            assert e.page in (76, 78)
            positions = [
                i
                for i, c in enumerate(prior.text)
                if c == diff and prior.text[:i] + prior.text[i + 1 :] == original
            ]
            assert len(positions) == 1
            offset = positions[0]
            left = re.search(r"[A-Za-z]+$", prior.text[:offset])
            right = re.match(r"[A-Za-z]+", prior.text[offset + 1 :])
            assert left and right
            (prov,) = [p for p in n["prov"] if p["page_no"] == e.page]
            bbox = prov["bbox"]
            assert bbox["coord_origin"] == "BOTTOMLEFT"
            doc = manifest["document"]
            space_evidence = None
            with pdfium.PdfDocument(
                checked(workspace / doc["artifact"], doc["sha256"])
            ) as pdf:
                tp = pdf[e.page - 1].get_textpage()
                local = tp.get_text_bounded(bbox["l"], bbox["b"], bbox["r"], bbox["t"])
                if e.page == 78:
                    space_evidence = word_evidence(tp, bbox, left[0], right[0])
            hits = local.count(left[0] + diff + right[0])
            assert hits == (1 if e.page == 78 else 0)

            assert b.text == (original if e.page == 76 else prior.text)
            qa = [
                q
                for q in source.uncertainties
                if block.block_id in q.target_ids
                and original in q.reason
                and prior.text in q.reason
                and any(
                    l.format == "pdf" and l.extraction_ref == e.ref and l.page == e.page
                    for l in q.locations
                )
            ]
            assert bool(qa) == (e.page == 76)
            differences.append(
                {
                    "page": e.page,
                    "offset": positions[0],
                    "docling_adopted": b.text == original,
                    "located_disagreement": bool(qa),
                    "bounded_hits": hits,
                    "word_boundary_drawing": space_evidence,
                }
            )
        elif b.kind == "code":
            lines = listing_evidence(b, prior, block, source, raw, workspace, manifest)
            blank_count_uncertain += sum(
                1 for row in lines if row["located_blank_count_uncertainty"]
            )
            control_results[-1]["lines"] = lines
        else:
            assert b.text == prior.text
    assert [r["source_index"] for r in control_results] == sorted(
        {r["source_index"] for r in control_results}
    )
    assert {
        i
        for i, b in enumerate(source.blocks)
        if any(l.format == "pdf" and 76 <= l.page <= 80 for l in b.locations)
    } == {
        i
        for i in found
        if any(l.format == "pdf" and l.page >= 76 for l in source.blocks[i].locations)
    } | {r["source_index"] for r in control_results}
    return {
        "gold_evidence": gold_results,
        "control_evidence": control_results,
        "gold": len(gold),
        "ordered_output_blocks": len(found),
        "controls": len(controls),
        "blank_count_uncertain": blank_count_uncertain,
        "local_differences": differences,
        "units": len(units),
        "english_sha256": sha(english.encode()),
        "chunks": digests,
    }


def replay(output, *, listings=False, roles=False):
    from doc_translate.inference import GenerationConfig
    from doc_translate.pdf_correction import correct_pdf, load_pdf_correction

    assert output.absolute().parent.parent == ROOT / "work"
    output = new_directory(output, BOOK)
    inventory = {}
    seeds = [SEED.parent / f"seed-{n}" for n in (111, 346)] if listings else [SEED]

    if roles:
        seeds = [SEED.parent / "seed-181"]

    def read(path, h=None):
        assert path == path.resolve() and (
            path.is_relative_to(BOOK) or path.parent in (FINAL, *seeds)
        )
        mtime = path.stat().st_mtime_ns
        h = h or sha(path.read_bytes())
        data = checked(path, h)
        assert path.stat().st_mtime_ns == mtime
        initial = inventory.setdefault(str(path), (h, mtime))
        assert initial == (h, mtime)
        return data

    try:
        old_ids = []
        for seed in seeds:
            rows = json.loads(read(seed / "correspondence.json"))
            old_ids.extend(
                dict.fromkeys(h for row in rows for h, _ in row["fragments"])
            )
            if not roles:
                read(seed / "current-source.json")
        scopes = [
            list(range(n, n + 5)) for n in ((111, 116, 121) if listings else (71, 76))
        ]
        if roles:
            scopes = [list(range(n, n + 5)) for n in (181, 186)]
        if listings:
            scopes.append([346])
        assert len(old_ids) == len(scopes)
        read(BOOK / ".writer.lock")
        with tempfile.TemporaryDirectory(dir=output, prefix="isolated-") as tmp:
            parent = Path(tmp)
            workspace = parent / "new"
            workspace.mkdir()
            digests = []
            for i, h in enumerate(old_ids):
                c = json.loads(read(BOOK / f"corrections/pdf/{h}.json", h))
                assert c["owned_pages"] == scopes[i]
                dh = c["draft_sha256"]
                rel = f"drafts/pdf/{dh}.json"
                if not (workspace / rel).exists():
                    draft_data = read(BOOK / rel, dh)
                    d = json.loads(draft_data)
                    for name, data in [(rel, draft_data)] + [
                        (a["artifact"], read(BOOK / a["artifact"], a["sha256"]))
                        for a in [
                            d["document"],
                            d["raw"],
                            *(p["image"] for p in d["pages"]),
                        ]
                    ]:
                        dest = workspace / name
                        assert dest.resolve().is_relative_to(workspace)
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        dest.write_bytes(data)
                record_bytes = read(BOOK / c["run"]["artifact"], c["run"]["sha256"])
                load_pdf_correction(
                    BOOK, h
                )  # historical request/projection/pages, no workspace lock
                record_path = parent / f"{i}.json"
                record_path.write_bytes(record_bytes)
                record = json.loads(record_bytes)
                assert record["status"] == "validated"
                command = [
                    sys.executable,
                    SCRIPT,
                    "wire",
                    str(record_path),
                    c["run"]["sha256"],
                ]

                marker = record_path.with_suffix(".sent")

                def before_send(config, request, pf, marker=marker):
                    marker.touch(exist_ok=False)

                digests.append(
                    correct_pdf(
                        workspace=workspace,
                        draft_sha256=dh,
                        owned_pages=c["owned_pages"],
                        reference_pages=c["reference_pages"],
                        config=GenerationConfig.model_validate(record["config"]),
                        policy=record["request"]["policy"],
                        process_command=command,
                        before_send=before_send,
                    )
                )
                assert record_path.with_suffix(".sent").is_file()
                if roles:
                    h = digests[-1]
                    stored = json.loads(
                        checked(workspace / f"corrections/pdf/{h}.json", h)
                    )["run"]
                    new = json.loads(
                        checked(workspace / stored["artifact"], stored["sha256"])
                    )
                    (attempt,) = new["attempts"]
                    assert (
                        new["status"] == "validated"
                        and attempt["final"] == record["attempts"][-1]["final"]
                    )
            # Gold never enters the wire, request, or workspace. Compare in a fresh process.
            if not (listings or roles):
                hashes = json.loads(read(FINAL / "hashes.json"))
                fixed = json.loads(
                    read(
                        FINAL / "fixed-expectations.json",
                        hashes["fixed-expectations.json"],
                    )
                )
                source_digest = fixed["source_sha256"]
                read(BOOK / f"snapshots/source/{source_digest}.json", source_digest)
                gold_name = "missing-content-gold.json"
                read(FINAL / gold_name, hashes[gold_name])
            result = subprocess.run(
                [
                    sys.executable,
                    SCRIPT,
                    "compare-108" if roles else "compare-98" if listings else "compare",
                    str(workspace),
                    *digests,
                    *old_ids,
                ],
                capture_output=True,
                check=False,
            )
            if result.returncode:
                (output / "compare-error.log").write_bytes(result.stderr)
                raise AssertionError("fresh comparison failed")
            observations = json.loads(
                (parent / "listing-observations.json").read_bytes()
                if listings or roles
                else result.stdout
            )
            if listings or roles:
                for path, (digest, mtime) in observations["comparison_inputs"].items():
                    assert inventory.setdefault(path, (digest, mtime)) == (
                        digest,
                        mtime,
                    )
    finally:
        for p, (h, mtime) in inventory.items():
            assert (
                sha(Path(p).read_bytes()) == h and Path(p).stat().st_mtime_ns == mtime
            )
        write_json(output / "inputs.json", inventory)
    finish(output, observations)
    if listings or roles:
        print(json.dumps({"chunks": len(digests)}))
        return
    print(
        json.dumps(
            {k: v for k, v in observations.items() if not k.endswith("_evidence")}
        )
    )


if __name__ == "__main__":
    # No exception text or local values: either can carry book content.
    def private_error(t, v, tb):
        import traceback

        print(
            t.__name__,
            [(Path(f.filename).name, f.lineno) for f in traceback.extract_tb(tb)],
            file=sys.stderr,
        )

    sys.excepthook = private_error
    sys.addaudithook(deny_network)
    if not __debug__:
        raise RuntimeError("Assertions required")
    if sys.argv[1] in ("wire", "compare", "compare-98", "compare-108"):
        target = Path(sys.argv[2])
        parent = target.parent
        assert (
            target == target.resolve()
            and parent.name.startswith("isolated-")
            and parent.parents[2] == ROOT / "work"
            and parent.stat().st_mode & 0o077 == 0
        )
    if sys.argv[1] == "wire":
        wire(Path(sys.argv[2]), sys.argv[3])
    elif sys.argv[1] == "compare":
        from doc_translate.pdf_correction import load_pdf_correction

        assert target.name == "new" and not (target / "source-snapshot.json").exists()
        gold = json.loads(
            checked(
                FINAL / "missing-content-gold.json",
                json.loads((FINAL / "hashes.json").read_bytes())[
                    "missing-content-gold.json"
                ],
            )
        )
        print(
            json.dumps(
                compare(
                    Path(sys.argv[2]),
                    sys.argv[3:5],
                    [load_pdf_correction(BOOK, h) for h in sys.argv[5:7]],
                    gold,
                )
            )
        )
    elif sys.argv[1] == "compare-98":
        from pdf_listing_comparison import compare_listings

        assert target.name == "new" and not (target / "source-snapshot.json").exists()
        write_json(
            target.parent / "listing-observations.json",
            compare_listings(target, sys.argv[3:7], sys.argv[7:]),
        )
    elif sys.argv[1] == "compare-108":
        from pdf_role_comparison import compare_roles

        assert target.name == "new" and not (target / "source-snapshot.json").exists()
        write_json(
            target.parent / "listing-observations.json",
            compare_roles(target, sys.argv[3:5], sys.argv[5:]),
        )
    elif sys.argv[1] == "--roles":
        replay(Path(sys.argv[2]), roles=True)
    elif sys.argv[1] == "--listings":
        replay(Path(sys.argv[2]), listings=True)
    else:
        replay(Path(sys.argv[1]))
