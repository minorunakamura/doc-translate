import { spawn } from "node:child_process";
import { mkdtemp, rm, stat } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { expect, it } from "vitest";
import { FileCredentialStore } from "../src/credentials.js";
import { encodeRecord, readRecords, type JSONLRecord } from "../src/jsonl.js";

const fixture = fileURLToPath(new URL("./auth-process.mjs", import.meta.url));

async function exchange(
  directory: string,
  mode = "secret",
  payload: JSONLRecord["payload"] = { provider: "faux", method: "api_key" },
  action = "respond",
  operation = "login",
) {
  const child = spawn(process.execPath, [fixture, directory, mode]);
  const closed = new Promise<number | null>((resolve) =>
    child.on("close", resolve),
  );
  let stderr = "";
  child.stderr.on("data", (data: Buffer) => {
    stderr += data.toString();
  });
  child.stdin.on("error", () => {});
  child.stdin.write(
    encodeRecord({
      schema_version: 1,
      request_id: "auth",
      type: operation,
      payload,
    }),
  );
  const records: JSONLRecord[] = [];
  for await (const record of readRecords(child.stdout)) {
    records.push(record);
    if (
      record.type === "notify" &&
      (mode === "saving" || mode === "saving-failure")
    )
      child.kill("SIGTERM");
    if (record.type === "prompt") {
      if (action === "eof") child.stdin.end();
      else if (action === "SIGINT" || action === "SIGTERM") child.kill(action);
      else if (action === "disconnect") {
        child.stdin.end();
        break;
      } else if (mode !== "callback")
        child.stdin.write(
          encodeRecord({
            ...record,
            type: "response",
            payload: {
              prompt_id: record.payload.prompt_id,
              value: mode === "select" ? "one" : "ARTIFICIAL_KEY",
            },
          }),
        );
    }
    if (record.type === "result" || record.type === "error") {
      if (action === "late-response")
        child.stdin.write(
          encodeRecord({
            ...record,
            type: "response",
            payload: { prompt_id: "1", value: "ARTIFICIAL_LATE_CODE" },
          }),
        );
      child.stdin.end();
    }
  }
  return { status: await closed, records, stderr };
}

it.each(["secret", "text", "select"])(
  "logs in through the helper/public provider using %s without returning credentials",
  async (mode) => {
    const directory = await mkdtemp(join(tmpdir(), "doc-auth-"));
    try {
      const { status, records, stderr } = await exchange(directory, mode);
      expect(status).toBe(0);
      expect(stderr).toBe("");
      expect(records.at(-1)).toEqual({
        schema_version: 1,
        request_id: "auth",
        type: "result",
        payload: { provider: "faux", method: "api_key", operation: "login" },
      });
      expect(JSON.stringify(records)).not.toContain("ARTIFICIAL_KEY");
      expect(await new FileCredentialStore(directory).read("faux")).toEqual({
        type: "api_key",
        key: "ARTIFICIAL_KEY",
      });
      await expect(stat(join(directory, "installation-id"))).rejects.toThrow();
    } finally {
      await rm(directory, { recursive: true, force: true });
    }
  },
);

