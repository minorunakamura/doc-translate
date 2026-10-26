"""PDF input → saved extraction draft, with only Docling conversion replaced."""

import hashlib
import json
from types import SimpleNamespace

import pytest
from PIL import Image

from doc_translate.pdf import acquire_pdf, load_pdf_draft


def test_missing_local_models_stops_without_creating_a_draft(tmp_path):
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF-1.7\n")
    workspace = tmp_path / "workspace"
    with pytest.raises(
        FileNotFoundError, match="docling-project--docling-layout-heron/config.json"
    ):
        acquire_pdf(source, workspace=workspace, artifacts_path=tmp_path / "models")
    assert not (workspace / "pdf-draft.json").exists()
    assert source.read_bytes() == b"%PDF-1.7\n"


@pytest.fixture
def local_models(tmp_path):
    models = tmp_path / "models"
    for name in (
        "docling-project--docling-layout-heron/config.json",
        "docling-project--docling-layout-heron/preprocessor_config.json",
        "docling-project--docling-layout-heron/model.safetensors",
        "docling-project--docling-models/model_artifacts/tableformer/accurate/tm_config.json",
        "docling-project--docling-models/model_artifacts/tableformer/accurate/tableformer_accurate.safetensors",
    ):
        path = models / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"artificial model, conversion boundary is replaced")
    return models


@pytest.fixture
def conversion(monkeypatch):
    pytest.importorskip("docling")
    from docling.document_converter import DocumentConverter
    from docling_core.types.doc import (
        BoundingBox,
        DocItemLabel,
        DoclingDocument,
        ImageRef,
        ProvenanceItem,
        Size,
        TableCell,
        TableData,
    )

    doc = DoclingDocument(name="artificial extraction")
    doc.add_page(
        page_no=1,
        size=Size(width=200, height=100),
        image=ImageRef.from_pil(Image.new("RGB", (400, 200), "white"), dpi=144),
    )
    prov = ProvenanceItem(
        page_no=1,
        charspan=(0, 5),
        bbox=BoundingBox(l=10, t=80, r=100, b=60, coord_origin="BOTTOMLEFT"),
    )
    doc.add_text(label=DocItemLabel.TEXT, text="Hello", orig="Hello", prov=prov)
    doc.add_table(
        data=TableData(
            num_rows=1,
            num_cols=2,
            table_cells=[
                TableCell(
                    text="A",
                    start_row_offset_idx=0,
                    end_row_offset_idx=1,
                    start_col_offset_idx=0,
                    end_col_offset_idx=2,
                    col_span=2,
                )
            ],
        ),
        prov=prov.model_copy(deep=True),
    )
    result = SimpleNamespace(
        document=doc,
        status=SimpleNamespace(value="success"),
        errors=[],
        input=SimpleNamespace(page_count=1),
        rendering_events=[],
        render_failures=set(),
    )

    class PageBackend:
        def __init__(self, number):
            self.page_no = number

        def is_valid(self):
            return True

        def get_size(self):
            return doc.pages[self.page_no].size

        def get_page_image(self, scale=1, cropbox=None):
            assert scale == 9
            assert cropbox is None
            result.rendering_events.append(("render", self.page_no, scale))
            if self.page_no in result.render_failures:
                raise RuntimeError("Artificial page render failure")
            return Image.new("RGB", (1800, 900), "white")

        def unload(self):
            result.rendering_events.append(("page_unloaded", self.page_no))

    class DocumentBackend:
        def __init__(self, in_doc, path_or_stream, options=None):
            assert path_or_stream.getvalue().startswith(b"%PDF-")

        def iter_pages(self):
            for number in sorted(doc.pages):
                yield PageBackend(number)

        def unload(self):
            result.rendering_events.append("document_unloaded")

    monkeypatch.setattr(
        "docling.backend.docling_parse_backend.ThreadedDoclingParseDocumentBackend",
        DocumentBackend,
    )

    def convert(converter, source, **kwargs):
        from docling.datamodel.base_models import InputFormat

        options = converter.format_to_options[InputFormat.PDF].pipeline_options
        assert options.accelerator_options.device == "cpu"
        assert options.ocr_options.lang == ["en-US"]
        assert options.ocr_options.framework == "vision"
        assert options.ocr_options.recognition == "accurate"
        assert options.enable_remote_services is False
        assert options.allow_external_plugins is False
        assert options.artifacts_path is not None
        assert options.layout_options.kind == "layout_object_detection"
        assert (
            options.layout_options.model_spec.revision
            == "8f39ad3c0b4c58e9c2d2c84a38465abf757272d8"
        )
        assert kwargs["raises_on_error"] is False
        import os

        assert os.environ["HF_HUB_OFFLINE"] == "1"
        assert os.environ["TRANSFORMERS_OFFLINE"] == "1"
        return result

    monkeypatch.setattr(DocumentConverter, "convert", convert)
    return result


