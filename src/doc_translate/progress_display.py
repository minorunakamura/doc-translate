"""Rich is confined to this disposable stderr view, never worker callbacks."""

import os
import sys
import threading
import time
from contextlib import suppress

from rich.console import Console, Group
from rich.live import Live
from rich.segment import Segment, Segments
from rich.text import Text

from doc_translate.cli_output import _safe
from doc_translate.progress import ProgressEvent, ProgressState

_STAGES = {
    "review_validate": "保存データ・選択範囲の検証",
    "review": "AIによる内容確認",
    "review_save": "確認結果の保存",
    "review_verify": "終了時の保存データ検査",
    "extract": "本文・構造抽出",
    "validate": "保存データ検証",
    "correction": "PDF補正",
    "assemble": "PDF source組立",
    "prepare": "翻訳準備",
    "translation": "翻訳",
    "render": "Markdown出力",
}
_KINDS = {
    "start": "ローカル処理中",
    "done": "保存・処理完了",
    "partial": "部分完了",
    "failed": "失敗",
    "collect": "collect 情報収集",
    "organize": "organize 文書全体の整理",
    "saved": "検証済み収集記録保存（準備全体は未採用）",
    "reused": "検証済み記録を再利用（成功の内訳）",
    "repair": "回答訂正",
    "split": "入力分割",
    "adopted": "採用snapshot保存済み",
    "validating": "ローカル検証中（未保存）",
    "saving": "検証済み回答を保存中（成功数は未反映）",
    "request_start": "helper起動・preflight検証中",
    "before_send": "送信前（provider到達は未確認）",
    "waiting": "AI応答待ち（helperへの書込み完了）",
    "draining": "helper終了・認証保存drain待ち（AI応答待ちではない）",
    "request_end": "request終了／ローカル検証・保存",
    "retry": "通信再試行",
    "cancelled": "取消受付／child終了・保存処理を待機中",
    "finished": "child・保存処理終了",
}


def _pages(pages):
    if not pages:
        return "なし"
    if list(pages) == list(range(pages[0], pages[-1] + 1)):
        return str(pages[0]) if len(pages) == 1 else f"{pages[0]}–{pages[-1]}"
    return ", ".join(map(str, pages[:6])) + (" …（省略）" if len(pages) > 6 else "")


def _target(event):
    if event.owned_pages is not None:
        return f"所有 {_pages(event.owned_pages)}ページ／参照専用 {_pages(event.reference_pages)}ページ"
    ids = event.unit_ids
    target = (
        f"対象 {len(ids)} unit: " + ", ".join(_safe(i) for i in ids[:2]) if ids else ""
    )
    if len(ids) > 2:
        target += " …（省略）"
    if event.pages is not None:
        target += f" / 対象 {len(event.block_ids)} block"
        if event.block_ids:
            target += ": " + ", ".join(_safe(i) for i in event.block_ids[:2])
            if len(event.block_ids) > 2:
                target += " …（省略）"
        target += (
            f" / 画像 {_pages(event.pages)}ページ" if event.pages else " / 画像なし"
        )
    return target.removeprefix(" / ")


def _line(event):
    name = (
        "PDF本文・レイアウト抽出"
        if event.stage == "extract" and event.phase == "pdf"
        else _STAGES.get(event.stage, _safe(event.stage))
    )
    label = (
        "所有chunk・run manifest保存反映済み"
        if event.stage == "correction" and event.kind == "adopted"
        else _KINDS.get(event.kind, _safe(event.kind))
    )
    if event.stage == "review" and event.kind == "done":
        label = "内容確認回答の検証完了（記録は未保存）"
    text = f"{name}: {label}"
    if event.reason == "candidate_adoption_required":
        text += " / 候補の明示採用待ち（後段は未実行）"
    if event.kind == "split" and event.stage == "translation":
        text += "／総数更新中"
    if event.reason:
        text += f" / 理由 {_safe(event.reason)}"
    if event.phase:
        text += (
            " / organize 文書全体の収集結果整理（unit全文再送なし）"
            if event.phase == "organize"
            else f" / {_safe(event.phase)}"
        )
    if event.kind == "adopted":
        unit = "ページ" if event.stage == "correction" else "unit"
        text += f" 成功 {event.succeeded}/{'更新中' if event.total_updating else event.total} {unit}（うち再利用 {event.reused}）／失敗 {event.failed}／取消 {event.cancelled_count}／未処理 {event.not_processed}"
    if event.unit_ids or event.owned_pages is not None or event.pages is not None:
        text += " / " + _target(event)
    if event.provider:
        text += f" / 送信先 {_safe(event.provider)} / {_safe(event.model)} · thinking {_safe(event.thinking)}"
    if event.auth_type:
        text += f" / 認証 {_safe(event.auth_source) or 'source不明'} / {_safe(event.auth_type)}"
    if event.wait_seconds:
        text += f" / {event.wait_seconds}秒待機"
    return text


def _header(event):
    if event.phase == "review":
        return f"原本 {_safe(event.source_name)} / {_safe(event.format.upper())} 明示選択 {_target(event)} / SHA-256 {_safe(event.document_sha256)}"
    scope = (
        "全体"
        if event.format == "epub"
        else (
            f"所有意図 {_pages(event.owned_pages)}ページ"
            if event.owned_pages is not None
            else "所有意図 全ページ（抽出検証後に確定）"
        )
    )
    if event.format == "pdf":
        scope += f"／追加参照許可 {_pages(event.reference_pages)}ページ（requestでは所有外の隣接のみ参照）"
    return f"原本 {_safe(event.source_name)} / {_safe(event.format.upper())} {scope} / SHA-256 {_safe(event.document_sha256)}"


