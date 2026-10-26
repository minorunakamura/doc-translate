"""Local PDF acquisition; these artifacts are uncorrected extraction drafts.

Docling is imported only during acquisition. No source snapshot is adopted.
"""

import hashlib
import json
import os
import platform
import sys
from importlib.metadata import PackageNotFoundError, version
from io import BytesIO
from pathlib import Path
from typing import Annotated, Literal

from PIL import Image
from pydantic import Field, JsonValue, TypeAdapter

from doc_translate.snapshot import (
    Contract,
    MachineCheck,
    Nonempty,
    Sha256,
    SnapshotReference,
    SourceDocument,
    _atomic_write,
)
from doc_translate.workspace import workspace_lock

MODEL_FILES = {
    "docling-project/docling-layout-heron": (
        "8f39ad3c0b4c58e9c2d2c84a38465abf757272d8",
        ("config.json", "preprocessor_config.json", "model.safetensors"),
    ),
    "docling-project/docling-models": (
        "fc0f2d45e2218ea24bce5045f58a389aed16dc23",
        (
            "model_artifacts/tableformer/accurate/tm_config.json",
            "model_artifacts/tableformer/accurate/tableformer_accurate.safetensors",
        ),
    ),
}
PDF_VERSIONS = {"docling": "2.142.1", "docling-core": "2.104.0", "ocrmac": "1.0.1"}
PositiveInt = Annotated[int, Field(gt=0, strict=True)]
PositiveFloat = Annotated[float, Field(gt=0)]


class PDFExtractionError(RuntimeError):
    """Acquisition failed; draft_sha256 identifies retained output, if available."""

    def __init__(self, message: str, draft_sha256: str | None = None):
        self.draft_sha256 = draft_sha256
        super().__init__(message)


class PDFArtifact(Contract):
    artifact: Nonempty
    sha256: Sha256


class PDFModel(Contract):
    repo: Nonempty
    revision: Nonempty
    files: list[PDFArtifact]


class PDFPageImage(Contract):
    page: PositiveInt
    image: PDFArtifact
    width_points: PositiveFloat
    height_points: PositiveFloat
    width_pixels: PositiveInt
    height_pixels: PositiveInt
    image_origin: Literal["top-left"] = "top-left"
    page_unit: Literal["pt"] = "pt"
    scale_x: PositiveFloat
    scale_y: PositiveFloat
    rendering: dict[str, JsonValue]


class PDFDraft(Contract):
    schema_version: Literal[1] = 1
    kind: Literal["pdf-extraction-draft"] = "pdf-extraction-draft"
    document: SourceDocument
    source_name: Nonempty
    target_pages: list[PositiveInt]
    versions: dict[str, str]
    settings: dict[str, JsonValue]
    models: list[PDFModel]
    raw: PDFArtifact
    pages: list[PDFPageImage]
    conversion_status: Nonempty
    conversion_errors: list[dict[str, JsonValue]]
    checks: list[MachineCheck]


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _store(workspace: Path, directory: str, suffix: str, data: bytes) -> PDFArtifact:
    digest = _hash(data)
    relative = f"{directory}/{digest}.{suffix}"
    path = workspace / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != data:
            raise ValueError(f"PDF artifact SHA-256 mismatch: {relative}")
    else:
        _atomic_write(path, data)
    return PDFArtifact(artifact=relative, sha256=digest)


def _read_artifact(workspace: Path, artifact: PDFArtifact) -> bytes:
    path = (workspace / artifact.artifact).resolve()
    if not path.is_relative_to(workspace.resolve()):
        raise ValueError("PDF artifact must be inside workspace")
    data = path.read_bytes()
    if _hash(data) != artifact.sha256:
        raise ValueError(f"PDF artifact SHA-256 mismatch: {artifact.artifact}")
    return data


def load_pdf_draft(workspace: Path, sha256: str | None = None) -> PDFDraft:
    """Read a saved draft, checking its schema and source/raw/PNG bytes."""
    if sha256 is None:
        sha256 = SnapshotReference.model_validate_json(
            (workspace / "pdf-draft.json").read_bytes()
        ).sha256
    digest = TypeAdapter(Sha256).validate_python(sha256)
    data = _read_artifact(
        workspace,
        PDFArtifact(artifact=f"drafts/pdf/{digest}.json", sha256=digest),
    )
    draft = PDFDraft.model_validate_json(data)
    _read_artifact(
        workspace,
        PDFArtifact(artifact=draft.document.artifact, sha256=draft.document.sha256),
    )
    json.loads(_read_artifact(workspace, draft.raw))
    for page in draft.pages:
        with Image.open(BytesIO(_read_artifact(workspace, page.image))) as image:
            image.load()
            if image.format != "PNG" or image.size != (
                page.width_pixels,
                page.height_pixels,
            ):
                raise ValueError("PDF page image format/dimensions mismatch")
    return draft


