"""Small read-only projections of saved records, not domain or output verification."""

import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from doc_translate.inference import GenerationConfig
from doc_translate.preparation import StylePolicy
from doc_translate.snapshot import (
    Nonempty,
    Sha256,
    SnapshotReference,
    SourceCandidate,
    SourceDocument,
)


class _Record(BaseModel):
    model_config = ConfigDict(extra="ignore", allow_inf_nan=False)


class _Source(_Record):
    schema_version: Literal[1]
    document: SourceDocument


class _Unit(_Record):
    unit_id: Nonempty


class _Answer(_Record):
    translations: list[_Unit]


class _Chunk(_Record):
    units: list[_Unit] = Field(min_length=1)
    answer: _Answer | None
    error: Nonempty | None


class _Translation(_Record):
    schema_version: Literal[1]
    source_snapshot_sha256: Sha256
    preparation_sha256: Sha256
    config: GenerationConfig
    chunks: list[_Chunk]


class _Preparation(_Record):
    schema_version: Literal[1]
    policy: StylePolicy
    evidence_sha256: Sha256


class _Evidence(_Record):
    user_terms: dict[str, str]


class _RenderProcessing(_Record):
    status: Literal["completed", "partial"]
    output_reached: list[Nonempty]
    failed: list[Nonempty]
    not_processed: list[Nonempty]


class _RenderSummary(_Record):
    schema_version: Literal[1]
    source_snapshot_sha256: Sha256
    translation_snapshot_sha256: Sha256 | None
    processing: _RenderProcessing
    artifacts: dict[str, str]
    uncertainty_count: int = Field(ge=0)


def _validate[T: BaseModel](model: type[T], data: bytes) -> T:
    raw = json.loads(data)
    if isinstance(raw, dict) and (
        raw.get("schema_version") is True or raw.get("schema_version") is False
    ):
        raise ValueError("Invalid saved schema_version")
    return model.model_validate_json(data, strict=True)


def _translation_counts(snapshot: _Translation) -> dict:
    counts = {
        "total": 0,
        "succeeded": 0,
        "failed": 0,
        "not_processed": 0,
        "cancelled": 0,
    }
    seen = set()
    for chunk in snapshot.chunks:
        ids = [u.unit_id for u in chunk.units]
        if len(set(ids)) != len(ids) or seen.intersection(ids):
            raise ValueError("Duplicate saved leaf unit ID")
        seen.update(ids)
        if chunk.answer is not None:
            if (
                chunk.error is not None
                or [u.unit_id for u in chunk.answer.translations] != ids
            ):
                raise ValueError("Saved answer/unit mismatch")
            category = "succeeded"
        elif chunk.error in {"cancelled", "not_processed"}:
            category = chunk.error
        else:
            category = "failed" if chunk.error else "not_processed"
        counts["total"] += len(ids)
        counts[category] += len(ids)
    return counts


def _path(workspace: Path, relative: str) -> Path:
    path = workspace / relative
    if not path.resolve().is_relative_to(workspace.resolve()):
        raise ValueError("Saved record path outside workspace")
    return path


def _read(workspace: Path, relative: str, digest: str | None = None) -> bytes:
    try:
        data = _path(workspace, relative).read_bytes()
    except IsADirectoryError:
        raise ValueError("Saved record is a directory, not JSON") from None
    if digest is not None and hashlib.sha256(data).hexdigest() != digest:
        raise ValueError("Saved record hash mismatch")
    return data


def _optional(workspace: Path, relative: str) -> bytes | None:
    try:
        return _read(workspace, relative)
    except FileNotFoundError:
        if _path(workspace, relative).is_symlink():
            raise
        return None


def _status_records(workspace: Path) -> dict[str, bytes | None]:
    return {
        name: _optional(workspace, name)
        for name in (
            "run.json",
            "source-snapshot.json",
            "preparation-snapshot.json",
            "translation-snapshot.json",
            "render.json",
            "source-candidate.json",
            "pdf-draft.json",
        )
    }