def test_acquire_preserves_raw_provenance_table_cells_and_all_page_images(
    tmp_path, local_models, conversion
):
    source = tmp_path / "source.pdf"
    original = b"%PDF-1.7\nartificial input for conversion fixture"
    source.write_bytes(original)
    workspace = tmp_path / "workspace"
    digest = acquire_pdf(source, workspace=workspace, artifacts_path=local_models)
    draft = load_pdf_draft(workspace)
    assert load_pdf_draft(workspace, digest) == draft
    assert draft.kind == "pdf-extraction-draft"
    assert draft.document.sha256 == hashlib.sha256(original).hexdigest()
    assert (workspace / draft.document.artifact).read_bytes() == original
    raw = json.loads((workspace / draft.raw.artifact).read_bytes())
    assert raw == conversion.document.export_to_dict()
    assert raw["texts"][0]["self_ref"] == "#/texts/0"
    assert raw["texts"][0]["prov"][0]["charspan"] == [0, 5]
    assert raw["tables"][0]["data"]["table_cells"][0]["col_span"] == 2
    assert draft.target_pages == [1]
    assert draft.conversion_status == "success"
    assert draft.conversion_errors == []
    assert all(check.passed for check in draft.checks)
    page = draft.pages[0]
    assert (page.width_points, page.height_points) == (200, 100)
    assert (page.width_pixels, page.height_pixels) == (1800, 900)
    assert page.scale_x == 9
    assert page.scale_y == 9
    assert page.image_origin == "top-left"
    assert page.rendering["render_scale"] == 9
    assert conversion.rendering_events == [
        ("render", 1, 9),
        ("page_unloaded", 1),
        "document_unloaded",
    ]
    with Image.open(workspace / page.image.artifact) as image:
        assert image.format == "PNG"
        assert image.size == (1800, 900)
    assert draft.versions["docling"] == "2.142.1"
    assert draft.versions["docling-core"] == "2.104.0"
    assert draft.versions["ocrmac"] == "1.0.1"
    settings = json.loads((workspace / f"drafts/pdf/{digest}.json").read_bytes())
    options = settings["settings"]["pipeline_options"]
    assert options["ocr_options"]["framework"] == "vision"
    assert options["ocr_options"]["recognition"] == "accurate"
    assert (
        options["layout_options"]["model_spec"]["repo_id"]
        == "docling-project/docling-layout-heron"
    )
    assert options["table_structure_options"]["mode"] == "accurate"
    assert draft.models[0].repo == "docling-project/docling-layout-heron"
    assert draft.models[1].revision == "fc0f2d45e2218ea24bce5045f58a389aed16dc23"
    assert len(draft.models[0].files) == 3
    assert len(draft.models[1].files) == 2
    assert not (workspace / "source-snapshot.json").exists()


@pytest.mark.parametrize("span", [(0, 99), (-1, 3), (4, 2)])
def test_invalid_charspan_keeps_full_text_provenance_and_image_for_later_correction(
    tmp_path, local_models, conversion, span
):
    conversion.document.texts[0].prov[0].charspan = span
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF-1.7\nfixture")
    workspace = tmp_path / "workspace"
    digest = acquire_pdf(source, workspace=workspace, artifacts_path=local_models)
    draft = load_pdf_draft(workspace, digest)
    raw = json.loads((workspace / draft.raw.artifact).read_bytes())
    assert raw["texts"][0]["text"] == "Hello"
    assert raw["texts"][0]["prov"][0]["charspan"] == list(span)
    assert len(draft.pages) == 1
    check = next(check for check in draft.checks if check.name == "charspans")
    assert check.passed is False
    assert check.target_ids == ["#/texts/0"]
    assert "provenance 0" in check.details
    assert "text length 5" in check.details


