"""Public inference API -> compiled helper/public provider boundary (offline)."""

import base64
import hashlib
import json
import os
import shutil
import signal
import sys
import threading
import time
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image
from pydantic import BaseModel, ConfigDict, JsonValue, model_validator

from doc_translate.inference import (
    GenerationConfig,
    GenerationImage,
    GenerationInput,
    GenerationOptions,
    InferenceError,
    Selection,
    generate,
    resolve_config,
)

ROOT = Path(__file__).resolve().parents[1]


class EchoAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    context: dict[str, JsonValue]
    options: dict[str, JsonValue]
    calls: int


def helper(tmp_path, mode="stop"):
    node = shutil.which("node")
    assert node
    return [node, str(ROOT / "ai/tests/generation-process.mjs"), str(tmp_path), mode]


def test_generate_uses_real_helper_and_explicit_fresh_inputs(tmp_path):
    result = generate(
        GenerationConfig(provider="faux", model="vision", thinking="off"),
        GenerationInput(
            stage="translation",
            policy="Explicit policy",
            text="日本語\u2028line\u2029end",
            context={"chapter": "one"},
            glossary={"terms": []},
        ),
        response_model=EchoAnswer,
        process_command=helper(tmp_path),
    )
    assert result.answer.calls == 1
    messages = result.answer.context["messages"]
    assert isinstance(messages, list) and len(messages) == 2
    assert isinstance(messages[0], dict) and isinstance(messages[1], dict)
    assert messages[0] == {
        "role": "system",
        "content": "Explicit policy",
        "timestamp": 0,
    }
    assert messages[1]["role"] == "user"
    assert messages[1]["content"] == [
        {
            "type": "text",
            "text": '{"text":"日本語\u2028line\u2029end","context":{"chapter":"one"},"glossary":{"terms":[]}}',
        }
    ]
    assert result.answer.options["reasoning"] is None
    assert result.answer.options["maxRetries"] == 0
    assert result.answer.options["timeoutMs"] == 1_200_000
    assert len(result.attempts) == 1
    assert result.attempts[0].code is None
    final = result.attempts[0].final
    assert final is not None
    assert final.requested.thinking == "off"
    assert final.retry.internal_retries == "unknown"


def png(width=2, height=1, color="red"):
    output = BytesIO()
    Image.new("RGB", (width, height), color).save(output, format="PNG")
    return output.getvalue()


def test_images_keep_bytes_real_dimensions_coordinates_order_and_options(tmp_path):
    first, second = png(), png(1, 2, "blue")
    result = generate(
        GenerationConfig(
            provider="faux",
            model="vision",
            thinking="high",
            options=GenerationOptions(
                maxTokens=123,
                temperature=0.2,
                transport="sse",
                cacheRetention="short",
                thinkingBudgets={"high": 42},
            ),
        ),
        GenerationInput(
            stage="correction",
            policy="Only these pages",
            text="draft",
            context=None,
            glossary=None,
        ),
        images=[
            GenerationImage(
                data=first,
                mimeType="image/png",
                width=2,
                height=1,
                coordinates="page 2; pixels; top-left",
            ),
            GenerationImage(
                data=second,
                mimeType="image/png",
                width=1,
                height=2,
                coordinates="page 3; pixels; top-left",
            ),
        ],
        response_model=EchoAnswer,
        process_command=helper(tmp_path),
    )
    messages = result.answer.context["messages"]
    assert isinstance(messages, list) and isinstance(messages[1], dict)
    content = messages[1]["content"]
    assert isinstance(content, list)
    assert content[1:] == [
        {
            "type": "text",
            "text": '{"width":2,"height":1,"coordinates":"page 2; pixels; top-left"}',
        },
        {
            "type": "image",
            "data": base64.b64encode(first).decode(),
            "mimeType": "image/png",
        },
        {
            "type": "text",
            "text": '{"width":1,"height":2,"coordinates":"page 3; pixels; top-left"}',
        },
        {
            "type": "image",
            "data": base64.b64encode(second).decode(),
            "mimeType": "image/png",
        },
    ]
    assert result.answer.options == {
        "reasoning": "high",
        "maxRetries": 0,
        "timeoutMs": 1_200_000,
        "maxTokens": 123,
        "temperature": 0.2,
        "transport": "sse",
        "thinkingBudgets": {"high": 42},
    }
    metadata = result.attempts[0].images
    assert [item.sha256 for item in metadata] == [
        hashlib.sha256(first).hexdigest(),
        hashlib.sha256(second).hexdigest(),
    ]
    assert metadata[0].byte_size == len(first)
    assert metadata[0].base64_bytes == len(base64.b64encode(first))
    assert metadata[0].scale == 1


