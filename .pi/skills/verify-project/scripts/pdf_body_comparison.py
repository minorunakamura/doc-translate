"""Post-render comparison for #100. Run inside the private book evidence parent."""

import html
import json
import math
import os
import re
import sys
from contextlib import closing
from difflib import SequenceMatcher
from io import BytesIO
from pathlib import Path

import pypdfium2 as pdfium
import pypdfium2.raw as native
from pdf_adoption_replay import BOOK, FINAL, ROOT, checked, deny_network, sha
from pdf_listing_comparison import decoded
from pdf_source_feasibility import new_directory
from PIL import Image, ImageChops

from doc_translate.pdf_correction import CorrectionAnswer, PDFCorrectionChunk
from doc_translate.units import TranslationUnit


def check_output(data):
    source = data["source"]
    english = html.unescape(data["english"])
    qa = data["qa"]
    anchors = {m["block_id"]: m["output_anchor"] for m in data["source_map"]["blocks"]}
    markers = list(re.finditer(r'<a id="([^"]+)"></a>', english))
    assert [m[1] for m in markers] == list(anchors.values())
    assert list(anchors) == [b["block_id"] for b in source["blocks"]]
    boundaries = [m.start() for m in markers[1:]] + [len(english)]
    sections = {
        key: english[m.end() : end]
        for key, m, end in zip(anchors, markers, boundaries, strict=True)
    }
    units = [TranslationUnit.model_validate(u) for u in data["units"]]
    for b in source["blocks"]:
        key = b["block_id"]
        us = [u for u in units if u.block_id == key]
        ev = [
            e for frag in b["structure"]["fragments"] for e in frag["block"]["evidence"]
        ]
        assert [
            (loc["page"], loc["extraction_ref"], json.loads(loc["evidence"]))
            for loc in b["locations"]
        ] == [(e["page"], e["ref"], e) for e in ev]
        if b["kind"] in ("code", "picture"):
            assert not us
        else:
            for u in us:
                assert re.findall(r"⟦=\d+⟧", u.source_text) == [
                    t.start_marker for t in u.tokens
                ]
                assert all(
                    t.kind == "literal" and t.end_marker is None for t in u.tokens
                )
            assert (
                us
                and "".join(decoded(u) for u in us) == b["text"]
                and b["text"] in sections[key]
            )
    assert all(c in qa["checks"] for c in source["checks"])
    for q in source["uncertainties"]:
        (rendered,) = [v for v in qa["uncertainties"] if all(v[k] == q[k] for k in q)]
        assert set(rendered["output_anchors"]) == {
            anchors[k] for k in rendered["block_ids"]
        }
        assert set(q["target_ids"]).intersection(anchors) == set(rendered["block_ids"])
    return english, anchors, sections


