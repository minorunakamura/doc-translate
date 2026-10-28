"""Public run CLI through actual acquisition/stages and the offline faux provider."""

import hashlib
import json
import os
import shutil
from contextlib import redirect_stderr
from pathlib import Path

import pytest
from test_epub import make_epub

from doc_translate.cli import main
from doc_translate.snapshot import load_source_snapshot

pytest_plugins = ["test_pdf"]

ROOT = Path(__file__).resolve().parents[1]


def helper(tmp_path, **options):
    settings = tmp_path / "faux.json"
    settings.write_text(json.dumps(options))
    node = shutil.which("node")
    assert node
    return [
        node,
        str(ROOT / "ai/tests/run-process.mjs"),
        str(tmp_path / "credentials"),
        str(settings),
        str(tmp_path / "requests.jsonl"),
    ]


def run_args(source, workspace):
    return [
        "run",
        "--json",
        str(source),
        "--workspace",
        str(workspace),
        "--provider",
        "faux",
        "--model",
        "text",
        "--thinking",
        "off",
    ]


def test_run_epub_reaches_outputs_and_saved_only_status(tmp_path, capsys, monkeypatch):
    source = make_epub(tmp_path)
    workspace = tmp_path / "new"
    main(run_args(source, workspace), process_command=helper(tmp_path))
    summary = json.loads(capsys.readouterr().out)
    assert summary["processing"]["status"] == "completed"
    assert summary["scope"]["run"]["owned_pages"] is None
    assert summary["scope"]["run"]["reference_pages"] == []
    original_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    snapshot = load_source_snapshot(workspace)
    assert snapshot.document.format == "epub"
    assert snapshot.document.sha256 == original_hash
    assert (workspace / snapshot.document.artifact).read_bytes() == source.read_bytes()
    assert "訳:Original" in (workspace / summary["artifacts"]["japanese"]).read_text()
    requests = (tmp_path / "requests.jsonl").read_text().splitlines()
    assert len(requests) == 3
    assert "images" not in requests[-1]
    monkeypatch.setenv("PATH", "")
    main(["status", "--json", str(workspace)])
    status = json.loads(capsys.readouterr().out)
    assert status["render_current"] is True
    assert status["processing"]["status"] == "completed"


