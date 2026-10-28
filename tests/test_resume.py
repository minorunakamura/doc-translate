"""Resume CLI recovery through saved artifacts and the real offline helper."""

import hashlib
import json

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
    monkeypatch.setenv("PI_PROVIDER", "must-not-be-used")
    monkeypatch.setenv("PI_MODEL", "must-not-be-used")
    monkeypatch.setenv("PATH", "")
    main(["resume", str(workspace)])
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
    main(["resume", str(workspace)], process_command=helper(tmp_path))
    result = json.loads(capsys.readouterr().out)
    assert result["processing"]["status"] == "completed"
    assert result["scope"]["run"]["error"] is None
    assert len(requests(tmp_path)) - count == (3 if stage == "prepare" else 1)
    assert_preserved(old_files)


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
        ["resume", str(workspace), "--glossary", str(glossary)],
        process_command=helper(tmp_path),
    )
    assert json.loads(capsys.readouterr().out)["processing"]["status"] == "completed"
    assert (
        len(requests(tmp_path)) == 5
    )  # collection reused; organization and translation changed
    main(
        [
            "resume",
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
        main(["resume", str(workspace)], process_command=helper(tmp_path))
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
        ["resume", str(workspace), "--glossary", str(glossary)],
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
    resumed = external_cli(tmp_path, command, ["resume", str(workspace)])
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
        tmp_path, command, ["resume", str(workspace)], boundary="run.json"
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
    resumed = external_cli(tmp_path, helper(tmp_path), ["resume", str(workspace)])
    assert resumed.wait(timeout=15) == 0, (tmp_path / "process.err").read_text()
    summary = json.loads((tmp_path / "process.out").read_text())
    assert summary["processing"]["status"] == "completed"
    assert len(requests(tmp_path)) == 4  # unaccepted lost response must be sent again
    assert "unknown" in summary["usage"]["note"]
    assert "duplicate provider charges" in summary["usage"]["note"]