def check_drawing(original, prov, proposal, offset, image, evidence, used, context):
    # Locate from the independently bound proposal offset within this whole raw ref.
    # Submitted coordinates are compared only after the native glyph is known.
    page = prov["page_no"]
    with pdfium.PdfDocument(original) as pdf, Image.open(BytesIO(image)) as paper:
        p = pdf[page - 1]
        tp = p.get_textpage()
        box = prov["bbox"]
        assert box["coord_origin"] == "BOTTOMLEFT"
        l, b, r, t = (box[k] for k in ("l", "b", "r", "t"))
        printed = "\n".join(
            tp.get_text_bounded(l, b, r, t).replace("\x02", "").splitlines()
        )

        def pattern(text):
            return r"\n?".join("[ \n]" if c == " " else re.escape(c) for c in text)

        if context is None:
            assert re.fullmatch(pattern(proposal), printed)
            pos = len("".join(proposal[:offset].split()))
        else:
            # Locate by the unchanged runs on both sides, not by a submitted bbox.
            raw, left, right = context
            for flank in (left, right):
                needle = "".join(flank.split())
                assert needle
                for text in (raw, proposal, printed):
                    compact = "".join(text.split())
                    assert 0 <= compact.find(needle) == compact.rfind(needle)
            first = offset - len(left)
            last = offset + 1 + len(right)
            window = left + proposal[offset] + right
            assert proposal[first:last] == window
            expr = pattern(window)
            if first == 0:
                expr = "^" + expr
            if last == len(proposal):
                expr += r"\Z"
            match = re.search(expr, printed)
            assert match
            pos = len("".join((printed[: match.start()] + left).split()))
        glyphs = []
        for i in range(tp.count_chars()):
            c = chr(native.FPDFText_GetUnicode(tp, i))
            x, y, x2, y2 = tp.get_charbox(i)
            if (
                l <= (x + x2) / 2 <= r
                and b <= (y + y2) / 2 <= t
                and not c.isspace()
                and c != "\x02"
            ):
                glyphs.append((c, i))
        assert "".join(c for c, _ in glyphs) == "".join(printed.split())
        symbol, glyph = glyphs[pos]
        obj = native.FPDFText_GetTextObject(tp, glyph)
        assert symbol == proposal[offset] and (page, glyph) not in used
        used.add((page, glyph))
        assert native.FPDFText_IsGenerated(tp, glyph) == 0
        assert (
            native.FPDFTextObj_GetTextRenderMode(obj) == native.FPDF_TEXTRENDERMODE_FILL
        )
        bounds = tp.get_charbox(glyph)
        # The four isolated dashes have ink in the page PNG which disappears
        # when their text object is suppressed. Exclude other glyphs’ pixels.
        w, h = p.get_size()

        def pixels(bounds):
            x, y, x2, y2 = bounds
            return (
                math.floor(x * paper.width / w),
                math.floor((h - y2) * paper.height / h),
                math.ceil(x2 * paper.width / w),
                math.ceil((h - y) * paper.height / h),
            )

        crop = pixels(bounds)
        samples = {
            (x, y) for x in range(crop[0], crop[2]) for y in range(crop[1], crop[3])
        }
        for _, i in glyphs:
            if i != glyph:
                x, y, x2, y2 = pixels(tp.get_charbox(i))
                samples = {
                    pt for pt in samples if not (x <= pt[0] < x2 and y <= pt[1] < y2)
                }
        with closing(
            pdfium.PdfBitmap.new_native(*paper.size, native.FPDFBitmap_BGR)
        ) as bitmap:

            def draw():
                bitmap.fill_rect((255, 255, 255, 255), 0, 0, *paper.size)
                native.FPDF_RenderPageBitmap(bitmap, p, 0, 0, *paper.size, 0, 0)
                return bitmap.to_pil().convert("L").tobytes()

            painted = draw()
            assert native.FPDFPageObj_SetIsActive(obj, False)
            hidden = draw()
            paper_ink = paper.convert("L").tobytes()
            assert any(
                max(painted[i], paper_ink[i]) < 128 and hidden[i] == 255
                for i in (y * paper.width + x for x, y in samples)
            )
        expected = ", ".join(f"{x:.3f}" for x in bounds)
        for loc in evidence:
            a, b = loc["charspan"]
            assert (
                loc["position"]
                == f"characters {a}:{b}; glyph bbox ({expected}) pt, bottom-left"
            )


def check_difference_qa(rows, accepted, reason, spans, page, ref, original, proposal):
    wanted = f"{reason} not corroborated at this location; retained extraction"
    found = []
    for q, locations in rows:
        local = [e for e in locations if e["page"] == page and e["ref"] == ref]
        if not local:
            continue
        if accepted:
            assert q["reason"] not in (
                f"Extraction {original!r} differs from correction {proposal!r}",
                f"Adopted extraction {original!r} rather than correction {proposal!r}",
            )
        if [e["charspan"] for e in local] != spans:
            continue
        if accepted:
            assert not q["reason"].endswith("retained extraction") and not q[
                "reason"
            ].startswith(("Extraction ", "Adopted extraction "))
        elif q["reason"] == wanted:
            assert q["origin"] == "processing" and local == locations
            assert all(
                e["reason"] == reason and e["position"] == f"characters {a}:{b}"
                for e, (a, b) in zip(local, spans, strict=True)
            )
            found.append(q)
    assert len(found) == int(not accepted)
    return found