def test_run_announces_actual_auth_and_scope_before_provider_receives_data(
    tmp_path, capsys
):
    source = make_epub(tmp_path)
    notices = tmp_path / "notices.jsonl"
    with notices.open("w") as stream, redirect_stderr(stream):
        main(
            run_args(source, tmp_path / "new"),
            process_command=helper(tmp_path, notices=str(notices)),
        )
    summary = json.loads(capsys.readouterr().out)
    events = [json.loads(line) for line in notices.read_text().splitlines()]
    assert len(events) == 3
    assert [event["stage"] for event in events] == ["prepare", "prepare", "translation"]
    assert (
        events[0]["document_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    )
    assert events[0]["owned_pages"] is None
    assert events[0]["reference_pages"] == []
    assert events[0]["provider"] == "faux"
    assert events[0]["model"] == "text"
    assert events[0]["thinking"] == "off"
    assert events[0]["auth"] == {"source": None, "type": "api_key"}
    assert summary["processing"]["status"] == "completed"


@pytest.fixture
def pdf_input(tmp_path, conversion, monkeypatch):
    from docling_core.types.doc import (
        BoundingBox,
        DocItemLabel,
        ImageRef,
        ProvenanceItem,
        Size,
    )
    from PIL import Image

    for page in range(2, 10):
        conversion.document.add_page(
            page_no=page,
            size=Size(width=200, height=100),
            image=ImageRef.from_pil(Image.new("RGB", (400, 200), "white"), dpi=144),
        )
        text = f"ONLY_PAGE_{page}_TEXT"
        conversion.document.add_text(
            label=DocItemLabel.TEXT,
            text=text,
            orig=text,
            prov=ProvenanceItem(
                page_no=page,
                charspan=(0, len(text)),
                bbox=BoundingBox(l=10, t=80, r=100, b=60, coord_origin="BOTTOMLEFT"),
            ),
        )
    conversion.input.page_count = 9
    from copy import copy

    from docling.document_converter import DocumentConverter

    convert = DocumentConverter.convert

    def ranged_convert(converter, source, **kwargs):
        result = copy(convert(converter, source, **kwargs))
        result.document = result.document.model_copy(deep=True)
        start, end = kwargs["page_range"]
        result.document.pages = {
            page: value
            for page, value in result.document.pages.items()
            if start <= page <= end
        }
        return result

    monkeypatch.setattr(DocumentConverter, "convert", ranged_convert)
    source = tmp_path / "own.pdf"
    source.write_bytes(b"%PDF-1.7\nsynthetic run converter input")
    return source


@pytest.mark.parametrize(
    "reference,expected_refs",
    [(None, [[7], [6]]), ("1-1", [[1, 7], [6]]), ("9-9", [[7], [6]])],
)
def test_run_pdf_owns_requested_pages_and_sends_only_authorized_adjacent_context(
    tmp_path, pdf_input, local_models, reference, expected_refs, capsys
):
    workspace = tmp_path / "new"
    args = run_args(pdf_input, workspace) + [
        "--pages",
        "2-7",
        "--artifacts-path",
        str(local_models),
        "--correction-model",
        "vision",
    ]
    if reference:
        args += ["--reference-pages", reference]
    main(args, process_command=helper(tmp_path))
    summary = json.loads(capsys.readouterr().out)
    assert summary["processing"]["status"] == "completed"
    assert summary["scope"]["run"]["owned_pages"] == [2, 3, 4, 5, 6, 7]
    assert summary["scope"]["run"]["corrected_pages"] == [2, 3, 4, 5, 6, 7]
    assert summary["scope"]["whole_document_verified"] is False
    snapshot = load_source_snapshot(workspace)
    assert snapshot.document.format == "pdf"
    assert sorted(
        {
            location.page
            for block in snapshot.blocks
            for location in block.locations
            if location.format == "pdf"
        }
    ) == [2, 3, 4, 5, 6, 7]
    requests = [
        json.loads(line)
        for line in (tmp_path / "requests.jsonl").read_text().splitlines()
    ]
    corrections = [
        request
        for request in requests
        if any(part["type"] == "image" for part in request["messages"][1]["content"])
    ]
    inputs = [
        json.loads(request["messages"][1]["content"][0]["text"])
        for request in corrections
    ]
    assert [item["context"]["owned_pages"] for item in inputs] == [[2, 3, 4, 5, 6], [7]]
    assert [item["context"]["reference_pages"] for item in inputs] == expected_refs
    assert all(
        "ONLY_PAGE_8_TEXT" not in item["text"]
        and "ONLY_PAGE_9_TEXT" not in item["text"]
        for item in inputs
    )
    assert [
        sum(part["type"] == "image" for part in request["messages"][1]["content"])
        for request in corrections
    ] == [
        5 + len(expected_refs[0]),
        1 + len(expected_refs[1]),
    ]


@pytest.mark.parametrize("existing", ["empty", "file", "symlink", "dangling"])
def test_run_refuses_every_existing_workspace_without_touching_it(
    tmp_path, existing, capsys
):
    source = make_epub(tmp_path)
    workspace = tmp_path / "existing"
    if existing == "empty":
        workspace.mkdir()
    elif existing == "file":
        workspace.write_text("preserve")
    else:
        target = tmp_path / "target"
        if existing == "symlink":
            target.mkdir()
        workspace.symlink_to(target)
    with pytest.raises(SystemExit) as stopped:
        main(run_args(source, workspace), process_command=helper(tmp_path))
    assert stopped.value.code == 2
    assert "already exists" in capsys.readouterr().err
    assert not (tmp_path / "requests.jsonl").exists()
    if existing == "empty":
        assert list(workspace.iterdir()) == []
    elif existing == "file":
        assert workspace.read_text() == "preserve"
    else:
        assert workspace.is_symlink()


def test_run_partial_pdf_keeps_success_and_reports_intended_scope_in_status(
    tmp_path, pdf_input, local_models, capsys
):
    workspace = tmp_path / "new"
    args = run_args(pdf_input, workspace) + [
        "--pages",
        "2-7",
        "--artifacts-path",
        str(local_models),
        "--correction-model",
        "vision",
    ]
    with pytest.raises(SystemExit) as stopped:
        main(args, process_command=helper(tmp_path, correction_fail_page=7))
    assert stopped.value.code == 1
    summary = json.loads(capsys.readouterr().out)
    assert summary["processing"]["status"] == "partial"
    assert summary["scope"]["run"]["owned_pages"] == [2, 3, 4, 5, 6, 7]
    assert summary["scope"]["run"]["corrected_pages"] == [2, 3, 4, 5, 6]
    assert summary["scope"]["run"]["failed_pages"] == [7]
    assert load_source_snapshot(workspace).processing.status == "failed"
    assert "訳:Page" in (workspace / summary["artifacts"]["japanese"]).read_text()
    main(["status", "--json", str(workspace)])
    status = json.loads(capsys.readouterr().out)
    assert status["processing"]["status"] == "partial"
    assert status["scope"]["run"] == summary["scope"]["run"]


@pytest.mark.parametrize("stage", ["prepare", "translation"])
def test_run_stage_failure_still_renders_saved_source_as_partial(
    tmp_path, capsys, stage
):
    source = make_epub(tmp_path)
    workspace = tmp_path / "new"
    with pytest.raises(SystemExit) as stopped:
        main(
            run_args(source, workspace),
            process_command=helper(tmp_path, fail_stage=stage),
        )
    assert stopped.value.code == 1
    summary = json.loads(capsys.readouterr().out)
    assert summary["processing"]["status"] == "partial"
    assert summary["processing"][
        "not_processed" if stage == "prepare" else "failed"
    ] == ["unit-1"]
    assert "Original" in (workspace / summary["artifacts"]["japanese"]).read_text()
    assert (
        summary["usage"]["attempts_by_stage"][
            "preparation" if stage == "prepare" else "translation"
        ]
        >= 1
    )


def test_run_without_any_corrected_source_reports_failure_and_saved_paths(
    tmp_path, pdf_input, local_models, capsys
):
    workspace = tmp_path / "new"
    with pytest.raises(SystemExit) as stopped:
        main(
            run_args(pdf_input, workspace)
            + [
                "--pages",
                "2-3",
                "--artifacts-path",
                str(local_models),
                "--correction-model",
                "vision",
            ],
            process_command=helper(tmp_path, correction_fail_page=2),
        )
    assert stopped.value.code == 1
    summary = json.loads(capsys.readouterr().out)
    assert summary["processing"]["status"] == "partial"
    assert summary["scope"]["run"]["failed_pages"] == [2, 3]
    assert "japanese" not in summary["artifacts"]
    assert (workspace / summary["artifacts"]["original"]).is_file()
    assert (workspace / summary["artifacts"]["pdf_draft"]).is_file()
    assert not (workspace / "source-snapshot.json").exists()
    main(["status", "--json", str(workspace)])
    assert json.loads(capsys.readouterr().out)["scope"] == summary["scope"]


def test_run_pdf_shrinks_length_limited_chunks_without_losing_owned_pages(
    tmp_path, pdf_input, local_models, capsys
):
    workspace = tmp_path / "new"
    main(
        run_args(pdf_input, workspace)
        + [
            "--pages",
            "2-7",
            "--artifacts-path",
            str(local_models),
            "--correction-model",
            "vision",
        ],
        process_command=helper(tmp_path, correction_length_above=2),
    )
    summary = json.loads(capsys.readouterr().out)
    assert summary["processing"]["status"] == "completed"
    assert summary["scope"]["run"]["corrected_pages"] == [2, 3, 4, 5, 6, 7]
    inputs = [
        json.loads(json.loads(line)["messages"][1]["content"][0]["text"])
        for line in (tmp_path / "requests.jsonl").read_text().splitlines()
    ]
    assert [
        item["context"]["owned_pages"]
        for item in inputs
        if "owned_pages" in item["context"]
    ] == [[2, 3, 4, 5, 6], [2, 3], [4, 5, 6], [4], [5, 6], [7]]


def test_run_imports_glossary_terms_with_user_priority_and_attention_is_not_failure(
    tmp_path, capsys
):
    from doc_translate.preparation import load_preparation

    source = make_epub(tmp_path)
    glossary = tmp_path / "glossary.json"
    glossary.write_text(
        json.dumps(
            {
                "version": "old",
                "book": "other",
                "style": "ignored",
                "terms": {"Original": "利用者指定"},
            }
        )
    )
    workspace = tmp_path / "new"
    main(
        run_args(source, workspace) + ["--glossary", str(glossary)],
        process_command=helper(tmp_path, attention=True, candidate=True),
    )
    summary = json.loads(capsys.readouterr().out)
    assert summary["processing"]["status"] == "completed"
    assert summary["uncertainty_count"] == 1
    preparation = load_preparation(workspace)
    assert preparation.glossary.terms == {"Original": "利用者指定"}
    assert preparation.glossary.version == "1"
    assert preparation.glossary.book == "book"
    assert (
        preparation.glossary.style
        == "本文はですます調。見出しは自然な体言止めを認めます。"
    )
    assert summary["content_review"]["meaning_quality_approved"] is False


def test_run_unknown_model_is_config_error_without_generation_or_fallback(
    tmp_path, capsys
):
    source = make_epub(tmp_path)
    workspace = tmp_path / "new"
    with pytest.raises(SystemExit) as stopped:
        main(
            run_args(source, workspace) + ["--translation-model", "not-a-model"],
            process_command=helper(tmp_path),
        )
    assert stopped.value.code == 2
    summary = json.loads(capsys.readouterr().out)
    assert summary["processing"]["status"] == "partial"
    assert summary["scope"]["run"]["error_code"] == "unknown_model"
    assert not (tmp_path / "requests.jsonl").exists()
    main(["status", "--json", str(workspace)])
    assert (
        json.loads(capsys.readouterr().out)["scope"]["run"]["error_code"]
        == "unknown_model"
    )


def test_run_pdf_without_pages_owns_actual_full_document(
    tmp_path, pdf_input, local_models, capsys
):
    workspace = tmp_path / "new"
    main(
        run_args(pdf_input, workspace)
        + ["--artifacts-path", str(local_models), "--correction-model", "vision"],
        process_command=helper(tmp_path),
    )
    summary = json.loads(capsys.readouterr().out)
    assert summary["processing"]["status"] == "completed"
    assert summary["scope"]["run"]["owned_pages"] == [1, 2, 3, 4, 5, 6, 7, 8, 9]
    assert summary["scope"]["run"]["not_processed_pages"] == []


@pytest.mark.parametrize(
    "options",
    [
        ["--pages", "1-2"],
        ["--reference-pages", "1-1"],
        ["--pages", "0-1"],
        ["--pages", "3-2"],
    ],
)
def test_run_rejects_epub_page_options_without_transmission(tmp_path, options, capsys):
    source = make_epub(tmp_path)
    workspace = tmp_path / "new"
    with pytest.raises(SystemExit) as stopped:
        main(run_args(source, workspace) + options, process_command=helper(tmp_path))
    assert stopped.value.code == 2
    assert not workspace.exists()
    assert not (tmp_path / "requests.jsonl").exists()


def test_run_rejects_epub_reader_archive_limit_before_copy_or_transmission(tmp_path):
    from doc_translate.epub import MAX_ARCHIVE_BYTES

    source = tmp_path / "huge.epub"
    with source.open("wb") as stream:
        stream.truncate(MAX_ARCHIVE_BYTES + 1)
    workspace = tmp_path / "new"
    with pytest.raises(SystemExit) as stopped:
        main(run_args(source, workspace), process_command=helper(tmp_path))
    assert stopped.value.code == 2
    assert not workspace.exists()
    assert not (tmp_path / "requests.jsonl").exists()


def test_run_pdf_shrinks_preflight_image_limits_before_transmitting(
    tmp_path, pdf_input, local_models, capsys
):
    workspace = tmp_path / "new"
    main(
        run_args(pdf_input, workspace)
        + [
            "--pages",
            "2-7",
            "--artifacts-path",
            str(local_models),
            "--correction-model",
            "vision",
        ],
        process_command=helper(tmp_path, image_limit=3),
    )
    summary = json.loads(capsys.readouterr().out)
    assert summary["processing"]["status"] == "completed"
    assert summary["scope"]["run"]["corrected_pages"] == [2, 3, 4, 5, 6, 7]
    requests = [
        json.loads(line)
        for line in (tmp_path / "requests.jsonl").read_text().splitlines()
    ]
    image_counts = [
        sum(part["type"] == "image" for part in request["messages"][1]["content"])
        for request in requests
    ]
    assert max(image_counts) == 3
    assert summary["usage"]["attempts_by_stage"]["correction"] > sum(
        count > 0 for count in image_counts
    )


def test_run_pdf_singleton_length_failure_is_not_success_or_config_error(
    tmp_path, pdf_input, local_models, capsys
):
    workspace = tmp_path / "new"
    with pytest.raises(SystemExit) as stopped:
        main(
            run_args(pdf_input, workspace)
            + [
                "--pages",
                "2-2",
                "--artifacts-path",
                str(local_models),
                "--correction-model",
                "vision",
            ],
            process_command=helper(tmp_path, correction_length_above=0),
        )
    assert stopped.value.code == 1
    summary = json.loads(capsys.readouterr().out)
    assert summary["processing"]["status"] == "partial"
    assert summary["scope"]["run"]["failed_pages"] == [2]
    assert summary["scope"]["run"]["corrected_pages"] == []
    assert len((tmp_path / "requests.jsonl").read_text().splitlines()) == 1


def test_resume_pdf_fills_failed_ownership_without_acquisition_or_resending_accepted(
    tmp_path, pdf_input, local_models, capsys, monkeypatch
):
    from test_resume import assert_preserved, requests, saved_files

    workspace = tmp_path / "new"
    with pytest.raises(SystemExit) as stopped:
        main(
            run_args(pdf_input, workspace)
            + [
                "--pages",
                "2-7",
                "--artifacts-path",
                str(local_models),
                "--correction-model",
                "vision",
            ],
            process_command=helper(tmp_path, correction_fail_page=7),
        )
    assert stopped.value.code == 1
    capsys.readouterr()
    old_files = saved_files(workspace)
    count = len(requests(tmp_path))
    pdf_input.unlink()
    shutil.rmtree(local_models)
    main(["resume", str(workspace)], process_command=helper(tmp_path))
    result = json.loads(capsys.readouterr().out)
    assert result["processing"]["status"] == "completed"
    assert result["scope"]["run"]["corrected_pages"] == [2, 3, 4, 5, 6, 7]
    assert result["scope"]["run"]["failed_pages"] == []
    assert result["scope"]["run"]["not_processed_pages"] == []
    assert result["scope"]["run"]["error"] is None
    corrections = [
        item for item in requests(tmp_path)[count:] if "owned_pages" in item["context"]
    ]
    assert [item["context"]["owned_pages"] for item in corrections] == [[7]]
    assert_preserved(old_files)
    count = len(requests(tmp_path))
    monkeypatch.setenv("PATH", "")
    main(["resume", str(workspace)])
    assert json.loads(capsys.readouterr().out)["processing"]["status"] == "completed"
    assert len(requests(tmp_path)) == count


def test_resume_pdf_recovers_adaptive_reply_before_regrouping_pending_pages(
    tmp_path, pdf_input, local_models, capsys, monkeypatch
):
    from test_resume import assert_preserved, requests, saved_files

    workspace = tmp_path / "new"
    replace = os.replace
    chunks = 0

    def fail_replace(src, dst):
        nonlocal chunks
        if "corrections/pdf" in str(dst):
            chunks += 1
            if chunks == 2:
                raise OSError("interrupted second correction chunk")
        return replace(src, dst)

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", fail_replace)
        with pytest.raises(SystemExit) as stopped:
            main(
                run_args(pdf_input, workspace)
                + [
                    "--pages",
                    "2-7",
                    "--artifacts-path",
                    str(local_models),
                    "--correction-model",
                    "vision",
                ],
                process_command=helper(tmp_path, correction_length_above=3),
            )
        assert stopped.value.code == 1
    capsys.readouterr()
    old_files = saved_files(workspace)
    sent = requests(tmp_path)
    assert [item["context"]["owned_pages"] for item in sent] == [
        [2, 3, 4, 5, 6],
        [2, 3],
        [4, 5, 6],
    ]
    shutil.rmtree(local_models)
    main(
        ["resume", str(workspace)],
        process_command=helper(tmp_path, correction_length_above=3),
    )
    result = json.loads(capsys.readouterr().out)
    assert result["processing"]["status"] == "completed"
    assert result["scope"]["run"]["corrected_pages"] == [2, 3, 4, 5, 6, 7]
    corrections = [
        item
        for item in requests(tmp_path)[len(sent) :]
        if "owned_pages" in item["context"]
    ]
    assert [item["context"]["owned_pages"] for item in corrections] == [[7]]
    assert [item["context"]["reference_pages"] for item in corrections] == [[6]]
    assert_preserved(old_files)


def test_resume_pdf_glossary_never_reacquires_or_recorrrects(
    tmp_path, pdf_input, local_models, capsys
):
    from test_resume import requests

    workspace = tmp_path / "new"
    main(
        run_args(pdf_input, workspace)
        + [
            "--pages",
            "2-3",
            "--artifacts-path",
            str(local_models),
            "--correction-model",
            "vision",
        ],
        process_command=helper(tmp_path),
    )
    capsys.readouterr()
    count = len(requests(tmp_path))
    shutil.rmtree(local_models)
    glossary = tmp_path / "terms.json"
    glossary.write_text(
        json.dumps(
            {
                "version": "2",
                "book": "ignored",
                "style": "ignored",
                "terms": {"Page": "指定"},
            }
        )
    )
    main(
        ["resume", str(workspace), "--glossary", str(glossary)],
        process_command=helper(tmp_path),
    )
    assert json.loads(capsys.readouterr().out)["processing"]["status"] == "completed"
    new = requests(tmp_path)[count:]
    assert len(new) == 2
    assert all("owned_pages" not in item["context"] for item in new)


def test_resume_pdf_recovers_chunk_before_progress_replace_after_force_kill(
    tmp_path, pdf_input, local_models, capsys
):
    import signal

    from test_resume import (
        assert_preserved,
        await_boundary,
        external_cli,
        requests,
        saved_files,
    )

    workspace = tmp_path / "new"
    with pytest.raises(SystemExit):
        main(
            run_args(pdf_input, workspace)
            + [
                "--pages",
                "2-7",
                "--artifacts-path",
                str(local_models),
                "--correction-model",
                "vision",
            ],
            process_command=helper(tmp_path, correction_fail_page=7),
        )
    capsys.readouterr()
    old_files = saved_files(workspace)
    count = len(requests(tmp_path))
    shutil.rmtree(local_models)
    process = external_cli(
        tmp_path,
        helper(tmp_path),
        ["resume", str(workspace)],
        boundary="run.json",
        occurrence=3,
    )
    try:
        await_boundary(tmp_path, process)
        process.kill()
        assert process.wait(timeout=15) == -signal.SIGKILL
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
    assert len(requests(tmp_path)) == count + 1
    main(["resume", str(workspace)], process_command=helper(tmp_path))
    result = json.loads(capsys.readouterr().out)
    assert result["processing"]["status"] == "completed"
    new_corrections = [
        item for item in requests(tmp_path)[count:] if "owned_pages" in item["context"]
    ]
    assert [item["context"]["owned_pages"] for item in new_corrections] == [[7]]
    assert_preserved(old_files)


def test_resume_pdf_validates_whole_draft_images_once_per_pass_not_per_chunk(
    tmp_path, pdf_input, local_models, capsys, monkeypatch
):
    from PIL import Image
    from test_resume import requests

    workspace = tmp_path / "new"
    main(
        run_args(pdf_input, workspace)
        + [
            "--pages",
            "2-7",
            "--reference-pages",
            "9-9",
            "--artifacts-path",
            str(local_models),
            "--correction-model",
            "vision",
        ],
        process_command=helper(tmp_path, correction_length_above=1),
    )
    result = json.loads(capsys.readouterr().out)
    assert len(result["scope"]["run"]["chunk_sha256s"]) == 6
    sent = requests(tmp_path)
    opens = 0
    image_open = Image.open

    def count_open(fp, *args, **kwargs):
        nonlocal opens
        opens += 1
        return image_open(fp, *args, **kwargs)

    monkeypatch.setattr(Image, "open", count_open)
    main(["resume", str(workspace)], process_command=helper(tmp_path))
    assert json.loads(capsys.readouterr().out)["processing"]["status"] == "completed"
    assert requests(tmp_path) == sent
    # Eight draft PNGs: initial verification, reconciliation and source assembly.
    assert 1 <= opens <= 3 * 8


def test_resume_incomplete_pdf_draft_reacquires_and_old_manifest_can_supply_models(
    tmp_path, pdf_input, local_models, conversion, capsys
):
    from test_resume import requests

    from doc_translate.pdf import load_pdf_draft

    workspace = tmp_path / "new"
    conversion.status.value = "partial_success"
    with pytest.raises(SystemExit) as stopped:
        main(
            run_args(pdf_input, workspace)
            + [
                "--pages",
                "2-3",
                "--artifacts-path",
                str(local_models),
                "--correction-model",
                "vision",
            ],
            process_command=helper(tmp_path),
        )
    assert stopped.value.code == 1
    result = json.loads(capsys.readouterr().out)
    assert "duplicate provider charges" in result["usage"]["note"]
    old_draft = (workspace / "pdf-draft.json").read_bytes()
    assert not requests(tmp_path)
    run = json.loads((workspace / "run.json").read_bytes())
    del run["artifacts_path"]  # before resume supported saving acquisition paths
    (workspace / "run.json").write_text(json.dumps(run))
    with pytest.raises(SystemExit) as stopped:
        main(["resume", str(workspace)], process_command=helper(tmp_path))
    assert stopped.value.code == 2
    assert "--artifacts-path" in capsys.readouterr().err
    assert not requests(tmp_path)
    conversion.status.value = "success"
    main(
        ["resume", str(workspace), "--artifacts-path", str(local_models)],
        process_command=helper(tmp_path),
    )
    assert json.loads(capsys.readouterr().out)["processing"]["status"] == "completed"
    assert (workspace / "pdf-draft.json").read_bytes() != old_draft
    assert load_pdf_draft(workspace).conversion_status == "success"
    old_digest = json.loads(old_draft)["sha256"]
    assert load_pdf_draft(workspace, old_digest).conversion_status == "partial_success"


def test_revised_pdf_source_and_policy_resume_keep_acquisition_and_correction(
    tmp_path, pdf_input, local_models, capsys, monkeypatch
):
    from test_resume import assert_preserved, requests, saved_files
    from test_revise import pointer

    workspace = tmp_path / "new"
    main(
        run_args(pdf_input, workspace)
        + [
            "--pages",
            "2-3",
            "--artifacts-path",
            str(local_models),
            "--correction-model",
            "vision",
        ],
        process_command=helper(tmp_path),
    )
    capsys.readouterr()
    before = saved_files(workspace)
    source = load_source_snapshot(workspace).model_dump(mode="json")
    body = next(block for block in source["blocks"] if block["kind"] == "body")
    body["text"] = "Revised paragraph."
    replacement = tmp_path / "source.json"
    replacement.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "base_source_sha256": pointer(workspace, "source"),
                "source": source,
            }
        )
    )
    main(
        ["revise", str(workspace), "--source", str(replacement), "--reason", "原本照合"]
    )
    capsys.readouterr()
    adopted = pointer(workspace, "source")
    count = len(requests(tmp_path))
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps({"version": "2", "text": "本文は常体。"}))
    glossary = tmp_path / "glossary.json"
    glossary.write_text(
        json.dumps(
            {
                "version": "2",
                "book": "ignored",
                "style": "ignored",
                "terms": {"paragraph": "段落"},
            }
        )
    )
    # The external Docling API must not be invoked on a saved source revision.
    from docling.document_converter import DocumentConverter

    def no_acquisition(*args, **kwargs):
        pytest.fail("reacquired revised PDF")

    monkeypatch.setattr(DocumentConverter, "convert", no_acquisition)
    main(
        [
            "resume",
            str(workspace),
            "--policy",
            str(policy),
            "--glossary",
            str(glossary),
        ],
        process_command=helper(tmp_path),
    )
    result = json.loads(capsys.readouterr().out)
    assert result["processing"]["status"] == "completed"
    assert pointer(workspace, "source") == adopted
    assert all(
        "owned_pages" not in request["context"]
        for request in requests(tmp_path)[count:]
    )
    assert (
        "Revised paragraph" in (workspace / result["artifacts"]["english"]).read_text()
    )
    assert requests(tmp_path)[-1]["glossary"]["terms"]["paragraph"] == "段落"
    assert requests(tmp_path)[-1]["context"]["style_policy"]["version"] == "2"
    assert_preserved(before)


