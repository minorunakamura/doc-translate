import { spawn } from "node:child_process";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { expect, it } from "vitest";
import { encodeRecord, readRecords, type JSONLRecord } from "../src/jsonl.js";
import { FileCredentialStore } from "../src/credentials.js";

const fixture = fileURLToPath(
  new URL("./generation-process.mjs", import.meta.url),
);
const config = {
  provider: "faux",
  model: "vision",
  thinking: "off",
  image_input: false,
  options: {},
};
const document = {
  policy: "Translate without execution",
  text: "日本語\u2028A\u2029B",
  context: { chapter: "Context" },
  glossary: { terms: ["term"] },
  images: [],
};

function answer(record: JSONLRecord | undefined): unknown {
  const message = record?.payload.message;
  if (
    !message ||
    typeof message !== "object" ||
    Array.isArray(message) ||
    !Array.isArray(message.content)
  )
    throw new Error("Missing message");
  const part = message.content[0];
  if (
    !part ||
    typeof part !== "object" ||
    Array.isArray(part) ||
    typeof part.text !== "string"
  )
    throw new Error("Missing answer text");
  return JSON.parse(part.text);
}

async function exchange(
  mode = "stop",
  preflight: JSONLRecord["payload"] = config,
  input: JSONLRecord["payload"] = document,
  action = "finish",
) {
  const directory = await mkdtemp(join(tmpdir(), "doc-generation-"));
  const child = spawn(process.execPath, [fixture, directory, mode]);
  const closed = new Promise<number | null>((resolve) =>
    child.on("close", resolve),
  );
  let stderr = "";
  child.stderr.on("data", (data: Buffer) => {
    stderr += data.toString();
  });
  child.stdin.on("error", () => {});
  const request = {
    schema_version: 1 as const,
    request_id: "generation",
    type: "preflight",
    payload: preflight,
  };
  child.stdin.write(encodeRecord(request));
  const records: JSONLRecord[] = [];
  try {
    for await (const record of readRecords(child.stdout)) {
      records.push(record);
      if (record.type === "result" && record.payload.operation === "preflight")
        child.stdin.write(
          encodeRecord({
            ...request,
            request_id: action === "wrong-id" ? "other" : request.request_id,
            type: "generate",
            payload: input,
          }),
        );
      else if (record.type === "fixture-ready") {
        if (action === "eof") child.stdin.end();
        else if (action === "SIGINT" || action === "SIGTERM")
          child.kill(action);
        else if (action === "bad-jsonl") child.stdin.end("{broken\n");
        else if (action === "disconnect") {
          child.stdin.end();
          break;
        }
      } else if (action === "repeat" && record.type === "result") {
        child.stdin.write(
          encodeRecord({ ...request, type: "generate", payload: input }),
        );
      } else child.stdin.end();
    }
    return {
      status: await closed,
      records,
      stderr,
      credential: mode.startsWith("refresh")
        ? await new FileCredentialStore(directory).read("faux")
        : undefined,
    };
  } finally {
    child.kill();
    await closed;
    await rm(directory, { recursive: true, force: true });
  }
}

it.each([
  "error",
  "aborted",
  "length",
  "toolUse",
  "deferred",
  "missing-terminal",
  "wrong-model",
  "toolcall",
  "error-zero",
  "partial-usage",
])("does not adopt a %s response even when result resolves", async (mode) => {
  const result = await exchange(mode);
  expect(result.status).toBe(1);
  expect(result.records.at(-1)?.type).toBe("error");
  expect(result.records.at(-1)?.payload).toMatchObject({
    operation: "generate",
    code:
      mode === "missing-terminal"
        ? "missing_terminal"
        : mode === "wrong-model"
          ? "model_mismatch"
          : mode === "toolcall"
            ? "tool_call"
            : "generation_failed",
  });
  expect(JSON.stringify(result.records)).not.toContain("ARTIFICIAL_SECRET");
  if (mode === "error-zero")
    expect(result.records.at(-1)?.payload).toMatchObject({
      usage: null,
      usage_status: "unknown",
    });
  if (mode === "partial-usage")
    expect(result.records.at(-1)?.payload).toMatchObject({
      usage: { input: 17, totalTokens: 17 },
      usage_status: "observed",
    });
});

