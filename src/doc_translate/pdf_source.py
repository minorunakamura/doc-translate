"""Assemble validated owned PDF chunks; saving/adoption remains the caller's job."""

from collections.abc import Sequence
from pathlib import Path

from doc_translate.pdf_correction import _load_pdf_correction
from doc_translate.snapshot import (
    MachineCheck,
    PDFLocation,
    Processing,
    SourceBlock,
    SourceSnapshot,
    Uncertainty,
)


def _matches(selector, evidence, selector_items, evidence_items):
    if (
        selector.page != evidence.page
        or (selector.charspan is not None and selector.charspan != evidence.charspan)
        or (
            selector.cell_index is not None
            and selector.cell_index != evidence.cell_index
        )
    ):
        return False
    if selector.ref is None or evidence.ref is None:
        return selector.ref == evidence.ref and selector.position == evidence.position
    if selector_items is evidence_items:
        return selector.ref == evidence.ref
    # Refs are draft-local; equal spellings alone cannot identify a cross-draft item.
    return selector.position == evidence.position and {
        key: value
        for key, value in selector_items[selector.ref].items()
        if key != "self_ref"
    } == {
        key: value
        for key, value in evidence_items[evidence.ref].items()
        if key != "self_ref"
    }


def _locations(evidence, draft_sha256):
    return [
        PDFLocation(
            format="pdf",
            page=item.page,
            extraction_ref=item.ref,
            extraction_snapshot=draft_sha256 if item.ref is not None else None,
            evidence=item.model_dump_json(),
        )
        for item in evidence
    ]