def test_revise_pdf_refuses_unknown_extraction_reference(
    tmp_path, pdf_input, local_models, capsys
):
    from test_resume import requests
    from test_revise import pointer

    workspace = tmp_path / "new"
    main(
        run_args(pdf_input, workspace)
        + [
            "--pages",
            "2-2",
            "--artifacts-path",
            str(local_models),
            "--correction-model",
            "vision",
        ],
        process_command=helper(tmp_path),
    )
    capsys.readouterr()
    data = load_source_snapshot(workspace).model_dump(mode="json")
    data["blocks"][0]["locations"][0]["extraction_ref"] = "#/texts/unknown"
    data["blocks"][0]["locations"][0]["extraction_snapshot"] = json.loads(
        (workspace / "pdf-draft.json").read_bytes()
    )["sha256"]
    base = pointer(workspace, "source")
    replacement = tmp_path / "source.json"
    replacement.write_text(
        json.dumps({"schema_version": 1, "base_source_sha256": base, "source": data})
    )
    count = len(requests(tmp_path))
    with pytest.raises(SystemExit) as rejected:
        main(
            [
                "revise",
                str(workspace),
                "--source",
                str(replacement),
                "--reason",
                "invalid",
            ]
        )
    assert rejected.value.code == 2
    assert pointer(workspace, "source") == base
    assert len(requests(tmp_path)) == count


