"""Independent, schema-validated inference over the application-owned Node helper."""

import base64
import hashlib
import json
import math
import os
import selectors
import signal
import subprocess
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from io import BytesIO
from typing import Annotated, Literal
from uuid import uuid4

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from doc_translate import ai_runtime
from doc_translate.jsonl import JSONLRecord, encode_record, read_records

Thinking = Literal["off", "minimal", "low", "medium", "high", "xhigh", "max"]
Positive = Annotated[int, Field(gt=0, le=9007199254740991)]
Nonnegative = Annotated[float, Field(ge=0)]
Nonempty = Annotated[str, Field(min_length=1)]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class GenerationOptions(_Model):
    temperature: float | None = None
    maxTokens: Positive | None = None
    transport: Literal["sse", "websocket", "websocket-cached", "auto"] | None = None
    cacheRetention: Literal["none", "short", "long"] | None = None
    thinkingBudgets: (
        dict[Literal["minimal", "low", "medium", "high"], Positive] | None
    ) = None
    timeoutMs: Annotated[int, Field(gt=0, le=2147483647)] = 1_200_000


class Selection(_Model):
    """Partial explicit selection; no defaults or Pi CLI configuration are read."""

    provider: Nonempty | None = None
    model: Nonempty | None = None
    thinking: Thinking | None = None


class GenerationConfig(_Model):
    provider: Nonempty
    model: Nonempty
    thinking: Thinking
    options: GenerationOptions = Field(default_factory=GenerationOptions)


def resolve_config(
    *,
    stage: Selection | None = None,
    shared: Selection | None = None,
    options: GenerationOptions | None = None,
) -> GenerationConfig:
    """Resolve each field: stage > shared > PI_* environment; require all three."""
    values = {}
    for name, environment in (
        ("provider", "PI_PROVIDER"),
        ("model", "PI_MODEL"),
        ("thinking", "PI_REASONING_LEVEL"),
    ):
        value = getattr(stage, name, None)
        if value is None:
            value = getattr(shared, name, None)
        if value is None:
            value = os.environ.get(environment)
        values[name] = value
    return GenerationConfig.model_validate(
        {
            **values,
            "options": options or GenerationOptions(),
        }
    )


class GenerationInput(_Model):
    stage: Literal["correction", "prepare", "translation", "review"]
    policy: str
    text: str
    context: JsonValue
    glossary: JsonValue


class GenerationImage(_Model):
    data: bytes
    mimeType: Literal["image/png"]
    width: Positive
    height: Positive
    coordinates: Nonempty


@dataclass(frozen=True)
class ImageMetadata:
    sha256: str
    byte_size: int
    base64_bytes: int
    mimeType: str
    width: int
    height: int
    coordinates: str
    scale: int = 1


class _Versions(_Model):
    helper_version: str
    library_version: str


class _Auth(_Model):
    source: str | None
    type: Literal["api_key", "oauth"]


class _Resize(_Model):
    maxWidth: Positive | None
    maxHeight: Positive | None
    maxBytes: Positive | None
    jpegQuality: Nonnegative | None


class _ImageLimits(_Model):
    maxPerMessage: Positive | None
    maxPerRequest: Positive | None
    resize: _Resize | None


class _InputLimits(_Model):
    maxRequestBytes: Positive | None
    images: _ImageLimits | None


class Preflight(_Versions):
    operation: Literal["preflight"]
    provider: Nonempty
    model: Nonempty
    api: Nonempty
    image_input: bool
    thinking_levels: list[Thinking]
    context_window: Positive
    max_tokens: Positive
    auth: _Auth
    input_limits: _InputLimits | None


class _Terminal(_Model):
    type: Literal["done", "error"]
    reason: Literal["stop", "length", "toolUse", "error", "aborted", "deferred"]


class _Text(_Model):
    type: Literal["text"]
    text: str


class _OtherContent(_Model):
    type: Literal["thinking", "toolCall"]


