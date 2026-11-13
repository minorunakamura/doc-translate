"""Self-authored, drawn inputs and artificial answers, not a quality oracle."""

from copy import deepcopy
from importlib import import_module
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FAMILIES = ("history-layout", "figure", "body-connection", "code-connection")


def evidence(page, position, ref=None):
    return {
        "page": page,
        "ref": ref,
        "position": position,
        "reason": "Artificial visible location",
    }


def block(key, text, page=1, kind="body", **fields):
    return {
        "block_id": key,
        "kind": kind,
        "text": text,
        "evidence": [evidence(page, key)],
        **fields,
    }


def recipe(family, defective):
    """Return artificial correction answers and a separate proposed comparison."""
    if family == "history-layout":
        blocks = [
            block("opening", "Keep the blue valve closed."),
            block("paragraph", "Record the pressure before opening."),
            block("number", "Limit: 72 kPa."),
            block("code", "retry_count = 3", kind="code", language="python"),
            block(
                "table",
                "",
                kind="table",
                rows=1,
                columns=2,
                cells=[
                    {
                        "cell_id": f"cell-{i}",
                        "row": 0,
                        "column": i,
                        "row_span": 1,
                        "column_span": 1,
                        "text": text,
                        "heading": False,
                        "evidence": [evidence(1, "table")],
                    }
                    for i, text in enumerate(["Valve", "72"])
                ],
            ),
            block("measurement-label", "Measurement:", page=2),
        ]
        if defective:
            blocks.pop(1)
            blocks.insert(1, block("invented", "Open the red valve immediately."))
            blocks[2]["text"] = "Limit: 27 kPa."
            blocks[3]["text"] = "retry_count = 8"
            blocks[4]["cells"][1]["text"] = "27"
        uncertain = [
            {
                "reason": "Unreadable obliterated measurement value; readable label retained",
                "target_ids": [],
                "evidence": [
                    evidence(
                        2, "measurement value: black rectangle (420,190)-(900,265)"
                    )
                ],
                "origin": "original",
            }
        ]
        proposal = "Compare targetless charspan history independently of current text; paragraph deletion/addition, table, number and code restoration. Page 2 retains the readable Measurement: label; only the obliterated value is indeterminate in both."
    elif family == "figure":
        blocks = [
            block(
                "diagram",
                "",
                kind="picture",
                region={
                    "page": 1,
                    "left": 200,
                    "top": 200,
                    "right": 850 if defective else 1550,
                    "bottom": 650,
                },
                caption_ids=[],
                members=[],
            )
        ]
        if defective:
            blocks.append(block("duplicate", "OUTLET", 1))
        uncertain = [
            {
                "reason": "Diagram internal lettering retained in image",
                "target_ids": ["diagram"],
                "evidence": [evidence(1, "diagram")],
                "origin": "processing",
            }
        ]
        proposal = "Empty picture text is normal; compare crop including INLET and OUTLET, with no duplicate body lettering."
    else:
        code = family == "code-connection"
        texts = (
            ["if ready:", "    send(72)\n    close()"]
            if code
            else ["The blue valve", "must remain closed."]
        )
        mode = "newline" if code else "space"
        blocks = [
            block(
                f"part-{page}",
                text,
                page,
                "code" if code else "body",
                **({"language": "python"} if code else {}),
            )
            for page, text in enumerate(texts, 1)
        ]
        blocks[0]["outgoing"] = {"target": evidence(2, "part-2"), "mode": mode}
        blocks[1]["incoming"] = {"target": evidence(1, "part-1"), "mode": mode}
        if defective:
            blocks[1]["text"] = "    close()" if code else "remain closed."
            if not code:
                blocks[1]["incoming"]["mode"] = "dehyphenate"
        uncertain = []
        proposal = "Compare both page fragments and assembled text; reciprocal body/code joins versus missing text and (body) contradictory connection."
    return blocks, uncertain, proposal