def revise_pdf_paragraph(tmp_path, workspace, capsys, text="Human correction."):
    from test_revise import pointer

    source = load_source_snapshot(workspace).model_dump(mode="json")
    next(block for block in source["blocks"] if block["kind"] == "body")["text"] = text
    replacement = tmp_path / "human-source.json"
    replacement.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "base_source_sha256": pointer(workspace, "source"),
                "source": source,
            }
        )
    )
    main(
        [
            "revise",
            str(workspace),
            "--source",
            str(replacement),
            "--reason",
            "Original checked",
        ]
    )
    capsys.readouterr()
    return pointer(workspace, "source")


def test_revised_partial_pdf_saves_candidate_without_downstream_or_pointer_changes(
    tmp_path, pdf_input, local_models, capsys, monkeypatch
):
    from docling.document_converter import DocumentConverter
    from test_resume import assert_preserved, requests, saved_files
    from test_revise import pointer

    workspace = tmp_path / "new"
    with pytest.raises(SystemExit):
        main(
            run_args(pdf_input, workspace)
            + [
                "--pages",
                "2-7",
                "--artifacts-path",
                str(local_models),
                "--correction-model",
                "vision",
            ],
            process_command=helper(tmp_path, correction_fail_page=7),
        )
    capsys.readouterr()
    baseline = pointer(workspace, "source")
    adopted = revise_pdf_paragraph(tmp_path, workspace, capsys)
    preserved = saved_files(workspace)
    pointers = {
        name: (workspace / name).read_bytes()
        for name in (
            "source-snapshot.json",
            "preparation-snapshot.json",
            "translation-snapshot.json",
            "render.json",
        )
    }
    count = len(requests(tmp_path))
    monkeypatch.setattr(
        DocumentConverter,
        "convert",
        lambda *a, **kw: pytest.fail("reacquired saved PDF"),
    )
    with pytest.raises(SystemExit) as waiting:
        main(["resume", str(workspace)], process_command=helper(tmp_path))
    assert waiting.value.code == 1
    summary = json.loads(capsys.readouterr().out)
    sent = requests(tmp_path)[count:]
    assert [item["context"]["owned_pages"] for item in sent] == [[7]]
    assert all(
        (workspace / name).read_bytes() == data for name, data in pointers.items()
    )
    candidate = summary["source_adoption"]
    assert candidate["status"] == "pending"
    assert candidate["H"]["sha256"] == adopted
    assert candidate["B"]["sha256"] == baseline
    assert candidate["old_inputs"]["corrected_pages"] == [2, 3, 4, 5, 6]
    assert candidate["new_inputs"]["corrected_pages"] == [2, 3, 4, 5, 6, 7]
    assert candidate["page_changes"]["added"] == [7]
    assert "Human correction" in load_source_snapshot(workspace).blocks[0].text
    assert_preserved(preserved)
    command = helper(tmp_path)
    monkeypatch.setenv("PATH", "")
    main(["status", "--json", str(workspace)])
    status = json.loads(capsys.readouterr().out)
    assert status["source_adoption"] == candidate
    assert status["processing"]["status"] == "partial"
    with pytest.raises(SystemExit) as rendered:
        main(["render", str(workspace)])
    assert rendered.value.code == 1
    output = json.loads(capsys.readouterr().out)
    assert (
        "Human correction" in (workspace / output["artifacts"]["english"]).read_text()
    )
    with pytest.raises(SystemExit):
        main(["resume", str(workspace)], process_command=command)
    capsys.readouterr()
    assert len(requests(tmp_path)) == count + 1