def check_figure(
    fig,
    right,
    saved,
    prior,
    by_local,
    c,
    raw,
    sections,
    english,
    image,
    proof,
    hashes,
    read,
):
    f = saved[fig]
    sf = by_local[fig]
    assert sf["kind"] == f["kind"] == "picture"
    st = sf["structure"]
    assert (
        prior[fig]["region"] == st["region"] == f["region"]
        and st["region"]["page"] == right
    )
    assert st["caption_ids"] == f["caption_ids"] == prior[fig][
        "caption_ids"
    ] == [] and all(m in f["members"] for m in prior[fig]["members"])
    pic = raw["pictures"][int(f["evidence"][0]["ref"].split("/")[-1])]
    refs = {m["ref"]["ref"] for m in f["members"]}
    assert (
        refs == {ch["$ref"] for ch in pic["children"]}
        and all(m["status"] == "confirmed" for m in f["members"])
        and st["members"] == f["members"]
    )
    assert refs <= {
        e["ref"] for v in c["retained_blocks"] for e in v["evidence"]
    } and not any(e["ref"] in refs for v in saved.values() for e in v["evidence"])
    (asset,) = re.findall(r'<img src="([^"]+)"', sections[sf["block_id"]])
    assert english.count(f'<img src="{asset}"') == 1
    box = tuple(int(f["region"][k]) for k in ("left", "top", "right", "bottom"))
    l, t, r, bottom = box
    with (
        Image.open(BytesIO(image)) as page_im,
        Image.open(BytesIO(read(proof / asset, hashes[asset]))) as crop,
    ):
        page_im = page_im.convert("RGB")
        expected = page_im.crop((l, t, r, bottom))
        assert (
            crop.size == expected.size
            and ImageChops.difference(crop.convert("RGB"), expected).getbbox() is None
        )
        # These isolated figures have white rows separating them from surrounding text.
        band = ImageChops.invert(page_im.crop((0, t - 1, page_im.width, bottom + 1)))
        extent = band.getbbox()
        assert extent
        x, y, x2, y2 = extent
        assert l <= x < x2 <= r and 1 <= y < y2 <= bottom - t + 1
        return {
            "figure_asset": asset,
            "box": list(box),
            "ink": [x, y + t - 1, x2, y2 + t - 1],
            "members": sorted(refs),
        }