@pytest.mark.parametrize("stage", ["translation", "prepare"])
def test_text_stages_reject_source_images_without_launch(stage):
    with pytest.raises(InferenceError, match="unsupported_image") as error:
        generate(
            GenerationConfig(provider="faux", model="vision", thinking="off"),
            GenerationInput(
                stage=stage, policy="", text="", context=None, glossary=None
            ),
            images=[
                GenerationImage(
                    data=png(),
                    mimeType="image/png",
                    width=2,
                    height=1,
                    coordinates="p1",
                )
            ],
            response_model=EchoAnswer,
            process_command=["must-not-launch"],
        )
    assert error.value.attempts == []


@pytest.mark.parametrize(
    "data,width,mode,code",
    [
        (b"broken", 2, "stop", "invalid_image"),
        (png()[:-12], 2, "stop", "invalid_image"),
        (png(), 3, "stop", "invalid_image"),
        (png(), 2, "image-limit", "input_limit"),
    ],
)
def test_invalid_or_oversized_images_never_generate(tmp_path, data, width, mode, code):
    with pytest.raises(InferenceError) as error:
        generate(
            GenerationConfig(provider="faux", model="vision", thinking="off"),
            GenerationInput(
                stage="review", policy="", text="", context=None, glossary=None
            ),
            images=[
                GenerationImage(
                    data=data,
                    mimeType="image/png",
                    width=width,
                    height=1,
                    coordinates="p1",
                )
            ],
            response_model=EchoAnswer,
            process_command=helper(tmp_path, mode),
        )
    assert error.value.code == code
    assert error.value.attempts[0].preflight is not None
    assert error.value.attempts[0].final is None


@pytest.mark.parametrize("mode", ["image-limit", "image-limit-equal"])
def test_image_max_bytes_counts_base64_not_raw_png(tmp_path, mode):
    # Valid 1×1 RGB PNG with a text chunk: 88 raw bytes, 120 base64 bytes.
    encoded = (
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAAB3RFWHRhAGhlbGxvYYw2tw"
        "AAAAxJREFUeJxj+M/AAAADAQEAyf6S7wAAAABJRU5ErkJggg=="
    )
    data = base64.b64decode(encoded)
    assert len(data) == 88 and len(encoded) == 120
    try:
        result = generate(
            GenerationConfig(provider="faux", model="vision", thinking="off"),
            GenerationInput(
                stage="review", policy="p", text="", context=None, glossary=None
            ),
            images=[
                GenerationImage(
                    data=data,
                    mimeType="image/png",
                    width=1,
                    height=1,
                    coordinates="p1",
                )
            ],
            response_model=EchoAnswer,
            process_command=helper(tmp_path, mode),
        )
    except InferenceError as error:
        assert mode == "image-limit"
        assert error.code == "input_limit"
        assert len(error.attempts) == 1
        assert error.attempts[0].preflight is not None
        assert error.attempts[0].final is None  # Rejected before generate is sent.
    else:
        assert mode == "image-limit-equal"
        messages = result.answer.context["messages"]
        assert isinstance(messages, list) and isinstance(messages[1], dict)
        content = messages[1]["content"]
        assert isinstance(content, list)
        assert content[-1] == {
            "type": "image",
            "data": encoded,
            "mimeType": "image/png",
        }
        metadata = result.attempts[0].images[0]
        assert metadata.byte_size == 88 and metadata.base64_bytes == 120
        assert metadata.sha256 == hashlib.sha256(data).hexdigest()
        assert metadata.scale == 1