@pytest.mark.parametrize("edited", [False, True])
def test_pdf_candidate_offline_adoption_advances_machine_base_across_revisions(
    tmp_path, pdf_input, local_models, capsys, monkeypatch, edited
):
    from test_resume import assert_preserved, requests, saved_files
    from test_revise import pointer

    workspace = tmp_path / "new"
    with pytest.raises(SystemExit):
        main(
            run_args(pdf_input, workspace)
            + [
                "--pages",
                "2-7",
                "--artifacts-path",
                str(local_models),
                "--correction-model",
                "vision",
            ],
            process_command=helper(tmp_path, correction_fail_page=7),
        )
    capsys.readouterr()
    baseline = pointer(workspace, "source")
    revise_pdf_paragraph(tmp_path, workspace, capsys)
    head = revise_pdf_paragraph(tmp_path, workspace, capsys, "Second human correction.")
    with pytest.raises(SystemExit):
        main(["resume", str(workspace)], process_command=helper(tmp_path))
    candidate = json.loads(capsys.readouterr().out)["source_adoption"]
    before = saved_files(workspace)
    count = len(requests(tmp_path))
    envelope = json.loads((workspace / candidate["envelope"]).read_bytes())
    if edited:
        envelope["source"]["blocks"][0]["text"] = "Explicitly integrated correction."
    # Provenance and scope are application-owned, not the user's declarations.
    envelope["source"]["machine_base_sha256"] = baseline
    envelope["source"]["machine_inputs"]["failed_pages"] = [123]
    replacement = tmp_path / "adopt.json"
    replacement.write_text(json.dumps(envelope))
    command = helper(tmp_path)
    monkeypatch.setenv("PATH", "")
    main(
        [
            "revise",
            str(workspace),
            "--source",
            str(replacement),
            "--reason",
            "Candidate compared with original",
        ]
    )
    capsys.readouterr()
    adopted = load_source_snapshot(workspace)
    assert adopted.previous_snapshot_sha256 == head
    assert adopted.machine_base_sha256 == candidate["C"]["sha256"]
    assert adopted.adopted_candidate_sha256 == candidate["candidate_sha256"]
    assert adopted.machine_inputs is not None
    assert adopted.machine_inputs.corrected_pages == [2, 3, 4, 5, 6, 7]
    assert adopted.machine_inputs.failed_pages == []
    if edited:
        assert adopted.blocks[0].text == "Explicitly integrated correction."
    assert len(requests(tmp_path)) == count
    with pytest.raises(SystemExit) as stale:
        main(
            [
                "revise",
                str(workspace),
                "--source",
                str(replacement),
                "--reason",
                "Duplicate",
            ]
        )
    assert stale.value.code == 2
    capsys.readouterr()
    latest = revise_pdf_paragraph(
        tmp_path, workspace, capsys, "Post adoption correction."
    )
    main(["resume", str(workspace)], process_command=command)
    summary = json.loads(capsys.readouterr().out)
    assert summary["processing"]["status"] == "completed"
    assert "source_adoption" not in summary
    assert pointer(workspace, "source") == latest
    assert (
        load_source_snapshot(workspace).machine_base_sha256 == candidate["C"]["sha256"]
    )
    assert all(
        "owned_pages" not in item["context"] for item in requests(tmp_path)[count:]
    )
    assert_preserved(before)