class ProgressDisplay:
    def __init__(self):
        self.state = ProgressState()
        self.console = Console(
            stderr=True,
            no_color="NO_COLOR" in os.environ,
            markup=False,
            highlight=False,
        )
        self.live: Live | None = None
        self.stopped = threading.Event()
        self.render_lock = threading.Lock()
        self.thread: threading.Thread | None = None
        self.started = time.monotonic()

    def __enter__(self):
        if self.console.is_terminal:
            try:
                self.live = Live(
                    Text("開始準備中"),
                    console=self.console,
                    auto_refresh=False,
                    refresh_per_second=4,
                    redirect_stdout=False,
                    redirect_stderr=False,
                )
                self.live.start()
            except Exception:  # noqa: BLE001 - rendering alone may fall back; domain errors stay visible.
                self._disable_live()
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()
        return self.observe

    def observe(self, event: ProgressEvent):
        self.state.observe(event)
        if (
            event.kind == "start"
            and threading.current_thread() is threading.main_thread()
        ):
            self._refresh()

    def _disable_live(self):
        if self.live is not None:
            with suppress(Exception):
                self.live.stop()
        self.live = None

    def _write(self, line):
        # Display failures must not replace a domain/storage exception.
        with suppress(Exception):
            sys.stderr.write(line + "\n")
            sys.stderr.flush()

    def _refresh(self):
        with self.render_lock:
            self._render_snapshot()

    def _render_snapshot(self):
        header, stages, requests, splitting, current, history, lines = (
            self.state.snapshot()
        )
        if self.live is None:
            for event in lines:
                if event.kind == "document":
                    self._write(_header(event))
                else:
                    self._write(_line(event))
            return
        try:
            for event in lines:
                if event.kind in {"retry", "failed", "cancelled"} or (
                    event.kind == "request_end" and event.reason
                ):
                    self.console.print(Text(_line(event), style="yellow"))
            rows = []
            if header:
                rows.append(
                    Text(
                        _header(header),
                        overflow="fold",
                    )
                )
            order = (
                ["validate", "extract"]
                if header and header.phase == "resume"
                else ["extract", "validate"]
            )
            order += ["prepare", "translation", "render"]
            if header and header.format == "pdf":
                order = [
                    "validate",
                    "extract",
                    "correction",
                    "assemble",
                    "prepare",
                    "translation",
                    "render",
                ]
            if (header and header.phase == "review") or "review_validate" in stages:
                order = ["review_validate", "review", "review_save", "review_verify"]
            for stage in order:
                event = stages.get(stage)
                kind = event.kind if event else "pending"
                symbol, label, style = (
                    ("○", "未着手", "grey50")
                    if kind == "pending"
                    else ("✓", "完了", "green")
                    if kind == "done"
                    else (
                        "!",
                        "失敗" if kind == "failed" else "部分完了",
                        "red" if kind == "failed" else "yellow",
                    )
                    if kind in {"failed", "partial"}
                    else ("⠋⠙⠹⠸"[int(time.monotonic() * 4) % 4], "実行中", "cyan")
                )
                name = (
                    "PDF本文・レイアウト抽出"
                    if stage == "extract" and header and header.format == "pdf"
                    else _STAGES.get(stage, stage)
                )
                row = Text(f"{symbol} {name} {label}", style=style)
                if event and event.total is not None:
                    unit = "ページ" if stage == "correction" else "unit"
                    row.append(
                        f" 成功 {event.succeeded}/{'更新中' if splitting and stage == 'translation' else event.total} {unit}（再利用 {event.reused}）／失敗 {event.failed}／取消 {event.cancelled_count}／未処理 {event.not_processed}",
                        style="default",
                    )
                rows.append(row)
            detail = []
            if current:
                detail.append(Text(_line(current), overflow="fold"))
            waiting = [e for e in requests if e.kind == "waiting"]
            detail.append(
                Text(
                    f"AI応答待ち {len(waiting)}件 / 経過 {int(time.monotonic() - self.started)}秒"
                )
            )
            detail.extend(Text(_target(e), overflow="fold") for e in waiting)
            height = self.console.size.height
            visible_history = history[-1:] if height < 14 else history
            detail.extend(
                Text("履歴（狭高さ:最新のみ）: " + _line(e), overflow="fold")
                if height < 14
                else Text("履歴: " + _line(e), overflow="fold")
                for e in visible_history
            )
            if height < 14:
                rows = (
                    rows[-1:]
                    if not current
                    else [Text(f"現在: {_STAGES.get(current.stage, current.stage)}")]
                )
                # Bound physical lines, not Text objects, including Live's final repaint.
                lines = self.console.render_lines(
                    Group(*rows, *detail), self.console.options, pad=False
                )[: max(1, height - 1)]
                view = Segments(
                    segment for line in lines for segment in [*line, Segment.line()]
                )
            else:
                view = Group(*rows, *detail)
            self.live.update(view, refresh=True)
        except Exception:  # noqa: BLE001 - rendering alone may fall back; domain errors stay visible.
            self._disable_live()
            self._write("進捗表示: 行表示へ切替（処理は継続）")
            if current:
                self._write(_line(current))

    def _loop(self):
        while not self.stopped.wait(0.25):
            self._refresh()

    def __exit__(self, exc_type, *_):
        if exc_type is not None:
            with self.state.lock:
                current = self.state.current
            if current:
                self.state.observe(
                    ProgressEvent(
                        stage=current.stage, kind="failed", reason="execution_failed"
                    )
                )
        self.stopped.set()
        if self.thread:
            self.thread.join()
        try:
            self._refresh()
        finally:
            if self.live:
                with suppress(Exception):
                    self.live.stop()
