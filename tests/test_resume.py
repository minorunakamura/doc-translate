"""Resume CLI recovery through saved artifacts and the real offline helper."""

import hashlib
import json
from pathlib import Path
from zipfile import ZipFile, ZipInfo

import pytest
from test_epub import make_epub
from test_run import helper, run_args

from doc_translate.cli import main
from doc_translate.run import RunManifest


def saved_files(workspace):
    return {
        path: hashlib.sha256(path.read_bytes()).hexdigest()
        for directory in ("originals", "snapshots", "renders", "corrections")
        for path in (workspace / directory).rglob("*")
        if path.is_file()
    }


def assert_preserved(files):
    assert all(
        hashlib.sha256(path.read_bytes()).hexdigest() == digest
        for path, digest in files.items()
    )


FIXTURES = Path(__file__).parent / "fixtures"
SPLIT_UNITS_GUIDANCE = (
    "保存済みの訳と現在の文の分け方が合いません。"
    "新しいworkspaceで `run` し直してください。"
)
OLD_WORKSPACE_GUIDANCE = (
    "このworkspaceは古い形式で保存されています。"
    "新しいworkspaceで `run` し直してください。"
)


def nested_list_epub(path):
    """Byte-stable artificial EPUB: item text continues after a child list."""
    members = {
        "mimetype": b"application/epub+zip",
        "META-INF/container.xml": b'<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0"><rootfiles><rootfile full-path="OPS/book.opf" media-type="application/oebps-package+xml"/></rootfiles></container>',
        "OPS/book.opf": b'<package xmlns="http://www.idpf.org/2007/opf" version="3.0"><metadata/><manifest><item id="ch" href="text/ch.xhtml" media-type="application/xhtml+xml"/></manifest><spine><itemref idref="ch"/></spine></package>',
        "OPS/text/ch.xhtml": b'<html xmlns="http://www.w3.org/1999/xhtml"><head/><body><ol start="3"><li id="p">Before <em>x</em><ul><li>Child</li></ul>After</li></ol></body></html>',
    }
    with ZipFile(path, "w") as archive:
        for name, data in members.items():
            archive.writestr(ZipInfo(name, (2020, 1, 1, 0, 0, 0)), data)
    return path


def requests(tmp_path):
    path = tmp_path / "requests.jsonl"
    return (
        [
            json.loads(json.loads(line)["messages"][1]["content"][0]["text"])
            for line in path.read_text().splitlines()
        ]
        if path.exists()
        else []
    )


def test_completed_resume_uses_saved_settings_and_original_without_runtime(
    tmp_path, capsys, monkeypatch
):
    source = make_epub(tmp_path)
    workspace = tmp_path / "run"
    main(run_args(source, workspace), process_command=helper(tmp_path))
    first = json.loads(capsys.readouterr().out)
    old_files = saved_files(workspace)
    source.unlink()
    monkeypatch.setenv("DOC_TRANSLATE_PROVIDER", "must-not-be-used")
    monkeypatch.setenv("DOC_TRANSLATE_MODEL", "must-not-be-used")
    monkeypatch.setenv("PATH", "")
    main(["resume", "--json", str(workspace)])
    resumed = json.loads(capsys.readouterr().out)
    assert resumed["processing"]["status"] == "completed"
    assert (
        resumed["scope"]["run"]["translation"] == first["scope"]["run"]["translation"]
    )
    assert len(requests(tmp_path)) == 3
    assert_preserved(old_files)


@pytest.mark.parametrize("stage", ["prepare", "translation"])
def test_api_failure_resume_retries_only_unaccepted_requests(tmp_path, capsys, stage):
    source = make_epub(tmp_path)
    workspace = tmp_path / "run"
    with pytest.raises(SystemExit) as stopped:
        main(
            run_args(source, workspace),
            process_command=helper(tmp_path, fail_stage=stage),
        )
    assert stopped.value.code == 1
    failed = json.loads(capsys.readouterr().out)
    assert "unknown" in failed["usage"]["note"]
    assert "duplicate" in failed["usage"]["note"]
    old_files = saved_files(workspace)
    count = len(requests(tmp_path))
    main(["resume", "--json", str(workspace)], process_command=helper(tmp_path))
    result = json.loads(capsys.readouterr().out)
    assert result["processing"]["status"] == "completed"
    assert result["scope"]["run"]["error"] is None
    assert len(requests(tmp_path)) - count == (3 if stage == "prepare" else 1)
    assert_preserved(old_files)