def assemble_pdf_source(
    *, workspace: Path, chunk_sha256s: Sequence[str], snapshot_id: str
) -> SourceSnapshot:
    """Load accepted chunks of one document and return source, including unresolved QA.

    The union of owned pages is the selected scope, not necessarily the whole PDF.
    No inference, source adoption, unit generation or workspace write is performed.
    """
    drafts = {}
    chunks = [
        (digest, _load_pdf_correction(workspace, digest, drafts))
        for digest in chunk_sha256s
    ]
    if not chunks:
        raise ValueError("At least one accepted PDF correction chunk is required")
    owned = [page for _, chunk in chunks for page in chunk.owned_pages]
    if len(owned) != len(set(owned)):
        raise ValueError("PDF source chunks have overlapping ownership")
    raw_items = {digest: items for digest, (_draft, items) in drafts.items()}
    document = next(iter(drafts.values()))[0].document
    if any(draft.document != document for draft, _items in drafts.values()):
        raise ValueError("PDF source chunks must use the same original document")
    chunks.sort(key=lambda item: min(item[1].owned_pages))
    originals = {}
    blocks = {}
    structures = {}
    id_count = 0
    block_drafts = {}
    uncertainties = []
    checks = []
    for digest, chunk in chunks:
        draft_sha256 = chunk.draft_sha256
        ids = {}
        for block in [*chunk.blocks, *chunk.retained_blocks]:
            ids[block.block_id] = f"pdf-{id_count + len(ids) + 1}"
            if block.kind == "table":
                for cell in [*block.cells, *getattr(block, "retained_cells", [])]:
                    ids[cell.cell_id] = f"pdf-{id_count + len(ids) + 1}"
        id_count += len(ids)
        for block in chunk.blocks:
            key = ids[block.block_id]
            originals[key] = block
            block_drafts[key] = draft_sha256
            structure = block.model_dump(
                mode="json", exclude={"block_id", "text", "evidence"}
            )
            for field in ("target_id", "parent_id", "code_id"):
                if structure.get(field) is not None:
                    structure[field] = ids[structure[field]]
            if block.kind == "table":
                for cell in [*structure["cells"], *structure["retained_cells"]]:
                    cell["cell_id"] = ids[cell["cell_id"]]
            if block.kind == "picture":
                structure["caption_ids"] = [ids[key] for key in block.caption_ids]
                for member in structure["members"]:
                    if member["target_id"] is not None:
                        member["target_id"] = ids[member["target_id"]]
            structure["fragments"] = [
                {
                    "chunk_sha256": digest,
                    "draft_sha256": draft_sha256,
                    "block": block.model_dump(mode="json"),
                    "id_map": ids,
                }
            ]
            structure["page_images"] = [
                page.model_dump(mode="json") for page in chunk.page_images
            ]
            structures[key] = structure
            blocks[key] = SourceBlock(
                block_id=key,
                kind=block.kind,
                text=block.text,
                locations=_locations(block.evidence, draft_sha256),
                structure=structure,
            )
        for item in chunk.uncertainties:
            uncertainties.append(
                Uncertainty(
                    reason=item.reason,
                    target_ids=[ids[key] for key in item.target_ids],
                    locations=_locations(item.evidence, draft_sha256),
                    stage="pdf_correction",
                    origin=item.origin,
                )
            )
        checks.append(
            MachineCheck(
                name="pdf_chunk_ownership",
                target_ids=list(ids.values()),
                passed=True,
                details=f"chunk={digest}; owned_pages={chunk.owned_pages}",
            )
        )

    def candidates(selector, source_key):
        if selector is None:
            return []
        return [
            key
            for key, block in originals.items()
            if any(
                _matches(
                    selector,
                    item,
                    raw_items[block_drafts[source_key]],
                    raw_items[block_drafts[key]],
                )
                for item in block.evidence
            )
        ]

    def problem(reason, keys):
        uncertainties.append(
            Uncertainty(
                reason=reason,
                target_ids=keys,
                locations=[
                    location for key in keys for location in blocks[key].locations
                ],
                stage="pdf_connection",
                origin="processing",
            )
        )
        checks.append(
            MachineCheck(
                name="pdf_connection", target_ids=keys, passed=False, details=reason
            )
        )

    order = sorted(
        blocks, key=lambda key: min(item.page for item in originals[key].evidence)
    )
    for key, block in originals.items():
        if block.kind == "table" and not block.structure_confirmed:
            structures[key]["retained_cells"] += structures[key]["cells"]
            structures[key]["cells"] = []
            problem(
                "Unconfirmed table structure; original cells and page image retained",
                [key],
            )
        if block.kind != "list":
            continue
        parent_id = structures[key]["parent_id"]
        if block.parent_evidence is not None:
            matches = candidates(block.parent_evidence, key)
            if len(matches) != 1 or (parent_id is not None and matches != [parent_id]):
                structures[key]["parent_id"] = None
                problem(
                    f"Unresolved list parent: {len(matches)} owned candidates or conflicting local parent",
                    [key],
                )
                continue
            parent_id = matches[0]
        if parent_id is None:
            if block.depth > 1:
                problem("Unresolved list parent for nested item", [key])
            continue
        parent = originals.get(parent_id)
        if (
            parent is None
            or parent.kind != "list"
            or parent.depth != block.depth - 1
            or order.index(parent_id) >= order.index(key)
        ):
            structures[key]["parent_id"] = None
            problem(
                "Invalid list parent kind, depth or reading order; items retained",
                [key, parent_id] if parent_id in blocks else [key],
            )
        else:
            structures[key]["parent_id"] = parent_id
    edges = {}
    for key, block in originals.items():
        for direction in ("incoming", "outgoing"):
            link = getattr(block, direction)
            if link is None:
                continue
            matches = candidates(link.target, key)
            if len(matches) != 1:
                problem(
                    f"Unresolved {direction} continuation: {len(matches)} owned candidates",
                    [key],
                )
                continue
            other_key = matches[0]
            other = originals[other_key]
            reciprocal = getattr(
                other, "outgoing" if direction == "incoming" else "incoming"
            )
            start, end = (
                (other_key, key) if direction == "incoming" else (key, other_key)
            )
            start_index, end_index = order.index(start), order.index(end)
            prose = block.kind in {"body", "caption", "footnote", "list"}
            same_structure = all(
                structures[start].get(field) == structures[end].get(field)
                for field in ("depth", "marker", "parent_id", "target_id")
            )
            valid = (
                reciprocal is not None
                and reciprocal.mode == link.mode
                and candidates(reciprocal.target, other_key) == [key]
                and block.kind == other.kind
                and same_structure
                and (
                    (prose and link.mode == "space")
                    or (
                        prose
                        and link.mode == "dehyphenate"
                        and blocks[start].text.rstrip().endswith("-")
                    )
                    or (
                        block.kind == "code"
                        and link.mode == "newline"
                        and structures[start]["language"] == structures[end]["language"]
                    )
                )
                and start_index < end_index
                and max(item.page for item in originals[start].evidence)
                <= min(item.page for item in originals[end].evidence)
                and all(
                    originals[item].kind == "footnote"
                    for item in order[start_index + 1 : end_index]
                )
            )
            if not valid:
                problem(
                    "Contradictory continuation; fragments retained", [key, other_key]
                )
                continue
            edges[start] = (end, link.mode)
    aliases = {key: key for key in blocks}
    line_offsets = {}
    for start in order:
        if start not in blocks:
            continue
        while start in edges:
            end, mode = edges.pop(start)
            left, right = blocks[start], blocks.pop(end)
            if mode == "newline":
                separator = (
                    ""
                    if left.text.endswith("\n") or right.text.startswith("\n")
                    else "\n"
                )
                left.text += separator + right.text
                # Match callout validation, including CRLF formed across the boundary.
                line_offsets[end] = len(left.text.splitlines()) - len(
                    right.text.splitlines()
                )
            elif mode == "dehyphenate":
                left.text = left.text.rstrip()[:-1] + right.text.lstrip()
            else:
                left.text = left.text.rstrip() + " " + right.text.lstrip()
            left.locations.extend(right.locations)
            structures[start]["fragments"].extend(structures[end]["fragments"])
            for page in structures[end]["page_images"]:
                if page not in structures[start]["page_images"]:
                    structures[start]["page_images"].append(page)
            aliases[end] = start
            if end in edges:
                edges[start] = edges.pop(end)
    for block in blocks.values():
        structure = structures[block.block_id]
        if block.kind == "callout":
            offset = line_offsets.get(structure["code_id"], 0)
            structure["lines"] = [line + offset for line in structure["lines"]]
        for field in ("target_id", "parent_id", "code_id"):
            value = structure.get(field)
            if value in aliases:
                structure[field] = aliases[value]
        if block.kind == "picture":
            structure["caption_ids"] = [
                aliases[key] for key in structure["caption_ids"]
            ]
            for member in structure["members"]:
                if member["target_id"] in aliases:
                    member["target_id"] = aliases[member["target_id"]]
        block.structure = structure
    for item in uncertainties:
        item.target_ids = list(
            dict.fromkeys(aliases.get(key, key) for key in item.target_ids)
        )
    return SourceSnapshot(
        schema_version=1,
        snapshot_id=snapshot_id,
        document=document,
        blocks=[blocks[key] for key in order if key in blocks],
        uncertainties=uncertainties,
        checks=checks,
        processing=Processing(stage="pdf_connection", status="completed"),
    )
