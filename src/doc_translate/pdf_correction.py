"""PDF correction chunks, kept separate from the joined source snapshot."""

import json
import threading
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import Field, TypeAdapter, model_validator

from doc_translate.inference import (
    BeforeSend,
    GenerationConfig,
    GenerationImage,
    GenerationInput,
    InferenceError,
    generate,
)
from doc_translate.pdf import (
    PDFArtifact,
    PDFPageImage,
    PositiveInt,
    _read_artifact,
    _store,
    load_pdf_draft,
)
from doc_translate.snapshot import Contract, Nonempty, Sha256
from doc_translate.workspace import workspace_lock

Visible = Annotated[str, Field(min_length=1, pattern=r"\S")]


class CorrectionEvidence(Contract):
    page: PositiveInt
    ref: Nonempty | None = None
    position: Visible
    reason: Visible
    charspan: (
        tuple[
            Annotated[int, Field(ge=0, strict=True)],
            Annotated[int, Field(ge=0, strict=True)],
        ]
        | None
    ) = None
    cell_index: Annotated[int, Field(ge=0, strict=True)] | None = None

    @model_validator(mode="after")
    def validate_selector(self) -> Self:
        if self.ref is None and (
            self.charspan is not None or self.cell_index is not None
        ):
            raise ValueError("Extraction selectors require a real ref")
        if self.charspan is not None and self.cell_index is not None:
            raise ValueError("Use either a text span or a table cell")
        if self.charspan is not None and self.charspan[0] >= self.charspan[1]:
            raise ValueError("Text span must be nonempty and ascending")
        return self


class Continuation(Contract):
    target: CorrectionEvidence | None = None
    mode: Literal["space", "dehyphenate", "newline"]


class _Block(Contract):
    block_id: Nonempty
    text: str
    evidence: list[CorrectionEvidence] = Field(min_length=1)
    incoming: Continuation | None = None
    outgoing: Continuation | None = None


class TextBlock(_Block):
    kind: Literal["body", "formula", "caption", "footnote"]
    target_id: Nonempty | None = None


class HeadingBlock(_Block):
    kind: Literal["heading"]
    level: PositiveInt


class ListBlock(_Block):
    kind: Literal["list"]
    depth: PositiveInt
    marker: str
    parent_id: Nonempty | None = None
    parent_evidence: CorrectionEvidence | None = None


class CodeBlock(_Block):
    kind: Literal["code"]
    language: Visible | None = None


class CalloutBlock(_Block):
    kind: Literal["callout"]
    code_id: Nonempty
    marker: Visible
    lines: list[PositiveInt] = Field(min_length=1)


class TableCell(Contract):
    cell_id: Nonempty
    row: Annotated[int, Field(ge=0, strict=True)]
    column: Annotated[int, Field(ge=0, strict=True)]
    row_span: PositiveInt
    column_span: PositiveInt
    text: str
    heading: bool
    evidence: list[CorrectionEvidence] = Field(min_length=1)


class TableBlock(_Block):
    kind: Literal["table"]
    rows: PositiveInt
    columns: PositiveInt
    cells: list[TableCell]
    structure_confirmed: bool = True


class PictureRegion(Contract):
    """Original page PNG pixels, origin top-left; the chunk retains that PNG."""

    page: PositiveInt
    left: Annotated[float, Field(ge=0)]
    top: Annotated[float, Field(ge=0)]
    right: Annotated[float, Field(gt=0)]
    bottom: Annotated[float, Field(gt=0)]


class FigureMember(Contract):
    target_id: Nonempty | None = None
    ref: CorrectionEvidence | None = None
    status: Literal["confirmed", "uncertain", "partial"]
    reason: Visible

    @model_validator(mode="after")
    def validate_target(self) -> Self:
        if (self.target_id is None) == (self.ref is None):
            raise ValueError(
                "Figure membership needs exactly one block/cell ID or extraction ref"
            )
        if self.ref is not None and self.ref.ref is None:
            raise ValueError("Ref membership requires a real extraction ref")
        return self


class PictureBlock(_Block):
    kind: Literal["picture"]
    region: PictureRegion
    caption_ids: list[Nonempty]
    members: list[FigureMember]