def test_resume_repairs_only_failed_marker_chunk_and_reuses_accepted_output(
    tmp_path, capsys
):
    from doc_translate.translation import load_translation

    source = make_epub(
        tmp_path,
        extra={
            "OPS/text/ch.xhtml": b'<html xmlns="http://www.w3.org/1999/xhtml"><body><h1>Good</h1><p>Original</p><h1>Protected</h1><p>Use <code>ARTIFICIAL_PRIVATE_CODE</code>.</p></body></html>'
        },
    )
    workspace = tmp_path / "run"
    with pytest.raises(SystemExit) as error:
        main(
            run_args(source, workspace),
            process_command=helper(tmp_path, marker_failure="always"),
        )
    assert error.value.code == 1
    capsys.readouterr()
    partial = load_translation(workspace)
    assert len(partial.chunks) == 2
    accepted = next(c for c in partial.chunks if c.answer is not None)
    failed = next(c for c in partial.chunks if c.answer is None)
    accepted_path = (
        workspace / "translations/chunks" / f"{accepted.artifact_sha256}.json"
    )
    accepted_bytes = accepted_path.read_bytes()
    before = len(requests(tmp_path))
    main(
        ["resume", "--json", str(workspace)],
        process_command=helper(tmp_path, marker_failure="once"),
    )
    result = json.loads(capsys.readouterr().out)
    assert result["processing"]["status"] == "completed"
    completed = load_translation(workspace)
    assert completed.processing.status == "completed"
    assert any(c.artifact_sha256 == accepted.artifact_sha256 for c in completed.chunks)
    assert accepted_path.read_bytes() == accepted_bytes
    new_requests = requests(tmp_path)[before:]
    assert len(new_requests) == 2
    assert all(
        [u["unit_id"] for u in json.loads(r["text"])]
        == [f"request-unit-{i}" for i in range(1, len(failed.units) + 1)]
        and [u["source_text"] for u in json.loads(r["text"])]
        == [u.source_text for u in failed.units]
        for r in new_requests
    )
    assert "translation_feedback" in new_requests[1]["context"]
    before = len(requests(tmp_path))
    main(["resume", "--json", str(workspace)], process_command=helper(tmp_path))
    capsys.readouterr()
    assert len(requests(tmp_path)) == before
    assert "ARTIFICIAL_PRIVATE_CODE" not in (tmp_path / "requests.jsonl").read_text()


def test_glossary_and_explicit_config_change_only_affected_requests(tmp_path, capsys):
    source = make_epub(tmp_path)
    workspace = tmp_path / "run"
    main(run_args(source, workspace), process_command=helper(tmp_path))
    capsys.readouterr()
    old_files = saved_files(workspace)
    glossary = tmp_path / "glossary.json"
    glossary.write_text(
        json.dumps(
            {
                "version": "2",
                "book": "ignored",
                "style": "ignored",
                "terms": {"Original": "指定"},
            }
        )
    )
    main(
        ["resume", "--json", str(workspace), "--glossary", str(glossary)],
        process_command=helper(tmp_path),
    )
    assert json.loads(capsys.readouterr().out)["processing"]["status"] == "completed"
    assert (
        len(requests(tmp_path)) == 5
    )  # collection reused; organization and translation changed
    main(
        [
            "resume",
            "--json",
            str(workspace),
            "--model",
            "not-a-model",
            "--translation-model",
            "vision",
        ],
        process_command=helper(tmp_path),
    )
    result = json.loads(capsys.readouterr().out)
    assert result["processing"]["status"] == "completed"
    assert result["scope"]["run"]["translation"]["model"] == "vision"
    assert len(requests(tmp_path)) == 8
    assert_preserved(old_files)