class _Message(_Model):
    role: Literal["assistant"]
    provider: str
    model: str
    api: str
    stopReason: Literal["stop", "length", "toolUse", "error", "aborted", "deferred"]
    content: list[_Text | _OtherContent]
    provider_thinking_level: str | None
    response_model: str | None


class _Cost(_Model):
    input: Nonnegative
    output: Nonnegative
    cacheRead: Nonnegative
    cacheWrite: Nonnegative
    total: Nonnegative


class Usage(_Model):
    input: Nonnegative
    output: Nonnegative
    cacheRead: Nonnegative
    cacheWrite: Nonnegative
    totalTokens: Nonnegative
    cost: _Cost
    reasoning: Nonnegative | None = None
    cacheWrite1h: Nonnegative | None = None


class _Requested(_Model):
    provider: str
    model: str
    thinking: Thinking


class _Submitted(_Model):
    reasoning: Thinking | None
    maxRetries: Annotated[int, Field(ge=0, le=0)]
    timeoutMs: Positive
    maxTokens: Positive | None
    temperature: float | None
    transport: Literal["sse", "websocket", "websocket-cached", "auto"] | None
    cacheRetention: Literal["none", "short", "long"] | None
    thinkingBudgets: dict[Literal["minimal", "low", "medium", "high"], Positive] | None


class _Confirmed(_Model):
    thinking: str | None


class _Retry(_Model):
    helper_attempts: Annotated[int, Field(ge=1, le=1)]
    maxRetries: Annotated[int, Field(ge=0, le=0)]
    observed_responses: Annotated[int, Field(ge=0)]
    internal_retries: Literal["unknown"]
    retryable: bool
    classification: Literal["library_heuristic"] | None


ErrorCode = Literal[
    "invalid_request",
    "unknown_provider",
    "unknown_model",
    "unsupported_thinking",
    "unsupported_image",
    "invalid_image",
    "input_limit",
    "auth_unavailable",
    "auth_failed",
    "generation_failed",
    "missing_terminal",
    "model_mismatch",
    "tool_call",
    "timeout",
    "cancelled",
    "storage_failed",
]


class _EarlyError(_Model):
    code: ErrorCode


class _PreflightError(Preflight):
    code: ErrorCode


class GenerationFinal(_Versions):
    operation: Literal["generate"]
    code: ErrorCode | None
    terminal: _Terminal | None
    message: _Message | None
    usage: Usage | None
    usage_status: Literal["observed", "unknown"]
    elapsed_ms: Nonnegative
    requested: _Requested
    submitted: _Submitted
    provider_confirmed: _Confirmed
    retry: _Retry
    request_bytes_checked: bool


@dataclass
class Attempt:
    request_id: str
    preflight: Preflight | None = None
    final: GenerationFinal | None = None
    code: str | None = None
    exit_code: int | None = None
    elapsed_seconds: float = 0
    wait_seconds: int = 0
    images: list[ImageMetadata] = field(default_factory=list)


@dataclass
class GenerationResult[T: BaseModel]:
    answer: T
    attempts: list[Attempt]


class InferenceError(RuntimeError):
    """Safe error code and attempt metadata; never raw helper/provider exceptions."""

    def __init__(self, code: str, attempts: list[Attempt]):
        super().__init__(f"Inference failed: {code}.")
        self.code = code
        self.attempts = attempts


def _check_versions(value: _Versions) -> None:
    package = json.loads((ai_runtime._resources() / "package.json").read_text())
    if (
        value.helper_version != package["version"]
        or value.library_version != package["dependencies"]["@earendil-works/pi-ai"]
    ):
        raise ValueError("version mismatch")