CorrectionBlock = Annotated[
    TextBlock
    | HeadingBlock
    | ListBlock
    | CodeBlock
    | CalloutBlock
    | TableBlock
    | PictureBlock,
    Field(discriminator="kind"),
]


class AcceptedTable(TableBlock):
    cells: list[TableCell]
    retained_cells: list[TableCell]


AcceptedBlock = Annotated[
    TextBlock
    | HeadingBlock
    | ListBlock
    | CodeBlock
    | CalloutBlock
    | AcceptedTable
    | PictureBlock,
    Field(discriminator="kind"),
]


class CorrectionExclusion(Contract):
    text: str
    reason: Visible
    evidence: list[CorrectionEvidence] = Field(min_length=1)


class CorrectionUncertainty(Contract):
    reason: Visible
    target_ids: list[Nonempty]
    evidence: list[CorrectionEvidence] = Field(min_length=1)
    origin: Literal["original", "processing", "undetermined"]


class CorrectionAnswer(Contract):
    schema_version: Literal[1]
    draft_sha256: Sha256
    owned_pages: list[PositiveInt] = Field(min_length=1)
    blocks: list[CorrectionBlock]
    exclusions: list[CorrectionExclusion]
    uncertainties: list[CorrectionUncertainty]


class PDFCorrectionChunk(Contract):
    schema_version: Literal[1] = 1
    draft_sha256: Sha256
    owned_pages: list[PositiveInt]
    reference_pages: list[PositiveInt]
    blocks: list[AcceptedBlock]
    retained_blocks: list[CorrectionBlock]
    exclusions: list[CorrectionExclusion]
    uncertainties: list[CorrectionUncertainty]
    page_images: list[PDFPageImage]
    run: PDFArtifact


def _authorized_draft(workspace, digest, owned, reference, drafts=None):
    owned = TypeAdapter(list[PositiveInt]).validate_python(owned)
    reference = TypeAdapter(list[PositiveInt]).validate_python(reference)
    allowed = owned + reference
    if not owned or len(allowed) != len(set(allowed)):
        raise ValueError(
            "Owned/reference pages must be nonempty and disjoint without duplicates"
        )
    if drafts is None:
        drafts = {}
    if digest not in drafts:
        draft = load_pdf_draft(workspace, digest)
        raw = json.loads(_read_artifact(workspace, draft.raw))
        items = {}
        pending = [(raw, "#")]
        while pending:
            node, path = pending.pop()
            if isinstance(node, dict):
                if "self_ref" in node and node.get("prov"):
                    if node["self_ref"] != path:
                        raise ValueError("Raw extraction ref does not match its item")
                    items[path] = node
                pending.extend((value, f"{path}/{key}") for key, value in node.items())
            elif isinstance(node, list):
                pending.extend(
                    (value, f"{path}/{index}") for index, value in enumerate(node)
                )
        drafts[digest] = (draft, items)
    draft, items = drafts[digest]
    if draft.document.format != "pdf" or draft.conversion_status != "success":
        raise ValueError("Correction requires a successful PDF draft")
    if not set(allowed) <= set(draft.target_pages):
        raise ValueError("Authorized pages are outside the draft")
    pages = []
    for number in allowed:
        matches = [page for page in draft.pages if page.page == number]
        if len(matches) != 1:
            raise ValueError("Each authorized page needs exactly one image")
        pages.extend(matches)
    return draft, pages, items


def _validate_evidence(evidence, owned, items):
    if evidence.page not in owned:
        raise ValueError("Correction output must belong to owned pages")
    if evidence.ref is None:
        return
    node = items.get(evidence.ref)
    if node is None or evidence.page not in [prov["page_no"] for prov in node["prov"]]:
        raise ValueError("Extraction ref has no provenance on this page")
    if evidence.charspan is not None:
        text = node.get("text")
        start, end = evidence.charspan
        if (
            not isinstance(text, str)
            or end > len(text)
            or not any(
                prov["page_no"] == evidence.page
                and len(prov.get("charspan", [])) == 2
                and 0
                <= prov["charspan"][0]
                <= start
                < end
                <= prov["charspan"][1]
                <= len(text)
                for prov in node["prov"]
            )
        ):
            raise ValueError("Text span is not backed by valid page provenance")
    if evidence.cell_index is not None:
        cells = node.get("data", {}).get("table_cells", [])
        # Docling cells without page provenance are page-specific only in a single-page table.
        if evidence.cell_index >= len(cells) or {
            prov["page_no"] for prov in node["prov"]
        } != {evidence.page}:
            raise ValueError("Table cell has no unambiguous provenance on this page")