@pytest.mark.parametrize(
    "artifact",
    ["original", "source", "preparation", "translation", "attempt", "render"],
)
def test_resume_refuses_corrupt_saved_artifact_before_submission(
    tmp_path, capsys, artifact
):
    source = make_epub(tmp_path)
    workspace = tmp_path / "run"
    main(run_args(source, workspace), process_command=helper(tmp_path))
    result = json.loads(capsys.readouterr().out)
    path = {
        "original": next((workspace / "originals").glob("*")),
        "source": next((workspace / "snapshots/source").glob("*")),
        "preparation": next((workspace / "snapshots/preparation").glob("*")),
        "translation": next((workspace / "translations/chunks").glob("*")),
        "attempt": next((workspace / "attempts/preparation").glob("*")),
        "render": workspace / result["artifacts"]["japanese"],
    }[artifact]
    path.write_bytes(b"corrupt")
    with pytest.raises(SystemExit) as stopped:
        main(["resume", "--json", str(workspace)], process_command=helper(tmp_path))
    assert stopped.value.code == 2
    assert len(requests(tmp_path)) == 3
    assert path.read_bytes() == b"corrupt"


def external_cli(
    tmp_path, command, arguments, *, boundary=None, edge="before", occurrence=1
):
    import os
    import subprocess
    import sys
    from pathlib import Path

    marker = tmp_path / "boundary.marker"
    argv = [
        sys.executable,
        str(Path(__file__).parent / "fixtures/resume_process.py"),
        "--helper",
        json.dumps(command),
    ]
    if boundary:
        marker.unlink(missing_ok=True)
        marker.with_suffix(".release").unlink(missing_ok=True)
        argv += [
            "--boundary",
            boundary,
            "--edge",
            edge,
            "--marker",
            str(marker),
            "--occurrence",
            str(occurrence),
        ]
    out = (tmp_path / "process.out").open("w")
    err = (tmp_path / "process.err").open("w")
    try:
        return subprocess.Popen(
            [*argv, "--", *arguments],
            stdout=out,
            stderr=err,
            env={**os.environ, "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"},
        )
    finally:
        out.close()
        err.close()


def await_boundary(tmp_path, process):
    import time

    deadline = time.monotonic() + 15
    while not (tmp_path / "boundary.marker").exists():
        assert process.poll() is None, (tmp_path / "process.err").read_text()
        assert time.monotonic() < deadline, "CLI did not reach OS replace boundary"
        time.sleep(0.01)


@pytest.mark.parametrize(
    "boundary",
    ["source-snapshot.json", "preparation-snapshot.json", "translation-snapshot.json"],
)
@pytest.mark.parametrize("edge", ["before", "after"])
@pytest.mark.parametrize("termination", ["SIGINT", "SIGKILL"])
def test_real_cli_signal_at_atomic_boundary_reuses_accepted_stages(
    tmp_path, capsys, boundary, edge, termination
):
    import signal

    source = make_epub(tmp_path)
    workspace = tmp_path / "run"
    command = helper(tmp_path)
    main(run_args(source, workspace), process_command=command)
    capsys.readouterr()
    old_files = saved_files(workspace)
    glossary = tmp_path / "changed.json"
    glossary.write_text(
        json.dumps(
            {
                "version": "2",
                "book": "ignored",
                "style": "ignored",
                "terms": {"Original": "改版"},
            }
        )
    )
    process = external_cli(
        tmp_path,
        command,
        ["resume", "--json", str(workspace), "--glossary", str(glossary)],
        boundary=boundary,
        edge=edge,
    )
    try:
        await_boundary(tmp_path, process)
        main(["status", "--json", str(workspace)])
        assert json.loads(capsys.readouterr().out)["processing"]["status"] == "partial"
        process.send_signal(getattr(signal, termination))
        (tmp_path / "boundary.release").touch()
        code = process.wait(timeout=15)
        assert code in (0, 1) if termination == "SIGINT" else code == -signal.SIGKILL
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
    assert_preserved(old_files)
    resumed = external_cli(tmp_path, command, ["resume", "--json", str(workspace)])
    assert resumed.wait(timeout=15) == 0, (tmp_path / "process.err").read_text()
    result = json.loads((tmp_path / "process.out").read_text())
    assert result["processing"]["status"] == "completed"
    assert result["scope"]["run"]["user_terms"] == {"Original": "改版"}
    assert (
        len(requests(tmp_path)) == 5
    )  # never resend accepted collection/organization/translation
    assert_preserved(old_files)
    main(["status", "--json", str(workspace)])
    assert json.loads(capsys.readouterr().out)["render_current"] is True


