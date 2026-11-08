"""Human-readable views of validated CLI results; saved data stays unchanged."""

import shlex
import unicodedata
from datetime import UTC
from pathlib import Path


def _safe(value: str) -> str:
    return "".join(
        c if not unicodedata.category(c).startswith("C") else ascii(c)[1:-1]
        for c in value
    )


def _table(rows: list[list[str]]) -> str:
    # Escape control characters from filenames/catalogs instead of running terminal codes.
    rows = [[_safe(cell) for cell in row] for row in rows]

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
        reason = {
            "prepare_unknown_unit": "用語の根拠が未知のunitを参照しています。",
            "prepare_quote_mismatch": "用語の引用が原文と一致しません（大文字・小文字・markerを含む）。",
            "prepare_term_mismatch": "用語が根拠の引用と一致しません（大文字・小文字を含む）。",
            "prepare_ungrounded_term": "整理した用語に本文根拠または利用者指定がありません。",
        }.get(run["error_code"])
        if reason:
            rows.append(["原因", reason])
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


def review_text(
    result: dict,
    workspace: Path,
    *,
    unit_ids: list[str] | None = None,
    pages: tuple[int, int] | None = None,
) -> str:
    review = result["review"]
    accepted = review["status"] == "validated"
    rows = [
        ["Workspace", str(workspace)],
        [
            "確認記録",
            "保存済み（終了時の詳細検査済み）"
            if accepted
            else "確認失敗（確認記録は未保存）",
        ],
    ]
    if accepted:
        record = next(
            record
            for record in result["content_review"]["review_history"]
            if record["sha256"] == review["sha256"]
            and record["artifact"] == review["artifact"]
        )
        rows += [
            ["対象unit", ", ".join(record["unit_ids"]) or "なし"],
            ["対象block", ", ".join(record["block_ids"]) or "なし"],
            ["対象画像ページ", _pages(record["pages"])],
            ["今回の指摘", f"{len(record['findings'])}件"],
            ["記録先", str(workspace / review["artifact"])],
        ]
    else:
        rows += [
            [
                "明示選択",
                f"{pages[0]}–{pages[1]}ページ" if pages else ", ".join(unit_ids or []),
            ],
            ["今回の指摘", "未確定（処理失敗は指摘の有無とは別です）"],
        ]
    rows += [
        ["試行記録", str(workspace / f"attempts/review/{review['run']}.json")],
        ["内容品質", "未承認（指摘0件でも品質承認ではありません）"],
    ]
    return _table(rows)


def saved_status_text(result: dict, workspace: Path) -> str:
    """Describe saved versions without claiming output validity or process liveness."""
    from datetime import datetime

    rows = [
        ["Workspace", str(workspace)],
        ["確認範囲", "保存記録のみ（成果物未検証）"],
        ["形式", result["format"].upper()],
    ]
    run = result["run"]
    if run:
        rows.append(["保存入力名", run["source_name"]])
        if result["format"] == "pdf":
            rows.extend(
                [
                    [
                        "保存run 所有範囲",
                        _pages(run["owned_pages"])
                        if run["owned_pages"] is not None
                        else "全ページ（総数不明）",
                    ],
                    ["保存run 参照専用", _pages(run["reference_pages"])],
                    ["保存run 補正成功", _pages(run["corrected_pages"])],
                    ["保存run 補正失敗", _pages(run["failed_pages"])],
                    ["保存run 補正未処理", _pages(run["not_processed_pages"])],
                ]
            )
        if run["error"]:
            rows.append(["保存run エラー記録", run["error"]])
        if run["error_code"]:
            rows.append(["保存run エラーコード", run["error_code"]])
    translation = result["translation"]
    if translation is None:
        rows.append(
            ["翻訳unit 総数・成功・失敗・未処理", "不明／未集計（訳文snapshotなし）"]
        )
    else:
        rows.extend(
            [
                ["集計対象の保存訳文版", translation["sha256"]],
                ["訳文の参照source版", translation["source_snapshot_sha256"]],
                ["訳文の参照preparation版", translation["preparation_sha256"]],
            ]
        )
        for key, label in (
            ("total", "総数"),
            ("succeeded", "成功"),
            ("failed", "失敗（取消を除く）"),
            ("not_processed", "未処理"),
            ("cancelled", "取消"),
        ):
            rows.append(["保存訳文 " + label, f"{translation[key]} unit"])
        for key, label in (
            ("source_matches_pointer", "source採用pointerとの記録上の対応"),
            ("preparation_matches_pointer", "preparation採用pointerとの記録上の対応"),
        ):
            rows.append(
                [
                    label,
                    "同じ版"
                    if translation[key]
                    else "差異あり／採用pointerなし（詳細確認が必要）",
                ]
            )
        if translation["run_settings_match"] is not None:
            for key, matches in translation["run_settings_match"].items():
                rows.append(
                    [
                        "保存訳文とrun設定 " + key,
                        "同じ設定" if matches else "差異あり（詳細確認が必要）",
                    ]
                )
    for name, digest in result["pointers"].items():
        rows.append(["記録pointer " + name, digest])
    for name, mtime in result["record_mtimes"].items():
        rows.append(
            [
                "記録file更新時刻 " + name,
                datetime.fromtimestamp(mtime, UTC).isoformat(),
            ]
        )
    sections = [_table(rows)]
    saved_render = result["render"]
    if saved_render:
        sections.append(
            _table(
                [
                    [
                        "過去renderの保存当時の結果",
                        saved_render["processing"]["status"],
                    ],
                    ["過去render source版", saved_render["source_snapshot_sha256"]],
                    [
                        "過去render translation版",
                        saved_render["translation_snapshot_sha256"] or "訳文なし",
                    ],
                    ["過去render 要確認", f"{saved_render['uncertainty_count']}件"],
                ]
            )
        )
        sections.append(
            "保存記録上のpath（成果物未検証）\n"
            + _table(
                [
                    [name, str(workspace / path)]
                    for name, path in saved_render["artifacts"].items()
                ]
            )
        )
    if result["candidate"]:
        sections.append(
            "原文候補の記録あり（採用済み原文とは別です）。詳細検査で比較してください。"
        )
    sections.append(
        "読まない成果物・原本・attempt・QA・内容品質は未検証です。新規／再利用数は未集計です。\n取消記録は実送信の有無を証明しません。記録更新時刻は最後の処理成功時刻ではなく、現在の生存状態も示しません。\n前後のpointer一致はtransaction整合性の保証ではありません。準備版の差だけで訳文の無効・再利用不可とは断定しません。"
    )
    sections.append(
        _table(
            [
                [
                    "詳細検査:",
                    shlex.join(["doc-translate", "status", str(workspace), "--verify"]),
                ],
                [
                    "完全JSON:",
                    shlex.join(["doc-translate", "status", str(workspace), "--json"]),
                ],
            ]
        )
    )
    return "\n\n".join(sections)