def test_revised_pdf_same_machine_content_under_explicit_correction_change_continues(
    tmp_path, pdf_input, local_models, capsys, monkeypatch
):
    from docling.document_converter import DocumentConverter
    from test_resume import requests
    from test_revise import pointer

    workspace = tmp_path / "new"
    main(
        run_args(pdf_input, workspace)
        + [
            "--pages",
            "2-3",
            "--reference-pages",
            "4-4",
            "--artifacts-path",
            str(local_models),
            "--correction-model",
            "vision",
        ],
        process_command=helper(tmp_path),
    )
    capsys.readouterr()
    adopted = revise_pdf_paragraph(tmp_path, workspace, capsys)
    count = len(requests(tmp_path))
    monkeypatch.setattr(
        DocumentConverter,
        "convert",
        lambda *a, **kw: pytest.fail("reacquired saved PDF"),
    )
    main(
        ["resume", str(workspace), "--correction-thinking", "minimal"],
        process_command=helper(tmp_path),
    )
    summary = json.loads(capsys.readouterr().out)
    assert summary["processing"]["status"] == "completed"
    assert "source_adoption" not in summary
    assert pointer(workspace, "source") == adopted
    correction = [
        item for item in requests(tmp_path)[count:] if "owned_pages" in item["context"]
    ]
    assert len(correction) == 1
    assert correction[0]["context"]["owned_pages"] == [2, 3]
    assert correction[0]["context"]["reference_pages"] == [4]


def test_legacy_pdf_revision_preserves_original_byte_hash_through_resume(
    tmp_path, pdf_input, local_models, capsys
):
    from test_revise import pointer

    from doc_translate.translation import load_translation

    workspace = tmp_path / "new"
    main(
        run_args(pdf_input, workspace)
        + [
            "--pages",
            "2-3",
            "--artifacts-path",
            str(local_models),
            "--correction-model",
            "vision",
        ],
        process_command=helper(tmp_path),
    )
    capsys.readouterr()
    legacy = load_source_snapshot(workspace).model_dump(mode="json")
    legacy.pop("machine_inputs")
    raw = json.dumps(legacy, indent=3).encode()
    base = hashlib.sha256(raw).hexdigest()
    (workspace / f"snapshots/source/{base}.json").write_bytes(raw)
    legacy["previous_snapshot_sha256"] = base
    legacy["change_reason"] = "Legacy original check"
    legacy["blocks"][0]["text"] = "Legacy human correction."
    head_bytes = json.dumps(legacy, indent=4).encode()
    head = hashlib.sha256(head_bytes).hexdigest()
    (workspace / f"snapshots/source/{head}.json").write_bytes(head_bytes)
    (workspace / "source-snapshot.json").write_text(
        json.dumps({"schema_version": 1, "sha256": head})
    )
    main(["status", "--json", str(workspace)])
    assert json.loads(capsys.readouterr().out)["source_snapshot_sha256"] == head
    main(["resume", str(workspace)], process_command=helper(tmp_path))
    summary = json.loads(capsys.readouterr().out)
    assert summary["processing"]["status"] == "completed"
    assert pointer(workspace, "source") == head
    translated = load_translation(workspace)
    assert translated.source_snapshot_sha256 == head
    assert {
        unit.source_snapshot_sha256
        for chunk in translated.chunks
        for unit in chunk.units
    } == {head}
    assert (workspace / f"snapshots/source/{base}.json").read_bytes() == raw
    assert (workspace / f"snapshots/source/{head}.json").read_bytes() == head_bytes


def pending_pdf_candidate(tmp_path, pdf_input, local_models, capsys):
    workspace = tmp_path / "new"
    with pytest.raises(SystemExit):
        main(
            run_args(pdf_input, workspace)
            + [
                "--pages",
                "2-7",
                "--artifacts-path",
                str(local_models),
                "--correction-model",
                "vision",
            ],
            process_command=helper(tmp_path, correction_fail_page=7),
        )
    capsys.readouterr()
    revise_pdf_paragraph(tmp_path, workspace, capsys)
    with pytest.raises(SystemExit):
        main(["resume", str(workspace)], process_command=helper(tmp_path))
    return workspace, json.loads(capsys.readouterr().out)["source_adoption"]


def test_normal_pdf_revision_rebinds_candidate_without_hidden_stale_render_summary(
    tmp_path, pdf_input, local_models, capsys
):
    from test_resume import requests
    from test_revise import pointer

    workspace, first = pending_pdf_candidate(tmp_path, pdf_input, local_models, capsys)
    old_envelope = workspace / first["envelope"]
    head = revise_pdf_paragraph(tmp_path, workspace, capsys, "Another original check.")
    with pytest.raises(SystemExit) as stale:
        main(
            [
                "revise",
                str(workspace),
                "--source",
                str(old_envelope),
                "--reason",
                "Old envelope",
            ]
        )
    assert stale.value.code == 2
    capsys.readouterr()
    with pytest.raises(SystemExit):
        main(["render", str(workspace)])
    old_render = json.loads(capsys.readouterr().out)
    assert old_render["source_adoption"]["stale"] is True
    count = len(requests(tmp_path))
    with pytest.raises(SystemExit):
        main(["resume", str(workspace)], process_command=helper(tmp_path))
    rebound = json.loads(capsys.readouterr().out)
    candidate = rebound["source_adoption"]
    assert candidate["candidate_sha256"] != first["candidate_sha256"]
    assert candidate["H"]["sha256"] == head
    assert candidate["C"] == first["C"]
    assert candidate["stale"] is False
    assert len(requests(tmp_path)) == count
    assert pointer(workspace, "source") == head
    main(["status", "--json", str(workspace)])
    assert json.loads(capsys.readouterr().out)["source_adoption"] == candidate
    main(
        [
            "revise",
            str(workspace),
            "--source",
            str(workspace / candidate["envelope"]),
            "--reason",
            "Rebound candidate accepted",
        ]
    )
    capsys.readouterr()
    main(["resume", str(workspace)], process_command=helper(tmp_path))
    assert json.loads(capsys.readouterr().out)["processing"]["status"] == "completed"


