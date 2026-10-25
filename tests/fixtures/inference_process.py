"""External protocol-fault proxy around the actual compiled generation helper."""

import json
import signal
import subprocess
import sys
import time

mode, *command = sys.argv[1:]
child = subprocess.Popen(
    command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
)
assert child.stdin is not None and child.stdout is not None


def send(data):
    wire = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode() + b"\n"
    if mode == "byte-chunks":
        for byte in wire:
            sys.stdout.buffer.write(bytes([byte]))
            sys.stdout.buffer.flush()
    else:
        sys.stdout.buffer.write(wire)
        sys.stdout.buffer.flush()


try:
    for stage in ("preflight", "generate"):
        request = sys.stdin.buffer.readline()
        if not request:
            break
        child.stdin.write(request)
        child.stdin.flush()
        data = json.loads(child.stdout.readline())
        payload = data["payload"]
        if stage == "preflight":
            if mode.startswith("preflight-error-"):
                data["type"] = "error"
                payload["code"] = mode.removeprefix("preflight-error-")
            elif mode == "preflight-version":
                payload["library_version"] = "wrong"
            elif mode == "preflight-model":
                payload["model"] = "wrong"
            elif mode == "preflight-thinking":
                payload["thinking_levels"] = ["low"]
            elif mode == "preflight-image":
                payload["image_input"] = False
            elif mode == "preflight-secret":
                payload["auth"]["key"] = "ARTIFICIAL_SECRET"
            send(data)
            if mode == "stall-input":

                def stop(_signum, _frame):
                    raise SystemExit(0)

                signal.signal(signal.SIGTERM, stop)
                time.sleep(10)
            continue
        if mode == "wrong-id":
            data["request_id"] = "wrong"
        elif mode == "version":
            payload["helper_version"] = "wrong"
        elif mode == "terminal":
            payload["terminal"] = None
        elif mode == "stop-reason":
            payload["message"]["stopReason"] = "length"
        elif mode == "model":
            payload["message"]["model"] = "wrong"
        elif mode == "api":
            payload["message"]["api"] = "wrong"
        elif mode == "tools":
            payload["message"]["content"].append({"type": "toolCall"})
        elif mode == "submitted":
            payload["submitted"]["maxRetries"] = 1
        elif mode == "confirmed":
            payload["provider_confirmed"]["thinking"] = "invented"
        elif mode == "usage":
            payload["usage_status"] = "unknown"
        elif mode == "request-bytes":
            payload["request_bytes_checked"] = True
        elif mode.startswith("override-"):
            data["type"] = "error"
            payload["code"] = mode.removeprefix("override-")
            payload["terminal"] = {"type": "error", "reason": "error"}
            payload["message"]["stopReason"] = "error"
            payload["retry"].update(retryable=True, classification="library_heuristic")
        elif mode == "retry-spoof":
            data["type"] = "error"
            payload["code"] = "generation_failed"
            payload["retry"].update(retryable=True, classification="library_heuristic")
        elif mode.startswith("answer-"):
            answer = {
                "answer-invalid": "not JSON",
                "answer-nonfinite": '{"id":"unit-1","evidence":NaN}',
                "answer-overflow": '{"id":"unit-1","evidence":1e400}',
                "answer-domain": '{"id":"unknown","evidence":"invented"}',
                "answer-valid": '{"id":"unit-1","evidence":"literal source"}',
            }[mode]
            payload["message"]["content"] = [{"type": "text", "text": answer}]
        if mode in {"truncated", "malformed", "invalid-utf8", "blank"}:
            wire = {
                "truncated": b'{"schema_version":1',
                "malformed": b"{}\n",
                "invalid-utf8": b"\xff\n",
                "blank": b"\n",
            }[mode]
            sys.stdout.buffer.write(wire)
            sys.stdout.buffer.flush()
        else:
            send(data)
            if mode == "extra":
                send(data)
        break
finally:
    child.stdin.close()
    child.wait(timeout=10)
    child.stdout.close()
sys.exit(
    7
    if mode == "exit"
    else 1
    if mode.startswith(("preflight-error-", "override-"))
    else child.returncode
)
