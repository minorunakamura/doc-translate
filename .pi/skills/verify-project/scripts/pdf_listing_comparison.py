"""Post-render checks for the four private #98 chunks. No provider or adoption calls."""

import ctypes
import html
import json
import re
from contextlib import closing
from math import isclose
from xml.etree import ElementTree

import pypdfium2 as pdfium
from pdf_adoption_replay import FINAL, checked, listing_evidence, sha
from PIL import Image

from doc_translate.pdf_correction import load_pdf_correction
from doc_translate.pdf_source import assemble_pdf_source
from doc_translate.render import render_document
from doc_translate.snapshot import save_source_snapshot
from doc_translate.units import generate_translation_units


def decoded(unit):
    text = unit.source_text
    for token in unit.tokens:
        text = text.replace(token.start_marker, token.value["text"])
        if token.end_marker:
            text = text.replace(token.end_marker, "")
    return text


def table_evidence(table, units, english, raw, tp, image, size):
    cells = table.structure["cells"]
    ref = table.locations[0].extraction_ref
    node = raw["texts"][int(ref.split("/")[-1])]
    (prov,) = node["prov"]
    box = prov["bbox"]
    assert node["self_ref"] == ref and prov["page_no"] == 346
    assert (table.structure["rows"], table.structure["columns"]) == (6, 2)
    assert len(cells) == len(units) == 12
    (markup,) = re.findall(r"^<table>.*?</table>", english, re.DOTALL)
    rows = ElementTree.fromstring(markup).findall("tr")
    assert len(rows) == 6 and all(len(r.findall("td")) == 2 for r in rows)
    result = []
    for i, cell in enumerate(cells):
        r, c = divmod(i, 2)
        text = cell["text"]
        (ev,) = cell["evidence"]
        (u,) = [u for u in units if u.cell_id == cell["cell_id"]]
        assert (
            ev["page"] == 346
            and ev["ref"] == ref
            and (cell["row"], cell["column"]) == (r, c)
        )
        assert u.locations[0].extraction_ref == ref and decoded(u) == text
        assert "".join(rows[r][c].itertext()).removesuffix("※") == text
        assert node["text"].count(text) == 1
        with closing(tp.search(text, match_case=True)) as search:
            hits = []
            while hit := search.get_next():
                k, count = hit
                bounds = [tp.get_charbox(j) for j in range(k, k + count)]
                l, b, rt, t = (
                    min(v[0] for v in bounds),
                    min(v[1] for v in bounds),
                    max(v[2] for v in bounds),
                    max(v[3] for v in bounds),
                )
                if (
                    box["l"] - 1 <= l
                    and rt <= box["r"] + 1
                    and box["b"] - 1 <= b
                    and t <= box["t"] + 1
                ):
                    assert tp.get_text_range(k, count) == text
                    hits.append((l, b, rt, t))
        ((l, b, rt, t),) = hits
        if c:
            prev = result[-1]["bbox"]
            assert isinstance(prev, list)
            assert prev[2] < l and max(prev[1], b) < min(prev[3], t)
        if r:
            above = result[-2]["bbox"]
            assert isinstance(above, list) and t < above[1]
        # Save the image coordinates, not a transcription inferred from raster density.
        sx, sy = image.width / size[0], image.height / size[1]
        pixels = [
            round(l * sx),
            round(image.height - t * sy),
            round(rt * sx),
            round(image.height - b * sy),
        ]
        crop = image.crop(pixels)
        assert crop.getextrema()[0] < 128
        start = node["text"].index(text)
        result.append(
            {
                "row": r,
                "column": c,
                "ref": ref,
                "span": [start, start + len(text)],
                "bbox": [l, b, rt, t],
                "pixels": pixels,
                "unit": u.unit_id,
                "sha256": sha(text.encode()),
            }
        )
    return result