def _validate_final(
    data: dict,
    config: GenerationConfig,
    preflight: Preflight,
    kind: str,
) -> GenerationFinal:
    final = GenerationFinal.model_validate(data)
    _check_versions(final)
    if final.requested.model_dump() != config.model_dump(exclude={"options"}):
        raise ValueError("request mismatch")
    expected = config.options.model_dump()
    expected.update(
        reasoning=None if config.thinking == "off" else config.thinking, maxRetries=0
    )
    if final.submitted.model_dump() != expected:
        raise ValueError("submitted mismatch")
    if final.request_bytes_checked and (
        preflight.input_limits is None or preflight.input_limits.maxRequestBytes is None
    ):
        raise ValueError("request byte check without published limit")
    if (final.usage is None) != (final.usage_status == "unknown"):
        raise ValueError("usage mismatch")
    if final.usage and not any(
        getattr(final.usage, name) > 0
        for name in ("input", "output", "cacheRead", "cacheWrite", "totalTokens")
    ):
        raise ValueError("unobserved usage")
    # Drain/abort can override code after generation has classified its retry.
    if final.retry.retryable and not (
        final.code
        in {
            "generation_failed",
            "storage_failed",
            "auth_failed",
            "cancelled",
            "timeout",
        }
        and final.terminal is not None
        and final.terminal.type == "error"
        and final.terminal.reason == "error"
        and final.message is not None
        and final.message.stopReason == "error"
        and (final.message.provider, final.message.model, final.message.api)
        == (config.provider, config.model, preflight.api)
        and not any(part.type == "toolCall" for part in final.message.content)
        and final.retry.classification == "library_heuristic"
    ):
        raise ValueError("invalid retry")
    if final.provider_confirmed.thinking != (
        final.message.provider_thinking_level if final.message else None
    ):
        raise ValueError("confirmation mismatch")
    if kind == "result":
        message = final.message
        if (
            final.code is not None
            or final.terminal is None
            or final.terminal.type != "done"
            or final.terminal.reason != "stop"
            or message is None
            or message.stopReason != "stop"
            or (message.provider, message.model, message.api)
            != (config.provider, config.model, preflight.api)
            or any(part.type == "toolCall" for part in message.content)
        ):
            raise ValueError("invalid success")
    elif kind != "error" or final.code is None:
        raise ValueError("invalid failure")
    return final


def _image_payload(
    images: Sequence[GenerationImage], preflight: Preflight, attempt: Attempt
) -> list[dict]:
    limits = preflight.input_limits.images if preflight.input_limits else None
    if images and not preflight.image_input:
        raise InferenceError("unsupported_image", [])
    if limits and any(
        limit is not None and len(images) > limit
        for limit in (limits.maxPerMessage, limits.maxPerRequest)
    ):
        raise InferenceError("input_limit", [])
    payload = []
    for image in images:
        try:
            with Image.open(BytesIO(image.data)) as decoded:
                if decoded.format != "PNG" or decoded.size != (
                    image.width,
                    image.height,
                ):
                    raise ValueError("image mismatch")
                decoded.verify()
            with Image.open(BytesIO(image.data)) as decoded:
                decoded.load()
        except OSError, ValueError, Image.DecompressionBombError, SyntaxError:
            raise InferenceError("invalid_image", []) from None
        data = base64.b64encode(image.data).decode("ascii")
        resize = limits.resize if limits else None
        if resize and any(
            maximum is not None and actual > maximum
            for actual, maximum in (
                (image.width, resize.maxWidth),
                (image.height, resize.maxHeight),
                (len(data), resize.maxBytes),
            )
        ):
            raise InferenceError("input_limit", [])
        payload.append({**image.model_dump(exclude={"data"}), "data": data})
        attempt.images.append(
            ImageMetadata(
                sha256=hashlib.sha256(image.data).hexdigest(),
                byte_size=len(image.data),
                base64_bytes=len(data),
                mimeType=image.mimeType,
                width=image.width,
                height=image.height,
                coordinates=image.coordinates,
            )
        )
    return payload


