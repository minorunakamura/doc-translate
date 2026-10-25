"""Terminal-only authentication UI over the helper's JSONL process boundary."""

import json
import os
import selectors
import signal
import subprocess
import termios
from io import BytesIO
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from doc_translate import ai_runtime
from doc_translate.jsonl import JSONLRecord, encode_record, read_records


class SelectOption(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: str
    label: str
    description: str | None = None


class AuthPrompt(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    type: Literal["text", "secret", "select", "manual_code"]
    message: str
    placeholder: str | None = None
    options: list[SelectOption] | None = None


class AuthenticationCancelled(Exception):
    """The user interrupted authentication."""


def authenticate(operation: str, provider: str, method: str | None = None) -> dict:
    node, runtime = ai_runtime.installed_runtime()
    payload = {"provider": provider}
    if operation == "login":
        assert method is not None
        payload["method"] = method
    request = JSONLRecord(
        schema_version=1, request_id=uuid4().hex, type=operation, payload=payload
    )
    child = subprocess.Popen(
        [node, str(runtime / "dist/main.js")],
        cwd=runtime,
        env=ai_runtime._environment(),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    assert child.stdin is not None and child.stdout is not None
    terminal: int | None = None
    attributes: list | None = None
    prompt_id: str | None = None
    prompt_type: str | None = None
    options: set[str] = set()
    result: dict | None = None
    error_code: str | None = None
    pending = b""
    terminal_input = b""
    previous_handlers = {}
    cancelled = False

    def interrupt(_signum, _frame):
        raise AuthenticationCancelled

    def restore_prompt(selector):
        nonlocal attributes, terminal_input
        if attributes is not None and terminal is not None:
            termios.tcsetattr(terminal, termios.TCSANOW, attributes)
            termios.tcflush(terminal, termios.TCIFLUSH)
            selector.unregister(terminal)
            attributes = None
            os.write(terminal, b"\n")
        terminal_input = b""

    try:
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous_handlers[signum] = signal.signal(signum, interrupt)
        child.stdin.write(encode_record(request))
        child.stdin.flush()
        # macOS kqueue cannot register /dev/tty; select also handles prompt cancellation.
        with selectors.SelectSelector() as selector:
            selector.register(child.stdout, selectors.EVENT_READ)
            while selector.get_map():
                for key, _ in selector.select():
                    if key.fileobj == child.stdout:
                        chunk = os.read(child.stdout.fileno(), 65536)
                        if not chunk:
                            if pending:
                                raise ValueError("Incomplete authentication record")
                            selector.unregister(child.stdout)
                            restore_prompt(selector)
                            break
                        pending += chunk
                        while b"\n" in pending:
                            line, pending = pending.split(b"\n", 1)
                            record = next(read_records(BytesIO(line + b"\n")))
                            if (
                                record.request_id != request.request_id
                                or result is not None
                                or error_code is not None
                            ):
                                raise ValueError("Invalid authentication response")
                            data = record.payload
                            if record.type in {"result", "error"}:
                                restore_prompt(selector)
                                if record.type == "result":
                                    expected = {
                                        "operation": operation,
                                        "provider": provider,
                                    }
                                    if operation == "login":
                                        expected["method"] = method
                                    if data != expected:
                                        raise ValueError(
                                            "Invalid authentication result"
                                        )
                                    result = data
                                else:
                                    if (
                                        set(data) != {"code"}
                                        or not isinstance(data["code"], str)
                                        or data["code"]
                                        not in {
                                            "invalid_request",
                                            "unknown_provider",
                                            "unsupported_method",
                                            "cancelled",
                                            "auth_failed",
                                            "storage_failed",
                                        }
                                    ):
                                        raise ValueError("Invalid authentication error")
                                    error_code = data["code"]
                                child.stdin.close()
                            elif record.type == "prompt_cancel":
                                if data != {"prompt_id": prompt_id}:
                                    raise ValueError("Invalid prompt cancellation")
                                restore_prompt(selector)
                            elif record.type in {"prompt", "notify"}:
                                if terminal is None:
                                    try:
                                        terminal = os.open(
                                            "/dev/tty", os.O_RDWR | os.O_NOCTTY
                                        )
                                    except OSError:
                                        raise RuntimeError(
                                            "Authentication requires a terminal; piped secret input is not accepted."
                                        ) from None
                                if record.type == "notify":
                                    event = data.get("event")
                                    if not isinstance(event, dict) or event.get(
                                        "type"
                                    ) not in {
                                        "info",
                                        "progress",
                                        "auth_url",
                                        "device_code",
                                    }:
                                        raise ValueError(
                                            "Invalid authentication notification"
                                        )
                                    os.write(
                                        terminal,
                                        (
                                            json.dumps(event, ensure_ascii=False) + "\n"
                                        ).encode(),
                                    )
                                else:
                                    if (
                                        attributes is not None
                                        or set(data) != {"prompt_id", "prompt"}
                                        or not isinstance(data["prompt_id"], str)
                                    ):
                                        raise ValueError(
                                            "Invalid authentication prompt"
                                        )
                                    prompt = AuthPrompt.model_validate(data["prompt"])
                                    prompt_id = data["prompt_id"]
                                    prompt_type = prompt.type
                                    options = set()
                                    message = prompt.message
                                    if prompt_type == "select":
                                        if not prompt.options:
                                            raise ValueError(
                                                "Missing authentication options"
                                            )
                                        for option in prompt.options:
                                            options.add(option.id)
                                            message += f"\n{option.id}: {option.label}"
                                    hidden = termios.tcgetattr(terminal)
                                    attributes = list(hidden)
                                    hidden[3] &= ~termios.ECHO
                                    termios.tcsetattr(terminal, termios.TCSANOW, hidden)
                                    selector.register(terminal, selectors.EVENT_READ)
                                    os.write(terminal, (message + ": ").encode())
                            else:
                                raise ValueError("Invalid authentication record")
                    elif (
                        terminal is not None
                        and prompt_id is not None
                        and attributes is not None
                    ):
                        chunk = os.read(terminal, 4096)
                        if not chunk:
                            raise AuthenticationCancelled
                        terminal_input += chunk
                        if b"\n" in terminal_input:
                            value = terminal_input.split(b"\n", 1)[0].decode("utf-8")
                            if prompt_type == "select" and value not in options:
                                raise ValueError("Invalid authentication selection")
                            response = JSONLRecord(
                                schema_version=1,
                                request_id=request.request_id,
                                type="response",
                                payload={"prompt_id": prompt_id, "value": value},
                            )
                            child.stdin.write(encode_record(response))
                            child.stdin.flush()
                            restore_prompt(selector)
        status = child.wait(timeout=30)
        if (
            error_code in {"unknown_provider", "unsupported_method", "invalid_request"}
            and status == 2
        ):
            raise ai_runtime.RuntimeUnavailable(
                "Unknown provider or unsupported login method; use doc-translate providers (login_methods)."
            )
        if status != 0 or error_code or result is None:
            raise RuntimeError(
                "Authentication failed or was cancelled; check credential storage and re-login if necessary."
            )
        return result
    except ai_runtime.RuntimeUnavailable:
        raise
    except AuthenticationCancelled, KeyboardInterrupt:
        cancelled = True
        raise RuntimeError("Authentication cancelled.") from None
    except RuntimeError:
        raise
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        termios.error,
        subprocess.TimeoutExpired,
    ):
        raise RuntimeError(
            "Authentication helper failed or returned invalid data."
        ) from None
    finally:
        for signum in previous_handlers:
            signal.signal(signum, signal.SIG_IGN)
        # A provider may still be saving a rotated credential: never kill before grace.
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=30)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        if cancelled:
            try:
                for record in read_records(BytesIO(pending + child.stdout.read())):
                    if (
                        record.request_id == request.request_id
                        and record.type == "error"
                        and record.payload == {"code": "storage_failed"}
                    ):
                        error_code = "storage_failed"
            except OSError, ValueError:
                pass  # Cancellation must not expose raw helper output.
        terminal_failed = False
        if attributes is not None and terminal is not None:
            try:
                termios.tcsetattr(terminal, termios.TCSANOW, attributes)
                termios.tcflush(terminal, termios.TCIFLUSH)
                os.write(terminal, b"\n")
            except termios.error, OSError:
                terminal_failed = True
        if terminal is not None:
            os.close(terminal)
        child.stdin.close()
        child.stdout.close()
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
        if terminal_failed:
            raise RuntimeError("Authentication terminal unavailable.") from None
        if cancelled and error_code == "storage_failed":
            raise RuntimeError(
                "Credential storage failed during cancellation; check storage and re-login if necessary."
            ) from None