it("preflights then generates once with fresh policy/context/glossary and LF-framed Unicode", async () => {
  const result = await exchange();
  expect(result.status).toBe(0);
  expect(result.stderr).toBe("");
  expect(result.records[0].payload).toMatchObject({
    operation: "preflight",
    provider: "faux",
    model: "vision",
    auth: { type: "api_key" },
    image_input: true,
  });
  const final = result.records.at(-1)!;
  expect(final.type).toBe("result");
  expect(final.payload).toMatchObject({
    operation: "generate",
    terminal: { type: "done", reason: "stop" },
    message: { provider: "faux", model: "vision", stopReason: "stop" },
  });
  expect(answer(final)).toMatchObject({
    context: {
      messages: [
        { role: "system", content: document.policy },
        {
          role: "user",
          content: [
            {
              type: "text",
              text: JSON.stringify({
                text: document.text,
                context: document.context,
                glossary: document.glossary,
              }),
            },
          ],
        },
      ],
    },
    options: { reasoning: null, maxRetries: 0, timeoutMs: 1200000 },
    calls: 1,
  });
});

it.each(["SIGINT", "SIGTERM", "eof", "bad-jsonl"])(
  "cancels active generation on %s without accepting another request",
  async (action) => {
    const result = await exchange("timeout", config, document, action);
    expect(result.status).not.toBe(0);
    expect(result.records.at(-1)?.payload.code).toBe("cancelled");
  },
);

it("enforces the single-attempt timeout even when the provider awaits abort", async () => {
  const result = await exchange("timeout", {
    ...config,
    options: { timeoutMs: 50 },
  });
  expect(result.status).toBe(1);
  expect(result.records.at(-1)?.payload).toMatchObject({
    code: "timeout",
    retry: { helper_attempts: 1, maxRetries: 0 },
  });
});

it.each(["finish", "SIGTERM"])(
  "retains delayed abort terminal and usage on %s without retry or success",
  async (action) => {
    const result = await exchange(
      "timeout",
      { ...config, options: { timeoutMs: 50 } },
      document,
      action,
    );
    expect(result.status).toBe(1);
    expect(result.records.at(-1)?.type).toBe("error");
    expect(result.records.at(-1)?.payload).toMatchObject({
      code: action === "finish" ? "timeout" : "cancelled",
      terminal: { type: "error", reason: "aborted" },
      message: { stopReason: "aborted" },
      usage: { input: 17, totalTokens: 17 },
      usage_status: "observed",
      retry: { helper_attempts: 1, maxRetries: 0, retryable: false },
    });
    expect(JSON.stringify(result.records)).not.toContain("ARTIFICIAL_SECRET");
  },
);

it("bounds an abort-ignoring provider by the 30-second grace", async () => {
  const started = performance.now();
  const result = await exchange("noncooperative", {
    ...config,
    options: { timeoutMs: 50 },
  });
  const elapsed = performance.now() - started;
  expect(elapsed).toBeGreaterThanOrEqual(30_000);
  expect(elapsed).toBeLessThan(40_000);
  expect(result.status).toBe(1);
  expect(result.records.at(-1)?.payload).toMatchObject({
    code: "timeout",
    terminal: null,
    message: null,
    usage: null,
    usage_status: "unknown",
    retry: { helper_attempts: 1, retryable: false },
  });
}, 45_000);

it.each(["refresh", "refresh-failure"])(
  "drains %s before returning cancellation",
  async (mode) => {
    const result = await exchange(mode, config, document, "SIGTERM");
    expect(result.status).toBe(1);
    expect(result.records.at(-1)?.payload.code).toBe(
      mode === "refresh" ? "cancelled" : "auth_failed",
    );
    expect(result.credential).toMatchObject({
      access: mode === "refresh" ? "ARTIFICIAL_ROTATED" : "ARTIFICIAL_OLD",
    });
    expect(JSON.stringify(result.records)).not.toContain("ARTIFICIAL_");
  },
);

