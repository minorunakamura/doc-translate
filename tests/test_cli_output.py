"""Readable views and the unchanged JSON/exit-code contracts."""

import json
import shlex
from pathlib import Path

import pytest
from test_epub import make_epub
from test_run import helper, run_args

from doc_translate.cli import main
from doc_translate.cli_output import catalog_text, saved_status_text, summary_text
from doc_translate.render import workspace_status


@pytest.fixture
def summary():
    return {
        "processing": {"status": "partial", "failed": ["unit-2"], "not_processed": []},
        "scope": {
            "format": "pdf",
            "run": {
                "source_name": "book.pdf",
                "owned_pages": [2, 3, 4, 5, 6, 7],
                "reference_pages": [8, 9, 12],
                "corrected_pages": [2, 3, 4, 5, 6],
                "failed_pages": [7],
                "not_processed_pages": [],
                "error": "Correction failed; inspect attempts",
                "error_code": None,
            },
        },
        "uncertainty_count": 3,
        "render_current": False,
        "adopted_output_current": False,
        "stale_translation": True,
        "translation_snapshot_sha256": "saved-translation",
        "artifacts": {},
        "previous_artifacts": {"japanese": "renders/old/japanese.md"},
    }


def test_partial_status_distinguishes_scope_stale_translation_and_old_output(summary):
    output = summary_text("status", summary, Path("work/my book"))
    assert "部分出力／処理未完了" in output
    for value in (
        "2–7ページ",
        "8–9, 12ページ",
        "2–6ページ",
        "7ページ",
        "1 unit",
        "3件",
    ):
        assert value in output
    assert "PDF全体の品質確認ではありません" in output
    assert "古い（現在の入力に未対応）" in output
    assert "旧成果物（現在の状態とは限りません）" in output
    assert "work/my book/renders/old/japanese.md" in output
    command = next(line for line in output.splitlines() if line.startswith("次の操作"))
    assert shlex.split(command)[1:] == ["doc-translate", "resume", "work/my book"]
    assert "出力まで完了" not in output
    assert "未承認" in output


@pytest.mark.parametrize("stale", [False, True])
def test_pending_candidate_requires_inspection_and_never_just_rerender(summary, stale):
    summary["source_adoption"] = {
        "stale": stale,
        "next_operation": "doc-translate revise work/book --source envelope.json --reason TEXT",
    }
    output = summary_text("status", summary, Path("work/book"))
    assert "doc-translate status work/book --json" in output
    if stale:
        assert "候補が古い" in output
        assert "doc-translate resume work/book" in output
        assert "--reason TEXT" not in output
    else:
        assert "採用待ち" in output
        assert "作業用にコピー" in output
        assert "TEXTを実際の判断理由へ置換" in output
    assert "doc-translate render" not in output


def test_failure_before_source_has_no_false_zero_concerns_or_valid_translation(summary):
    summary.pop("uncertainty_count")
    summary.pop("translation_snapshot_sha256")
    summary.pop("stale_translation")
    summary["scope"]["run"]["owned_pages"] = None
    summary["artifacts"] = {
        "original": "originals/book.pdf",
        "pdf_draft": "pdf-draft.json",
    }
    output = summary_text("status", summary, Path("work/book"))
    for value in (
        "未集計",
        "訳文なし",
        "ページ数未確定",
        "work/book/originals/book.pdf",
    ):
        assert value in output
    assert "0件" not in output


@pytest.mark.parametrize("json_output", [False, True])
def test_preparation_evidence_failure_reports_safe_reason_and_stops(
    tmp_path, capsys, json_output
):
    source = make_epub(
        tmp_path,
        extra={
            "OPS/text/ch.xhtml": b'<html xmlns="http://www.w3.org/1999/xhtml"><body><p>original</p></body></html>'
        },
    )
    workspace = tmp_path / "new"
    args = run_args(source, workspace)
    if not json_output:
        args.remove("--json")
    with pytest.raises(SystemExit) as error:
        main(args, process_command=helper(tmp_path, candidate=True))
    assert error.value.code == 1
    captured = capsys.readouterr()
    saved = workspace_status(workspace)
    assert saved["scope"]["run"]["error_code"] == "prepare_quote_mismatch"
    assert saved["processing"]["status"] == "partial"
    assert len((tmp_path / "requests.jsonl").read_text().splitlines()) == 3
    if json_output:
        assert (
            json.loads(captured.out)["scope"]["run"]["error_code"]
            == "prepare_quote_mismatch"
        )
    else:
        assert "prepare_quote_mismatch" in captured.out
        assert "引用が原文と一致しません" in captured.out
    assert all(
        json.loads(line)["stage"] == "prepare" for line in captured.err.splitlines()
    )