def test_candidate_instructions_are_executable_from_outside_workspace(
    tmp_path, pdf_input, local_models, capsys
):
    import shlex

    workspace, candidate = pending_pdf_candidate(
        tmp_path, pdf_input, local_models, capsys
    )
    command = shlex.split(candidate["next_operation"].split(" && ")[0])
    assert command[:2] == ["doc-translate", "revise"]
    assert command[command.index("--source") + 1] == str(
        workspace / candidate["envelope"]
    )


def test_obsolete_candidate_does_not_wait_when_current_machine_content_returns_to_base(
    tmp_path, pdf_input, local_models, capsys
):
    from test_resume import requests
    from test_revise import pointer

    workspace = tmp_path / "new"
    main(
        run_args(pdf_input, workspace)
        + [
            "--pages",
            "2-2",
            "--artifacts-path",
            str(local_models),
            "--correction-model",
            "vision",
        ],
        process_command=helper(tmp_path),
    )
    capsys.readouterr()
    head = revise_pdf_paragraph(tmp_path, workspace, capsys)
    changed = [
        {
            "block_id": "changed",
            "kind": "body",
            "text": "Changed machine paragraph.",
            "evidence": [
                {
                    "page": 2,
                    "ref": None,
                    "position": "center",
                    "reason": "Synthetic visible paragraph.",
                }
            ],
        }
    ]
    with pytest.raises(SystemExit):
        main(
            ["resume", str(workspace), "--correction-model", "vision-alt"],
            process_command=helper(tmp_path, correction_blocks=changed),
        )
    candidate = json.loads(capsys.readouterr().out)["source_adoption"]
    record = workspace / "candidates/source" / f"{candidate['candidate_sha256']}.json"
    data = record.read_bytes()
    count = len(requests(tmp_path))
    main(
        ["resume", str(workspace), "--correction-model", "vision"],
        process_command=helper(tmp_path),
    )
    summary = json.loads(capsys.readouterr().out)
    assert summary["processing"]["status"] == "completed"
    assert "source_adoption" not in summary
    assert pointer(workspace, "source") == head
    assert record.read_bytes() == data
    assert all(
        "owned_pages" not in item["context"] for item in requests(tmp_path)[count:]
    )
    main(["status", "--json", str(workspace)])
    assert "source_adoption" not in json.loads(capsys.readouterr().out)


def test_revised_pdf_all_corrections_fail_retains_adopted_source_and_outputs(
    tmp_path, pdf_input, local_models, capsys
):
    from test_resume import requests
    from test_revise import pointer

    workspace = tmp_path / "new"
    main(
        run_args(pdf_input, workspace)
        + [
            "--pages",
            "2-3",
            "--artifacts-path",
            str(local_models),
            "--correction-model",
            "vision",
        ],
        process_command=helper(tmp_path),
    )
    capsys.readouterr()
    head = revise_pdf_paragraph(tmp_path, workspace, capsys)
    pointers = {
        name: (workspace / name).read_bytes()
        for name in (
            "source-snapshot.json",
            "preparation-snapshot.json",
            "translation-snapshot.json",
            "render.json",
        )
    }
    count = len(requests(tmp_path))
    with pytest.raises(SystemExit) as failed:
        main(
            ["resume", str(workspace), "--correction-model", "vision-alt"],
            process_command=helper(tmp_path, correction_fail_page=2),
        )
    assert failed.value.code == 1
    summary = json.loads(capsys.readouterr().out)
    assert summary["source_snapshot_sha256"] == head
    assert summary["processing"]["status"] == "partial"
    assert summary["scope"]["run"]["corrected_pages"] == []
    assert summary["scope"]["run"]["failed_pages"] == [2, 3]
    assert "source_adoption" not in summary
    assert all(
        (workspace / name).read_bytes() == data for name, data in pointers.items()
    )
    assert all("owned_pages" in item["context"] for item in requests(tmp_path)[count:])
    assert pointer(workspace, "source") == head


@pytest.mark.parametrize(
    "damage",
    [
        "unknown_candidate",
        "page_outside_ownership",
        "unknown_reference",
        "bad_structure",
        "stale_generation",
    ],
)
def test_candidate_adoption_rejects_invalid_input_before_source_switch(
    tmp_path, pdf_input, local_models, capsys, monkeypatch, damage
):
    from test_resume import requests
    from test_revise import pointer

    workspace, candidate = pending_pdf_candidate(
        tmp_path, pdf_input, local_models, capsys
    )
    envelope = json.loads((workspace / candidate["envelope"]).read_bytes())
    if damage == "unknown_candidate":
        envelope["candidate_sha256"] = "0" * 64
    elif damage == "page_outside_ownership":
        envelope["source"]["blocks"][0]["locations"][0]["page"] = 9
    elif damage == "unknown_reference":
        envelope["source"]["blocks"][0]["structure"]["target_id"] = "unknown"
    elif damage == "bad_structure":
        envelope["source"]["blocks"][0].update(
            kind="list", structure={"depth": -1, "marker": "-"}
        )
    else:
        with pytest.raises(SystemExit) as invalid_config:
            main(
                ["resume", str(workspace), "--correction-model", "unknown-model"],
                process_command=helper(tmp_path),
            )
        assert invalid_config.value.code == 2
        capsys.readouterr()
    head = pointer(workspace, "source")
    before = {
        path: path.read_bytes() for path in workspace.rglob("*") if path.is_file()
    }
    count = len(requests(tmp_path))
    path = tmp_path / "invalid-candidate.json"
    path.write_text(json.dumps(envelope))
    monkeypatch.setenv("PATH", "")
    with pytest.raises(SystemExit) as rejected:
        main(
            [
                "revise",
                str(workspace),
                "--source",
                str(path),
                "--reason",
                "Invalid candidate",
            ]
        )
    assert rejected.value.code == 2
    assert pointer(workspace, "source") == head
    assert all(path.read_bytes() == data for path, data in before.items())
    assert len(requests(tmp_path)) == count