def test_selection_priority_is_per_field_and_has_no_defaults(monkeypatch):
    monkeypatch.setenv("PI_PROVIDER", "environment-provider")
    monkeypatch.setenv("PI_MODEL", "environment-model")
    monkeypatch.setenv("PI_REASONING_LEVEL", "low")
    config = resolve_config(
        stage=Selection(model="stage-model"),
        shared=Selection(provider="shared-provider"),
    )
    assert (config.provider, config.model, config.thinking) == (
        "shared-provider",
        "stage-model",
        "low",
    )
    monkeypatch.delenv("PI_REASONING_LEVEL")
    with pytest.raises(ValueError):
        resolve_config()


@pytest.mark.parametrize(
    "mode,code",
    [
        ("error", "generation_failed"),
        ("aborted", "generation_failed"),
        ("length", "generation_failed"),
        ("toolUse", "generation_failed"),
        ("deferred", "generation_failed"),
        ("missing-terminal", "missing_terminal"),
        ("wrong-model", "model_mismatch"),
        ("toolcall", "tool_call"),
        ("quota", "generation_failed"),
        ("billing", "generation_failed"),
        ("provider-auth", "generation_failed"),
        ("http-limit", "input_limit"),
    ],
)
def test_resolved_errors_are_not_success_or_blindly_retried(tmp_path, mode, code):
    with pytest.raises(InferenceError) as error:
        generate(
            GenerationConfig(provider="faux", model="vision", thinking="off"),
            GenerationInput(
                stage="translation", policy="p", text="t", context=None, glossary=None
            ),
            response_model=EchoAnswer,
            process_command=helper(tmp_path, mode),
        )
    assert error.value.code == code
    assert len(error.value.attempts) == 1
    assert error.value.attempts[0].final is not None
    assert "ARTIFICIAL" not in str(error.value)


@pytest.mark.parametrize("mode,expected", [("error-zero", None), ("partial-usage", 17)])
def test_failed_usage_is_unknown_or_retains_observed_partial(tmp_path, mode, expected):
    with pytest.raises(InferenceError) as error:
        generate(
            GenerationConfig(provider="faux", model="vision", thinking="off"),
            GenerationInput(
                stage="translation", policy="p", text="t", context=None, glossary=None
            ),
            response_model=EchoAnswer,
            process_command=helper(tmp_path, mode),
        )
    final = error.value.attempts[0].final
    assert final is not None
    if expected is None:
        assert final.usage is None and final.usage_status == "unknown"
    else:
        assert final.usage is not None and final.usage.input == expected
        assert final.usage_status == "observed"


def test_only_known_transient_errors_get_three_fresh_attempts_with_real_3_6_waits(
    tmp_path,
):
    started = time.monotonic()
    with pytest.raises(InferenceError) as error:
        generate(
            GenerationConfig(provider="faux", model="vision", thinking="off"),
            GenerationInput(
                stage="translation", policy="p", text="t", context=None, glossary=None
            ),
            response_model=EchoAnswer,
            process_command=helper(tmp_path, "http-retry"),
        )
    attempts = error.value.attempts
    assert time.monotonic() - started >= 9
    assert len(attempts) == 3
    assert len({a.request_id for a in attempts}) == 3
    assert [a.wait_seconds for a in attempts] == [3, 6, 0]
    assert all(a.final is not None and a.final.retry.retryable for a in attempts)
    assert all(
        a.final is not None and a.final.retry.observed_responses == 0 for a in attempts
    )
    assert all(
        a.final is not None and a.final.retry.internal_retries == "unknown"
        for a in attempts
    )


class EvidenceAnswer(BaseModel):
    id: str
    evidence: str

    @model_validator(mode="after")
    def known_id_and_evidence(self):
        if self.id != "unit-1" or self.evidence != "literal source":
            raise ValueError("unknown ID or unsupported evidence")
        return self


@pytest.mark.parametrize("model", [EvidenceAnswer])
def test_caller_schema_and_domain_validators_gate_acceptance(tmp_path, model):
    with pytest.raises(InferenceError) as error:
        generate(
            GenerationConfig(provider="faux", model="vision", thinking="off"),
            GenerationInput(
                stage="translation", policy="", text="", context=None, glossary=None
            ),
            response_model=model,
            process_command=helper(tmp_path),
        )
    assert error.value.code == "answer_invalid"
    assert len(error.value.attempts) == 1
    assert error.value.attempts[0].final is not None


