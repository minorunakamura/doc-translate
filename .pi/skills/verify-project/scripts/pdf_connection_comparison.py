"""#99: retain the fresh six-chunk output before reading any fixed comparator."""

import json
import re

from doc_translate.pdf_correction import load_pdf_correction
from doc_translate.pdf_source import assemble_pdf_source
from doc_translate.render import render_document
from doc_translate.snapshot import save_source_snapshot
from doc_translate.units import generate_translation_units


def capture(workspace, digests, old_ids, *, bodies=False):
    chunks = [load_pdf_correction(workspace, h) for h in digests]
    starts = (61, 121, 211) if bodies else (71, 111, 116, 121, 181, 186)
    assert [c.owned_pages for c in chunks] == [list(range(n, n + 5)) for n in starts]
    assert all(c.schema_version == (14 if bodies else 13) for c in chunks)
    return capture_output(
        workspace,
        digests,
        old_ids,
        chunks,
        "body-connections" if bodies else "code-connections",
        assets=bodies,
    )


def capture_output(workspace, digests, old_ids, chunks, snapshot_id, *, assets):
    source = assemble_pdf_source(
        workspace=workspace,
        chunk_sha256s=digests,
        snapshot_id=snapshot_id,
    )
    save_source_snapshot(workspace, source)
    units = generate_translation_units(source)
    out = render_document(workspace)["artifacts"]
    english = workspace / out["english"]
    if assets:
        # Retain actual referenced crops (not crops synthesized from the map).
        for path in re.findall(r'<img src="([^"]+)"', english.read_text()):
            assert re.fullmatch(r"assets/[a-f0-9]{64}\.png", path)
            dest = workspace.parent.parent / path
            dest.parent.mkdir(exist_ok=True)
            dest.write_bytes((english.parent / path).read_bytes())
    return {
        "digests": digests,
        "old_ids": old_ids,
        "chunks": [c.model_dump(mode="json") for c in chunks],
        "source": source.model_dump(mode="json"),
        "units": [u.model_dump(mode="json") for u in units],
        "english": english.read_text(),
        "qa": json.loads((workspace / out["qa"]).read_bytes()),
        "source_map": json.loads((workspace / out["source_map"]).read_bytes()),
    }


def score(proof):
    from pdf_adoption_replay import BOOK, checked, sha
    from pdf_listing_comparison import score_listings
    from pdf_role_comparison import score_roles

    from doc_translate.pdf_correction import PDFCorrectionChunk
    from doc_translate.snapshot import SourceSnapshot
    from doc_translate.units import TranslationUnit

    hashes = json.loads((proof / "hashes.json").read_bytes())
    data = json.loads(checked(proof / "observations.json", hashes["observations.json"]))
    digests, old_ids = data["digests"], data["old_ids"]
    chunks = [PDFCorrectionChunk.model_validate(c) for c in data["chunks"]]
    assert [sha(c.model_dump_json().encode()) for c in chunks] == digests
    previous = [load_pdf_correction(BOOK, h) for h in old_ids]
    assert all(
        c.owned_pages == old.owned_pages and c.draft_sha256 == old.draft_sha256
        for c, old in zip(chunks, previous, strict=True)
    )
    source = SourceSnapshot.model_validate(data["source"])
    units = [TranslationUnit.model_validate(u) for u in data["units"]]
    english = data["english"]
    qa = data["qa"]["uncertainties"]
    anchors = {r["block_id"]: r["output_anchor"] for r in data["source_map"]["blocks"]}
    outputs = (source, units, english)
    listings = score_listings(
        BOOK,
        digests[1:4],
        old_ids[1:4],
        chunks[1:4],
        *outputs,
        json.dumps(data["qa"]),
        anchors,
        previous[1:4],
        table_page=False,
    )
    roles = score_roles(
        BOOK,
        digests[4:],
        old_ids[4:],
        chunks[4:],
        *outputs,
        qa,
        anchors,
        previous[4:],
        joined=True,
    )
    inventory = json.loads(checked(proof / "inputs.json", hashes["inputs.json"]))
    for subset in (roles, listings):
        for path, stamp in subset["comparison_inputs"].items():
            assert list(inventory.setdefault(path, stamp)) == list(stamp)
    connections = score_connections(data, inventory)
    from pathlib import Path

    for path, (h, m) in inventory.items():
        checked(Path(path), h)
        assert Path(path).stat().st_mtime_ns == m
    return {
        "listings": listings,
        "roles": roles,
        "connections": connections,
        "inputs": inventory,
    }