def test_ordinary_pdf_revision_cannot_forge_machine_scope_or_candidate_provenance(
    tmp_path, pdf_input, local_models, capsys
):
    from test_revise import pointer

    workspace = tmp_path / "new"
    with pytest.raises(SystemExit):
        main(
            run_args(pdf_input, workspace)
            + [
                "--pages",
                "2-7",
                "--artifacts-path",
                str(local_models),
                "--correction-model",
                "vision",
            ],
            process_command=helper(tmp_path, correction_fail_page=7),
        )
    capsys.readouterr()
    base = pointer(workspace, "source")
    source = load_source_snapshot(workspace).model_dump(mode="json")
    source["machine_base_sha256"] = "0" * 64
    source["adopted_candidate_sha256"] = "0" * 64
    source["machine_inputs"]["corrected_pages"] = [2, 3, 4, 5, 6, 7]
    source["machine_inputs"]["failed_pages"] = []
    source["processing"]["status"] = "completed"
    source["checks"] = []
    source["uncertainties"] = []
    path = tmp_path / "source.json"
    path.write_text(
        json.dumps({"schema_version": 1, "base_source_sha256": base, "source": source})
    )
    main(
        [
            "revise",
            str(workspace),
            "--source",
            str(path),
            "--reason",
            "Original checked",
        ]
    )
    capsys.readouterr()
    adopted = load_source_snapshot(workspace)
    assert adopted.machine_base_sha256 == base
    assert adopted.adopted_candidate_sha256 is None
    assert adopted.machine_inputs is not None
    assert adopted.machine_inputs.failed_pages == [7]
    assert adopted.processing.status == "failed"
    scope = next(check for check in adopted.checks if check.name == "pdf_run_scope")
    assert json.loads(scope.details)["failed_pages"] == [7]
    assert any(item.stage == "correction" for item in adopted.uncertainties)


@pytest.mark.parametrize(
    "artifact", ["candidate", "C", "B", "H", "chunk", "missing_parent"]
)
def test_pending_pdf_corruption_is_rejected_before_settings_mutation_or_submission(
    tmp_path, pdf_input, local_models, capsys, artifact
):
    from test_resume import requests

    workspace, candidate = pending_pdf_candidate(
        tmp_path, pdf_input, local_models, capsys
    )
    if artifact == "candidate":
        path = workspace / f"candidates/source/{candidate['candidate_sha256']}.json"
    elif artifact == "chunk":
        path = (
            workspace
            / f"corrections/pdf/{candidate['new_inputs']['chunk_sha256s'][-1]}.json"
        )
    elif artifact == "missing_parent":
        data = load_source_snapshot(workspace).model_dump(mode="json")
        data["previous_snapshot_sha256"] = "0" * 64
        raw = json.dumps(data).encode()
        digest = hashlib.sha256(raw).hexdigest()
        (workspace / f"snapshots/source/{digest}.json").write_bytes(raw)
        (workspace / "source-snapshot.json").write_text(
            json.dumps({"schema_version": 1, "sha256": digest})
        )
        path = None
    else:
        path = workspace / candidate[artifact]["path"]
    if path is not None:
        path.write_bytes(b"corrupt saved artifact")
    manifest = (workspace / "run.json").read_bytes()
    pointer = (workspace / "source-snapshot.json").read_bytes()
    count = len(requests(tmp_path))
    with pytest.raises(SystemExit) as rejected:
        main(
            ["resume", str(workspace), "--correction-model", "vision-alt"],
            process_command=helper(tmp_path),
        )
    assert rejected.value.code == 2
    assert (workspace / "run.json").read_bytes() == manifest
    assert (workspace / "source-snapshot.json").read_bytes() == pointer
    assert len(requests(tmp_path)) == count


@pytest.mark.parametrize("termination", ["SIGINT", "SIGKILL"])
@pytest.mark.parametrize("edge", ["before", "after"])
def test_pdf_adoption_source_pointer_is_only_commit_despite_remaining_candidate_pointer(
    tmp_path, pdf_input, local_models, capsys, edge, termination
):
    import signal

    from test_resume import await_boundary, external_cli, requests
    from test_revise import pointer

    workspace, candidate = pending_pdf_candidate(
        tmp_path, pdf_input, local_models, capsys
    )
    head = pointer(workspace, "source")
    command = helper(tmp_path)
    count = len(requests(tmp_path))
    envelope = workspace / candidate["envelope"]
    adopt = [
        "revise",
        str(workspace),
        "--source",
        str(envelope),
        "--reason",
        "Explicit adoption",
    ]
    process = external_cli(
        tmp_path, command, adopt, boundary="source-snapshot.json", edge=edge
    )
    try:
        await_boundary(tmp_path, process)
        assert (pointer(workspace, "source") == head) == (edge == "before")
        for operation in ("resume", "render"):
            with pytest.raises(SystemExit) as locked:
                main([operation, str(workspace)], process_command=command)
            assert locked.value.code == 1
        capsys.readouterr()
        process.send_signal(getattr(signal, termination))
        assert process.wait(timeout=15) == -getattr(signal, termination)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
    assert (workspace / "source-candidate.json").exists()
    main(["status", "--json", str(workspace)])
    status = json.loads(capsys.readouterr().out)
    if edge == "before":
        assert status["source_adoption"]["status"] == "pending"
        with pytest.raises(SystemExit) as pending:
            main(["resume", str(workspace)], process_command=command)
        assert pending.value.code == 1
        capsys.readouterr()
        assert len(requests(tmp_path)) == count
        main(adopt)
        capsys.readouterr()
    else:
        assert "source_adoption" not in status
    main(["resume", str(workspace)], process_command=command)
    result = json.loads(capsys.readouterr().out)
    assert result["processing"]["status"] == "completed"
    assert "source_adoption" not in result
    assert (
        load_source_snapshot(workspace).machine_base_sha256 == candidate["C"]["sha256"]
    )
    assert all(
        "owned_pages" not in item["context"] for item in requests(tmp_path)[count:]
    )


def test_legacy_pdf_baseline_proves_chunks_with_only_exclusions_from_ownership_records(
    tmp_path, pdf_input, local_models, capsys
):
    from test_resume import requests

    workspace = tmp_path / "new"
    command = helper(
        tmp_path,
        correction_blocks=[],
        correction_exclusions=[
            {
                "text": "Synthetic blank page",
                "reason": "No translatable source.",
                "evidence": [
                    {
                        "page": 2,
                        "ref": None,
                        "position": "center",
                        "reason": "Synthetic blank page.",
                    }
                ],
            }
        ],
    )
    main(
        run_args(pdf_input, workspace)
        + [
            "--pages",
            "2-2",
            "--artifacts-path",
            str(local_models),
            "--correction-model",
            "vision",
        ],
        process_command=command,
    )
    capsys.readouterr()
    source = load_source_snapshot(workspace).model_dump(mode="json")
    assert source["blocks"] == []
    source.pop("machine_inputs")
    raw = json.dumps(source).encode()
    base = hashlib.sha256(raw).hexdigest()
    (workspace / f"snapshots/source/{base}.json").write_bytes(raw)
    source.update(
        previous_snapshot_sha256=base, change_reason="Legacy blank page checked"
    )
    raw_head = json.dumps(source).encode()
    head = hashlib.sha256(raw_head).hexdigest()
    (workspace / f"snapshots/source/{head}.json").write_bytes(raw_head)
    (workspace / "source-snapshot.json").write_text(
        json.dumps({"schema_version": 1, "sha256": head})
    )
    count = len(requests(tmp_path))
    main(["resume", str(workspace)], process_command=command)
    summary = json.loads(capsys.readouterr().out)
    assert summary["source_snapshot_sha256"] == head
    assert summary["processing"]["status"] == "completed"
    assert len(requests(tmp_path)) == count
    assert (workspace / f"snapshots/source/{base}.json").read_bytes() == raw