def _validate_table(block):
    area = 0
    for index, cell in enumerate(block.cells):
        if (
            cell.row + cell.row_span > block.rows
            or cell.column + cell.column_span > block.columns
        ):
            raise ValueError("Table cell exceeds table dimensions")
        area += cell.row_span * cell.column_span
        for other in block.cells[:index]:
            if (
                cell.row < other.row + other.row_span
                and other.row < cell.row + cell.row_span
                and cell.column < other.column + other.column_span
                and other.column < cell.column + cell.column_span
            ):
                raise ValueError("Overlapping table cells")
    if area != block.rows * block.columns:
        raise ValueError("Table must represent every cell, including empty cells")


def _validate_answer(answer, digest, owned, items, pages):
    if answer.draft_sha256 != digest or answer.owned_pages != owned:
        raise ValueError("Correction draft/ownership mismatch")
    blocks = {block.block_id: block for block in answer.blocks}
    cells = [
        cell
        for block in answer.blocks
        if isinstance(block, TableBlock)
        for cell in block.cells
    ]
    ids = [block.block_id for block in answer.blocks] + [cell.cell_id for cell in cells]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate correction block/cell ID")
    targets = {**blocks, **{cell.cell_id: cell for cell in cells}}
    covered = set()
    for item in [*answer.blocks, *cells, *answer.exclusions, *answer.uncertainties]:
        for evidence in item.evidence:
            _validate_evidence(evidence, owned, items)
            covered.add(evidence.page)
    if covered != set(owned):
        raise ValueError(
            "Owned page result coverage is incomplete; blank pages need explicit evidence/reason"
        )
    for block in answer.blocks:
        allowed = [page.page for page in pages]
        for continuation in (block.incoming, block.outgoing):
            if continuation is not None and continuation.target is not None:
                _validate_evidence(continuation.target, allowed, items)
        if isinstance(block, ListBlock) and block.parent_evidence is not None:
            _validate_evidence(block.parent_evidence, allowed, items)
        if isinstance(block, TableBlock) and block.structure_confirmed:
            _validate_table(block)
        if (
            isinstance(block, TextBlock)
            and block.target_id is not None
            and block.target_id not in blocks
        ):
            raise ValueError("Unknown caption/footnote target")
        if isinstance(block, ListBlock) and block.parent_id is not None:
            parent = blocks.get(block.parent_id)
            if not isinstance(parent, ListBlock) or parent.depth != block.depth - 1:
                raise ValueError("Invalid local list parent")
        if isinstance(block, CalloutBlock):
            code = blocks.get(block.code_id)
            if not isinstance(code, CodeBlock) or max(block.lines) > len(
                code.text.splitlines()
            ):
                raise ValueError("Invalid code callout target/line")
        if isinstance(block, PictureBlock):
            region = block.region
            image = next((page for page in pages if page.page == region.page), None)
            if (
                region.page not in owned
                or image is None
                or not 0 <= region.left < region.right <= image.width_pixels
                or not 0 <= region.top < region.bottom <= image.height_pixels
                or region.page not in {item.page for item in block.evidence}
            ):
                raise ValueError("Invalid figure region/image/page")
            if any(
                not isinstance(blocks.get(caption), TextBlock)
                or blocks[caption].kind != "caption"
                for caption in block.caption_ids
            ):
                raise ValueError("Unknown figure caption")
            for member in block.members:
                if member.ref is not None:
                    _validate_evidence(member.ref, owned, items)
                    if member.ref.page != region.page:
                        raise ValueError("Figure member ref is on another page")
                else:
                    target = targets.get(member.target_id)
                    if target is None or isinstance(target, PictureBlock):
                        raise ValueError("Unknown/invalid figure member")
                    if region.page not in {item.page for item in target.evidence}:
                        raise ValueError("Figure member is on another page")
                    if (
                        isinstance(target, TextBlock)
                        and target.kind in {"caption", "footnote"}
                        and member.status == "confirmed"
                    ):
                        raise ValueError(
                            "Caption/footnote cannot be suppressed as figure internal"
                        )
    for uncertainty in answer.uncertainties:
        if not set(uncertainty.target_ids) <= set(ids):
            raise ValueError("Unknown uncertainty target")