@pytest.mark.parametrize(
    "failure", ["partial_success", "failure", "missing_page", "missing_image"]
)
def test_incomplete_conversion_saves_raw_and_available_images_but_raises_with_digest(
    tmp_path, local_models, conversion, failure
):
    from doc_translate.pdf import PDFExtractionError

    if failure in {"partial_success", "failure"}:
        conversion.status.value = failure
    elif failure == "missing_page":
        conversion.input.page_count = 2
    else:
        conversion.render_failures.add(1)
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF-1.7\nfixture")
    workspace = tmp_path / "workspace"
    with pytest.raises(PDFExtractionError) as caught:
        acquire_pdf(source, workspace=workspace, artifacts_path=local_models)
    assert caught.value.draft_sha256 is not None
    draft = load_pdf_draft(workspace, caught.value.draft_sha256)
    assert draft.conversion_status == conversion.status.value
    assert (
        json.loads((workspace / draft.raw.artifact).read_bytes())
        == conversion.document.export_to_dict()
    )
    assert any(not check.passed for check in draft.checks)
    assert len(draft.pages) == (0 if failure == "missing_image" else 1)
    assert not (workspace / "source-snapshot.json").exists()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"pages": (0, 1)},
        {"pages": (2, 1)},
        {"pages": (1, True)},
        {"image_long_edge": 0},
    ],
)
def test_invalid_range_or_image_setting_is_rejected_before_conversion(
    tmp_path, local_models, conversion, kwargs
):
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF-1.7\nfixture")
    with pytest.raises(ValueError):
        acquire_pdf(
            source,
            workspace=tmp_path / "workspace",
            artifacts_path=local_models,
            **kwargs,
        )
    assert not (tmp_path / "workspace" / "pdf-draft.json").exists()


def test_non_pdf_input_fails_explicitly_and_keeps_original_bytes(
    tmp_path, local_models
):
    pytest.importorskip("docling")
    from doc_translate.pdf import PDFExtractionError

    source = tmp_path / "broken.pdf"
    source.write_bytes(b"This is not a PDF.")
    workspace = tmp_path / "workspace"
    with pytest.raises(PDFExtractionError):
        acquire_pdf(source, workspace=workspace, artifacts_path=local_models)
    assert (
        next(iter((workspace / "originals").glob("*.pdf"))).read_bytes()
        == b"This is not a PDF."
    )


def test_selected_pages_use_original_one_based_numbers_and_do_not_expand_scope(
    tmp_path, local_models, conversion, monkeypatch
):
    from docling.document_converter import DocumentConverter

    page = conversion.document.pages.pop(1)
    page.page_no = 2
    conversion.document.pages[2] = page
    conversion.input.page_count = 3
    for item in [*conversion.document.texts, *conversion.document.tables]:
        item.prov[0].page_no = 2
    original_convert = DocumentConverter.convert

    def convert(converter, source, **kwargs):
        assert kwargs["page_range"] == (2, 2)
        assert source.stream.getvalue() == b"%PDF-1.7\nselected-page fixture"
        return original_convert(converter, source, **kwargs)

    monkeypatch.setattr(DocumentConverter, "convert", convert)
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF-1.7\nselected-page fixture")
    workspace = tmp_path / "workspace"
    digest = acquire_pdf(
        source, workspace=workspace, artifacts_path=local_models, pages=(2, 2)
    )
    draft = load_pdf_draft(workspace, digest)
    assert draft.target_pages == [2]
    assert [page.page for page in draft.pages] == [2]
    assert all(check.passed for check in draft.checks)


def test_conversion_diagnostics_are_retained_without_blanket_failure(
    tmp_path, local_models, conversion
):
    from docling.datamodel.base_models import DoclingComponentType, ErrorItem

    conversion.errors = [
        ErrorItem(
            component_type=DoclingComponentType.PIPELINE,
            module_name="artificial",
            error_message="Nonfatal diagnostic from external boundary",
        )
    ]
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF-1.7\nfixture")
    workspace = tmp_path / "workspace"
    digest = acquire_pdf(source, workspace=workspace, artifacts_path=local_models)
    draft = load_pdf_draft(workspace, digest)
    assert (
        draft.conversion_errors[0]["error_message"]
        == "Nonfatal diagnostic from external boundary"
    )
    assert draft.conversion_errors[0]["category"] == "unknown"
    assert (
        next(
            check for check in draft.checks if check.name == "conversion_errors"
        ).passed
        is False
    )