def drawn_pages(family):
    pages = [
        Image.new("RGB", (1800, 900), "white")
        for _ in range(1 if family == "figure" else 2)
    ]
    font = ImageFont.load_default(size=36)
    for number, image in enumerate(pages, 1):
        ImageDraw.Draw(image).text(
            (60, 40), f"Self-authored {family} / page {number}", fill="black", font=font
        )
    draw = ImageDraw.Draw(pages[0])
    if family == "history-layout":
        for y, text in zip(
            [150, 250, 350, 450],
            [
                "Keep the blue valve closed.",
                "Record the pressure before opening.",
                "Limit: 72 kPa.",
                "retry_count = 3",
            ],
            strict=True,
        ):
            draw.text((100, y), text, fill="black", font=font)
        draw.rectangle((100, 550, 800, 670), outline="black", width=4)
        draw.line((500, 550, 500, 670), fill="black", width=4)
        draw.text((130, 585), "Valve", fill="black", font=font)
        draw.text((540, 585), "72", fill="black", font=font)
        second = ImageDraw.Draw(pages[1])
        second.text((100, 200), "Measurement:", fill="black", font=font)
        # No recoverable hidden answer: the original measurement is obliterated.
        second.rectangle((420, 190, 900, 265), fill="black")
    elif family == "figure":
        draw.rectangle((220, 220, 1530, 630), outline="black", width=5)
        draw.text((300, 350), "INLET", fill="black", font=font)
        draw.line((650, 375, 1100, 375), fill="black", width=5)
        draw.polygon([(1100, 375), (1060, 355), (1060, 395)], fill="black")
        draw.text((1200, 350), "OUTLET", fill="black", font=font)
    else:
        normal, _, _ = recipe(family, False)
        for image, item in zip(pages, normal, strict=True):
            ImageDraw.Draw(image).multiline_text(
                (100, 220), item["text"], fill="black", font=font, spacing=20
            )
    return pages


def acquire_fixture(directory: Path, workspace: Path, family: str, patch):
    """Reuse only the existing converter/backend fixture; local rendering is drawn."""
    from docling_core.types.doc import BoundingBox, DocItemLabel, ProvenanceItem, Size

    from doc_translate.pdf import acquire_pdf, load_pdf_draft

    fixtures = import_module("test_pdf")
    converted = fixtures.conversion.__wrapped__(patch)
    models = fixtures.local_models.__wrapped__(directory)
    doc = converted.document
    doc.texts.clear()
    doc.tables.clear()
    doc.body.children.clear()
    doc.pages.clear()
    images = drawn_pages(family)
    for page in range(1, len(images) + 1):
        doc.add_page(page_no=page, size=Size(width=200, height=100))
    normal, _, _ = recipe(family, False)
    for item in normal:
        if not item["text"]:
            continue
        page = item["evidence"][0]["page"]
        text = item["text"]
        doc.add_text(
            label=DocItemLabel.TEXT,
            text=text,
            orig=text,
            prov=ProvenanceItem(
                page_no=page,
                charspan=(0, len(text) + (50 if family == "history-layout" else 0)),
                bbox=BoundingBox(l=10, t=20, r=180, b=80, coord_origin="TOPLEFT"),
            ),
        )
    converted.input.page_count = len(images)
    backend = import_module("docling.backend.docling_parse_backend")
    original = backend.ThreadedDoclingParseDocumentBackend

    class DrawnBackend(original):
        def iter_pages(self):
            for page in super().iter_pages():
                original_render = page.get_page_image

                def render(
                    scale=1, cropbox=None, *, page=page, original_render=original_render
                ):
                    original_render(scale, cropbox)
                    return images[page.page_no - 1].copy()

                patch.setattr(page, "get_page_image", render)
                yield page

    patch.setattr(backend, "ThreadedDoclingParseDocumentBackend", DrawnBackend)
    source = directory / "self-authored.pdf"
    images[0].save(
        source, "PDF", save_all=True, append_images=images[1:], resolution=648
    )
    digest = acquire_pdf(source, workspace=workspace, artifacts_path=models)
    return workspace, digest, load_pdf_draft(workspace, digest)


def response(digest, family, defective, owned):
    blocks, uncertainties, _ = recipe(family, defective)
    return {
        "schema_version": 1,
        "draft_sha256": digest,
        "owned_pages": owned,
        "blocks": deepcopy(
            [item for item in blocks if item["evidence"][0]["page"] in owned]
        ),
        "exclusions": [
            {
                "text": f"Self-authored {family} / page {page}",
                "reason": "Page header identifying synthetic document and page; not body content",
                "evidence": [evidence(page, "page header at (60,40)")],
            }
            for page in owned
        ],
        "uncertainties": deepcopy(
            [item for item in uncertainties if item["evidence"][0]["page"] in owned]
        ),
    }