it("cancels only the manual prompt when a callback wins and wires stable OpenAI device ID/agentName", async () => {
  const directory = await mkdtemp(join(tmpdir(), "doc-auth-"));
  try {
    const payload = { provider: "openai", method: "oauth" };
    const first = await exchange(directory, "callback", payload);
    expect(first.status).toBe(0);
    expect(first.records.map((r) => r.type)).toEqual([
      "prompt",
      "prompt_cancel",
      "result",
    ]);
    const store = new FileCredentialStore(directory);
    const id = await store.getDeviceId();
    expect(await store.read("openai")).toMatchObject({
      type: "oauth",
      access: "ARTIFICIAL_ACCESS",
    });
    expect((await exchange(directory, "callback", payload)).status).toBe(0);
    expect(await store.getDeviceId()).toBe(id);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

it.each(["eof", "SIGINT", "SIGTERM", "disconnect"])(
  "aborts the whole authentication on %s without saving",
  async (action) => {
    const directory = await mkdtemp(join(tmpdir(), "doc-auth-"));
    try {
      const result = await exchange(directory, "secret", undefined, action);
      expect(result.status).toBe(1);
      expect(result.stderr).toBe("");
      expect(
        await new FileCredentialStore(directory).read("faux"),
      ).toBeUndefined();
      if (action !== "disconnect")
        expect(result.records.at(-1)).toMatchObject({
          type: "error",
          payload: { code: "cancelled" },
        });
    } finally {
      await rm(directory, { recursive: true, force: true });
    }
  },
);

it.each(["error", "drain"])(
  "does not expose provider errors or turn %s failure into success",
  async (mode) => {
    const directory = await mkdtemp(join(tmpdir(), "doc-auth-"));
    try {
      const result = await exchange(directory, mode);
      expect(result.status).toBe(1);
      expect(result.records.at(-1)).toMatchObject({
        type: "error",
        payload: { code: mode === "drain" ? "storage_failed" : "auth_failed" },
      });
      expect(JSON.stringify(result)).not.toContain("ARTIFICIAL_SECRET_ERROR");
    } finally {
      await rm(directory, { recursive: true, force: true });
    }
  },
);

it.each([
  [{ provider: "unknown", method: "oauth" }, "unknown_provider"],
  [{ provider: "ambient", method: "api_key" }, "unsupported_method"],
  [{ provider: "faux", method: "other" }, "unsupported_method"],
])("rejects impossible login %j", async (payload, code) => {
  const directory = await mkdtemp(join(tmpdir(), "doc-auth-"));
  try {
    const result = await exchange(directory, "secret", payload);
    expect(result.status).toBe(2);
    expect(result.records).toEqual([
      {
        schema_version: 1,
        request_id: "auth",
        type: "error",
        payload: { code },
      },
    ]);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

it("logs out only the selected provider through the real helper process", async () => {
  const directory = await mkdtemp(join(tmpdir(), "doc-auth-"));
  try {
    const store = new FileCredentialStore(directory);
    await store.modify("faux", async () => ({
      type: "api_key",
      key: "DELETE",
    }));
    await store.modify("openai", async () => ({
      type: "api_key",
      key: "KEEP",
    }));
    const result = await exchange(
      directory,
      "secret",
      { provider: "faux" },
      "respond",
      "logout",
    );
    expect(result.status).toBe(0);
    expect(result.records.at(-1)).toMatchObject({
      type: "result",
      payload: { operation: "logout", provider: "faux" },
    });
    expect(await store.read("faux")).toBeUndefined();
    expect(await store.read("openai")).toEqual({
      type: "api_key",
      key: "KEEP",
    });
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

it.each(["saving", "saving-failure"])(
  "drains in-progress storage on signal, including %s errors",
  async (mode) => {
    const directory = await mkdtemp(join(tmpdir(), "doc-auth-"));
    try {
      const result = await exchange(directory, mode);
      expect(result.status).toBe(1);
      expect(result.records.at(-1)).toMatchObject({
        type: "error",
        payload: { code: mode === "saving" ? "cancelled" : "storage_failed" },
      });
      expect(
        await new FileCredentialStore(directory).read("background"),
      ).toEqual(
        mode === "saving"
          ? { type: "api_key", key: "SAVED_AFTER_CANCEL" }
          : undefined,
      );
      expect(JSON.stringify(result)).not.toContain("ARTIFICIAL_SECRET_ERROR");
    } finally {
      await rm(directory, { recursive: true, force: true });
    }
  },
);

it("lists multiple provider login methods but never advertises an ambient-only login", async () => {
  const directory = await mkdtemp(join(tmpdir(), "doc-auth-"));
  try {
    const result = await exchange(
      directory,
      "secret",
      {},
      "respond",
      "providers",
    );
    expect(result.status).toBe(0);
    expect(result.records[0]?.payload.providers).toEqual([
      { id: "faux", name: "faux", login_methods: ["api_key", "oauth"] },
      { id: "openai", name: "openai", login_methods: ["api_key", "oauth"] },
      { id: "ambient", name: "Ambient", login_methods: [] },
    ]);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

it("ignores a manual-code response already in flight when the callback wins", async () => {
  const directory = await mkdtemp(join(tmpdir(), "doc-auth-"));
  try {
    const result = await exchange(
      directory,
      "callback",
      { provider: "openai", method: "oauth" },
      "late-response",
    );
    expect(result.status).toBe(0);
    expect(JSON.stringify(result)).not.toContain("ARTIFICIAL_LATE_CODE");
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

it("does not cancel a terminal prompt that was already aborted before it was shown", async () => {
  const directory = await mkdtemp(join(tmpdir(), "doc-auth-"));
  try {
    const result = await exchange(directory, "already-cancelled", {
      provider: "openai",
      method: "oauth",
    });
    expect(result.status).toBe(0);
    expect(result.records.map((record) => record.type)).toEqual(["result"]);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});