def compare_listings(workspace, digests, old_ids):
    chunks = [load_pdf_correction(workspace, h) for h in digests]
    assert [c.owned_pages for c in chunks] == [
        list(range(n, n + 5)) for n in (111, 116, 121)
    ] + [[346]]
    source = assemble_pdf_source(
        workspace=workspace, chunk_sha256s=digests, snapshot_id="listing-replay"
    )
    save_source_snapshot(workspace, source)
    units = generate_translation_units(source)
    out = render_document(workspace)["artifacts"]
    english = (workspace / out["english"]).read_text()
    qa = (workspace / out["qa"]).read_text()
    anchors = {
        b["block_id"]: b["output_anchor"]
        for b in json.loads((workspace / out["source_map"]).read_bytes())["blocks"]
    }
    # Only now read the comparator. Never pass it to correct_pdf or the wire process.
    inputs = {}

    def read(path, digest=None):
        mtime = path.stat().st_mtime_ns
        digest = digest or sha(path.read_bytes())
        data = checked(path, digest)
        inputs[str(path)] = [digest, mtime]
        return data

    hashes = json.loads(read(FINAL / "hashes.json"))
    gold = json.loads(read(FINAL / "code-gold.json", hashes["code-gold.json"]))
    fixed = json.loads(
        read(FINAL / "fixed-expectations.json", hashes["fixed-expectations.json"])
    )
    assert [
        h
        for case in fixed["cases"]
        if case["owned_pages"][0] in (111, 346)
        for h in case["chunk_sha256s"]
    ] == old_ids
    (draft,) = {c.draft_sha256 for c in chunks}
    manifest = json.loads(checked(workspace / f"drafts/pdf/{draft}.json", draft))
    raw = json.loads(
        checked(workspace / manifest["raw"]["artifact"], manifest["raw"]["sha256"])
    )
    records = {}
    for h, c in zip(digests, chunks, strict=True):
        record = json.loads(checked(workspace / c.run.artifact, c.run.sha256))
        (text,) = [
            v["text"]
            for v in record["attempts"][-1]["final"]["message"]["content"]
            if v["type"] == "text"
        ]
        records[h] = json.loads(text)["blocks"]

    def on(page, kind):
        (block,) = [
            b
            for b in source.blocks
            if b.kind == kind
            and any(l.format == "pdf" and l.page == page for l in b.locations)
        ]
        return block

    def actual_units(b):
        return [u for u in units if u.block_id == b.block_id]

    scores = {}
    paragraphs = []
    qa_rows = json.loads(qa)["uncertainties"]
    for pages, counts in (([115, 116], [4, 29]), ([119, 120, 121], [6, 45, 21])):
        (case,) = [g for g in gold if g["pages"] == pages]
        expected = iter(re.findall(r"([^\n]+)(\n*)", case["expected_text"]))
        for page, count in zip(pages, counts, strict=True):
            block = on(page, "code")
            fragments = block.structure["fragments"]
            assert "\n".join(
                f["block"]["text"] for f in fragments
            ) == block.text and not actual_units(block)
            (fence,) = re.finditer(
                rf'^<a id="{re.escape(anchors[block.block_id])}"></a>\n\n(`{{3,}})[^`\n]*\n'
                + re.escape(block.text)
                + r"\n\1$",
                english,
                re.MULTILINE,
            )
            (frag,) = [
                f for f in fragments if f["block"]["evidence"][0]["page"] == page
            ]
            h = frag["chunk_sha256"]
            (item,) = [
                b
                for b in chunks[digests.index(h)].blocks
                if b.block_id == frag["block"]["block_id"]
            ]
            assert frag["block"]["text"] == item.text
            (p,) = [b for b in records[h] if b["block_id"] == item.block_id]
            prior = item.model_copy(update={"text": p["text"]})
            lines = [l for l in item.text.splitlines() if l.strip()]
            assert len(lines) == count
            local = [
                q
                for q in qa_rows
                if block.block_id in q["block_ids"]
                and anchors[block.block_id] in q["output_anchors"]
                and all(l["page"] == page for l in q["locations"])
            ]
            margins = [
                q for q in local if q["reason"].startswith("Unverified common margin")
            ]
            proposed = [l for l in prior.text.splitlines() if l.strip()]
            proof = []
            caption = on(page, "caption").text if page in (115, 119) else None
            if page in (115, 116):
                proof = listing_evidence(
                    item,
                    prior,
                    block,
                    source,
                    raw,
                    workspace,
                    manifest,
                    caption=caption,
                    relative=page == 116,
                )
                for r in proof:
                    for q in r["qa"]:
                        assert any(
                            all(
                                v[k] == q[k]
                                for k in ("locations", "reason", "target_ids")
                            )
                            for v in local
                        )
            if page in (116, 120, 121):
                (margin,) = margins
                loc = margin["locations"]
                assert len(loc) == count
                shift = len(proposed[0]) - len(lines[0])
                assert (
                    shift > 0
                    and f"{shift} spaces shallower" in margin["reason"]
                    and "absolute indentation" in margin["reason"]
                )
                assert proposed == [" " * shift + l for l in lines]
            else:
                assert not margins
            for i, line in enumerate(lines):
                want, fixed_separator = next(expected)
                diff = len(line) - len(want)
                assert (
                    want == " " * (-diff) + line
                    if diff <= 0
                    else line == " " * diff + want
                )
                row: dict[str, object] = proof[i] if proof else {"line": i + 1}
                row.update(
                    fixed_delta=diff,
                    fixed_newlines=len(fixed_separator),
                    fixed_sha256=sha(want.encode()),
                    output_line_sha256=sha(line.encode()),
                )
                if proof and i + 1 < count:
                    assert row["expected_newlines"] == len(fixed_separator)
                if margins:
                    ev = json.loads(loc[i]["evidence"])
                    ref = loc[i]["extraction_ref"]
                    span = ev["charspan"]
                    a, b = span
                    n = raw["texts"][int(ref.split("/")[-1])]
                    assert n["text"][a:b] == line.lstrip(" ") and ev[
                        "position"
                    ].startswith("line origin")
                    row.update(
                        margin_qa={"ref": ref, "span": span, "position": ev["position"]}
                    )
                    if proof:
                        assert (ref, span) == (row["ref"], row["span"])
                if not proof:
                    scores.setdefault(str(page), []).append(row)
            if proof:
                scores[str(page)] = proof
            if page in (115, 116):
                assert all(
                    r["fixed_delta"] == (0 if page == 115 else -2) for r in proof
                )
                k = source.blocks.index(block)
                neighbors = (
                    source.blocks[k - 2 : k]
                    if page == 115
                    else source.blocks[k + 1 : k + 2]
                )
                assert [b.kind for b in neighbors] == (
                    ["body", "caption"] if page == 115 else ["body"]
                )
                positions = []
                for b in neighbors:
                    selected = actual_units(b)
                    assert selected and "".join(map(decoded, selected)) == b.text
                    rendered = html.unescape(english)
                    assert rendered.count(b.text) == 1
                    pos = rendered.index(b.text)
                    positions.append(pos)
                    paragraphs.append(
                        {
                            "page": page,
                            "kind": b.kind,
                            "units": [u.unit_id for u in selected],
                            "offset": pos,
                        }
                    )
                fpos = len(html.unescape(english[: fence.start()]))
                assert positions == sorted(positions) and (
                    positions[-1] < fpos if page == 115 else positions[0] > fpos
                )
        assert next(expected, None) is None
    body = source.blocks[source.blocks.index(on(116, "code")) + 1]
    body_location = body.locations[0]
    assert body_location.format == "pdf" and body_location.extraction_ref is not None
    body_node = raw["texts"][int(body_location.extraction_ref.split("/")[-1])]
    box = body_node["prov"][0]["bbox"]
    with pdfium.PdfDocument(workspace / manifest["document"]["artifact"]) as doc:
        tp = doc[115].get_textpage()
        first = tp.get_text_bounded(
            box["l"], box["b"], box["r"], box["t"]
        ).splitlines()[0]
        with closing(tp.search(first, match_case=True)) as search:
            (j, _) = search.get_next()
            assert search.get_next() is None
        x, y = ctypes.c_double(), ctypes.c_double()
        assert pdfium.raw.FPDFText_GetCharOrigin(tp, j, x, y)
        body_x = x.value
    for page in (115, 116):
        img = next(p["image"] for p in manifest["pages"] if p["page"] == page)
        read(FINAL.parent / f"root-survey/seed-111/page-{page}.png", img["sha256"])

    rows = scores["116"]
    assert (
        rows[0]["indent"] == 2
        and rows[2]["indent"] == 0
        and isclose(rows[2]["original_xy"][0], body_x, abs_tol=0.02)
    )
    table = on(346, "table")
    image_info = next(p["image"] for p in manifest["pages"] if p["page"] == 346)
    image_path = FINAL.parent / "root-survey/seed-346/page-346.png"
    read(image_path, image_info["sha256"])
    with (
        pdfium.PdfDocument(workspace / manifest["document"]["artifact"]) as doc,
        Image.open(image_path) as im,
    ):
        pg = doc[345]
        cells = table_evidence(
            table,
            actual_units(table),
            english.split(f'<a id="{anchors[table.block_id]}"></a>\n\n')[1],
            raw,
            pg.get_textpage(),
            im.convert("L"),
            pg.get_size(),
        )
    return {
        "chunks": [c.model_dump(mode="json") for c in chunks],
        "source": source.model_dump(mode="json"),
        "units": [u.model_dump(mode="json") for u in units],
        "english": english,
        "qa": qa,
        "comparison_inputs": inputs,
        "score": {
            "lines": scores,
            "cells": cells,
            "paragraphs": paragraphs,
            "body_x": body_x,
        },
    }