def _covers(selector, evidence):
    if selector.page != evidence.page or selector.ref != evidence.ref:
        return False
    if selector.cell_index is not None:
        return selector.cell_index == evidence.cell_index
    if selector.charspan is not None:
        return (
            evidence.charspan is not None
            and selector.charspan[0]
            <= evidence.charspan[0]
            < evidence.charspan[1]
            <= selector.charspan[1]
        )
    return True


def _overlaps(selector, evidence):
    if selector.page != evidence.page or selector.ref != evidence.ref:
        return False
    if selector.cell_index is not None and evidence.cell_index is not None:
        return selector.cell_index == evidence.cell_index
    if selector.charspan is not None and evidence.charspan is not None:
        return (
            selector.charspan[0] < evidence.charspan[1]
            and evidence.charspan[0] < selector.charspan[1]
        )
    return True


def _make_chunk(answer, reference_pages, pages, run, problems=()):
    candidates = {block.block_id: block for block in answer.blocks}
    candidates.update(
        (cell.cell_id, cell)
        for block in answer.blocks
        if isinstance(block, TableBlock)
        for cell in block.cells
    )
    internal = set()
    unresolved = set()
    uncertainties = list(answer.uncertainties)
    for problem in problems:
        if problem["page"] in answer.owned_pages:
            uncertainties.append(
                CorrectionUncertainty(
                    reason=problem["reason"],
                    target_ids=[],
                    evidence=[
                        CorrectionEvidence(
                            page=problem["page"],
                            ref=problem["ref"],
                            position="extraction provenance; inspect original page image",
                            reason=problem["reason"],
                        )
                    ],
                    origin="processing",
                )
            )
    for figure in answer.blocks:
        if not isinstance(figure, PictureBlock):
            continue
        for member in figure.members:
            matched = (
                [member.target_id]
                if member.target_id is not None
                else [
                    key
                    for key, candidate in candidates.items()
                    if not isinstance(candidate, PictureBlock)
                    and member.ref is not None
                    and any(_overlaps(member.ref, item) for item in candidate.evidence)
                ]
            )
            coarse_targets = set(matched)
            if member.ref is not None:
                for block in answer.blocks:
                    if isinstance(block, TableBlock) and block.block_id in matched:
                        coarse_targets.difference_update(
                            cell.cell_id
                            for cell in block.cells
                            if all(_covers(member.ref, item) for item in cell.evidence)
                        )
            for key in matched or [figure.block_id]:
                candidate = candidates[key]
                candidate_evidence = list(candidate.evidence)
                if isinstance(candidate, TableBlock):
                    candidate_evidence.extend(
                        item for cell in candidate.cells for item in cell.evidence
                    )
                safe = member.status == "confirmed" and {
                    item.page for item in candidate_evidence
                } == {figure.region.page}
                if member.ref is not None:
                    safe = safe and all(
                        _covers(member.ref, item) for item in candidate_evidence
                    )
                    # A table and its covered cells are one group, unlike independent blocks.
                    if member.ref.charspan is None and member.ref.cell_index is None:
                        safe = safe and len(coarse_targets) == 1
                if isinstance(candidate, TextBlock) and candidate.kind in {
                    "caption",
                    "footnote",
                }:
                    safe = False
                if safe and key != figure.block_id:
                    internal.add(key)
                else:
                    unresolved.add(key)
                    uncertainties.append(
                        CorrectionUncertainty(
                            reason=member.reason,
                            target_ids=[key],
                            evidence=candidate_evidence,
                            origin="processing",
                        )
                    )
    internal -= unresolved
    for block in answer.blocks:
        if isinstance(block, TableBlock) and any(
            cell.cell_id in unresolved for cell in block.cells
        ):
            internal.discard(block.block_id)
    adopted = []
    retained = []
    for block in answer.blocks:
        if block.block_id in internal:
            retained.append(block)
        elif isinstance(block, TableBlock):
            adopted.append(
                AcceptedTable(
                    **block.model_dump(exclude={"cells"}),
                    cells=[
                        cell for cell in block.cells if cell.cell_id not in internal
                    ],
                    retained_cells=[
                        cell for cell in block.cells if cell.cell_id in internal
                    ],
                )
            )
        else:
            adopted.append(block)
    return PDFCorrectionChunk(
        draft_sha256=answer.draft_sha256,
        owned_pages=answer.owned_pages,
        reference_pages=reference_pages,
        blocks=adopted,
        retained_blocks=retained,
        exclusions=answer.exclusions,
        uncertainties=uncertainties,
        page_images=pages,
        run=run,
    )


