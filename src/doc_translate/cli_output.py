"""Human-readable views of validated CLI results; saved data stays unchanged."""

import shlex
import unicodedata
from pathlib import Path


def _table(rows: list[list[str]]) -> str:
    # Escape control characters from filenames/catalogs instead of running terminal codes.
    rows = [
        [
            "".join(
                c if not unicodedata.category(c).startswith("C") else ascii(c)[1:-1]
                for c in cell
            )
            for cell in row
        ]
        for row in rows
    ]

    def width(value):
        return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in value)

    widths = [max(map(width, column)) for column in zip(*rows)]
    return "\n".join(
        "  ".join(
            cell + " " * (size - width(cell)) for cell, size in zip(row, widths)
        ).rstrip()
        for row in rows
    )


def catalog_text(operation: str, result: dict) -> str:
    if operation == "providers":
        rows = [["PROVIDER ID", "NAME", "LOGIN METHODS"]]
        rows += [
            [p["id"], p["name"], ", ".join(p["login_methods"]) or "環境認証のみ"]
            for p in result["providers"]
        ]
        return (
            _table(rows)
            + "\n\n認証例: doc-translate login --provider openai --method oauth"
        )
    rows = [["MODEL ID", "NAME", "IMAGE INPUT", "THINKING LEVELS"]]
    rows += [
        [
            m["id"],
            m["name"],
            "yes" if m["image_input"] else "no",
            ", ".join(m["thinking_levels"]),
        ]
        for m in result["models"]
    ]
    return (
        _table([["Provider:", result["provider"]]])
        + "\n\n"
        + _table(rows)
        + ("\n\n該当するmodelはありません。" if not result["models"] else "")
        + "\n\n翻訳にはMODEL IDを指定してください。\n※ 一覧への掲載は、accountでの利用権を保証しません。"
    )


def _pages(pages: list[int]) -> str:
    ranges = []
    for page in pages:
        if ranges and page == ranges[-1][1] + 1:
            ranges[-1][1] = page
        else:
            ranges.append([page, page])
    return (
        ", ".join(str(a) if a == b else f"{a}–{b}" for a, b in ranges) + "ページ"
        if ranges
        else "なし"
    )


def summary_text(operation: str, result: dict, workspace: Path) -> str:
    processing = result["processing"]
    scope = result["scope"]
    run = scope.get("run", {})
    rows = [["Workspace", str(workspace)]]
    rows.append(
        [
            "処理結果",
            "出力まで完了"
            if processing["status"] == "completed"
            else "部分出力／処理未完了",
        ]
    )
    if run:
        rows.append(["入力", run["source_name"]])
    if scope["format"] == "epub":
        rows.append(["対象範囲", "EPUB全体"])
    elif run:
        rows += [
            [
                "対象範囲",
                _pages(run["owned_pages"])
                if run["owned_pages"] is not None
                else "PDF全ページ（ページ数未確定）",
            ],
            ["参照専用", _pages(run["reference_pages"])],
            ["PDF補正 成功", _pages(run["corrected_pages"])],
            ["PDF補正 失敗", _pages(run["failed_pages"])],
            ["PDF補正 未処理", _pages(run["not_processed_pages"])],
        ]
        rows.append(
            [
                "範囲の注意",
                "指定された所有範囲の処理結果です。PDF全体の品質確認ではありません。",
            ]
        )
    if operation == "status":
        rows += [
            [
                "出力全体の更新状態",
                "最新" if result["render_current"] else "未出力／更新が必要",
            ],
            [
                "採用原文・訳文との対応",
                "一致" if result.get("adopted_output_current") else "未出力／不一致",
            ],
            [
                "訳文の入力対応",
                "古い（現在の入力に未対応）"
                if result.get("stale_translation")
                else "有効"
                if result.get("translation_snapshot_sha256")
                else "訳文なし",
            ],
        ]
    for key, label in (
        ("output_reached", "翻訳 出力到達"),
        ("failed", "翻訳 失敗"),
        ("not_processed", "翻訳 未処理"),
        ("render_failed", "出力失敗"),
    ):
        if key in processing:
            rows.append([label, f"{len(processing[key])} unit"])
    rows += [
        [
            "要確認",
            f"{result['uncertainty_count']}件"
            if "uncertainty_count" in result
            else "未集計",
        ],
        ["内容品質", "未承認（出力完了と品質承認は別です）"],
    ]
    if run.get("error"):
        rows.append(["処理エラー", run["error"]])
    if run.get("error_code"):
        rows.append(["エラーコード", run["error_code"]])
    adoption = result.get("source_adoption")
    if adoption:
        rows.append(
            [
                "原文候補",
                "候補が古いため再確認が必要"
                if adoption["stale"]
                else "採用待ち（原本・現在版・候補を照合し、明示採用が必要）",
            ]
        )
    sections = [_table(rows)]
    labels = {
        "japanese": "日本語",
        "english": "英語",
        "source_map": "対応表",
        "qa": "QA",
        "usage": "利用記録",
        "summary": "Summary",
        "original": "原本",
        "run": "処理記録",
        "pdf_draft": "PDF下書き",
    }
    for key, title in (
        ("artifacts", "成果物"),
        ("previous_artifacts", "旧成果物（現在の状態とは限りません）"),
    ):
        if result.get(key):
            sections.append(
                title
                + "\n"
                + _table(
                    [
                        [labels[name], str(workspace / path)]
                        for name, path in result[key].items()
                    ]
                )
            )
    command = None
    if adoption:
        sections.append(
            "採用前に詳細を確認: "
            + shlex.join(["doc-translate", "status", str(workspace), "--json"])
        )
        if adoption["stale"]:
            command = shlex.join(["doc-translate", "resume", str(workspace)])
        else:
            sections.append(
                "以下は操作例です。候補の雛形を作業用にコピーして照合・編集し、--sourceをコピー先へ、TEXTを実際の判断理由へ置換してください。"
            )
            command = adoption["next_operation"]
    elif processing["status"] == "partial" and run:
        command = shlex.join(["doc-translate", "resume", str(workspace)])
    elif not result["render_current"]:
        command = shlex.join(["doc-translate", "render", str(workspace)])
    if command:
        sections.append(_table([["次の操作", command]]))
    return "\n\n".join(sections)
