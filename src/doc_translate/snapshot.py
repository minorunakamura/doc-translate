"""Validated source snapshots, stored as immutable content-addressed artifacts.

Block list order is reading order; structure is preserved JSON, not a claim that
format-specific extraction or structural QA has been performed.
"""

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    TypeAdapter,
    model_validator,
)

from doc_translate.inference import GenerationConfig
from doc_translate.workspace import workspace_lock

Nonempty = Annotated[str, Field(min_length=1)]
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class SourceDocument(Contract):
    format: Literal["pdf", "epub"]
    sha256: Sha256
    artifact: Nonempty


class EPUBLocation(Contract):
    format: Literal["epub"]
    resource_path: Nonempty
    element_id: Nonempty | None = None
    node_path: Nonempty | None = None
    node_snapshot: Nonempty | None = None

    @model_validator(mode="after")
    def validate_node_reference(self) -> Self:
        if (self.node_path is None) != (self.node_snapshot is None):
            raise ValueError("node_path and node_snapshot must be supplied together")
        if self.element_id is None and self.node_path is None:
            raise ValueError("EPUB location needs element_id or a snapshot-bound node")
        return self


class BoundingBox(Contract):
    left: float
    top: float
    right: float
    bottom: float
    origin: Literal["top-left", "bottom-left"]
    unit: Nonempty
    reference_image: Nonempty
    rendering: dict[str, JsonValue]


class PDFLocation(Contract):
    format: Literal["pdf"]
    page: Annotated[int, Field(ge=1)]
    extraction_ref: Nonempty | None = None
    extraction_snapshot: Nonempty | None = None
    bbox: BoundingBox | None = None
    evidence: Nonempty | None = None

    @model_validator(mode="after")
    def validate_extraction_reference(self) -> Self:
        if (self.extraction_ref is None) != (self.extraction_snapshot is None):
            raise ValueError(
                "extraction_ref and extraction_snapshot must be supplied together"
            )
        if self.extraction_ref is None and self.evidence is None:
            raise ValueError(
                "PDF location without extraction_ref needs visible evidence and reason"
            )
        return self


SourceLocation = Annotated[PDFLocation | EPUBLocation, Field(discriminator="format")]


class SourceBlock(Contract):
    block_id: Nonempty
    kind: Nonempty
    text: str
    locations: list[SourceLocation] = Field(min_length=1)
    # shortcut: structure is preserved JSON; add kind-specific validation with extraction.
    structure: dict[str, JsonValue] = Field(default_factory=dict)


class Uncertainty(Contract):
    reason: Nonempty
    target_ids: list[Nonempty]
    locations: list[SourceLocation]
    stage: Nonempty
    origin: Literal["original", "processing", "undetermined"]


class Processing(Contract):
    stage: Nonempty
    status: Literal["not_processed", "completed", "failed"]


class MachineCheck(Contract):
    name: Nonempty
    target_ids: list[Nonempty]
    passed: bool
    details: str = ""


class ContentReview(Contract):
    reviewer: Nonempty
    method: Nonempty
    target_ids: list[Nonempty]
    details: Nonempty


class PDFSourceInputs(Contract):
    draft_sha256: Sha256
    chunk_sha256s: list[Sha256]
    correction: GenerationConfig
    correction_policy: Nonempty
    owned_pages: list[int]
    reference_pages: list[int]
    corrected_pages: list[int]
    failed_pages: list[int]
    not_processed_pages: list[int]

    def generation_sha256(self, document: SourceDocument) -> str:
        data = self.model_dump(
            mode="json",
            exclude={
                "chunk_sha256s",
                "corrected_pages",
                "failed_pages",
                "not_processed_pages",
            },
        )
        data["document"] = document.model_dump(mode="json")
        return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


