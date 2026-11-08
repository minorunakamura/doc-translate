"""Optional process-local observations; no request or saved-artifact fields."""

import threading
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doc_translate.inference import (
        BeforeSend,
        GenerationConfig,
        GenerationInput,
        Preflight,
    )


@dataclass(frozen=True)
class ProgressEvent:
    stage: str
    kind: str
    phase: str = ""
    unit_ids: tuple[str, ...] = ()
    owned_pages: tuple[int, ...] | None = None
    reference_pages: tuple[int, ...] = ()
    job_id: str = ""
    request_id: str = ""
    reason: str = ""
    provider: str = ""
    model: str = ""
    thinking: str = ""
    auth_source: str = ""
    auth_type: str = ""
    total: int | None = None
    total_updating: bool = False
    succeeded: int = 0
    reused: int = 0
    failed: int = 0
    cancelled_count: int = 0
    not_processed: int = 0
    wait_seconds: int = 0
    source_name: str = ""
    document_sha256: str = ""
    format: str = ""


Observer = Callable[[ProgressEvent], None]


def emit(observer: Observer | None, stage: str, kind: str, **metadata) -> None:
    if observer is not None:
        observer(ProgressEvent(stage=stage, kind=kind, **metadata))


def scoped_observer(observer: Observer | None, **metadata) -> Observer | None:
    if observer is None:
        return None
    return lambda event: observer(replace(event, **metadata))


@dataclass(frozen=True)
class SendNotice:
    """Keep BeforeSend's three-argument contract; CLI alone consumes extra metadata."""

    callback: Callable[[GenerationConfig, GenerationInput, Preflight, dict], None]
    metadata: dict

    def __call__(self, config, inputs, preflight):
        self.callback(config, inputs, preflight, self.metadata)


def scoped_before_send(callback: BeforeSend | None, **metadata) -> BeforeSend | None:
    if isinstance(callback, SendNotice):
        return replace(callback, metadata={**callback.metadata, **metadata})
    return callback


class ProgressState:
    """Worker callbacks only mutate data; the display thread renders a snapshot."""

    def __init__(self):
        self.lock = threading.Lock()
        self.stages: dict[str, ProgressEvent] = {}
        self.requests: dict[str, ProgressEvent] = {}
        self.splitting: set[str] = set()
        self.history: list[ProgressEvent] = []
        self.pending_lines: list[ProgressEvent] = []
        self.current: ProgressEvent | None = None
        self.header: ProgressEvent | None = None

    def observe(self, event: ProgressEvent) -> None:
        with self.lock:
            if event.kind == "document":
                self.header = event
            elif event.request_id:
                if event.kind == "request_end":
                    self.requests.pop(event.request_id, None)
                else:
                    self.requests[event.request_id] = event
            elif event.kind == "split" and event.stage == "translation":
                self.splitting.add(event.job_id)
            elif event.kind == "adopted":
                if event.stage == "translation":
                    self.splitting.discard(event.job_id)
                    event = replace(event, total_updating=bool(self.splitting))
                self.stages[event.stage] = event
            elif event.kind in {"start", "done", "failed", "partial"}:
                previous = self.stages.get(event.stage)
                if (
                    previous
                    and previous.total is not None
                    and event.kind in {"done", "partial", "failed"}
                ):
                    event = replace(
                        previous,
                        kind=event.kind,
                        reason=event.reason,
                        owned_pages=event.owned_pages,
                        reference_pages=event.reference_pages,
                    )
                self.stages[event.stage] = event
            self.current = event
            if event.kind in {
                "collect",
                "organize",
                "retry",
                "repair",
                "failed",
                "cancelled",
                "split",
                "reused",
            } or (event.kind == "request_end" and event.reason):
                self.history = [*self.history[-2:], event]
            self.pending_lines.append(event)

    def snapshot(self):
        with self.lock:
            lines, self.pending_lines = self.pending_lines, []
            return (
                self.header,
                dict(self.stages),
                list(self.requests.values()),
                bool(self.splitting),
                self.current,
                list(self.history),
                lines,
            )