def score(proof):
    hashes = json.loads((proof / "hashes.json").read_bytes())
    stamps = json.loads(checked(proof / "inputs.json", hashes["inputs.json"]))

    def read(path, digest=None):
        stamp = path.stat().st_mtime_ns
        data = checked(path, digest or sha(path.read_bytes()))
        assert stamps.setdefault(str(path), [sha(data), stamp]) == [sha(data), stamp]
        return data

    data = json.loads(read(proof / "observations.json", hashes["observations.json"]))
    source, qa = data["source"], data["qa"]
    english, anchors, sections = check_output(data)
    gold = json.loads(
        read(
            FINAL / "fixed-expectations.json",
            json.loads(read(FINAL / "hashes.json"))["fixed-expectations.json"],
        )
    )
    cases = {e["id"]: e for c in gold["cases"] for e in c["mandatory_expectations"]}
    results = {}
    used = set()
    for n, seed, start, h, old_h, c in zip(
        (65, 124, 214),
        (61, 111, 211),
        (61, 121, 211),
        data["digests"],
        data["old_ids"],
        data["chunks"],
        strict=True,
    ):
        assert c["schema_version"] == 14 and c["owned_pages"] == list(
            range(start, start + 5)
        )
        assert sha(PDFCorrectionChunk.model_validate(c).model_dump_json().encode()) == h
        old = json.loads(read(BOOK / f"corrections/pdf/{old_h}.json", old_h))
        record = json.loads(read(BOOK / old["run"]["artifact"], old["run"]["sha256"]))
        (answer,) = [
            json.loads(v["text"])
            for v in record["attempts"][-1]["final"]["message"]["content"]
            if v["type"] == "text"
        ]
        prior = {
            v.block_id: v.model_dump(mode="json")
            for v in CorrectionAnswer.model_validate(answer).blocks
        }
        saved = {v["block_id"]: v for v in c["blocks"]}
        d = json.loads(
            read(BOOK / f"drafts/pdf/{c['draft_sha256']}.json", c["draft_sha256"])
        )
        raw = json.loads(read(BOOK / d["raw"]["artifact"], d["raw"]["sha256"]))
        original = read(BOOK / d["document"]["artifact"], d["document"]["sha256"])
        directory = FINAL.parent / f"root-survey/seed-{seed}"
        correspondence = json.loads(read(directory / "correspondence.json"))
        index = json.loads(read(directory / "image-index.json"))
        pairs = [
            (b, f)
            for b in source["blocks"]
            for f in b["structure"]["fragments"]
            if f["chunk_sha256"] == h
        ]
        assert [f["block"] for _, f in pairs] == c["blocks"] and all(
            f["draft_sha256"] == c["draft_sha256"] for _, f in pairs
        )
        by_local = {f["block"]["block_id"]: b for b, f in pairs}
        for right in [62, 65] if start == 61 else [n]:
            left = right - 1
            case = cases[f"body{left}-{right}"]
            ends = []
            diff = []
            for page, field, original_id in zip(
                (left, right),
                ("outgoing", "incoming"),
                case["original_block_ids"],
                strict=True,
            ):
                (mapping,) = [
                    r for r in correspondence if r["original_block_id"] == original_id
                ]
                ((bound, key),) = mapping["fragments"]
                assert bound == old_h
                (baseline,) = [
                    b
                    for b in json.loads(read(directory / f"page-{page}.json"))[
                        "original_blocks"
                    ]
                    if b["block_id"] == original_id
                ]
                v = prior[key]
                assert (
                    v["kind"] == baseline["kind"] == "body"
                    and v["text"] == baseline["text"]
                    and v[field]["mode"] == "space"
                )
                ends.append(key)
                (e,) = v["evidence"]
                assert e["page"] == page and saved[key]["evidence"][0] == e
                node = raw["texts"][int(e["ref"].split("/")[-1])]
                (prov,) = [p for p in node["prov"] if p["page_no"] == page]
                rawspan = e.get("charspan") or prov["charspan"]
                assert rawspan == prov["charspan"] and node["self_ref"] == e["ref"]
                text = node["text"][slice(*rawspan)]
                if right != 62:
                    assert saved[key]["text"] == v["text"]
                (img,) = [i for i in index if i["page"] == page]
                (pg,) = [p for p in c["page_images"] if p["page"] == page]
                image = read(directory / f"page-{page}.png", pg["image"]["sha256"])
                assert img["image_sha256"] == sha(image)
                reconstructed = []
                # Inspect each delta independently of the adopted-text comparison.
                edits = SequenceMatcher(
                    None, text, v["text"], autojunk=False
                ).get_opcodes()
                for i, (op, a, b, j, k) in enumerate(edits):
                    if op == "equal":
                        reconstructed.append(text[a:b])
                        continue
                    delta = {
                        "page": page,
                        "ref": e["ref"],
                        "source": [rawspan[0] + a, rawspan[0] + b],
                        "proposal": [j, k],
                        "op": op,
                        "before": text[a:b],
                        "after": v["text"][j:k],
                    }
                    diff.append(delta)
                    spans = (
                        [[rawspan[0] + a, rawspan[0] + b]]
                        if a != b
                        else [[rawspan[0] + x, rawspan[0] + x + 1] for x in (a - 1, a)]
                    )
                    evidence = [
                        loc
                        for loc in saved[key]["evidence"][1:]
                        if loc["charspan"] in spans
                        and loc["ref"] == e["ref"]
                        and loc["page"] == page
                    ]
                    reason = (
                        f"{text[a:b]!r} → {v['text'][j:k]!r}"
                        if a != b
                        else f"Insertion of {v['text'][j:k]!r} between extraction characters"
                    )
                    args = (
                        bool(evidence),
                        reason,
                        spans,
                        page,
                        e["ref"],
                        text,
                        v["text"],
                    )
                    unresolved = check_difference_qa(
                        (
                            (q, q["evidence"])
                            for q in c["uncertainties"]
                            if key in q["target_ids"]
                        ),
                        *args,
                    )
                    global_key = by_local[key]["block_id"]
                    rendered = check_difference_qa(
                        (
                            (q, [json.loads(loc["evidence"]) for loc in q["locations"]])
                            for q in qa["uncertainties"]
                            if q["stage"] == "pdf_correction"
                            and global_key in q["target_ids"]
                        ),
                        *args,
                    )
                    if evidence:
                        assert [loc["charspan"] for loc in evidence] == spans
                        reconstructed.append(v["text"][j:k])
                        assert k == j + 1 and all(
                            loc["reason"] == reason for loc in evidence
                        )
                        context = None
                        if right == 62:
                            assert 0 < i < len(edits) - 1
                            prev, following = edits[i - 1], edits[i + 1]
                            assert prev[0] == following[0] == "equal"
                            context = (text, text[prev[1] : a], text[b : following[2]])
                        check_drawing(
                            original, prov, v["text"], j, image, evidence, used, context
                        )
                        delta["witness"] = evidence
                    else:
                        assert right == 62 and unresolved[0]["target_ids"] == [key]
                        assert rendered[0]["target_ids"] == [global_key]
                        assert [
                            (
                                loc["page"],
                                loc["extraction_ref"],
                                json.loads(loc["evidence"]),
                            )
                            for loc in rendered[0]["locations"]
                        ] == [
                            (page, e["ref"], loc) for loc in unresolved[0]["evidence"]
                        ]
                        delta["unresolved"] = unresolved
                        reconstructed.append(text[a:b])
                assert "".join(reconstructed) == saved[key]["text"]
            a, b = ends
            endpoint = by_local[a]
            key = endpoint["block_id"]
            begin = list(saved).index(a)
            end = list(saved).index(b)
            between = list(saved)[begin + 1 : end]
            old_between = list(prior)[list(prior).index(a) + 1 : list(prior).index(b)]
            expectation = case["expected_text"]
            assert prior[a]["text"] + " " + prior[b]["text"] == expectation
            assert (
                prior[a]["outgoing"]["target"]["ref"] == prior[b]["evidence"][0]["ref"]
                and prior[b]["incoming"]["target"]["ref"]
                == prior[a]["evidence"][0]["ref"]
            )
            for current, peer, link in ((a, b, "outgoing"), (b, a, "incoming")):
                t = prior[current][link]["target"]
                ev = prior[peer]["evidence"][0]
                assert (
                    saved[current][link] == prior[current][link]
                    and t["page"] == ev["page"]
                    and t["charspan"] in (None, ev["charspan"])
                )
            if right != 62:
                ea, eb = prior[a]["evidence"][0], prior[b]["evidence"][0]
                assert (
                    ea["ref"] == eb["ref"]
                    and ea["charspan"][1] <= eb["charspan"][0]
                    and not node["text"][ea["charspan"][1] : eb["charspan"][0]].strip()
                )
            report = {
                "ordering_rule": case["ordering_rule"],
                "ends": ends,
                "old_between": old_between,
                "adopted_between": between,
                "differences": diff,
                "anchor": anchors[key],
            }
            results[case["id"]] = report
            if right == 62:
                assert old_between == between and [
                    saved[k]["kind"] for k in between
                ] == ["footnote", "heading", "body", "body", "body"]
                assert by_local[b] != endpoint and expectation not in english
                for end_id, other in ((a, b), (b, a)):
                    src = by_local[end_id]
                    other_src = by_local[other]
                    assert len(src["structure"]["fragments"]) == 1
                    targets = [src["block_id"], other_src["block_id"]]
                    assert any(
                        q["stage"] == "pdf_connection"
                        and q["target_ids"] == targets
                        and q["locations"] == src["locations"] + other_src["locations"]
                        for q in qa["uncertainties"]
                    )
                    assert any(
                        q["name"] == "pdf_connection"
                        and not q["passed"]
                        and q["target_ids"] == targets
                        for q in qa["checks"]
                    )
                continue
            assert (
                by_local[b] == endpoint
                and [f["block"]["block_id"] for f in endpoint["structure"]["fragments"]]
                == ends
            )
            assert (
                not between
                and endpoint["text"] == expectation
                and sections[key].count(expectation) == english.count(expectation) == 1
            )
            assert [(d["op"], d["after"]) for d in diff] == {
                65: [("replace", "–"), ("replace", "–"), ("replace", "—")],
                124: [("insert", "—")],
                214: [],
            }[right]
            (fig,) = old_between
            assert list(saved)[end + 1] == fig and list(saved)[end + 2] in prior
            prior_order = [x["block_id"] for x in old["blocks"] if x["block_id"] != fig]
            assert [x for x in saved if x in prior_order] == prior_order
            for local, block in saved.items():
                if block["kind"] in ("footnote", "caption") and block["target_id"]:
                    assert (
                        by_local[local]["structure"]["target_id"]
                        == by_local[block["target_id"]]["block_id"]
                    )
            for added in saved.keys() - prior.keys():
                (ev,) = saved[added]["evidence"]
                extra = raw["texts"][int(ev["ref"].split("/")[-1])]
                assert (
                    extra["self_ref"] == ev["ref"]
                    and extra["parent"] == {"$ref": "#/body"}
                    and extra["text"][slice(*ev["charspan"])] == saved[added]["text"]
                )
            report.update(
                check_figure(
                    fig,
                    right,
                    saved,
                    prior,
                    by_local,
                    c,
                    raw,
                    sections,
                    english,
                    image,
                    proof,
                    hashes,
                    read,
                )
            )
    assert len(results) == 4
    for name, (h, mtime) in stamps.items():
        path = Path(name)
        checked(path, h)
        assert path.stat().st_mtime_ns == mtime
    return {"cases": results, "inputs": stamps}


if __name__ == "__main__":
    if not __debug__:
        sys.exit("Assertions required")
    sys.addaudithook(deny_network)
    os.umask(0o077)
    try:
        proof = Path(sys.argv[1]).absolute()
        assert proof.name == "proof" and proof.parent.parent == ROOT / "work"
        assert proof == proof.resolve() and proof.parent.stat().st_uid == os.getuid()
        output = new_directory(proof.parent / "scoring")
    except IndexError, AssertionError, OSError, ValueError:
        sys.exit("New private scoring directory required")
    with (output / "error.log").open("x") as errors:
        os.dup2(errors.fileno(), 2)
    result = score(proof)
    path = output / "score.json"
    path.write_text(json.dumps(result))
    assert json.loads(path.read_bytes()) == result
    print("#100 comparison passed")