def score_connections(data, inventory):
    import pypdfium2 as pdfium
    from pdf_adoption_replay import BOOK, FINAL, checked, sha
    from pdf_listing_comparison import decoded

    from doc_translate.units import TranslationUnit

    def read(path, digest=None):
        stamp = path.stat().st_mtime_ns
        value = checked(path, digest or sha(path.read_bytes()))
        assert list(inventory.setdefault(str(path), [sha(value), stamp])) == [
            sha(value),
            stamp,
        ]
        return json.loads(value)

    gold = read(FINAL / "code-gold.json", read(FINAL / "hashes.json")["code-gold.json"])
    chunks = dict(zip(data["digests"], data["chunks"], strict=True))
    source = data["source"]
    blocks = source["blocks"]
    english = data["english"]
    series = []
    rejected = []
    units = [TranslationUnit.model_validate(v) for v in data["units"]]
    # These six complete answers have no callout block; don't count that as a tested line shift.
    for h in data["old_ids"]:
        old = read(BOOK / f"corrections/pdf/{h}.json", h)
        record = read(BOOK / old["run"]["artifact"], old["run"]["sha256"])
        (answer,) = [
            json.loads(x["text"])
            for x in record["attempts"][-1]["final"]["message"]["content"]
            if x["type"] == "text"
        ]
        assert not any(b["kind"] == "callout" for b in answer["blocks"])
    assert not any(b["kind"] == "callout" for b in blocks)
    for i, b in enumerate(blocks):
        if b["kind"] != "code":
            continue
        fs = b["structure"]["fragments"]
        pages = [f["block"]["evidence"][0]["page"] for f in fs]
        key = b["block_id"]
        refused = (
            pages == [73]
            and b["structure"]["outgoing"]
            or pages == [74]
            and b["structure"]["incoming"]
        )
        if len(fs) == 1 and not refused:
            continue
        locations = []
        texts = []
        for f in fs:
            (part,) = [
                p
                for p in chunks[f["chunk_sha256"]]["blocks"]
                if p["block_id"] == f["block"]["block_id"]
            ]
            assert part == f["block"]
            texts.append(part["text"])
            locations.extend((e["page"], e["ref"], e) for e in part["evidence"])
        assert [
            (l["page"], l["extraction_ref"], json.loads(l["evidence"]))
            for l in b["locations"]
        ] == locations
        assert (
            b["text"] == "\n".join(texts)
            and len(
                re.findall(
                    r"^(`{3,})[^`\n]*\n" + re.escape(b["text"]) + r"\n\1$",
                    english,
                    re.MULTILINE,
                )
            )
            == 1
        )
        if refused:
            assert any(
                q["stage"] == "pdf_connection"
                and key in q["target_ids"]
                and q["locations"] == b["locations"]
                for q in source["uncertainties"]
            )
            assert any(
                q["name"] == "pdf_connection"
                and not q["passed"]
                and key in q["target_ids"]
                for q in source["checks"]
            )
            rejected.extend(pages)
            continue
        # Compare the saved rows again at the actual fence, including all in-line spaces.
        actual = re.findall(r"([^\n]+)(\n*)", b["text"])
        fixed = next((g for g in gold if g["pages"] == pages), None)
        if fixed:
            expected = re.findall(r"([^\n]+)(\n*)", fixed["expected_text"])
            assert all(
                a.lstrip(" ") == w.lstrip(" ")
                for (a, _), (w, _) in zip(actual, expected, strict=True)
            )
        else:
            # Additional joined series: printed page 189 is a listing in a table-labelled item.
            assert pages == [188, 189]
            for f in fs:
                dh = f["draft_sha256"]
                d = read(BOOK / f"drafts/pdf/{dh}.json", dh)
                raw = read(BOOK / d["raw"]["artifact"], d["raw"]["sha256"])
                (e,) = f["block"]["evidence"]
                _, collection, index = e["ref"].split("/")
                (prov,) = raw[collection][int(index)]["prov"]
                assert prov["page_no"] == e["page"]
                with pdfium.PdfDocument(
                    checked(BOOK / d["document"]["artifact"], d["document"]["sha256"])
                ) as pdf:
                    printed = (
                        pdf[e["page"] - 1]
                        .get_textpage()
                        .get_text_bounded(
                            *(prov["bbox"][k] for k in ("l", "b", "r", "t"))
                        )
                    )
                assert [l.lstrip(" ") for l in printed.splitlines() if l.strip()] == [
                    l.lstrip(" ") for l in f["block"]["text"].splitlines() if l.strip()
                ]
            expected = []
        neighbors = []
        seed = FINAL.parent / f"root-survey/seed-{111 if pages[0] < 181 else 181}"
        corr = read(seed / "correspondence.json")
        order = []
        for n in [*blocks[i - 2 : i], b, *blocks[i + 1 : i + 3]]:
            anchor = next(
                x["output_anchor"]
                for x in data["source_map"]["blocks"]
                if x["block_id"] == n["block_id"]
            )
            order.append(english.index(f'<a id="{anchor}"></a>'))
            if n is b:
                continue
            (f,) = n["structure"]["fragments"]
            used = [u for u in units if u.block_id == n["block_id"]]
            assert used and "".join(decoded(u) for u in used) == n["text"]
            page = n["locations"][0]["page"]
            old_h = data["old_ids"][data["digests"].index(f["chunk_sha256"])]
            (binding,) = [
                v for v in corr if [old_h, f["block"]["block_id"]] in v["fragments"]
            ]
            (prior,) = [
                v
                for v in read(seed / f"page-{page}.json")["original_blocks"]
                if v["block_id"] == binding["original_block_id"]
            ]
            assert prior["kind"] == n["kind"]
            if n["text"] != prior["text"]:
                dh = f["draft_sha256"]
                d = read(BOOK / f"drafts/pdf/{dh}.json", dh)
                raw = read(BOOK / d["raw"]["artifact"], d["raw"]["sha256"])
                parts = []
                for e in f["block"]["evidence"]:
                    node = raw["texts"][int(e["ref"].split("/")[-1])]
                    parts.extend(
                        node["text"][slice(*(e["charspan"] or p["charspan"]))]
                        for p in node["prov"]
                        if p["page_no"] == e["page"]
                    )
                assert " ".join(parts) == n["text"]
            neighbors.append([page, n["kind"], anchor, [u.unit_id for u in used]])
        assert order == sorted(order)
        series.append(
            {
                "pages": pages,
                "rows": len(actual),
                "fence_lines": len(b["text"].splitlines()),
                "neighbors": neighbors,
                "indent_delta": [
                    len(a) - len(w) for (a, _), (w, _) in zip(actual, expected)
                ],
                "blank_delta": [
                    len(s) - len(t) for (_, s), (_, t) in zip(actual, expected)
                ],
            }
        )
    assert [s["pages"] for s in series] == [
        [115, 116],
        [117, 118],
        [119, 120, 121],
        [185, 186],
        [187, 188],
        [188, 189],
    ] and rejected == [73, 74]
    return {"series": series, "refused": rejected, "callouts": 0}
