import subprocess
from io import BytesIO
from pathlib import Path

import pytest
from pydantic import ValidationError

from doc_translate.jsonl import JSONLRecord, encode_record, read_records

NODE_PROCESS = Path(__file__).resolve().parents[1] / "ai/tests/jsonl-process.mjs"


def test_jsonl_round_trip_through_external_node_process_keeps_unicode_line_separators():
    records = [
        JSONLRecord(
            schema_version=1,
            request_id="request-1",
            type="metadata",
            payload={"text": "first\u2028second\u2029third\n日本語"},
        ),
        JSONLRecord(
            schema_version=1, request_id="request-2", type="result", payload={}
        ),
    ]
    child = subprocess.Popen(
        ["node", str(NODE_PROCESS), "--byte-chunks"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
    )
    try:
        assert child.stdin is not None and child.stdout is not None
        child.stdin.write(b"".join(encode_record(record) for record in records))
        child.stdin.close()
        assert list(read_records(child.stdout)) == records
        assert child.wait(timeout=5) == 0
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)
        if child.stdout is not None:
            child.stdout.close()


@pytest.mark.parametrize(
    "wire",
    [
        b'{"schema_version":1,"request_id":"r","type":"result","payload":{}}',
        b'{"schema_version":2,"request_id":"r","type":"result","payload":{}}\n',
        b'{"schema_version":1,"type":"result","payload":{}}\n',
        b'{"schema_version":1,"request_id":"r","type":"result","payload":[]}\n',
        b'{"schema_version":true,"request_id":"r","type":"result","payload":{}}\n',
        b'{"schema_version":1,"request_id":"r","type":"result","payload":{"text":"\xff"}}\n',
        b"not json\n",
        b"\n",
        b'{"schema_version":1,"request_id":"r","type":"result","payload":{"cost":NaN}}\n',
    ],
)
def test_jsonl_rejects_incomplete_or_invalid_envelopes(wire):
    with pytest.raises(ValueError):
        list(read_records(BytesIO(wire)))
    child = subprocess.run(
        ["node", str(NODE_PROCESS), "--byte-chunks"],
        input=wire,
        check=False,
        capture_output=True,
        timeout=5,
    )
    assert child.returncode != 0
    assert child.stdout == b""


@pytest.mark.parametrize(
    "number",
    [
        "9007199254740992",
        "-9007199254740992",
        "9007199254740993",
        "-9007199254740993",
        "9007199254740992.0",
        "-9007199254740992.0",
        "9.007199254740992e15",
        "-9.007199254740992e15",
    ],
)
@pytest.mark.parametrize("nested", [False, True])
def test_jsonl_rejects_unsafe_integers_without_returning_a_modified_record(
    number, nested
):
    payload = f'{{"count":{number}}}'
    if nested:
        payload = f'{{"nested":[{payload}]}}'
    wire = (
        f'{{"schema_version":1,"request_id":"r","type":"result","payload":{payload}}}\n'
    ).encode()
    child = subprocess.run(
        ["node", str(NODE_PROCESS), "--byte-chunks"],
        input=wire,
        check=False,
        capture_output=True,
        timeout=5,
    )
    assert child.returncode != 0
    assert child.stdout == b""
    with pytest.raises(ValidationError):
        list(read_records(BytesIO(wire)))


@pytest.mark.parametrize(
    "number",
    [
        9007199254740992,
        -9007199254740992,
        9007199254740993,
        -9007199254740993,
        9007199254740992.0,
        -9007199254740992.0,
    ],
)
@pytest.mark.parametrize("nested", [False, True])
def test_jsonl_rejects_unsafe_integers_before_encoding(number, nested):
    payload = {"nested": [{"count": number}]} if nested else {"count": number}
    with pytest.raises(ValidationError):
        JSONLRecord(schema_version=1, request_id="r", type="result", payload=payload)
    record = JSONLRecord(schema_version=1, request_id="r", type="result", payload={})
    record.payload.update(payload)
    with pytest.raises(ValidationError):
        encode_record(record)


@pytest.mark.parametrize(
    "number",
    [9007199254740991, -9007199254740991, 9007199254740991.0, -9007199254740991.0],
)
def test_jsonl_round_trip_through_external_node_process_preserves_safe_numbers(number):
    record = JSONLRecord(
        schema_version=1,
        request_id="r",
        type="result",
        payload={
            "count": number,
            "nested": [{"count": number}],
            "other": [0, 1.25, -1.25, 5e-324, True, False, None, "9007199254740993"],
        },
    )
    child = subprocess.run(
        ["node", str(NODE_PROCESS), "--byte-chunks"],
        input=encode_record(record),
        check=False,
        capture_output=True,
        timeout=5,
    )
    assert child.returncode == 0
    assert child.stderr == b""
    assert list(read_records(BytesIO(child.stdout))) == [record]


def test_jsonl_rejects_boolean_schema_version():
    with pytest.raises(ValidationError):
        JSONLRecord.model_validate(
            {"schema_version": True, "request_id": "r", "type": "result", "payload": {}}
        )


def test_jsonl_rejects_non_json_numbers_before_encoding():
    with pytest.raises(ValidationError):
        JSONLRecord(
            schema_version=1,
            request_id="r",
            type="result",
            payload={"cost": float("inf")},
        )