it.each<JSONLRecord["payload"]>([
  { ...config, provider: "missing" },
  { ...config, model: "missing" },
  { ...config, thinking: "max" },
  { ...config, model: "text", image_input: true },
  { ...config, options: { headers: { authorization: "ARTIFICIAL_SECRET" } } },
  { ...config, options: { maxRetries: 1 } },
])(
  "rejects unsupported preflight before document send: %j",
  async (preflight) => {
    const result = await exchange("stop", preflight);
    expect(result.status).toBe(2);
    expect(result.records).toHaveLength(1);
    expect(result.records[0].type).toBe("error");
    expect(JSON.stringify(result.records)).not.toContain("ARTIFICIAL_SECRET");
  },
);

it("retains explicit reasoning, budgets, output and transport options at the public provider", async () => {
  const result = await exchange("stop", {
    ...config,
    thinking: "high",
    options: {
      thinkingBudgets: { high: 64 },
      maxTokens: 256,
      temperature: 0,
      transport: "sse",
      cacheRetention: "none",
    },
  });
  expect(result.status).toBe(0);
  expect(answer(result.records.at(-1))).toMatchObject({
    options: {
      reasoning: "high",
      thinkingBudgets: { high: 64 },
      maxTokens: 256,
      temperature: 0,
      transport: "sse",
      maxRetries: 0,
    },
  });
  expect(result.records.at(-1)?.payload.provider_confirmed).toEqual({
    thinking: null,
  });
});

// Two artificial valid PNGs (1×1); Python owns full decoding/hash validation.
const png =
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4//8/AAX+Av4N70a4AAAAAElFTkSuQmCC";
const secondPng =
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGNgYGAAAAAEAAH2FzhVAAAAAElFTkSuQmCC";
it("preserves PNG bytes, MIME, order, dimensions and coordinate explanations", async () => {
  const result = await exchange(
    "stop",
    { ...config, image_input: true },
    {
      ...document,
      images: [
        {
          data: png,
          mimeType: "image/png",
          width: 1,
          height: 1,
          coordinates: "page 2; top-left pixels",
        },
        {
          data: secondPng,
          mimeType: "image/png",
          width: 1,
          height: 1,
          coordinates: "page 1; bottom-left pixels",
        },
      ],
    },
  );
  expect(result.status).toBe(0);
  expect(answer(result.records.at(-1))).toMatchObject({
    context: {
      messages: [
        expect.anything(),
        {
          content: [
            {
              type: "text",
              text: JSON.stringify({
                text: document.text,
                context: document.context,
                glossary: document.glossary,
              }),
            },
            {
              type: "text",
              text: '{"width":1,"height":1,"coordinates":"page 2; top-left pixels"}',
            },
            { type: "image", data: png, mimeType: "image/png" },
            {
              type: "text",
              text: '{"width":1,"height":1,"coordinates":"page 1; bottom-left pixels"}',
            },
            { type: "image", data: secondPng, mimeType: "image/png" },
          ],
        },
      ],
    },
  });
});

it("classifies OAuth refresh failure before any generation send without fallback", async () => {
  const result = await exchange("refresh-failure");
  expect(result.status).toBe(1);
  expect(result.records.at(-1)?.payload.code).toBe("auth_failed");
  expect(result.records.at(-1)?.payload).toMatchObject({
    terminal: null,
    message: null,
    retry: { observed_responses: 0 },
  });
  expect(JSON.stringify(result.records)).not.toContain("ARTIFICIAL_");
});

it("propagates maxRetries:0 to the real HTTP adapter and records positively retryable status only", async () => {
  const result = await exchange("http-retry");
  expect(result.status).toBe(1);
  expect(result.records.at(-1)?.payload).toMatchObject({
    code: "generation_failed",
    usage_status: "unknown",
    retry: {
      maxRetries: 0,
      observed_responses: 0,
      retryable: true,
      classification: "library_heuristic",
    },
  });
  expect(JSON.stringify(result.records)).not.toContain(
    "ARTIFICIAL_SECRET_HTTP",
  );
  expect(JSON.stringify(result.records)).not.toContain("ARTIFICIAL_API_KEY");
});

it("retains policy and explicit off through the built-in HTTP adapter payload", async () => {
  const result = await exchange("http-stop", {
    ...config,
    options: { maxTokens: 256, temperature: 0 },
  });
  expect(result.status).toBe(0);
  expect(answer(result.records.at(-1))).toMatchObject({
    body: {
      model: "vision",
      messages: [
        { role: "developer", content: document.policy },
        {
          role: "user",
          content: [
            {
              type: "text",
              text: JSON.stringify({
                text: document.text,
                context: document.context,
                glossary: document.glossary,
              }),
            },
          ],
        },
      ],
      max_completion_tokens: 256,
      temperature: 0,
      reasoning_effort: "none",
    },
    sends: 1,
  });
});

