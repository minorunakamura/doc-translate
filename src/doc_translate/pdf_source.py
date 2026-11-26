"""Assemble validated owned PDF chunks; saving/adoption remains the caller's job."""

from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path

from doc_translate.pdf_correction import _validated_correction
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
        (digest, *_validated_correction(workspace, digest, drafts))
        for digest in chunk_sha256s
    ]
    if not chunks:
        raise ValueError("At least one accepted PDF correction chunk is required")
    owned = [page for _, chunk, _ in chunks for page in chunk.owned_pages]
    if len(owned) != len(set(owned)):
        raise ValueError("PDF source chunks have overlapping ownership")
    raw_items = {digest: items for digest, (_draft, items, _raw) in drafts.items()}
    raw_docs = {digest: raw for digest, (_draft, _items, raw) in drafts.items()}
    document = next(iter(drafts.values()))[0].document
    if any(draft.document != document for draft, _items, _raw in drafts.values()):
        raise ValueError("PDF source chunks must use the same original document")
    chunks.sort(key=lambda item: min(item[1].owned_pages))
    originals = {}
    blocks = {}
    structures = {}
    id_count = 0
    block_drafts = {}
    uncertainties = []
    checks = []
    code_proposals = {}
    linked_proposals = {}
    drawn_proposal_order = {}
    selector_evidence = {}
    sidebars = {}
    for digest, chunk, answer in chunks:
        # Adoption preserves the validated local ID of a linked code proposal;
        # newly allocated/split fragments never inherit continuations.
        proposed = {
            b.block_id: (i, b) for i, b in enumerate(answer.blocks) if b.kind == "code"
        }
        # A verified symbol's subspan locates the drawing, not a new fragment boundary.
        body_selectors = {
            b.block_id: b.evidence
            for b in answer.blocks
            if b.kind == "body"
            and len(b.evidence) == 1
            and b.evidence[0].ref is not None
        }
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
            if getattr(block, "enclosure", None):
                sidebars[key] = (digest, block.enclosure)
            proposal_evidence = body_selectors.get(block.block_id)
            selector_evidence[key] = (
                proposal_evidence
                if proposal_evidence is not None
                and proposal_evidence == block.evidence[:1]
                else block.evidence
            )
            if block.kind == "code" and (block.incoming or block.outgoing):
                rank, proposal = proposed[block.block_id]
                linked_proposals[key] = proposal
                code_proposals[key] = proposal
                drawn_proposal_order[key] = rank
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
            for key in originals
            if any(
                _matches(
                    selector,
                    item,
                    raw_items[block_drafts[source_key]],
                    raw_items[block_drafts[key]],
                )
                for item in selector_evidence[key]
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
    # Count distinct reciprocal partners on the immutable reading order, before
    # allowing any intervening block (or another selected edge) to affect it.
    forward = defaultdict(list)
    backward = defaultdict(list)
    index = {key: i for i, key in enumerate(order)}

    def points_to(link, key, other):
        if link is None or link.mode != "newline" or link.target is None:
            return False
        return all(
            any(
                _matches(
                    link.target,
                    e,
                    raw_items[block_drafts[key]],
                    raw_items[block_drafts[other]],
                )
                for e in part.evidence
            )
            for part in (linked_proposals[other], originals[other])
        )

    def adopted_edge_target(link, key, other, *, first_line):
        """Schema 18: a target adopted into `other` from its proposed edge line only.

        The text of the target's adopted ranges must be in the first (or last)
        line of the partner's proposed text, with all proposed evidence on the
        other lines. Only those characters are compared, whitespace aside.
        The adopted ranges must also come first (or last) among the partner's
        adopted ranges in the extraction reading order (character position
        within one item); a range without a reading position is rejected.
        """
        from doc_translate.pdf_adoption import _reading_order

        if link is None or link.mode != "newline" or link.target is None:
            return False
        source, partner = raw_items[block_drafts[key]], raw_items[block_drafts[other]]

        def hits(evidence, owner=other):
            items = raw_items[block_drafts[owner]]
            return [e for e in evidence if _matches(link.target, e, source, items)]

        proposal = linked_proposals[other]
        owners = [k for k in originals if hits(originals[k].evidence, k)]
        ranges = hits(originals[other].evidence)
        if owners != [other]:
            return False
        sequence = {
            ref: i
            for i, (ref, _node, _container) in enumerate(
                _reading_order(raw_docs[block_drafts[other]])
            )
        }
        rest_ranges = [e for e in originals[other].evidence if e not in ranges]
        if any(e.ref not in sequence for e in ranges + rest_ranges):
            return False

        def key(e):
            return (sequence[e.ref], e.charspan[0] if e.charspan is not None else -1)

        before = (lambda a, b: a < b) if first_line else (lambda a, b: a > b)
        if not all(before(key(a), key(b)) for a in ranges for b in rest_ranges):
            return False

        def printed(evidence):
            text = partner[evidence.ref].get("text") if evidence.ref else None
            if not isinstance(text, str):
                return None
            if evidence.charspan is not None:
                text = text[evidence.charspan[0] : evidence.charspan[1]]
            return "".join(text.split()) or None

        adopted = [printed(e) for e in ranges]
        cited = [printed(e) for e in proposal.evidence]
        if None in adopted or None in cited:
            return False
        lines = ["".join(line.split()) for line in proposal.text.splitlines()]
        if len(lines) < 2:
            return False
        edge, rest = (lines[0], lines[1:]) if first_line else (lines[-1], lines[:-1])
        return "".join(adopted) in edge and all(text in "".join(rest) for text in cited)

    def ordered_proposals(left, right):
        if left not in drawn_proposal_order and right not in drawn_proposal_order:
            return True
        last = max(e.page for e in linked_proposals[left].evidence)
        first = min(e.page for e in linked_proposals[right].evidence)
        # A shared owned page means one chunk, so both ranks are in the same answer.
        return last < first or (
            last == first and drawn_proposal_order[left] < drawn_proposal_order[right]
        )

    for left, proposal in code_proposals.items():
        if not proposal.outgoing:
            continue
        for right, partner in code_proposals.items():
            if (
                index[left] < index[right]
                and ordered_proposals(left, right)
                and proposal.language == partner.language
                and max(e.page for e in originals[left].evidence)
                <= min(e.page for e in originals[right].evidence)
            ):
                outgoing = points_to(proposal.outgoing, left, right)
                incoming = points_to(partner.incoming, right, left)
                # One direction only; the reverse link must match both evidences.
                if incoming and not outgoing:
                    outgoing = adopted_edge_target(
                        proposal.outgoing, left, right, first_line=True
                    )
                elif outgoing and not incoming:
                    incoming = adopted_edge_target(
                        partner.incoming, right, left, first_line=False
                    )
                if not (outgoing and incoming):
                    continue
                forward[left].append(right)
                backward[right].append(left)

    def between_allowed(start, end):
        between = order[order.index(start) + 1 : order.index(end)]
        if all(originals[k].kind == "footnote" for k in between):
            return True
        # #109: whole drawn sidebars may also sit between the two fragments.
        drawn = {sidebars[k] for k in between if k in sidebars}
        return (
            bool(drawn)
            and start not in sidebars
            and end not in sidebars
            and all(originals[k].kind == "footnote" or k in sidebars for k in between)
            and all(k in between for k, sidebar in sidebars.items() if sidebar in drawn)
        )

    edges = {}
    for key, block in originals.items():
        for direction in ("incoming", "outgoing"):
            link = getattr(block, direction)
            if link is None:
                continue
            matches = candidates(link.target, key)
            if key in code_proposals and not (
                len(matches) == 1 and matches[0] not in code_proposals
            ):
                # A mixed boundary still requires the old unique raw target on both ends.
                incoming = direction == "incoming"
                partners, reverse = (
                    (backward, forward) if incoming else (forward, backward)
                )
                if len(partners[key]) == 1 and reverse[partners[key][0]] == [key]:
                    other = partners[key][0]
                    start, end = (other, key) if incoming else (key, other)
                    if all(
                        originals[k].kind == "footnote"
                        for k in order[index[start] + 1 : index[end]]
                    ):
                        edges[start] = (end, "newline")
                        continue
                problem(f"Unresolved {direction} code continuation", [key])
                continue
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
                    (
                        key not in drawn_proposal_order
                        and other_key not in drawn_proposal_order
                    )
                    or (
                        ordered_proposals(start, end)
                        and points_to(link, key, other_key)
                        and points_to(reciprocal, other_key, key)
                    )
                )
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
                and between_allowed(start, end)
            )
            if not valid:
                problem(
                    "Contradictory continuation; fragments retained", [key, other_key]
                )
                continue
            edges[start] = (end, link.mode)
    for start, (end, _mode) in edges.items():
        # Sidebars follow the joined paragraph and its footnotes, in their own order.
        i, j = order.index(start) + 1, order.index(end)
        order[i:j] = sorted(order[i:j], key=lambda k: k in sidebars)
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
    for item in [*uncertainties, *checks]:
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