def proxy(tmp_path, mode):
    return [
        sys.executable,
        str(ROOT / "tests/fixtures/inference_process.py"),
        mode,
        *helper(tmp_path),
    ]


@pytest.mark.parametrize(
    "mode",
    [
        "preflight-version",
        "preflight-model",
        "preflight-thinking",
        "preflight-secret",
        "wrong-id",
        "version",
        "terminal",
        "stop-reason",
        "model",
        "api",
        "tools",
        "submitted",
        "confirmed",
        "usage",
        "request-bytes",
        "retry-spoof",
        "truncated",
        "malformed",
        "invalid-utf8",
        "blank",
        "extra",
        "exit",
    ],
)
def test_independent_python_validation_rejects_faulty_process_contract(tmp_path, mode):
    with pytest.raises(InferenceError) as error:
        generate(
            GenerationConfig(provider="faux", model="vision", thinking="off"),
            GenerationInput(
                stage="translation", policy="p", text="t", context=None, glossary=None
            ),
            response_model=EchoAnswer,
            process_command=proxy(tmp_path, mode),
        )
    assert error.value.code == "protocol_error"
    assert len(error.value.attempts) == 1
    if mode in {"extra", "exit"}:
        assert error.value.attempts[0].final is not None


def test_unicode_byte_chunks_are_not_jsonl_boundaries(tmp_path):
    result = generate(
        GenerationConfig(provider="faux", model="vision", thinking="off"),
        GenerationInput(
            stage="translation",
            policy="日本語\u2028\u2029",
            text="data",
            context=None,
            glossary=None,
        ),
        response_model=EchoAnswer,
        process_command=proxy(tmp_path, "byte-chunks"),
    )
    assert "日本語\u2028\u2029" in json.dumps(
        result.answer.model_dump(), ensure_ascii=False
    )


@pytest.mark.parametrize(
    "mode", ["answer-invalid", "answer-nonfinite", "answer-overflow", "answer-domain"]
)
def test_answer_json_and_evidence_validator_failures_do_not_retry(tmp_path, mode):
    with pytest.raises(InferenceError) as error:
        generate(
            GenerationConfig(provider="faux", model="vision", thinking="off"),
            GenerationInput(
                stage="translation", policy="", text="", context=None, glossary=None
            ),
            response_model=EvidenceAnswer,
            process_command=proxy(tmp_path, mode),
        )
    assert error.value.code == "answer_invalid"
    assert len(error.value.attempts) == 1


def test_caller_id_and_evidence_validator_accepts_matching_answer(tmp_path):
    result = generate(
        GenerationConfig(provider="faux", model="vision", thinking="off"),
        GenerationInput(
            stage="translation", policy="", text="", context=None, glossary=None
        ),
        response_model=EvidenceAnswer,
        process_command=proxy(tmp_path, "answer-valid"),
    )
    assert result.answer == EvidenceAnswer(id="unit-1", evidence="literal source")


def controlled_helper(tmp_path, mode):
    node = shutil.which("node")
    assert node
    return [
        node,
        str(ROOT / "ai/tests/inference-provider.mjs"),
        str(tmp_path),
        mode,
    ]


def interrupt_when_ready(tmp_path, interrupt):
    def wait():
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if (tmp_path / "ready").exists():
                interrupt()
                return
            time.sleep(0.01)

    thread = threading.Thread(target=wait)
    thread.start()
    return thread