def test_conversion_exception_is_explicit_without_claiming_a_saved_draft(
    tmp_path, local_models, conversion, monkeypatch
):
    from docling.document_converter import DocumentConverter

    from doc_translate.pdf import PDFExtractionError

    def convert(*args, **kwargs):
        raise RuntimeError("artificial backend failure")

    monkeypatch.setattr(DocumentConverter, "convert", convert)
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF-1.7\nfixture")
    workspace = tmp_path / "workspace"
    with pytest.raises(PDFExtractionError, match="Docling conversion failed") as caught:
        acquire_pdf(source, workspace=workspace, artifacts_path=local_models)
    assert caught.value.draft_sha256 is None
    assert not (workspace / "pdf-draft.json").exists()


@pytest.mark.parametrize("artifact", ["original", "raw", "image", "draft"])
def test_load_pdf_draft_detects_tampering_in_every_saved_artifact(
    tmp_path, local_models, conversion, artifact
):
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF-1.7\nfixture")
    workspace = tmp_path / "workspace"
    digest = acquire_pdf(source, workspace=workspace, artifacts_path=local_models)
    draft = load_pdf_draft(workspace, digest)
    relative = {
        "original": draft.document.artifact,
        "raw": draft.raw.artifact,
        "image": draft.pages[0].image.artifact,
        "draft": f"drafts/pdf/{digest}.json",
    }[artifact]
    (workspace / relative).write_bytes(b"damaged bytes retained")
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        load_pdf_draft(workspace)


def test_other_writer_is_rejected_and_failed_reference_replace_keeps_previous_draft(
    tmp_path, local_models, conversion, monkeypatch
):
    import os
    from pathlib import Path

    from doc_translate.workspace import WorkspaceLockedError, workspace_lock

    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF-1.7\nfirst")
    workspace = tmp_path / "workspace"
    previous = acquire_pdf(source, workspace=workspace, artifacts_path=local_models)
    with workspace_lock(workspace), pytest.raises(WorkspaceLockedError):
        acquire_pdf(source, workspace=workspace, artifacts_path=local_models)
    source.write_bytes(b"%PDF-1.7\nsecond")
    replace = os.replace

    def fail_reference_replace(src, dst):
        if Path(dst).name == "pdf-draft.json":
            raise OSError("artificial reference replacement failure")
        return replace(src, dst)

    monkeypatch.setattr(os, "replace", fail_reference_replace)
    with pytest.raises(OSError, match="replacement failure"):
        acquire_pdf(source, workspace=workspace, artifacts_path=local_models)
    restored = load_pdf_draft(workspace)
    assert restored == load_pdf_draft(workspace, previous)
    assert (workspace / restored.document.artifact).read_bytes() == b"%PDF-1.7\nfirst"
    assert len(list((workspace / "drafts/pdf").glob("*.json"))) == 2


def test_importing_pdf_and_epub_apis_does_not_import_optional_pdf_dependencies():
    import subprocess
    import sys

    process = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import sys
from importlib.abc import MetaPathFinder
class BlockPDFImports(MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname.split(".")[0] in {"docling", "docling_core", "ocrmac", "torch", "transformers", "huggingface_hub"}:
            raise AssertionError(f"Optional import: {fullname}")
sys.meta_path.insert(0, BlockPDFImports())
import doc_translate.pdf
import doc_translate.epub_source
""",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert process.returncode == 0, process.stderr


def test_page_range_past_original_document_is_rejected_without_expanding_it(
    tmp_path, local_models, conversion
):
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF-1.7\nfixture")
    workspace = tmp_path / "workspace"
    with pytest.raises(ValueError, match="exceeds original PDF page count 1"):
        acquire_pdf(
            source, workspace=workspace, artifacts_path=local_models, pages=(2, 2)
        )
    assert not (workspace / "pdf-draft.json").exists()