class SourceSnapshot(Contract):
    schema_version: Literal[1]
    snapshot_id: Nonempty
    previous_snapshot_sha256: Sha256 | None = None
    change_reason: Nonempty | None = None
    machine_base_sha256: Sha256 | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    adopted_candidate_sha256: Sha256 | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    machine_inputs: PDFSourceInputs | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    document: SourceDocument
    blocks: list[SourceBlock]
    uncertainties: list[Uncertainty] = Field(default_factory=list)
    processing: Processing
    checks: list[MachineCheck] = Field(default_factory=list)
    content_reviews: list[ContentReview] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_identity_and_format(self) -> Self:
        if self.previous_snapshot_sha256 is not None and self.change_reason is None:
            raise ValueError("A source revision needs change_reason")
        ids = [block.block_id for block in self.blocks]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate source block ID")
        locations = [location for block in self.blocks for location in block.locations]
        locations.extend(
            location for item in self.uncertainties for location in item.locations
        )
        if any(location.format != self.document.format for location in locations):
            raise ValueError("Source location format must match the document")
        return self


class SourceCandidate(Contract):
    schema_version: Literal[1] = 1
    document: SourceDocument
    head_sha256: Sha256
    base_sha256: Sha256
    source_sha256: Sha256
    generation_sha256: Sha256
    inputs: PDFSourceInputs


class SnapshotReference(Contract):
    schema_version: Literal[1] = 1
    sha256: Sha256


def _atomic_write(path: Path, data: bytes) -> None:
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as temporary:
        temporary_path = Path(temporary.name)
        try:
            temporary.write(data)
            temporary.flush()
            os.fsync(temporary.fileno())
        except BaseException:
            temporary_path.unlink(missing_ok=True)
            raise
    try:
        os.replace(temporary_path, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary_path.unlink(missing_ok=True)


def _store_source_snapshot(workspace: Path, snapshot: SourceSnapshot) -> str:
    """Caller holds the writer lock; persist without changing the adopted reference."""
    snapshot = SourceSnapshot.model_validate(snapshot.model_dump(mode="json"))
    data = snapshot.model_dump_json().encode("utf-8")
    digest = hashlib.sha256(data).hexdigest()
    directory = workspace / "snapshots" / "source"
    directory.mkdir(parents=True, exist_ok=True)
    artifact = directory / f"{digest}.json"
    if artifact.exists():
        load_source_snapshot(workspace, digest)
    else:
        _atomic_write(artifact, data)
    return digest


def store_source_snapshot(workspace: Path, snapshot: SourceSnapshot) -> str:
    """Validate and save a machine candidate without adopting it."""
    with workspace_lock(workspace):
        return _store_source_snapshot(workspace, snapshot)


def source_snapshot_sha256(workspace: Path) -> str:
    return SnapshotReference.model_validate_json(
        (workspace / "source-snapshot.json").read_bytes()
    ).sha256


def save_source_snapshot(workspace: Path, snapshot: SourceSnapshot) -> str:
    """Persist first; the atomic adopted reference switch is the commit point."""
    with workspace_lock(workspace):
        digest = _store_source_snapshot(workspace, snapshot)
        _atomic_write(
            workspace / "source-snapshot.json",
            SnapshotReference(sha256=digest).model_dump_json().encode(),
        )
    return digest


def load_source_snapshot(workspace: Path, sha256: str | None = None) -> SourceSnapshot:
    """Load the adopted snapshot (or an old digest), verifying bytes and schema."""
    if sha256 is None:
        sha256 = SnapshotReference.model_validate_json(
            (workspace / "source-snapshot.json").read_bytes()
        ).sha256
    digest = TypeAdapter(Sha256).validate_python(sha256)
    data = (workspace / "snapshots" / "source" / f"{digest}.json").read_bytes()
    if hashlib.sha256(data).hexdigest() != digest:
        raise ValueError("Source snapshot SHA-256 mismatch")
    return SourceSnapshot.model_validate(json.loads(data.decode("utf-8")))
