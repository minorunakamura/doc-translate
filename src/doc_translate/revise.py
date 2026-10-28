"""Validate user revisions and adopt immutable snapshots without inference."""

import json
from pathlib import Path
from typing import Literal

from pydantic import Field

from doc_translate.preparation import Visible, load_preparation
from doc_translate.render import workspace_status
from doc_translate.snapshot import (
    Contract,
    Processing,
    Sha256,
    SnapshotReference,
    SourceSnapshot,
    _atomic_write,
    load_source_snapshot,
    save_source_snapshot,
)
from doc_translate.translation import (
    TranslationAnswer,
    TranslationChunk,
    UnitTranslation,
    _request,
    _store,
    _validate,
    load_translation,
)
from doc_translate.units import generate_translation_units, validate_unit_text
from doc_translate.workspace import _workflow_lock


class TranslationPatch(Contract):
    schema_version: Literal[1]
    base_source_sha256: Sha256
    base_translation_sha256: Sha256
    translations: list[UnitTranslation] = Field(min_length=1)


def _digest(workspace, name):
    return SnapshotReference.model_validate_json(
        (workspace / f"{name}-snapshot.json").read_bytes()
    ).sha256


class SourceReplacement(Contract):
    schema_version: Literal[1]
    base_source_sha256: Sha256
    source: SourceSnapshot
    candidate_sha256: Sha256 | None = None