def test_empty_catalog_and_ambient_only_provider_are_explicit():
    assert "該当するmodelはありません" in catalog_text(
        "models", {"provider": "empty", "models": []}
    )
    output = catalog_text(
        "providers",
        {"providers": [{"id": "ambient", "name": "Ambient", "login_methods": []}]},
    )
    assert "環境認証のみ" in output


def test_terminal_controls_in_catalog_are_escaped_without_losing_unicode():
    output = catalog_text(
        "providers",
        {
            "providers": [
                {"id": "test", "name": "日本語\n\x1b[2J", "login_methods": ["api_key"]}
            ]
        },
    )
    assert "日本語" in output
    assert "\\n\\x1b[2J" in output
    assert "\x1b" not in output


def test_saved_status_escapes_workspace_controls_including_both_command_hints():
    output = saved_status_text(
        {
            "format": "epub",
            "run": None,
            "translation": None,
            "render": None,
            "pointers": {},
            "record_mtimes": {},
            "candidate": None,
        },
        Path("work/日本語\x1b[2J\r\t\x07"),
    )
    assert (
        not any(control in output for control in "\x1b\r\t\x07")
        and output.count(r"日本語\x1b[2J\r\t\x07") == 3
        and "--verify" in output
        and "--json" in output
    )


def test_readable_run_and_saved_only_status_keep_json_artifacts(
    tmp_path, capsys, monkeypatch
):
    workspace = tmp_path / "my book"
    args = run_args(make_epub(tmp_path), workspace)
    args.remove("--json")
    args.remove("--events-json")
    main(args, process_command=helper(tmp_path))
    captured = capsys.readouterr()
    assert "出力まで完了" in captured.out
    assert "未承認" in captured.out
    assert "EPUB全体" in captured.out
    assert "本文・構造抽出" in captured.err
    assert "collect" in captured.err and "organize" in captured.err
    assert "AI応答待ち" in captured.err
    assert "PDF" not in captured.err and "ページ" not in captured.err
    assert "Original" not in captured.err
    assert "\x1b" not in captured.err
    saved = workspace_status(workspace)
    assert str(workspace / saved["artifacts"]["japanese"]) in captured.out
    assert (
        json.loads((workspace / saved["artifacts"]["summary"]).read_text())[
            "processing"
        ]["status"]
        == "completed"
    )
    monkeypatch.setenv("PATH", "")
    main(["status", str(workspace)])
    text = capsys.readouterr().out
    assert "保存記録のみ" in text and "成果物未検証" in text
    assert "保存訳文 成功" in text and "同じ版" in text
    assert "最新" not in text and "有効" not in text
    main(["status", str(workspace), "--verify"])
    verified = capsys.readouterr().out
    assert "最新" in verified and "一致" in verified and "有効" in verified
    main(["status", str(workspace), "--verify", "--json"])
    assert json.loads(capsys.readouterr().out) == saved
    main(["status", str(workspace), "--json"])
    assert json.loads(capsys.readouterr().out) == saved


@pytest.mark.parametrize("json_output", [False, True])
def test_config_failure_keeps_exit_two_in_both_formats(tmp_path, capsys, json_output):
    workspace = tmp_path / "new"
    args = run_args(make_epub(tmp_path), workspace)
    if not json_output:
        args.remove("--json")
    with pytest.raises(SystemExit) as error:
        main(
            args + ["--translation-model", "not-a-model"],
            process_command=helper(tmp_path),
        )
    assert error.value.code == 2
    output = capsys.readouterr().out
    if json_output:
        assert json.loads(output)["scope"]["run"]["error_code"] == "unknown_model"
    else:
        assert "unknown_model" in output
        assert "部分出力／処理未完了" in output