@pytest.mark.parametrize("mode", ["timeout", "preflight-timeout"])
def test_parent_timeout_cancels_and_reaps_without_retry(tmp_path, mode):
    with pytest.raises(InferenceError) as error:
        generate(
            GenerationConfig(
                provider="faux",
                model="vision",
                thinking="off",
                options=GenerationOptions(timeoutMs=1000 if mode == "timeout" else 120),
            ),
            GenerationInput(
                stage="review", policy="", text="", context=None, glossary=None
            ),
            response_model=EvidenceAnswer,
            process_command=controlled_helper(tmp_path, mode),
        )
    assert error.value.code == "timeout"
    assert len(error.value.attempts) == 1
    assert error.value.attempts[0].exit_code is not None
    if mode == "timeout":
        final = error.value.attempts[0].final
        assert final is not None
        assert final.terminal is not None and final.terminal.reason == "aborted"
        assert final.message is not None and final.message.stopReason == "aborted"
        assert final.usage is not None and final.usage.input == 17
        assert final.usage.totalTokens == 17 and final.usage_status == "observed"
        assert not final.retry.retryable


@pytest.mark.parametrize("signum", [signal.SIGINT, signal.SIGTERM])
def test_signals_cancel_generation_and_restore_handlers_after_reaping(tmp_path, signum):
    before = signal.getsignal(signum)
    thread = interrupt_when_ready(tmp_path, lambda: os.kill(os.getpid(), signum))
    try:
        with pytest.raises(InferenceError) as error:
            generate(
                GenerationConfig(provider="faux", model="vision", thinking="off"),
                GenerationInput(
                    stage="review", policy="", text="", context=None, glossary=None
                ),
                response_model=EvidenceAnswer,
                process_command=controlled_helper(tmp_path, "cancel"),
            )
        assert error.value.code == "cancelled"
        assert len(error.value.attempts) == 1
        final = error.value.attempts[0].final
        assert final is not None and final.code == "cancelled"
        assert final.terminal is not None and final.terminal.reason == "aborted"
        assert final.message is not None and final.message.stopReason == "aborted"
        assert final.usage is not None and final.usage.input == 17
        assert final.usage.totalTokens == 17 and final.usage_status == "observed"
        assert not final.retry.retryable
        assert signal.getsignal(signum) == before
        with pytest.raises(ProcessLookupError):
            os.kill(int((tmp_path / "ready").read_text()), 0)
    finally:
        thread.join(timeout=6)


@pytest.mark.parametrize(
    "mode,expected", [("refresh", "cancelled"), ("refresh-failure", "auth_failed")]
)
def test_cancellation_drains_rotated_credentials_and_preserves_auth_failure(
    tmp_path, mode, expected
):
    cancel = threading.Event()
    thread = interrupt_when_ready(tmp_path, cancel.set)
    started = time.monotonic()
    try:
        with pytest.raises(InferenceError) as error:
            generate(
                GenerationConfig(provider="faux", model="vision", thinking="off"),
                GenerationInput(
                    stage="review", policy="", text="", context=None, glossary=None
                ),
                response_model=EvidenceAnswer,
                process_command=controlled_helper(tmp_path, mode),
                cancel=cancel,
            )
        assert error.value.code == expected
        assert time.monotonic() - started >= 0.25
        assert len(error.value.attempts) == 1
        final = error.value.attempts[0].final
        assert final is not None and final.code == expected
        if mode == "refresh":
            credentials = json.loads((tmp_path / "credentials.json").read_text())
            assert credentials["faux"]["access"] == "ARTIFICIAL_ROTATED"
        assert "ARTIFICIAL" not in str(error.value)
        with pytest.raises(ProcessLookupError):
            os.kill(int((tmp_path / "ready").read_text()), 0)
    finally:
        thread.join(timeout=6)


@pytest.mark.parametrize(
    "code", ["storage_failed", "auth_failed", "cancelled", "timeout"]
)
def test_full_preflight_drain_errors_are_preserved_without_document_send(
    tmp_path, code
):
    with pytest.raises(InferenceError) as error:
        generate(
            GenerationConfig(provider="faux", model="vision", thinking="off"),
            GenerationInput(
                stage="translation",
                policy="never sent",
                text="never sent",
                context=None,
                glossary=None,
            ),
            response_model=EchoAnswer,
            process_command=proxy(tmp_path, f"preflight-error-{code}"),
        )
    assert error.value.code == code
    assert len(error.value.attempts) == 1
    assert error.value.attempts[0].preflight is not None
    assert error.value.attempts[0].final is None


