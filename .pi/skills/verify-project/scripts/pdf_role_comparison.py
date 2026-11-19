"""Fresh, post-render comparison of the two #108 scopes (no adoption or provider)."""

import html
import itertools
import json
import re
from io import BytesIO

import pypdfium2 as pdfium
from docling_parse.pdf_parser import (
    DoclingThreadedPdfParser,
    RenderConfig,
    ThreadedPdfParserConfig,
)
from pdf_adoption_replay import FINAL, SEED, checked, listing_evidence, sha
from pdf_listing_comparison import decoded
from PIL import Image, ImageChops

from doc_translate.pdf_correction import load_pdf_correction
from doc_translate.pdf_source import assemble_pdf_source
from doc_translate.render import render_document
from doc_translate.snapshot import save_source_snapshot
from doc_translate.units import generate_translation_units


def compare_roles(workspace, digests, old_ids):
    chunks = [load_pdf_correction(workspace, h) for h in digests]
    assert [c.schema_version for c in chunks] == [11, 11]
    source = assemble_pdf_source(
        workspace=workspace, chunk_sha256s=digests, snapshot_id="shared-roles"
    )
    save_source_snapshot(workspace, source)
    units = generate_translation_units(source)
    out = render_document(workspace)["artifacts"]
    english = (workspace / out["english"]).read_text()
    qa = json.loads((workspace / out["qa"]).read_bytes())["uncertainties"]
    anchors = {
        v["block_id"]: v["output_anchor"]
        for v in json.loads((workspace / out["source_map"]).read_bytes())["blocks"]
    }
    # Index actual English sections, not an independently re-rendered source snapshot.
    matches = list(re.finditer(r'<a id="([^"]+)"></a>', english))
    bodies = {
        m[1]: english[
            m.end() : matches[i + 1].start() if i + 1 < len(matches) else len(english)
        ]
        for i, m in enumerate(matches)
    }
    assert len(matches) == len(bodies)
    order = [anchors[b.block_id] for b in source.blocks]
    assert [m[1] for m in matches if m[1] in order] == order
    checked_blocks = []
    for block in source.blocks:
        selected = [u for u in units if u.block_id == block.block_id]
        body = bodies[anchors[block.block_id]]
        if block.kind == "code":
            (fence,) = re.findall(
                r"^(`{3,})[^`\n]*\n([\s\S]*?)\n\1$", body, re.MULTILINE
            )
            assert not selected and fence[1] == block.text.removesuffix("\n")
        elif block.kind in ("body", "caption", "footnote", "heading", "list"):
            assert selected and "".join(decoded(u) for u in selected) == block.text
            assert html.unescape(body).count(block.text) == 1
        checked_blocks.append(
            [block.kind, anchors[block.block_id], [u.unit_id for u in selected]]
        )
    inputs = {}

    def read(p, h=None):
        m = p.stat().st_mtime_ns
        data = checked(p, h or sha(p.read_bytes()))
        fingerprint = (sha(data), m)
        assert (
            p.stat().st_mtime_ns == m
            and inputs.setdefault(str(p), fingerprint) == fingerprint
        )
        return data

    hashes = json.loads(read(FINAL / "hashes.json"))
    fixed = json.loads(
        read(FINAL / "fixed-expectations.json", hashes["fixed-expectations.json"])
    )
    (case,) = [c for c in fixed["cases"] if c["owned_pages"][0] == 181]
    assert case["chunk_sha256s"] == old_ids
    (dh,) = {c.draft_sha256 for c in chunks}
    d = json.loads(checked(workspace / f"drafts/pdf/{dh}.json", dh))
    raw = json.loads(checked(workspace / d["raw"]["artifact"], d["raw"]["sha256"]))
    pdf = checked(workspace / d["document"]["artifact"], d["document"]["sha256"])
    seed = SEED.parent / "seed-181"
    image_index = json.loads(read(seed / "image-index.json"))
    # Repaint precisely these pages at the saved acquisition scale. No OCR/converter.
    config = RenderConfig()
    config.scale = 1
    painter = DoclingThreadedPdfParser(
        ThreadedPdfParserConfig(threads=1, render_config=config)
    )
    key = painter.load(BytesIO(pdf), page_range=(181, 190))
    seen = set()
    try:
        for result in painter.iterate_results():
            n = result.page_number
            (page,) = [p for p in d["pages"] if p["page"] == n]
            (image,) = [i for i in image_index if i["page"] == n]
            assert image["image_sha256"] == page["image"]["sha256"]
            with Image.open(
                BytesIO(read(seed / f"page-{n}.png", image["image_sha256"]))
            ) as original:
                painted = result.get_image(
                    scale=page["rendering"]["render_scale"]
                ).convert("RGB")
                assert (
                    painted.size == original.size
                    and ImageChops.difference(
                        painted, original.convert("RGB")
                    ).getbbox()
                    is None
                )
            seen.add(n)
    finally:
        painter.unload(key)
    assert seen == set(range(181, 191))
    correspondence = json.loads(read(seed / "correspondence.json"))

    def from_fixed(key):
        (binding,) = [r for r in correspondence if r["original_block_id"] == key]
        wanted = [(digests[old_ids.index(h)], key) for h, key in binding["fragments"]]
        (block,) = [
            b
            for b in source.blocks
            if [
                (f["chunk_sha256"], f["block"]["block_id"])
                for f in b.model_dump()["structure"]["fragments"]
            ]
            == wanted
        ]
        return block

    # Compare against the original proposal, not the adopted fragment's formatting.
    proposals = {}
    for h, c in zip(digests, chunks, strict=True):
        rec = json.loads(checked(workspace / c.run.artifact, c.run.sha256))
        (answer,) = [
            x["text"]
            for x in rec["attempts"][-1]["final"]["message"]["content"]
            if x["type"] == "text"
        ]
        for b in json.loads(answer)["blocks"]:
            proposals[h, b["block_id"]] = b

    def fragment(f):
        h = f["chunk_sha256"]
        key = f["block"]["block_id"]
        (item,) = [b for b in chunks[digests.index(h)].blocks if b.block_id == key]
        assert item.kind == "code" and f["block"]["text"] == item.text
        return item, item.model_copy(update={"text": proposals[h, key]["text"]})

    def printed(code, item, prior, caption=None, footnote=None):
        proof = listing_evidence(
            item,
            prior,
            code,
            source,
            raw,
            workspace,
            d,
            caption=caption,
            footnote=footnote,
        )
        for row in proof:
            for q in row["qa"]:
                assert any(
                    all(v[k] == q[k] for k in ("reason", "locations", "target_ids"))
                    and anchors[code.block_id] in v["output_anchors"]
                    for v in qa
                )
        return proof

    printed_lines = {}
    rows = []
    with pdfium.PdfDocument(pdf) as doc:
        for n, kind in (
            (181, "caption"),
            (182, "caption"),
            (183, "body"),
            (185, "footnote"),
            (186, "caption"),
            (187, "caption"),
        ):
            pairs = [
                (code, note)
                for code in source.blocks
                if code.kind == "code"
                for note in source.blocks
                if note.kind == kind
                and any(
                    a.format == "pdf"
                    and b.format == "pdf"
                    and a.page == b.page == n
                    and a.extraction_ref
                    and a.extraction_ref == b.extraction_ref
                    for a in code.locations
                    for b in note.locations
                )
            ]
            ((code, note),) = pairs
            assert abs(source.blocks.index(code) - source.blocks.index(note)) == 1
            if kind == "caption":
                assert note.structure["target_id"] == code.block_id
                (cap,) = note.model_dump()["structure"]["fragments"]
                (listing,) = code.model_dump()["structure"]["fragments"]
                assert (
                    cap["chunk_sha256"] == listing["chunk_sha256"]
                    and proposals[cap["chunk_sha256"], cap["block"]["block_id"]][
                        "target_id"
                    ]
                    == listing["block"]["block_id"]
                )
            (loc,) = [
                l
                for l in note.locations
                if l.format == "pdf" and l.page == n and l.extraction_ref
            ]
            assert loc.format == "pdf" and loc.extraction_ref and loc.evidence
            ref = loc.extraction_ref
            node = raw["texts"][int(ref.split("/")[-1])]
            (prov,) = node["prov"]
            box = prov["bbox"]
            assert box["coord_origin"] == "BOTTOMLEFT" and prov["page_no"] == n
            textpage = doc[n - 1].get_textpage()
            lines = textpage.get_text_bounded(
                box["l"], box["b"], box["r"], box["t"]
            ).splitlines()
            before = kind == "caption"
            assert before == (source.blocks.index(note) < source.blocks.index(code))
            assert (
                lines.pop(0 if before else -1) == note.text
                and node["text"].count(note.text) == 1
            )
            # In these six original items there is exactly one literal space between roles.
            # Compare the complete raw slice, not a normalized copy of the output.
            text = node["text"]
            if before:
                assert text.startswith(note.text + " ")
                l, r = len(note.text) + 1, len(text)
                note_span = [0, len(note.text)]
            else:
                assert text.endswith(" " + note.text)
                l, r = 0, len(text) - 1 - len(note.text)
                note_span = [r + 1, len(text)]
            assert json.loads(loc.evidence)["charspan"] == note_span
            (code_location,) = [
                v
                for v in code.locations
                if v.format == "pdf" and v.extraction_ref == ref and v.evidence
            ]
            assert (
                code_location.format == "pdf"
                and code_location.evidence
                and json.loads(code_location.evidence)["charspan"] == [l, r]
            )
            original = text[l:r]
            (f,) = code.model_dump()["structure"]["fragments"]
            item, prior = fragment(f)
            assert code.text == item.text
            if n in (181, 182, 186):
                assert code.text == original and code.text != prior.text
                reason = f"Line boundary or indentation not corroborated; adopted extraction {original!r} rather than correction {prior.text!r}"
                (q,) = [
                    q
                    for q in qa
                    if q["reason"] == reason
                    and q["target_ids"] == [code.block_id]
                    and q["output_anchors"] == [anchors[code.block_id]]
                ]
                # Locate every printed row in extraction order; QA must name exactly those lines.
                cursor = l
                for line, location in zip(lines, q["locations"], strict=True):
                    start = text.index(line, cursor, r)
                    end = start + len(line)
                    assert (
                        not text[cursor:start].strip()
                        and location["page"] == n
                        and location["extraction_ref"] == ref
                        and location["extraction_snapshot"] == dh
                    )
                    evidence = json.loads(location["evidence"])
                    assert (
                        evidence["charspan"] == [start, end]
                        and evidence["position"] == f"characters {start}:{end}"
                    )
                    cursor = end
                assert cursor == r
                mode = "raw-with-located-qa"
            elif n == 183:
                assert lines == [code.text] and code.text == original == prior.text
                mode = "literal"
            else:
                proof = printed(
                    code,
                    item,
                    prior,
                    caption=note.text if before else None,
                    footnote=None if before else note.text,
                )
                assert len(proof) == len(lines)
                printed_lines[code.block_id] = proof
                mode = "printed"
            # Check the fixed neighboring prose and its actual translation and display position.
            prior_blocks = json.loads(read(seed / f"page-{n}.json"))["original_blocks"]
            at = [
                i
                for i, b in enumerate(prior_blocks)
                if b["kind"] in ("code", kind)
                and any(l["extraction_ref"] == ref for l in b["locations"])
            ]
            assert len(at) == 2
            # Compare the fixed page's original blocks as a subsequence of actual English anchors.
            # Recovered blocks can intervene; a swap on the same side of the listing cannot.
            bound = [from_fixed(prior["block_id"]) for prior in prior_blocks]
            positions = [order.index(anchors[b.block_id]) for b in bound]
            assert all(i < j for i, j in itertools.pairwise(positions))
            for k in (min(at) - 1, max(at) + 1):
                if not 0 <= k < len(prior_blocks) or prior_blocks[k]["kind"] not in (
                    "body",
                    "caption",
                ):
                    continue
                prior = prior_blocks[k]
                neighbor = bound[k]
                assert neighbor.text == prior["text"] and neighbor.kind == prior["kind"]
            rows.append(
                {
                    "page": n,
                    "role": kind,
                    "printed_lines": len(lines),
                    "code": anchors[code.block_id],
                    "note": anchors[note.block_id],
                    "display": mode,
                }
            )
    gold = json.loads(read(FINAL / "code-gold.json", hashes["code-gold.json"]))
    for g in gold:
        if g["pages"] not in ([185, 186], [187, 188]):
            continue
        fragments = [from_fixed(k) for k in g["original_block_ids"]]
        assert all(f.kind == "code" for f in fragments) and source.blocks.index(
            fragments[0]
        ) <= source.blocks.index(fragments[1])
        # Fixed examples can differ in indentation, not in the ordered nonempty lines.
        # The independent painted-row check above (or below) already proves each actual column and separator.
        for code in fragments:
            if code.block_id not in printed_lines:
                (f,) = code.model_dump()["structure"]["fragments"]
                item, prior = fragment(f)
                assert code.text == item.text
                printed_lines[code.block_id] = printed(code, item, prior)
        actual = [
            line
            for code in fragments
            for line in code.text.splitlines()
            if line.strip()
        ]
        fixed_lines = [line for line in g["expected_text"].splitlines() if line.strip()]
        assert all(
            a.lstrip(" ") == f.lstrip(" ")
            for a, f in zip(actual, fixed_lines, strict=True)
        )
    # In the same two answers, check proposed extra blank lines even if a QA is missing.
    blank_boundaries = []
    for code in source.blocks:
        if code.kind != "code":
            continue
        fragments = [fragment(f) for f in code.model_dump()["structure"]["fragments"]]
        assert code.text == "\n".join(item.text for item, _ in fragments)
        for item, prior in fragments:
            if item.evidence[0].page not in (185, 188) or "\n\n" not in prior.text:
                continue  # Neighboring printed listings, not table/image reconstruction.
            proof = printed(code, item, prior)
            blank_boundaries.extend(
                row for row in proof if row["located_blank_count_uncertainty"]
            )
    page190 = json.loads(read(seed / "page-190.json"))
    (prior,) = [q for q in page190["warnings"] if "filename" in q["reason"].lower()]
    (target,) = prior["target_ids"]
    verified = from_fixed(target)
    (ref,) = {
        loc.extraction_ref
        for loc in verified.locations
        if loc.format == "pdf" and loc.extraction_ref
    }
    assert verified.text == raw["texts"][int(ref.split("/")[-1])]["text"]
    k = next(
        i for i, b in enumerate(page190["original_blocks"]) if b["block_id"] == target
    )
    for delta in (-1, 1):
        neighbor = page190["original_blocks"][k + delta]
        actual = from_fixed(neighbor["block_id"])
        assert (
            actual.text == neighbor["text"]
            and source.blocks.index(actual) == source.blocks.index(verified) + delta
        )
    (filename,) = [
        q
        for q in qa
        if q["reason"] == prior["reason"]
        and q["locations"] == prior["locations"]
        and verified.block_id in q["block_ids"]
    ]
    assert (
        anchors[verified.block_id] in filename["output_anchors"]
        and "※" in bodies[anchors[verified.block_id]]
    )
    return {
        "scope": [c.owned_pages for c in chunks],
        "printed_evidence": printed_lines,
        "blank_boundaries": blank_boundaries,
        "blocks": checked_blocks,
        "shared": rows,
        "filename_anchors": filename["output_anchors"],
        "comparison_inputs": inputs,
    }