class _DraftBox(Contract):
    l: float
    t: float
    r: float
    b: float
    coord_origin: Literal["TOPLEFT", "BOTTOMLEFT"]


def _valid_span(span, text):
    return (
        isinstance(span, list)
        and len(span) == 2
        and all(type(offset) is int for offset in span)
        and 0 <= span[0] <= span[1] <= len(text)
    )


def _projection(items, allowed):
    fragments = []
    problems = []
    for ref, item in sorted(items.items()):
        text = item.get("text")
        for provenance in item["prov"]:
            page = provenance["page_no"]
            if page not in allowed:
                continue
            fragment = {"ref": ref, "page": page}
            if "bbox" in provenance:
                try:
                    fragment["bbox"] = _DraftBox.model_validate(
                        provenance["bbox"]
                    ).model_dump(mode="json")
                except ValueError:
                    problems.append(
                        {
                            "ref": ref,
                            "page": page,
                            "reason": "Invalid extraction bbox omitted; inspect original page image",
                        }
                    )
            if isinstance(text, str):
                span = provenance.get("charspan")
                forbidden = [
                    prov.get("charspan")
                    for prov in item["prov"]
                    if prov["page_no"] not in allowed
                ]
                if _valid_span(span, text) and all(
                    _valid_span(other, text)
                    and not (span[0] < other[1] and other[0] < span[1])
                    for other in forbidden
                ):
                    fragment.update(text=text[span[0] : span[1]], charspan=span)
                else:
                    problems.append(
                        {
                            "ref": ref,
                            "page": page,
                            "reason": "Unsafe/invalid charspan: text omitted, no clipping; inspect page image",
                        }
                    )
            fragments.append(fragment)
        cells = item.get("data", {}).get("table_cells", [])
        if cells:
            item_pages = {prov["page_no"] for prov in item["prov"]}
            if len(item_pages) == 1 and item_pages <= set(allowed):
                page = next(iter(item_pages))
                for index, cell in enumerate(cells):
                    fragments.append(
                        {
                            "ref": ref,
                            "page": page,
                            "cell_index": index,
                            "text": cell["text"],
                            **{
                                key: cell[key]
                                for key in (
                                    "start_row_offset_idx",
                                    "end_row_offset_idx",
                                    "start_col_offset_idx",
                                    "end_col_offset_idx",
                                    "row_span",
                                    "col_span",
                                    "column_header",
                                    "row_header",
                                )
                                if key in cell
                            },
                        }
                    )
            else:
                problems.extend(
                    {
                        "ref": ref,
                        "page": page,
                        "reason": "Table cell page is ambiguous: cell text omitted; retain original image for review",
                    }
                    for page in sorted(item_pages & set(allowed))
                )
    return {"fragments": fragments, "problems": problems}


def _record(workspace, status, request, config, attempts):
    return _store(
        workspace,
        "attempts/pdf-correction",
        "json",
        json.dumps(
            {
                "status": status,
                "request": request.model_dump(mode="json"),
                "config": config.model_dump(mode="json"),
                "attempts": [asdict(attempt) for attempt in attempts],
            },
            default=lambda value: value.model_dump(mode="json"),
        ).encode(),
    )


