import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { encodeRecord, type JSONLRecord } from "../src/jsonl.js";

const helper = fileURLToPath(new URL("../dist/main.js", import.meta.url));
const guard = fileURLToPath(new URL("./no-network.mjs", import.meta.url));

function request(type: string, payload: JSONLRecord["payload"] = {}) {
  return spawnSync(process.execPath, ["--import", guard, helper], {
    input: encodeRecord({
      schema_version: 1,
      request_id: "catalog",
      type,
      payload,
    }),
    encoding: "utf8",
  });
}

describe("offline metadata helper protocol", () => {
  it("lists built-in providers without network access or credentials", () => {
    const result = request("providers");
    expect(result.status).toBe(0);
    expect(result.stderr).toBe("");
    const response = JSON.parse(result.stdout);
    expect(response).toMatchObject({
      schema_version: 1,
      request_id: "catalog",
      type: "result",
    });
    expect(response.payload.providers).toEqual(
      expect.arrayContaining([
        {
          id: "openai",
          name: expect.any(String),
          login_methods: ["api_key", "oauth"],
        },
        {
          id: "anthropic",
          name: expect.any(String),
          login_methods: ["api_key", "oauth"],
        },
        { id: "google", name: expect.any(String), login_methods: ["api_key"] },
      ]),
    );
    expect(response.payload.library_version).toBe("1.4.0");
  });

  it("projects only non-secret chat model capabilities and supported thinking levels", () => {
    const result = request("models", { provider: "anthropic" });
    expect(result.status).toBe(0);
    const response = JSON.parse(result.stdout);
    expect(response.payload.models).toEqual(
      expect.arrayContaining([
        {
          id: "claude-fable-5",
          name: expect.any(String),
          image_input: true,
          reasoning: true,
          thinking_levels: ["minimal", "low", "medium", "high", "xhigh", "max"],
        },
        {
          id: "claude-haiku-4-5",
          name: expect.any(String),
          image_input: true,
          reasoning: true,
          thinking_levels: ["off", "minimal", "low", "medium", "high"],
        },
      ]),
    );
    for (const model of response.payload.models) {
      expect(Object.keys(model).toSorted()).toEqual([
        "id",
        "image_input",
        "name",
        "reasoning",
        "thinking_levels",
      ]);
    }
    const openai = JSON.parse(request("models", { provider: "openai" }).stdout);
    expect(openai.payload.models).toEqual(
      expect.arrayContaining([
        {
          id: "gpt-4",
          name: expect.any(String),
          image_input: false,
          reasoning: false,
          thinking_levels: ["off"],
        },
        {
          id: "gpt-4o",
          name: expect.any(String),
          image_input: true,
          reasoning: false,
          thinking_levels: ["off"],
        },
      ]),
    );
  });

  it.each([
    ["models", { provider: "unknown" }, "unknown_provider"],
    ["providers", { refresh: true }, "invalid_request"],
    ["models", {}, "invalid_request"],
    ["login", { provider: "openai" }, "invalid_request"],
  ])("rejects unsupported metadata requests: %s %j", (type, payload, code) => {
    const result = request(type, payload);
    expect(result.status).toBe(2);
    expect(JSON.parse(result.stdout)).toEqual({
      schema_version: 1,
      request_id: "catalog",
      type: "error",
      payload: { code },
    });
  });

  it("rejects an incomplete LF frame rather than replying successfully", () => {
    const result = spawnSync(process.execPath, ["--import", guard, helper], {
      input:
        '{"schema_version":1,"request_id":"r","type":"providers","payload":{}}',
      encoding: "utf8",
    });
    expect(result.status).toBe(2);
    expect(result.stdout).toBe("");
    expect(result.stderr).toBe("Invalid metadata JSONL input\n");
  });
});
