export type JSONValue =
  | null
  | boolean
  | number
  | string
  | JSONValue[]
  | { [key: string]: JSONValue };

export interface JSONLRecord {
  schema_version: 1;
  request_id: string;
  type: string;
  payload: { [key: string]: JSONValue };
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isJSONValue(value: unknown): value is JSONValue {
  if (value === null || typeof value === "string" || typeof value === "boolean")
    return true;
  if (typeof value === "number")
    return (
      Number.isFinite(value) &&
      (!Number.isInteger(value) || Number.isSafeInteger(value))
    );
  if (Array.isArray(value)) return value.every(isJSONValue);
  return isObject(value) && Object.values(value).every(isJSONValue);
}

function validateRecord(value: unknown): asserts value is JSONLRecord {
  if (
    !isObject(value) ||
    Object.keys(value).length !== 4 ||
    value.schema_version !== 1 ||
    typeof value.request_id !== "string" ||
    value.request_id.length === 0 ||
    typeof value.type !== "string" ||
    value.type.length === 0 ||
    !isObject(value.payload) ||
    !isJSONValue(value.payload)
  ) {
    throw new TypeError("Invalid JSONL envelope");
  }
}

/** Encode one UTF-8 envelope, including its final LF. */
export function encodeRecord(record: JSONLRecord): Buffer {
  validateRecord(record);
  return Buffer.from(`${JSON.stringify(record)}\n`, "utf8");
}

/** Consume arbitrary binary chunks; only LF (0x0A) terminates a record. */
export async function* readRecords(
  stream: AsyncIterable<Uint8Array> | Iterable<Uint8Array>,
): AsyncGenerator<JSONLRecord> {
  const decoder = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true });
  let pending = Buffer.alloc(0);
  for await (const chunk of stream) {
    pending = Buffer.concat([pending, chunk]);
    let end: number;
    while ((end = pending.indexOf(10)) !== -1) {
      const record: unknown = JSON.parse(
        decoder.decode(pending.subarray(0, end)),
      );
      validateRecord(record);
      yield record;
      pending = pending.subarray(end + 1);
    }
  }
  if (pending.length > 0) {
    throw new Error("Incomplete JSONL record: missing final LF");
  }
}