it("rejects an assembled HTTP payload above model maxRequestBytes before fetch", async () => {
  const result = await exchange("http-limit");
  expect(result.status).toBe(2);
  expect(result.records.at(-1)?.payload).toMatchObject({
    code: "input_limit",
    retry: { observed_responses: 0 },
  });
});

it.each(["quota", "billing", "provider-auth", "aborted"])(
  "does not recommend retry for %s",
  async (mode) => {
    const result = await exchange(mode);
    expect(result.records.at(-1)?.payload).toMatchObject({
      retry: { retryable: false },
    });
  },
);

it.each([
  {
    data: png,
    mimeType: "image/jpeg",
    width: 1,
    height: 1,
    coordinates: "top-left",
  },
  {
    data: png,
    mimeType: "image/png",
    width: 2,
    height: 1,
    coordinates: "top-left",
  },
  {
    data: "not-base64",
    mimeType: "image/png",
    width: 1,
    height: 1,
    coordinates: "top-left",
  },
])("rejects wire image drift without send: %j", async (image) => {
  const result = await exchange(
    "stop",
    { ...config, image_input: true },
    { ...document, images: [image] },
  );
  expect(result.status).toBe(2);
  expect(result.records.at(-1)?.payload.code).toBe("invalid_image");
});

it("exposes input limits in preflight and refuses excess images instead of dropping them", async () => {
  const image = {
    data: png,
    mimeType: "image/png",
    width: 1,
    height: 1,
    coordinates: "top-left",
  };
  const result = await exchange(
    "image-limit",
    { ...config, image_input: true },
    { ...document, images: [image, image] },
  );
  expect(result.records[0].payload.input_limits).toMatchObject({
    images: {
      maxPerMessage: 1,
      maxPerRequest: 1,
      resize: { maxWidth: 1, maxHeight: 1, maxBytes: 100 },
    },
  });
  expect(result.status).toBe(2);
  expect(result.records.at(-1)?.payload.code).toBe("input_limit");
});

it.each([
  ["image-limit", 2],
  ["image-limit-equal", 0],
] as const)("checks base64 image bytes against %s", async (mode, status) => {
  // Valid 1×1 RGB PNG with a text chunk: 88 raw bytes, 120 base64 bytes.
  const data =
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAAB3RFWHRhAGhlbGxvYYw2twAAAAxJREFUeJxj+M/AAAADAQEAyf6S7wAAAABJRU5ErkJggg==";
  const result = await exchange(
    mode,
    { ...config, image_input: true },
    {
      ...document,
      images: [
        {
          data,
          mimeType: "image/png",
          width: 1,
          height: 1,
          coordinates: "top-left",
        },
      ],
    },
  );
  expect(result.status).toBe(status);
  if (status === 2)
    expect(result.records.at(-1)?.payload.code).toBe("input_limit");
  else
    expect(answer(result.records.at(-1))).toMatchObject({
      context: {
        messages: [
          expect.anything(),
          {
            content: [
              expect.anything(),
              expect.anything(),
              { type: "image", data, mimeType: "image/png" },
            ],
          },
        ],
      },
      calls: 1,
    });
});

it("includes generation auth recheck in the timeout, not only the provider stream", async () => {
  const result = await exchange("preflight-timeout", {
    ...config,
    options: { timeoutMs: 50 },
  });
  expect(result.records.at(-1)?.payload.code).toBe("timeout");
});

it("cancels generation on parent pipe disconnect without leaking EPIPE errors", async () => {
  const result = await exchange("timeout", config, document, "disconnect");
  expect(result.status).toBe(1);
  expect(result.stderr).toBe("");
});

it.each(["wrong-id", "repeat"])(
  "refuses %s instead of sending another generation",
  async (action) => {
    const result = await exchange("stop", config, document, action);
    expect(result.status).toBe(2);
    expect(result.records.at(-1)?.payload.code).toBe("invalid_request");
    if (action === "repeat")
      expect(answer(result.records[1])).toMatchObject({ calls: 1 });
  },
);
