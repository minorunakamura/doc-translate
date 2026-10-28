"""Readable views and the unchanged JSON/exit-code contracts."""

import json
import shlex
from pathlib import Path

import pytest
from test_epub import make_epub
from test_run import helper, run_args

from doc_translate.cli import main
from doc_translate.cli_output import catalog_text, summary_text
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


def test_readable_run_and_saved_only_status_keep_json_artifacts(
    tmp_path, capsys, monkeypatch
):
    workspace = tmp_path / "my book"
    args = run_args(make_epub(tmp_path), workspace)
    args.remove("--json")
    main(args, process_command=helper(tmp_path))
    captured = capsys.readouterr()
    assert "出力まで完了" in captured.out
    assert "未承認" in captured.out
    assert "EPUB全体" in captured.out
    # Transmission announcements remain JSON on stderr, not part of stdout.
    assert all(
        json.loads(line)["stage"] in {"prepare", "translation"}
        for line in captured.err.splitlines()
    )
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
    assert "最新" in text and "一致" in text and "有効" in text
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