def read_saved_status(workspace: Path) -> dict:
    """Read saved progress, retrying once on change; not a transaction or liveness check."""
    for _ in range(2):
        records = _status_records(workspace)
        try:
            result = _saved_status(workspace, records)
        except OSError, ValueError:
            if records == _status_records(workspace):
                raise
        else:
            if records == _status_records(workspace):
                return result
    raise RuntimeError("保存記録の更新競合: statusを再実行してください")


def _saved_status(workspace: Path, records: dict[str, bytes | None]) -> dict:
    """Read saved progress only; artifacts, domain validity and liveness are unverified."""
    from doc_translate.run import RunManifest

    run = (
        _validate(RunManifest, records["run.json"])
        if records["run.json"] is not None
        else None
    )
    pointers = {
        name: _validate(SnapshotReference, data).sha256
        for name, data in records.items()
        if name not in {"run.json", "render.json"} and data is not None
    }
    source = None
    if run is None and "source-snapshot.json" in pointers:
        digest = pointers["source-snapshot.json"]
        source = _validate(
            _Source, _read(workspace, f"snapshots/source/{digest}.json", digest)
        )
    if run is not None:
        document = run.document
    elif source is not None:
        document = source.document
    else:
        raise FileNotFoundError("No saved run or source snapshot")
    _path(workspace, document.artifact)
    translation = None
    if "translation-snapshot.json" in pointers:
        digest = pointers["translation-snapshot.json"]
        snapshot = _validate(
            _Translation,
            _read(workspace, f"snapshots/translation/{digest}.json", digest),
        )
        settings = None
        if run is not None:
            preparation = _validate(
                _Preparation,
                _read(
                    workspace,
                    f"snapshots/preparation/{snapshot.preparation_sha256}.json",
                    snapshot.preparation_sha256,
                ),
            )
            evidence = _validate(
                _Evidence,
                _read(
                    workspace,
                    f"preparation/evidence/{preparation.evidence_sha256}.json",
                    preparation.evidence_sha256,
                ),
            )
            settings = {
                "translation": run.translation == snapshot.config,
                "policy": run.policy == preparation.policy,
                "user_terms": run.user_terms == evidence.user_terms,
            }
        translation = {
            "sha256": digest,
            "source_snapshot_sha256": snapshot.source_snapshot_sha256,
            "preparation_sha256": snapshot.preparation_sha256,
            "config": snapshot.config.model_dump(mode="json"),
            "source_matches_pointer": snapshot.source_snapshot_sha256
            == pointers.get("source-snapshot.json"),
            "preparation_matches_pointer": snapshot.preparation_sha256
            == pointers.get("preparation-snapshot.json"),
            "run_settings_match": settings,
            **_translation_counts(snapshot),
        }
    candidate = None
    if "source-candidate.json" in pointers:
        digest = pointers["source-candidate.json"]
        saved_candidate = _validate(
            SourceCandidate,
            _read(workspace, f"candidates/source/{digest}.json", digest),
        )
        _path(workspace, saved_candidate.document.artifact)
        candidate = {"sha256": digest, "source_sha256": saved_candidate.source_sha256}
    saved_render = None
    if records["render.json"] is not None:
        from doc_translate.render import RenderReference

        manifest = _validate(RenderReference, records["render.json"])
        if manifest.summary not in manifest.files:
            raise ValueError("Render summary missing from hashed files")
        for relative in manifest.files:
            _path(workspace, relative)
        saved_render = _validate(
            _RenderSummary,
            _read(workspace, manifest.summary, manifest.files[manifest.summary]),
        )
        for relative in saved_render.artifacts.values():
            _path(workspace, relative)
        saved_render = saved_render.model_dump(mode="json")
    return {
        "format": document.format,
        "translation": translation,
        "render": saved_render,
        "run": run.model_dump(mode="json") if run else None,
        "pointers": pointers,
        "candidate": candidate,
        "record_mtimes": {
            name: _path(workspace, name).stat().st_mtime
            for name, data in records.items()
            if data is not None
        },
    }