def correct_pdf(
    *,
    workspace: Path,
    draft_sha256: str,
    owned_pages: list[int],
    reference_pages: list[int],
    policy: str,
    config: GenerationConfig,
    process_command: Sequence[str] | None = None,
    cancel: threading.Event | None = None,
    before_send: BeforeSend | None = None,
) -> str:
    """Generate, validate and save one chunk; never adopt a source snapshot."""
    with workspace_lock(workspace):
        _draft, pages, items = _authorized_draft(
            workspace, draft_sha256, owned_pages, reference_pages
        )
        allowed = owned_pages + reference_pages
        projection = _projection(items, allowed)
        request = GenerationInput(
            stage="correction",
            policy=policy,
            text=json.dumps(projection, ensure_ascii=False),
            context={
                "draft_sha256": draft_sha256,
                "owned_pages": [number for number in owned_pages],
                "reference_pages": [number for number in reference_pages],
                "response_schema": CorrectionAnswer.model_json_schema(),
                "coordinates": "Draft bbox: original page points with coord_origin. Figure regions: original PNG pixels, origin top-left; image coordinates include point-to-pixel scales.",
            },
            glossary=None,
        )
        images = [
            GenerationImage(
                data=_read_artifact(workspace, page.image),
                mimeType="image/png",
                width=page.width_pixels,
                height=page.height_pixels,
                coordinates=f"page {page.page}; origin top-left; pixels; scale_x={page.scale_x}; scale_y={page.scale_y}; rendering={json.dumps(page.rendering)}",
            )
            for page in pages
        ]
        try:
            result = generate(
                config,
                request,
                response_model=CorrectionAnswer,
                images=images,
                process_command=process_command,
                cancel=cancel,
                before_send=before_send,
            )
        except InferenceError as error:
            _record(workspace, "rejected", request, config, error.attempts)
            raise
        try:
            _validate_answer(result.answer, draft_sha256, owned_pages, items, pages)
        except ValueError:
            _record(workspace, "rejected", request, config, result.attempts)
            raise
        run = _record(workspace, "validated", request, config, result.attempts)
        chunk = _make_chunk(
            result.answer, reference_pages, pages, run, projection["problems"]
        )
        return _store(
            workspace, "corrections/pdf", "json", chunk.model_dump_json().encode()
        ).sha256


def load_pdf_correction(workspace: Path, sha256: str) -> PDFCorrectionChunk:
    """Load one immutable accepted chunk by digest, verifying its evidence artifacts."""
    return _load_pdf_correction(workspace, sha256, {})


def _load_pdf_correction(workspace, sha256, drafts):
    chunk = PDFCorrectionChunk.model_validate_json(
        _read_artifact(
            workspace,
            PDFArtifact(artifact=f"corrections/pdf/{sha256}.json", sha256=sha256),
        )
    )
    _draft, pages, items = _authorized_draft(
        workspace, chunk.draft_sha256, chunk.owned_pages, chunk.reference_pages, drafts
    )
    record = json.loads(_read_artifact(workspace, chunk.run))
    if record["status"] != "validated":
        raise ValueError("Correction run was not validated")
    content = record["attempts"][-1]["final"]["message"]["content"]
    answer = CorrectionAnswer.model_validate_json(
        "".join(part["text"] for part in content if part["type"] == "text")
    )
    _validate_answer(answer, chunk.draft_sha256, chunk.owned_pages, items, pages)
    projection = _projection(items, chunk.owned_pages + chunk.reference_pages)
    context = record["request"]["context"]
    if (
        context["draft_sha256"] != chunk.draft_sha256
        or context["owned_pages"] != chunk.owned_pages
        or context["reference_pages"] != chunk.reference_pages
        or record["request"]["text"] != json.dumps(projection, ensure_ascii=False)
    ):
        raise ValueError("Saved correction differs from its authorized request")
    if chunk != _make_chunk(
        answer, chunk.reference_pages, pages, chunk.run, projection["problems"]
    ):
        raise ValueError("Saved correction differs from validated owned results")
    return chunk