def _check_charspans(raw: dict) -> MachineCheck:
    problems = []
    refs = []
    pending = [raw]
    while pending:
        node = pending.pop()
        if isinstance(node, dict):
            text = node.get("text")
            for index, provenance in enumerate(node.get("prov", [])):
                start, end = provenance["charspan"]
                if (
                    start < 0
                    or end < start
                    or (isinstance(text, str) and end > len(text))
                ):
                    ref = node["self_ref"]
                    if ref not in refs:
                        refs.append(ref)
                    problems.append(
                        f"{ref} provenance {index}: charspan [{start}, {end}], text length {len(text) if isinstance(text, str) else 'unavailable'}; raw span retained, no character-precision claim"
                    )
            pending.extend(reversed(list(node.values())))
        elif isinstance(node, list):
            pending.extend(reversed(node))
    return MachineCheck(
        name="charspans",
        passed=not problems,
        target_ids=refs,
        details="; ".join(problems),
    )


def acquire_pdf(
    source: Path,
    *,
    workspace: Path,
    artifacts_path: Path,
    pages: tuple[int, int] | None = None,
    image_long_edge: int = 1800,
) -> str:
    """Save local Docling output and every target page image; return draft digest.

    Model files must already exist in Docling's repo-folder layout. Hub and
    Transformers offline flags remain enabled for the calling process.
    """
    image_long_edge = TypeAdapter(PositiveInt).validate_python(image_long_edge)
    if pages is not None:
        pages = TypeAdapter(tuple[PositiveInt, PositiveInt]).validate_python(pages)
        if pages[0] > pages[1]:
            raise ValueError("PDF page range must be ascending and 1-based")
    models = []
    artifacts_path = artifacts_path.resolve()
    for repo, (revision, filenames) in MODEL_FILES.items():
        files = []
        for filename in filenames:
            path = artifacts_path / repo.replace("/", "--") / filename
            if not path.is_file():
                raise FileNotFoundError(f"Required local model artifact: {path}")
            with path.open("rb") as file:
                digest = hashlib.file_digest(file, "sha256").hexdigest()
            files.append(PDFArtifact(artifact=str(path), sha256=digest))
        models.append(PDFModel(repo=repo, revision=revision, files=files))

    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    try:
        versions = {name: version(name) for name in PDF_VERSIONS}
    except PackageNotFoundError as error:
        raise RuntimeError("PDF acquisition requires the pdf extra") from error
    if versions != PDF_VERSIONS:
        raise RuntimeError(
            f"PDF dependency versions must be {PDF_VERSIONS}; found {versions}"
        )

    from huggingface_hub import constants as hub_constants

    # Hub reads its offline flag at import time, including in caller-owned imports.
    hub_constants.HF_HUB_OFFLINE = True

    from docling.backend.docling_parse_backend import (
        ThreadedDoclingParseDocumentBackend,
    )
    from docling.datamodel.accelerator_options import (
        AcceleratorDevice,
        AcceleratorOptions,
    )
    from docling.datamodel.backend_options import ThreadedDoclingParseBackendOptions
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import (
        LayoutObjectDetectionOptions,
        OcrMacOptions,
        PdfPipelineOptions,
        TableFormerMode,
        TableStructureOptions,
    )
    from docling.datamodel.stage_model_specs import ObjectDetectionModelSpec
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling_core.types.io import DocumentStream

    options = PdfPipelineOptions(
        artifacts_path=artifacts_path,
        accelerator_options=AcceleratorOptions(device=AcceleratorDevice.CPU),
        enable_remote_services=False,
        allow_external_plugins=False,
        do_ocr=True,
        ocr_options=OcrMacOptions(
            lang=["en-US"], framework="vision", recognition="accurate"
        ),
        layout_options=LayoutObjectDetectionOptions(
            model_spec=ObjectDetectionModelSpec(
                name="docling_layout_heron",
                repo_id=models[0].repo,
                revision=models[0].revision,
            )
        ),
        do_table_structure=True,
        table_structure_options=TableStructureOptions(mode=TableFormerMode.ACCURATE),
        do_code_enrichment=False,
        do_formula_enrichment=False,
        do_picture_classification=False,
        do_picture_description=False,
        generate_page_images=False,
        images_scale=2.0,
    )
    versions.update(
        {
            name: version(name)
            for name in (
                "docling-parse",
                "docling-ibm-models",
                "torch",
                "transformers",
                "huggingface-hub",
                "Pillow",
            )
        }
    )
    versions.update(python=platform.python_version(), platform=platform.platform())
    with workspace_lock(workspace):
        original = source.read_bytes()
        original_artifact = _store(workspace, "originals", "pdf", original)
        converter = DocumentConverter(
            allowed_formats=[InputFormat.PDF],
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)},
        )
        try:
            result = converter.convert(
                DocumentStream(name=source.name, stream=BytesIO(original)),
                raises_on_error=False,
                page_range=pages or (1, sys.maxsize),
            )
        except Exception as error:
            raise PDFExtractionError(f"Docling conversion failed: {error}") from error
        raw = result.document.export_to_dict()
        raw_artifact = _store(
            workspace,
            "drafts/pdf/raw",
            "json",
            json.dumps(raw, ensure_ascii=False, allow_nan=False).encode(),
        )
        if pages and result.input.page_count > 0 and pages[1] > result.input.page_count:
            raise ValueError(
                f"Requested page range exceeds original PDF page count {result.input.page_count}"
            )
        target_pages = (
            list(range(pages[0], pages[1] + 1))
            if pages and result.input.page_count > 0
            else list(range(1, result.input.page_count + 1))
        )
        images = []
        rendering_errors = []
        backend = None
        render_options = ThreadedDoclingParseBackendOptions(render_scale=1.0)
        try:
            if target_pages:
                backend = ThreadedDoclingParseDocumentBackend(
                    result.input, BytesIO(original), options=render_options
                )
                for page in backend.iter_pages():
                    try:
                        number = page.page_no
                        if number not in target_pages:
                            continue
                        if not page.is_valid():
                            raise ValueError("Invalid Docling page backend")
                        size = page.get_size()
                        scale = image_long_edge / max(size.width, size.height)
                        image = page.get_page_image(scale=scale)
                        output = BytesIO()
                        image.save(output, format="PNG")
                        images.append(
                            PDFPageImage(
                                page=number,
                                image=_store(
                                    workspace,
                                    "drafts/pdf/images",
                                    "png",
                                    output.getvalue(),
                                ),
                                width_points=size.width,
                                height_points=size.height,
                                width_pixels=image.width,
                                height_pixels=image.height,
                                scale_x=image.width / size.width,
                                scale_y=image.height / size.height,
                                rendering={
                                    "api": "PdfPageBackend.get_page_image",
                                    "backend": "ThreadedDoclingParseDocumentBackend",
                                    "backend_options": render_options.model_dump(
                                        mode="json"
                                    ),
                                    "render_scale": scale,
                                    "cropbox": None,
                                    "resize": None,
                                    "image_long_edge": image_long_edge,
                                    "bbox_transform": "TOPLEFT: (x*scale_x,y*scale_y); BOTTOMLEFT: (x*scale_x,(height_points-y)*scale_y)",
                                },
                            )
                        )
                        extracted_page = result.document.pages.get(number)
                        if extracted_page and (
                            abs(extracted_page.size.width - size.width) > 0.001
                            or abs(extracted_page.size.height - size.height) > 0.001
                        ):
                            rendering_errors.append(
                                f"Page {number}: extraction/render page dimensions differ"
                            )
                    except (RuntimeError, ValueError) as error:
                        rendering_errors.append(f"Page {page.page_no}: {error}")
                    finally:
                        page.unload()
        except (RuntimeError, ValueError) as error:
            rendering_errors.append(f"Docling page rendering failed: {error}")
        finally:
            if backend is not None:
                backend.unload()
        draft = PDFDraft(
            document=SourceDocument(
                format="pdf",
                sha256=original_artifact.sha256,
                artifact=original_artifact.artifact,
            ),
            source_name=source.name,
            target_pages=target_pages,
            versions=versions,
            settings={
                "pipeline_options": options.model_dump(
                    mode="json", exclude_computed_fields=True, serialize_as_any=True
                ),
                "HF_HUB_OFFLINE": "1",
                "TRANSFORMERS_OFFLINE": "1",
                "image_long_edge": image_long_edge,
                "page_range": list(pages) if pages else None,
            },
            models=models,
            raw=raw_artifact,
            pages=images,
            conversion_status=result.status.value,
            conversion_errors=[
                error.model_dump(mode="json") for error in result.errors
            ],
            checks=[
                MachineCheck(
                    name="conversion_status",
                    passed=result.status.value == "success",
                    target_ids=[],
                    details=result.status.value,
                ),
                MachineCheck(
                    name="conversion_errors",
                    passed=not result.errors,
                    target_ids=[],
                    details="Docling diagnostics retained in conversion_errors"
                    if result.errors
                    else "",
                ),
                MachineCheck(
                    name="page_coverage",
                    passed=bool(target_pages)
                    and set(result.document.pages) == set(target_pages),
                    target_ids=[],
                    details=f"Expected {target_pages}; got {sorted(result.document.pages)}",
                ),
                MachineCheck(
                    name="page_images",
                    passed=not rendering_errors
                    and {page.page for page in images} == set(target_pages),
                    target_ids=[],
                    details="; ".join(
                        ["PNG required for every target page", *rendering_errors]
                    ),
                ),
                _check_charspans(raw),
            ],
        )
        artifact = _store(
            workspace, "drafts/pdf", "json", draft.model_dump_json().encode()
        )
        _atomic_write(
            workspace / "pdf-draft.json",
            SnapshotReference(sha256=artifact.sha256).model_dump_json().encode(),
        )
        if any(
            not check.passed
            for check in draft.checks
            if check.name in {"conversion_status", "page_coverage", "page_images"}
        ):
            raise PDFExtractionError(
                f"PDF acquisition incomplete; inspect draft {artifact.sha256}",
                artifact.sha256,
            )
        return artifact.sha256