def _attempt(
    config: GenerationConfig,
    inputs: GenerationInput,
    images: Sequence[GenerationImage],
    command: Sequence[str],
    cwd: str,
    cancelled: threading.Event,
) -> Attempt:
    attempt = Attempt(request_id=uuid4().hex)
    started = time.monotonic()
    try:
        child = subprocess.Popen(
            command,
            cwd=cwd,
            env=ai_runtime._environment(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        attempt.code = "process_failed"
        attempt.elapsed_seconds = time.monotonic() - started
        return attempt
    assert child.stdin is not None and child.stdout is not None
    pending = b""
    outgoing = b""
    finished = False
    stopping: str | None = None
    deadline = time.monotonic() + config.options.timeoutMs / 1000

    def send(kind, payload):
        nonlocal outgoing
        assert child.stdin is not None
        outgoing = encode_record(
            JSONLRecord(
                schema_version=1,
                request_id=attempt.request_id,
                type=kind,
                payload=payload,
            )
        )
        selector.register(child.stdin, selectors.EVENT_WRITE)

    def close_input():
        assert child.stdin is not None
        if child.stdin in [key.fileobj for key in selector.get_map().values()]:
            selector.unregister(child.stdin)
        child.stdin.close()

    try:
        os.set_blocking(child.stdin.fileno(), False)
        with selectors.SelectSelector() as selector:
            selector.register(child.stdout, selectors.EVENT_READ)
            send(
                "preflight",
                {
                    **config.model_dump(exclude={"options"}),
                    "image_input": bool(images),
                    "options": config.options.model_dump(exclude_none=True),
                },
            )
            stdout_open = True
            while stdout_open:
                now = time.monotonic()
                if (
                    stopping is None
                    and not finished
                    and (cancelled.is_set() or now >= deadline)
                ):
                    stopping = "cancelled" if cancelled.is_set() else "timeout"
                    child.terminate()
                    if outgoing:
                        selector.unregister(child.stdin)
                        outgoing = b""
                    deadline = now + 30
                if now >= deadline:
                    raise subprocess.TimeoutExpired(command, 30)
                for key, _ in selector.select(min(0.05, deadline - now)):
                    if key.fileobj == child.stdin:
                        written = os.write(child.stdin.fileno(), outgoing[:65536])
                        outgoing = outgoing[written:]
                        if not outgoing:
                            selector.unregister(child.stdin)
                        continue
                    chunk = os.read(child.stdout.fileno(), 65536)
                    if not chunk:
                        if pending:
                            raise ValueError("truncated record")
                        selector.unregister(child.stdout)
                        stdout_open = False
                        break
                    pending += chunk
                    while b"\n" in pending:
                        line, pending = pending.split(b"\n", 1)
                        record = next(read_records(BytesIO(line + b"\n")))
                        if record.request_id != attempt.request_id or finished:
                            raise ValueError("unexpected record")
                        if record.type == "error" and set(record.payload) == {"code"}:
                            attempt.code = _EarlyError.model_validate(
                                record.payload
                            ).code
                            finished = True
                        elif attempt.preflight is None:
                            if record.type == "result":
                                preflight = Preflight.model_validate(record.payload)
                            elif record.type == "error":
                                preflight = _PreflightError.model_validate(
                                    record.payload
                                )
                            else:
                                raise ValueError("invalid preflight")
                            _check_versions(preflight)
                            if (
                                (preflight.provider, preflight.model)
                                != (config.provider, config.model)
                                or config.thinking not in preflight.thinking_levels
                                or (
                                    config.options.maxTokens is not None
                                    and config.options.maxTokens > preflight.max_tokens
                                )
                            ):
                                raise ValueError("preflight mismatch")
                            attempt.preflight = Preflight.model_validate(
                                preflight.model_dump(exclude={"code"})
                            )
                            if isinstance(preflight, _PreflightError):
                                attempt.code = preflight.code
                                finished = True
                            elif stopping is None:
                                image_payload = _image_payload(
                                    images, preflight, attempt
                                )
                                send(
                                    "generate",
                                    {
                                        **inputs.model_dump(exclude={"stage"}),
                                        "images": image_payload,
                                    },
                                )
                                deadline = (
                                    time.monotonic() + config.options.timeoutMs / 1000
                                )
                        else:
                            attempt.final = _validate_final(
                                record.payload, config, attempt.preflight, record.type
                            )
                            attempt.code = attempt.final.code
                            finished = True
                        if finished:
                            close_input()
                            deadline = time.monotonic() + 30
        attempt.exit_code = child.wait(
            timeout=max(0, min(30, deadline - time.monotonic()))
        )
        if not finished:
            attempt.code = stopping or "protocol_error"
        elif attempt.exit_code != (
            0
            if attempt.code is None
            else 2
            if attempt.code
            in {
                "invalid_request",
                "unknown_provider",
                "unknown_model",
                "unsupported_thinking",
                "unsupported_image",
                "invalid_image",
                "input_limit",
            }
            else 1
        ):
            attempt.code = "protocol_error"
        if stopping and attempt.code not in {"storage_failed", "auth_failed"}:
            attempt.code = stopping
    except InferenceError as error:
        attempt.code = error.code
    except ValueError, OSError, subprocess.TimeoutExpired:
        attempt.code = stopping or "protocol_error"
    finally:
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(
                    timeout=max(0, deadline - time.monotonic()) if stopping else 30
                )
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        attempt.exit_code = child.returncode
        child.stdin.close()
        child.stdout.close()
        attempt.elapsed_seconds = time.monotonic() - started
    return attempt


def _invalid_constant(_value: str) -> float:
    raise ValueError("Non-finite answer number")


def _finite_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("Non-finite answer number")
    return number


def generate[T: BaseModel](
    config: GenerationConfig,
    inputs: GenerationInput,
    *,
    response_model: type[T],
    images: Sequence[GenerationImage] = (),
    process_command: Sequence[str] | None = None,
    cancel: threading.Event | None = None,
) -> GenerationResult[T]:
    """Validate one JSON answer; retry only known library-heuristic transient errors.

    process_command is an explicit external-process test seam, not a provider fallback.
    Production always uses the verified application-owned installed runtime.
    """
    config = GenerationConfig.model_validate(config.model_dump())
    inputs = GenerationInput.model_validate(inputs.model_dump())
    images = [GenerationImage.model_validate(image.model_dump()) for image in images]
    if images and inputs.stage in {"prepare", "translation"}:
        raise InferenceError("unsupported_image", [])
    if process_command is None:
        node, runtime = ai_runtime.installed_runtime()
        command = [node, str(runtime / "dist/main.js")]
        cwd = str(runtime)
    else:
        command = list(process_command)
        cwd = str(ai_runtime._resources())
    cancelled = cancel if cancel is not None else threading.Event()
    previous_handlers = {}
    attempts = []

    def interrupt(_signum, _frame):
        cancelled.set()

    try:
        if threading.current_thread() is threading.main_thread():
            for signum in (signal.SIGINT, signal.SIGTERM):
                previous_handlers[signum] = signal.signal(signum, interrupt)
        for number in range(3):
            if cancelled.is_set():
                raise InferenceError("cancelled", attempts)
            attempt = _attempt(config, inputs, images, command, cwd, cancelled)
            attempts.append(attempt)
            if cancelled.is_set() and attempt.code not in {
                "storage_failed",
                "auth_failed",
            }:
                attempt.code = "cancelled"
            if attempt.code is None:
                assert attempt.final is not None and attempt.final.message is not None
                text = "".join(
                    part.text
                    for part in attempt.final.message.content
                    if isinstance(part, _Text)
                )
                try:
                    # Parse separately: answer JSON is not a transport envelope.
                    value = json.loads(
                        text,
                        parse_constant=_invalid_constant,
                        parse_float=_finite_float,
                    )
                    answer = response_model.model_validate(value)
                except ValueError:
                    attempt.code = "answer_invalid"
                else:
                    return GenerationResult(answer=answer, attempts=attempts)
            if not (
                number < 2
                and attempt.code == "generation_failed"
                and attempt.final is not None
                and attempt.final.retry.retryable
            ):
                raise InferenceError(attempt.code or "protocol_error", attempts)
            attempt.wait_seconds = 3 * (number + 1)
            if cancelled.wait(attempt.wait_seconds):
                raise InferenceError("cancelled", attempts)
        raise AssertionError("unreachable")
    finally:
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