def test_whole_resume_lock_excludes_other_resume_render_and_stage_interstage(
    tmp_path, capsys
):
    from doc_translate.preparation import load_preparation, prepare_document
    from doc_translate.workspace import WorkspaceLockedError, workspace_lock

    source = make_epub(tmp_path)
    workspace = tmp_path / "run"
    command = helper(tmp_path)
    main(run_args(source, workspace), process_command=command)
    capsys.readouterr()
    old_files = saved_files(workspace)
    # Resume's manifest replacement happens between stages, not inside a stage lock.
    process = external_cli(
        tmp_path, command, ["resume", "--json", str(workspace)], boundary="run.json"
    )
    try:
        await_boundary(tmp_path, process)
        for operation in ("resume", "render"):
            with pytest.raises(SystemExit) as stopped:
                main([operation, str(workspace)], process_command=command)
            assert stopped.value.code == 1
        prep = load_preparation(workspace)
        with pytest.raises(WorkspaceLockedError):
            prepare_document(
                workspace=workspace,
                book="book",
                policy=prep.policy,
                config=RunManifest.model_validate_json(
                    (workspace / "run.json").read_bytes()
                ).translation,
                process_command=command,
            )
        (tmp_path / "boundary.release").touch()
        assert process.wait(timeout=15) == 0
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
    # Manual nesting still cannot borrow the orchestration-only privilege.
    with workspace_lock(workspace), pytest.raises(WorkspaceLockedError):
        prepare_document(
            workspace=workspace,
            book="book",
            policy=prep.policy,
            config=RunManifest.model_validate_json(
                (workspace / "run.json").read_bytes()
            ).translation,
            process_command=command,
        )
    assert len(requests(tmp_path)) == 3
    assert_preserved(old_files)


@pytest.mark.parametrize("termination", ["SIGINT", "SIGTERM", "SIGKILL"])
def test_real_cli_lost_response_is_unaccepted_and_discloses_possible_duplicate_charge(
    tmp_path, termination
):
    import signal
    import time

    source = make_epub(tmp_path)
    workspace = tmp_path / "run"
    command = helper(tmp_path, delay_phase="collect", delay_ms=500)
    process = external_cli(tmp_path, command, run_args(source, workspace))
    try:
        deadline = time.monotonic() + 15
        while not requests(tmp_path):
            assert process.poll() is None
            assert time.monotonic() < deadline
            time.sleep(0.01)
        process.send_signal(getattr(signal, termination))
        code = process.wait(timeout=15)
        assert code == 1 if termination != "SIGKILL" else code == -signal.SIGKILL
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
    assert len(requests(tmp_path)) == 1  # no next paid stage after cancellation
    resumed = external_cli(
        tmp_path, helper(tmp_path), ["resume", "--json", str(workspace)]
    )
    assert resumed.wait(timeout=15) == 0, (tmp_path / "process.err").read_text()
    summary = json.loads((tmp_path / "process.out").read_text())
    assert summary["processing"]["status"] == "completed"
    assert len(requests(tmp_path)) == 4  # unaccepted lost response must be sent again
    assert "unknown" in summary["usage"]["note"]
    assert "duplicate provider charges" in summary["usage"]["note"]


def test_resume_unexpected_validation_failure_records_cause_without_book_text(
    tmp_path, capsys, monkeypatch
):
    from pydantic import BaseModel, ValidationError
    from test_run import saved_failure_record

    import doc_translate.run
    from doc_translate.snapshot import load_source_snapshot

    class Strict(BaseModel):
        count: dict[str, int]

    def first_unexpected(**_):
        raise ValueError("artificial first failure")

    def unexpected(*, workspace, **_):
        try:
            Strict.model_validate(
                {"count": {load_source_snapshot(workspace).model_dump_json(): "x"}}
            )
        except ValidationError as error:
            raise RuntimeError("artificial wrapper") from error

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-artificial-credential")
    source = make_epub(tmp_path)
    workspace = tmp_path / "run"
    monkeypatch.setattr(doc_translate.run, "prepare_document", first_unexpected)
    with pytest.raises(SystemExit) as stopped:
        main(run_args(source, workspace), process_command=helper(tmp_path))
    assert stopped.value.code == 1
    first = json.loads(capsys.readouterr().out)["scope"]["run"]["error"]
    first_location, first_record = saved_failure_record(workspace, first)
    monkeypatch.undo()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-artificial-credential")
    monkeypatch.setattr(doc_translate.run, "translate_document", unexpected)
    with pytest.raises(SystemExit) as stopped:
        main(["resume", "--json", str(workspace)], process_command=helper(tmp_path))
    assert stopped.value.code == 1
    resumed = json.loads(capsys.readouterr().out)
    assert resumed["processing"]["status"] == "partial"
    error = resumed["scope"]["run"]["error"]
    assert error.startswith("translation failed")
    location, record = saved_failure_record(workspace, error)
    assert location != first_location
    assert (workspace / first_location).read_text() == first_record
    assert "ValueError: artificial first failure" in first_record
    assert "RuntimeError: artificial wrapper" in record
    assert "ValidationError" in record and "int_parsing" in record
    assert "direct cause" in record and record.count("Traceback") == 2
    assert "Original" not in record
    assert "sk-artificial-credential" not in record