def revise_document(
    *,
    workspace: Path,
    translations: Path | None = None,
    source: Path | None = None,
    reason: str,
) -> dict:
    reason = TranslationRevisionReason(reason=reason).reason
    if (source is None) == (translations is None):
        raise ValueError("Supply exactly one source replacement or translation patch")
    with _workflow_lock(workspace):
        status = workspace_status(workspace)
        if source is not None:
            replacement = SourceReplacement.model_validate_json(source.read_bytes())
            if replacement.base_source_sha256 != _digest(workspace, "source"):
                raise ValueError("Stale revision base")
            previous = load_source_snapshot(workspace)
            revised_source = replacement.source
            if revised_source.document != previous.document:
                raise ValueError("Source revision must preserve original document")
            base_digest = previous.machine_base_sha256 or replacement.base_source_sha256
            baseline = load_source_snapshot(workspace, base_digest)
            machine_inputs = baseline.machine_inputs
            adopted_candidate = previous.adopted_candidate_sha256
            if (workspace / "run.json").exists():
                from doc_translate.run import (
                    RunManifest,
                    _machine_source,
                    source_revision_base,
                )

                manifest = RunManifest.model_validate_json(
                    (workspace / "run.json").read_bytes()
                )
                _, base_digest, baseline = source_revision_base(
                    workspace, manifest.document
                )
                machine_inputs = (
                    _machine_source(workspace, baseline)
                    if manifest.document.format == "pdf"
                    else None
                )
            if replacement.candidate_sha256 is not None:
                from doc_translate.run import (
                    _candidate_current_generation,
                    load_source_candidate,
                )

                if not (workspace / "run.json").exists():
                    raise ValueError("Candidate adoption requires saved PDF inputs")
                saved = load_source_candidate(workspace, manifest)
                if saved is None or saved[0] != replacement.candidate_sha256:
                    raise ValueError("Stale or unknown source candidate")
                _, candidate = saved
                if (
                    candidate.head_sha256 != replacement.base_source_sha256
                    or candidate.base_sha256 != base_digest
                    or candidate.generation_sha256
                    != _candidate_current_generation(
                        workspace, manifest
                    ).generation_sha256(manifest.document)
                    or candidate.inputs
                    != _candidate_current_generation(workspace, manifest)
                ):
                    raise ValueError("Stale source candidate input generation or scope")
                baseline = load_source_snapshot(workspace, candidate.source_sha256)
                base_digest = candidate.source_sha256
                machine_inputs = candidate.inputs
                adopted_candidate = replacement.candidate_sha256
            if machine_inputs is not None:
                revised_source.processing = baseline.processing.model_copy(deep=True)
                revised_source.checks = [
                    check
                    for check in revised_source.checks
                    if check.name != "pdf_run_scope"
                ]
                revised_source.checks.extend(
                    check for check in baseline.checks if check.name == "pdf_run_scope"
                )
                revised_source.uncertainties = [
                    item
                    for item in revised_source.uncertainties
                    if item.stage != "correction"
                ]
                revised_source.uncertainties.extend(
                    item
                    for item in baseline.uncertainties
                    if item.stage == "correction"
                )
            revised_source.previous_snapshot_sha256 = replacement.base_source_sha256
            revised_source.change_reason = reason
            revised_source.machine_base_sha256 = base_digest
            revised_source.machine_inputs = machine_inputs
            revised_source.adopted_candidate_sha256 = adopted_candidate
            try:
                generate_translation_units(revised_source)
                if revised_source.document.format == "epub":
                    _validate_epub_source(workspace, revised_source)
                else:
                    _validate_pdf_source(workspace, revised_source, baseline)
            except (KeyError, TypeError, IndexError, RecursionError) as error:
                raise ValueError("Malformed source structure or reference") from error
            digest = save_source_snapshot(workspace, revised_source)
            return {
                "source_snapshot_sha256": digest,
                "reason": reason,
                "base_source_sha256": replacement.base_source_sha256,
            }
        assert translations is not None
        patch = TranslationPatch.model_validate_json(translations.read_bytes())
        if patch.base_source_sha256 != _digest(
            workspace, "source"
        ) or patch.base_translation_sha256 != _digest(workspace, "translation"):
            raise ValueError("Stale revision base")
        previous = load_translation(workspace)
        if status["stale_translation"]:
            raise ValueError("Translation patch needs current preparation and settings")
        if previous.source_snapshot_sha256 != patch.base_source_sha256:
            raise ValueError("Translation patch needs current source correspondence")
        changes = {item.unit_id: item for item in patch.translations}
        units = {
            unit.unit_id: unit for chunk in previous.chunks for unit in chunk.units
        }
        if (
            len(changes) != len(patch.translations)
            or not changes.keys() <= units.keys()
        ):
            raise ValueError("Unknown or duplicate translation unit ID")
        for unit_id, item in changes.items():
            validate_unit_text(units[unit_id], item.text)
        preparation = load_preparation(workspace, previous.preparation_sha256)
        revised = previous.model_copy(deep=True)
        revised.chunks = []
        for index, old in enumerate(previous.chunks):
            if not any(unit.unit_id in changes for unit in old.units):
                revised.chunks.append(old)
                continue
            if old.answer is not None:
                chunks = [
                    old.model_copy(
                        update={
                            "answer": TranslationAnswer(
                                translations=[
                                    changes.get(item.unit_id, item)
                                    for item in old.answer.translations
                                ]
                            )
                        }
                    )
                ]
            else:
                chunks = [
                    TranslationChunk(
                        units=[unit],
                        answer=TranslationAnswer(translations=[changes[unit.unit_id]])
                        if unit.unit_id in changes
                        else None,
                        error=None if unit.unit_id in changes else old.error,
                    )
                    for unit in old.units
                ]
            for chunk in chunks:
                if chunk.answer is not None:
                    _validate(chunk.answer, chunk.units)
                    chunk.artifact_sha256 = _store(
                        workspace,
                        "translations/chunks",
                        {
                            "request": _request(chunk.units, preparation),
                            "config": previous.config,
                            "answer": chunk.answer,
                            "manual_revision": {
                                "base_translation_sha256": patch.base_translation_sha256,
                                "base_chunk_index": index,
                                "reason": reason,
                            },
                        },
                    )
                revised.chunks.append(chunk)
        revised.processing = Processing(
            stage="translation",
            status="failed"
            if any(chunk.error for chunk in revised.chunks)
            else "completed",
        )
        digest = _store(workspace, "snapshots/translation", revised)
        _atomic_write(
            workspace / "translation-snapshot.json",
            SnapshotReference(sha256=digest).model_dump_json().encode(),
        )
        return {
            "translation_snapshot_sha256": digest,
            "reason": reason,
            "base_translation_sha256": patch.base_translation_sha256,
        }


class TranslationRevisionReason(Contract):
    reason: Visible