@pytest.mark.parametrize("json_output", [False, True])
@pytest.mark.parametrize("events_json", [False, True])
def test_run_resume_output_flags_are_independent(
    tmp_path, capsys, monkeypatch, json_output, events_json
):
    workspace = tmp_path / "flags"
    args = run_args(make_epub(tmp_path), workspace)
    if not json_output:
        args.remove("--json")
    if not events_json:
        args.remove("--events-json")
    main(args, process_command=helper(tmp_path))
    captured = capsys.readouterr()
    if json_output:
        assert json.loads(captured.out)["processing"]["status"] == "completed"
    else:
        assert "出力まで完了" in captured.out
    if events_json:
        assert len([json.loads(line) for line in captured.err.splitlines()]) == 3
    else:
        assert "collect" in captured.err and "organize" in captured.err
        assert (
            captured.err.index("本文・構造抽出")
            < captured.err.index("保存データ検証")
            < captured.err.index("翻訳準備")
            < captured.err.index("Markdown出力")
        )
    attempts = {p: p.read_bytes() for p in (workspace / "attempts").rglob("*.json")}
    monkeypatch.setenv("PATH", "")
    main(
        [
            "resume",
            str(workspace),
            *(["--json"] if json_output else []),
            *(["--events-json"] if events_json else []),
        ]
    )
    captured = capsys.readouterr()
    if json_output:
        assert json.loads(captured.out)["processing"]["status"] == "completed"
    else:
        assert "出力まで完了" in captured.out and "未承認" in captured.out
    if events_json:
        assert captured.err == ""
    else:
        assert captured.err.index("保存データ検証") < captured.err.index(
            "本文・構造抽出"
        )
        assert (
            "再利用" in captured.err and "成功 1/1 unit（うち再利用 1）" in captured.err
        )
    assert {
        p: p.read_bytes() for p in (workspace / "attempts").rglob("*.json")
    } == attempts


def test_progress_state_keeps_other_split_pending_until_its_adoption():
    from doc_translate.progress import ProgressEvent, ProgressState

    state = ProgressState()
    state.observe(ProgressEvent("translation", "split", job_id="one"))
    state.observe(ProgressEvent("translation", "split", job_id="two"))
    state.observe(
        ProgressEvent("translation", "adopted", job_id="one", total=4, succeeded=2)
    )
    snapshot = state.snapshot()
    assert snapshot[3] and snapshot[1]["translation"].total_updating
    assert snapshot[-1][-1].total_updating
    state.observe(
        ProgressEvent("translation", "adopted", job_id="two", total=6, succeeded=6)
    )
    assert not state.snapshot()[3]


def test_rich_display_failure_falls_back_without_masking_domain_error(
    monkeypatch, capsys
):
    from rich.console import Console
    from rich.live import Live

    from doc_translate.progress import ProgressEvent
    from doc_translate.progress_display import ProgressDisplay

    monkeypatch.setenv("NO_COLOR", "")
    display = ProgressDisplay()
    display.console = Console(stderr=True, force_terminal=True, no_color=True)

    def broken_update(*_, **__):
        raise RuntimeError("ARTIFICIAL_PRIVATE_RENDER_ERROR")

    monkeypatch.setattr(Live, "update", broken_update)
    with pytest.raises(ValueError, match="domain failure"), display as observe:
        observe(
            ProgressEvent(
                "prepare",
                "repair",
                unit_ids=("[red]literal[/red]\x1b[2J",),
                reason="prepare_quote_mismatch",
            )
        )
        raise ValueError("domain failure")
    output = capsys.readouterr().err
    assert "ARTIFICIAL_PRIVATE_RENDER_ERROR" not in output
    assert "execution_failed" in output
    assert display.thread is not None and not display.thread.is_alive()


