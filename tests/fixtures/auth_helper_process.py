"""External process fake for authentication JSONL, never used by production."""

import json
import os
import signal
import sys
import time
from pathlib import Path

if sys.argv[1:] == ["--version"]:
    print("v24.21.0")
    sys.exit(0)

request = json.loads(sys.stdin.readline())
mode = os.environ["AUTH_PROCESS_MODE"]


def emit(kind, payload, *, newline=True):
    print(
        json.dumps({**request, "type": kind, "payload": payload}),
        end="\n" if newline else "",
        flush=True,
    )


def result():
    return {"operation": "login", "provider": "anthropic", "method": "api_key"}


if mode == "error":
    print("ARTIFICIAL_SECRET_STDERR", file=sys.stderr)
    emit("error", {"code": "auth_failed"})
    sys.exit(1)
if mode in {"cancel", "cancel-storage-failure"}:
    marker = Path(os.environ["AUTH_PROCESS_MARKER"])
    marker.write_text(str(os.getpid()))

    def cancel(_signum, _frame):
        time.sleep(0.3)
        marker.with_suffix(".drained").write_text("saved")
        if mode == "cancel-storage-failure":
            print("ARTIFICIAL_SECRET_STDERR", file=sys.stderr)
            emit("prompt_cancel", {"prompt_id": "1"})
            emit("error", {"code": "storage_failed"})
        sys.exit(1)

    signal.signal(signal.SIGTERM, cancel)
    emit(
        "prompt",
        {
            "prompt_id": "1",
            "prompt": {"type": "secret", "message": "Artificial prompt"},
        },
    )
    time.sleep(20)
    sys.exit(1)
if mode in {"notifications", "notifications-auth-failed"}:
    for event in json.loads(os.environ["AUTH_PROCESS_EVENTS"]):
        emit("notify", {"event": event})
    emit(
        "prompt",
        {
            "prompt_id": "1",
            "prompt": {"type": "secret", "message": "Artificial prompt"},
        },
    )
    response = json.loads(sys.stdin.readline())
    assert response["payload"] == {"prompt_id": "1", "value": "ARTIFICIAL_SECRET"}
    if mode == "notifications-auth-failed":
        emit("error", {"code": "auth_failed"})
        sys.exit(1)
    emit("result", result())
    sys.exit(0)
if mode == "callback":
    emit(
        "notify",
        {
            "event": {
                "type": "device_code",
                "userCode": "ARTIFICIAL_DEVICE_CODE",
                "verificationUri": "https://example.invalid/auth",
            }
        },
    )
    emit(
        "prompt",
        {
            "prompt_id": "1",
            "prompt": {"type": "manual_code", "message": "Artificial prompt"},
        },
    )
    time.sleep(0.1)
    emit("prompt_cancel", {"prompt_id": "1"})
    emit("result", result())
    sys.exit(0)
if mode == "wrong-id":
    request["request_id"] = "other"
payload = result()
if mode == "secret-result":
    payload["key"] = "ARTIFICIAL_SECRET_RESULT"
emit("result", payload, newline=mode != "incomplete")
sys.exit(1 if mode == "exit" else 0)