def _validate_epub_source(workspace, source):
    from pydantic import TypeAdapter

    from doc_translate.epub_source import _Node
    from doc_translate.render import _Markdown
    from doc_translate.render_assets import RenderAssets
    from doc_translate.snapshot import EPUBLocation

    assets = RenderAssets(workspace, source)
    book = assets.epub()
    nodes = {}
    indices = {}
    for block in source.blocks:
        if block.kind != "section":
            raise ValueError("EPUB source needs section trees")
        tree = block.structure["tree"]
        for path, value in tree["nodes"].items():
            node = TypeAdapter(_Node).validate_python(value, strict=True)
            location = EPUBLocation.model_validate(node["location"])
            key = (location.resource_path, path)
            if (
                location.node_path != path
                or key in nodes
                or location.resource_path not in book.resources
            ):
                raise ValueError("EPUB node location/reference mismatch")
            if node["disposition"] not in {"structured", "excluded", "inert"}:
                raise ValueError("Invalid EPUB node disposition")
            if any(
                isinstance(part, dict) and set(part) != {"node_path"}
                for part in node["content"]
            ):
                raise ValueError("Invalid EPUB child reference")
            nodes[key] = node
        for location in block.locations:
            indices[location.resource_path] = block.structure.get("target_index", {})

    def target_location(value):
        location = EPUBLocation.model_validate(value)
        key = (location.resource_path, location.node_path)
        if key not in nodes:
            raise ValueError("Unknown EPUB reference target")

    for index in indices.values():
        for targets in index.values():
            for target in targets:
                target_location(target["location"])
    for node in nodes.values():
        for ref in node.get("references", []):
            if ref["status"] not in {
                "resolved",
                "ambiguous_fragment",
                "missing_fragment",
                "inert_resource",
                "external",
                "unresolved",
            }:
                raise ValueError("Invalid EPUB reference status")
            if (
                ref["resource_path"] is not None
                and ref["resource_path"] not in book.resources
                and ref["status"] != "unresolved"
            ):
                raise ValueError("Unknown EPUB resource reference")
            targets = ref["targets"]
            if "target_index" in ref:
                index = ref["target_index"]
                targets = indices.get(index["resource_path"], {}).get(
                    index["element_id"]
                )
                if targets is None:
                    raise ValueError("Unknown EPUB target index")
            for target in targets:
                target_location(target["location"])
            if ref["status"] == "resolved" and len(targets) != 1:
                raise ValueError("Resolved EPUB reference needs one target")
    units = generate_translation_units(source)
    ids = {block.block_id for block in source.blocks} | {unit.unit_id for unit in units}
    for record in [*source.uncertainties, *source.checks, *source.content_reviews]:
        if not set(record.target_ids) <= ids:
            raise ValueError("Unknown source QA reference ID")
    # Traverse without writes so original-resource lookups match rendering exactly.
    _Markdown(workspace, source, units, None).document(False)