@pytest.mark.parametrize(
    "code", ["storage_failed", "auth_failed", "cancelled", "timeout"]
)
def test_drain_override_retains_usage_and_retry_projection_but_never_retries(
    tmp_path, code
):
    with pytest.raises(InferenceError) as error:
        generate(
            GenerationConfig(provider="faux", model="vision", thinking="off"),
            GenerationInput(
                stage="translation", policy="", text="", context=None, glossary=None
            ),
            response_model=EchoAnswer,
            process_command=proxy(tmp_path, f"override-{code}"),
        )
    assert error.value.code == code
    assert len(error.value.attempts) == 1
    final = error.value.attempts[0].final
    assert final is not None and final.code == code
    assert final.retry.retryable
    assert final.usage is not None


def test_timeout_remains_live_while_helper_stops_reading_large_document(tmp_path):
    started = time.monotonic()
    with pytest.raises(InferenceError) as error:
        generate(
            GenerationConfig(
                provider="faux",
                model="vision",
                thinking="off",
                # Allow proxy/helper cold startup before testing the stalled write.
                options=GenerationOptions(timeoutMs=2000),
            ),
            GenerationInput(
                stage="translation",
                policy="",
                text="x" * 2_000_000,
                context=None,
                glossary=None,
            ),
            response_model=EchoAnswer,
            process_command=proxy(tmp_path, "stall-input"),
        )
    assert error.value.code == "timeout"
    assert error.value.attempts[0].preflight is not None
    assert time.monotonic() - started < 5


def test_process_launch_failure_retains_attempt_without_claiming_usage():
    with pytest.raises(InferenceError) as error:
        generate(
            GenerationConfig(provider="faux", model="vision", thinking="off"),
            GenerationInput(
                stage="translation", policy="", text="", context=None, glossary=None
            ),
            response_model=EchoAnswer,
            process_command=["/no-such-doc-translate-test-helper"],
        )
    assert error.value.code == "process_failed"
    assert len(error.value.attempts) == 1
    assert error.value.attempts[0].final is None
    assert error.value.attempts[0].exit_code is None


@pytest.mark.parametrize(
    "provider,model,thinking,code",
    [
        ("unknown", "vision", "off", "unknown_provider"),
        ("faux", "unknown", "off", "unknown_model"),
        ("faux", "text", "high", "unsupported_thinking"),
    ],
)
def test_exact_model_and_thinking_preflight_failures_do_not_send_or_fallback(
    tmp_path, provider, model, thinking, code
):
    with pytest.raises(InferenceError) as error:
        generate(
            GenerationConfig(provider=provider, model=model, thinking=thinking),
            GenerationInput(
                stage="translation", policy="", text="", context=None, glossary=None
            ),
            response_model=EchoAnswer,
            process_command=helper(tmp_path),
        )
    assert error.value.code == code
    assert len(error.value.attempts) == 1
    assert error.value.attempts[0].preflight is None
    assert error.value.attempts[0].final is None


def test_capability_is_checked_before_image_send(tmp_path):
    with pytest.raises(InferenceError) as error:
        generate(
            GenerationConfig(provider="faux", model="text", thinking="off"),
            GenerationInput(
                stage="correction", policy="", text="", context=None, glossary=None
            ),
            images=[
                GenerationImage(
                    data=png(),
                    mimeType="image/png",
                    width=2,
                    height=1,
                    coordinates="page 1",
                )
            ],
            response_model=EchoAnswer,
            process_command=helper(tmp_path),
        )
    assert error.value.code == "unsupported_image"
    assert error.value.attempts[0].preflight is None
    assert error.value.attempts[0].final is None


@pytest.mark.parametrize(
    "options",
    [
        {"maxRetries": 3},
        {"temperature": float("nan")},
        {"timeoutMs": 0},
        {"timeoutMs": 2147483648},
        {"maxTokens": True},
        {"thinkingBudgets": {"unknown": 1}},
        {"headers": {"authorization": "not allowed"}},
    ],
)
def test_options_reject_unsafe_unbounded_or_unrecognized_fields(options):
    with pytest.raises(ValueError):
        GenerationOptions.model_validate(options)
