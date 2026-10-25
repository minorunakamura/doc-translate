"""UTF-8, LF-framed transport envelopes, separate from model answer JSON.

Operation-specific record types and payload validation belong to the helper;
this codec neither dispatches operations nor accepts a generation as successful.
"""

import json
from collections.abc import Iterable, Iterator
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator


class JSONLRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    schema_version: Literal[1]
    request_id: Annotated[str, Field(min_length=1)]
    type: Annotated[str, Field(min_length=1)]
    payload: dict[str, JsonValue]

    @field_validator("schema_version", mode="before")
    @classmethod
    def reject_boolean_version(cls, value: object) -> object:
        if value is True or value is False:
            raise ValueError("schema_version must be a JSON number, not a boolean")
        return value

    @field_validator("payload")
    @classmethod
    def reject_unsafe_integers(
        cls, value: dict[str, JsonValue]
    ) -> dict[str, JsonValue]:
        pending = list(value.values())
        while pending:
            item = pending.pop()
            if isinstance(item, dict):
                pending.extend(item.values())
            elif isinstance(item, list):
                pending.extend(item)
            elif (
                isinstance(item, int) or (isinstance(item, float) and item.is_integer())
            ) and not -9007199254740991 <= item <= 9007199254740991:
                raise ValueError(
                    "JSONL payload integers must be JavaScript safe integers"
                )
        return value


def encode_record(record: JSONLRecord) -> bytes:
    """Encode exactly one complete LF-terminated envelope."""
    record = JSONLRecord.model_validate(record.model_dump(mode="json"))
    return record.model_dump_json().encode("utf-8") + b"\n"


def read_records(stream: Iterable[bytes]) -> Iterator[JSONLRecord]:
    """Consume LF records continuously; reject incomplete records at EOF."""
    for line in stream:
        if not line.endswith(b"\n"):
            raise ValueError("Incomplete JSONL record: missing final LF")
        yield JSONLRecord.model_validate(json.loads(line.decode("utf-8")))