def _validate_pdf_source(workspace, source, baseline):
    from types import SimpleNamespace

    from pydantic import TypeAdapter

    from doc_translate.pdf import PDFPageImage
    from doc_translate.pdf_correction import (
        CalloutBlock,
        CodeBlock,
        HeadingBlock,
        ListBlock,
        PictureBlock,
        TableBlock,
        TableCell,
        TextBlock,
        _authorized_draft,
        _load_pdf_correction,
        _validate_evidence,
        _validate_table,
        load_pdf_correction,
    )
    from doc_translate.render_assets import RenderAssets

    models = {
        "heading": HeadingBlock,
        "list": ListBlock,
        "code": CodeBlock,
        "callout": CalloutBlock,
        "table": TableBlock,
        "picture": PictureBlock,
        "figure": PictureBlock,
    }
    blocks = {block.block_id: block for block in source.blocks}
    ids = set(blocks)
    retained_ids = set()
    assets = RenderAssets(workspace, source)
    drafts = {}
    cell_drafts = {}
    cell_owned = set()

    def validate_cells(cells):
        if not cells:
            return
        if not cell_owned:
            if source.machine_inputs is not None:
                inputs = source.machine_inputs
                cell_owned.update(inputs.owned_pages)
                for page in inputs.owned_pages:
                    cell_drafts.setdefault(page, set()).add(inputs.draft_sha256)
            else:
                for original in baseline.blocks:
                    for location in original.locations:
                        cell_owned.add(location.page)
                        if location.extraction_snapshot is not None:
                            cell_drafts.setdefault(location.page, set()).add(
                                location.extraction_snapshot
                            )
                    for fragment in original.structure.get("fragments", []):
                        chunk = _load_pdf_correction(
                            workspace, fragment["chunk_sha256"], drafts
                        )
                        for page in chunk.owned_pages:
                            cell_owned.add(page)
                            cell_drafts.setdefault(page, set()).add(chunk.draft_sha256)
        for cell in cells:
            for evidence in cell.evidence:
                items = {}
                if evidence.ref is not None:
                    candidates = cell_drafts.get(evidence.page, set())
                    if len(candidates) != 1:
                        raise ValueError(
                            "Cell extraction evidence needs one original draft"
                        )
                    draft, _, items = _authorized_draft(
                        workspace, next(iter(candidates)), [evidence.page], [], drafts
                    )
                    if draft.document != source.document:
                        raise ValueError("Cell extraction belongs to another document")
                _validate_evidence(evidence, cell_owned, items)

    locations = [location for block in source.blocks for location in block.locations]
    locations.extend(
        location for item in source.uncertainties for location in item.locations
    )
    for location in locations:
        if (
            source.machine_inputs is not None
            and location.page not in source.machine_inputs.owned_pages
        ):
            raise ValueError("Source location outside adopted PDF ownership")
        if location.extraction_ref is None:
            continue
        draft, _, items = _authorized_draft(
            workspace, location.extraction_snapshot, [location.page], [], drafts
        )
        if draft.document != source.document:
            raise ValueError("Extraction reference belongs to another document")
        try:
            detail = json.loads(location.evidence or "null")
        except ValueError:
            detail = None
        detail = detail if isinstance(detail, dict) else {}
        _validate_evidence(
            SimpleNamespace(
                page=location.page,
                ref=location.extraction_ref,
                charspan=detail.get("charspan"),
                cell_index=detail.get("cell_index"),
            ),
            [location.page],
            items,
        )
    for block in source.blocks:
        structure = block.structure
        if block.kind not in {*models, "body", "formula", "caption", "footnote"}:
            raise ValueError("Unknown PDF source block kind")
        model = models.get(block.kind, TextBlock)
        for name, field in model.model_fields.items():
            if name in {"block_id", "kind", "text", "evidence"}:
                continue
            if name in structure:
                value = structure[name]
                if name == "cells":
                    value = [
                        {k: v for k, v in cell.items() if k != "inline"}
                        for cell in value
                    ]
                TypeAdapter(field.rebuild_annotation()).validate_python(
                    value, strict=True
                )
            elif field.is_required():
                raise ValueError(f"Missing {block.kind} structure: {name}")
        cells = [
            TableCell.model_validate({k: v for k, v in cell.items() if k != "inline"})
            for cell in [
                *structure.get("cells", []),
                *structure.get("retained_cells", []),
            ]
        ]
        validate_cells(cells)
        for cell in cells:
            if cell.cell_id in ids:
                raise ValueError("Duplicate source cell ID")
            ids.add(cell.cell_id)
        for fragment in structure.get("fragments", []):
            chunk = load_pdf_correction(workspace, fragment["chunk_sha256"])
            retained = {item.block_id for item in chunk.retained_blocks}
            retained.update(
                cell.cell_id
                for item in chunk.blocks
                if item.kind == "table"
                for cell in item.retained_cells
            )
            retained_ids.update(fragment["id_map"][key] for key in retained)
        for value in structure.get("page_images", []):
            page = PDFPageImage.model_validate(value)
            assets.read(page.image.artifact, page.image.sha256)
        if block.kind in {"picture", "figure"}:
            assets.pdf_picture(structure)
        if block.kind == "table" and structure.get("structure_confirmed", True):
            _validate_table(
                SimpleNamespace(
                    rows=structure["rows"], columns=structure["columns"], cells=cells
                )
            )
    ids.update(retained_ids)
    for block in source.blocks:
        structure = block.structure
        targets = [
            structure[field]
            for field in ("target_id", "parent_id", "code_id")
            if structure.get(field) is not None
        ]
        targets.extend(structure.get("caption_ids", []))
        targets.extend(
            member["target_id"]
            for member in structure.get("members", [])
            if member.get("target_id") is not None
        )
        if not set(targets) <= ids:
            raise ValueError("Unknown source reference ID")
        if block.kind == "list" and structure.get("parent_id") is not None:
            parent = blocks.get(structure["parent_id"])
            if (
                parent is None
                or parent.kind != "list"
                or parent.structure["depth"] != structure["depth"] - 1
            ):
                raise ValueError("Invalid list parent depth")
        if block.kind == "callout":
            code = blocks.get(structure["code_id"])
            if (
                code is None
                or code.kind != "code"
                or max(structure["lines"]) > len(code.text.splitlines())
            ):
                raise ValueError("Invalid code callout reference")
    for record in [*source.uncertainties, *source.checks, *source.content_reviews]:
        if not set(record.target_ids) <= ids:
            raise ValueError("Unknown source QA reference ID")