@pytest.mark.parametrize(
    "arguments",
    [["resume", "--json"], ["render"], ["status", "--verify"]],
)
def test_saved_translation_with_old_item_split_stops_with_rerun_guidance(
    tmp_path, capsys, arguments
):
    # Saved by the pre-#103 code (8d4988b) from nested_list_epub via public run.
    # #118 removed the five null generation options and re-hashed the records.
    workspace = tmp_path / "run"
    for name, text in json.loads(
        (FIXTURES / "epub-nested-list-before-103.json").read_bytes()
    ).items():
        path = workspace / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    manifest = RunManifest.model_validate_json((workspace / "run.json").read_bytes())
    (workspace / "originals").mkdir()
    original = nested_list_epub(workspace / manifest.document.artifact)
    assert hashlib.sha256(original.read_bytes()).hexdigest() == manifest.document.sha256
    before = {p: p.read_bytes() for p in workspace.rglob("*") if p.is_file()}
    with pytest.raises(SystemExit) as stopped:
        main([*arguments, str(workspace)], process_command=helper(tmp_path))
    assert stopped.value.code == 2
    error = capsys.readouterr().err
    assert SPLIT_UNITS_GUIDANCE in error
    assert "correspondence mismatch" not in error
    assert requests(tmp_path) == []
    assert {p: p.read_bytes() for p in workspace.rglob("*") if p.is_file()} == before


def options_before_118_workspace(tmp_path):
    # Saved by the pre-#118 code (4aae54e) from nested_list_epub via public run.
    workspace = tmp_path / "run"
    for name, text in json.loads(
        (FIXTURES / "epub-options-before-118.json").read_bytes()
    ).items():
        path = workspace / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    document = json.loads((workspace / "run.json").read_bytes())["document"]
    (workspace / "originals").mkdir()
    original = nested_list_epub(workspace / document["artifact"])
    assert hashlib.sha256(original.read_bytes()).hexdigest() == document["sha256"]
    return workspace


@pytest.mark.parametrize(
    "arguments",
    [["resume", "--json"], ["render"], ["status", "--verify"]],
)
def test_saved_workspace_with_removed_generation_options_stops_with_rerun_guidance(
    tmp_path, capsys, arguments
):
    workspace = options_before_118_workspace(tmp_path)
    before = {
        p: (p.read_bytes(), p.stat().st_mtime_ns)
        for p in workspace.rglob("*")
        if p.is_file()
    }
    with pytest.raises(SystemExit) as stopped:
        main([*arguments, str(workspace)], process_command=helper(tmp_path))
    assert stopped.value.code == 2
    error = capsys.readouterr().err
    assert OLD_WORKSPACE_GUIDANCE in error
    assert "validation error" not in error and "Traceback" not in error
    assert requests(tmp_path) == []
    assert {
        p: (p.read_bytes(), p.stat().st_mtime_ns)
        for p in workspace.rglob("*")
        if p.is_file()
    } == before


def test_saved_unknown_option_is_not_reported_as_old_workspace(tmp_path, capsys):
    workspace = options_before_118_workspace(tmp_path)
    manifest = json.loads((workspace / "run.json").read_bytes())
    # Only run.json is current; an unrelated extra key is not the old format.
    manifest["translation"]["options"] = {"timeoutMs": 1_200_000, "unknownOption": None}
    (workspace / "run.json").write_text(json.dumps(manifest))
    with pytest.raises(SystemExit) as stopped:
        main(["resume", "--json", str(workspace)], process_command=helper(tmp_path))
    assert stopped.value.code == 2
    error = capsys.readouterr().err
    assert OLD_WORKSPACE_GUIDANCE not in error
    assert "unknownOption" in error
    assert requests(tmp_path) == []