def test_events_json_write_failure_does_not_send_or_claim_success(tmp_path, capsys):
    from contextlib import redirect_stderr
    from io import StringIO

    class BrokenNotice(StringIO):
        def write(self, _value):
            raise OSError("ARTIFICIAL_NOTICE_FAILURE")

    workspace = tmp_path / "notice-failure"
    with redirect_stderr(BrokenNotice()), pytest.raises(SystemExit) as error:
        main(run_args(make_epub(tmp_path), workspace), process_command=helper(tmp_path))
    assert error.value.code == 1
    assert not (tmp_path / "requests.jsonl").exists()
    assert json.loads(capsys.readouterr().out)["processing"]["status"] == "partial"
    attempt = json.loads(
        next((workspace / "attempts/preparation").glob("*.json")).read_text()
    )
    assert attempt["status"] == "rejected" and attempt["attempts"][0]["final"] is None


def test_progress_metadata_controls_are_escaped_and_markup_is_literal(capsys):
    from doc_translate.progress import ProgressEvent
    from doc_translate.progress_display import ProgressDisplay

    with ProgressDisplay() as observe:
        observe(
            ProgressEvent(
                "extract",
                "document",
                source_name="[red]日本語[/red]\x1b[2J\n",
                document_sha256="a" * 64,
                format="epub",
            )
        )
        observe(
            ProgressEvent(
                "translation", "waiting", request_id="one", unit_ids=("unit-1\r\x07",)
            )
        )
    output = capsys.readouterr().err
    assert "[red]日本語[/red]" in output
    assert r"\x1b[2J\n" in output and r"unit-1\r\x07" in output
    assert "\x1b" not in output and "\r" not in output and "\x07" not in output


def test_progress_stage_is_visible_before_main_thread_enters_heavy_work(
    capsys, monkeypatch
):
    import threading

    from doc_translate.progress import ProgressEvent
    from doc_translate.progress_display import ProgressDisplay

    display = ProgressDisplay()
    refresh = display._refresh
    render_threads = []

    def track_refresh():
        render_threads.append(threading.current_thread())
        refresh()

    monkeypatch.setattr(display, "_refresh", track_refresh)
    with display as observe:
        observe(ProgressEvent("validate", "start"))
        assert "保存データ検証" in capsys.readouterr().err
        worker = threading.Thread(
            target=lambda: observe(ProgressEvent("translation", "start"))
        )
        worker.start()
        worker.join()
        assert worker not in render_threads
    assert threading.main_thread() in render_threads


def test_pdf_progress_split_keeps_owned_denominator_and_translation_state_separate(
    capsys,
):
    from doc_translate.progress import ProgressEvent, ProgressState
    from doc_translate.progress_display import ProgressDisplay

    state = ProgressState()
    state.observe(ProgressEvent("translation", "split", job_id="translate"))
    state.observe(
        ProgressEvent("correction", "split", reason="length", owned_pages=(2, 3))
    )
    state.observe(
        ProgressEvent("correction", "adopted", total=6, succeeded=2, reused=1)
    )
    snapshot = state.snapshot()
    assert snapshot[3] and not snapshot[1]["correction"].total_updating
    assert state.splitting == {"translate"}
    state.observe(
        ProgressEvent(
            "correction",
            "failed",
            reason="correction_rejected",
            owned_pages=(4,),
            reference_pages=(3,),
        )
    )
    failed = state.snapshot()[1]["correction"]
    assert failed.total == 6 and failed.succeeded == 2
    assert failed.owned_pages == (4,) and failed.reference_pages == (3,)
    with ProgressDisplay() as observe:
        observe(
            ProgressEvent(
                "validate",
                "document",
                format="pdf",
                source_name="self.pdf",
                owned_pages=(2, 3, 4),
                reference_pages=(6,),
            )
        )
        observe(
            ProgressEvent(
                "correction",
                "before_send",
                request_id="one",
                owned_pages=(2, 3),
                reference_pages=(4,),
            )
        )
        observe(ProgressEvent("correction", "adopted", total=3, succeeded=2, reused=1))
        observe(ProgressEvent("correction", "split", reason="length", owned_pages=(4,)))
        observe(
            ProgressEvent("validate", "partial", reason="candidate_adoption_required")
        )
    output = capsys.readouterr().err
    assert "所有意図 2–4ページ" in output and "追加参照許可 6ページ" in output
    assert "所有 2–3ページ／参照専用 4ページ" in output
    assert "成功 2/3 ページ（うち再利用 1）" in output
    assert "総数更新中" not in output
    assert "候補の明示採用待ち（後段は未実行）" in output
