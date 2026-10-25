import {
  chmod,
  mkdir,
  mkdtemp,
  readFile,
  readdir,
  rm,
  stat,
  symlink,
  writeFile,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { execFileSync, spawn } from "node:child_process";
import filesystem from "node:fs/promises";
import { syncBuiltinESMExports } from "node:module";
import { fileURLToPath } from "node:url";
import {
  createModels,
  createProvider,
  type ApiKeyAuth,
  type OAuthCredential,
} from "@earendil-works/pi-ai";
import { afterEach, beforeAll, expect, test, vi } from "vitest";
import { FileCredentialStore } from "../src/credentials.js";

const directories: string[] = [];
async function directory() {
  const path = await mkdtemp(join(tmpdir(), "doc-translate-credentials-"));
  directories.push(path);
  return join(path, "config");
}
afterEach(async () => {
  await Promise.all(
    directories
      .splice(0)
      .map((path) => rm(path, { recursive: true, force: true })),
  );
});

test("FileCredentialStore persists a provider credential across instances", async () => {
  const path = await directory();
  const store = new FileCredentialStore(path);
  expect(await store.read("example")).toBeUndefined();
  const credential = { type: "api_key" as const, key: "artificial-key" };
  expect(await store.modify("example", async () => credential)).toEqual(
    credential,
  );
  expect(await new FileCredentialStore(path).read("example")).toEqual(
    credential,
  );
});

test("FileCredentialStore lists only metadata and preserves OAuth fields on update and undefined", async () => {
  const store = new FileCredentialStore(await directory());
  const oauth = {
    type: "oauth" as const,
    refresh: "artificial-refresh",
    access: "artificial-access",
    expires: 10,
    clientId: "client",
    scopes: ["scope"],
    nested: { value: 1 },
  };
  await store.modify("example", async () => oauth);
  expect(await store.list()).toEqual([
    { providerId: "example", type: "oauth" },
  ]);
  expect(await store.modify("example", async () => undefined)).toEqual(oauth);
  expect(
    await store.modify("example", async (current) => {
      if (current?.type !== "oauth")
        throw new Error("Expected OAuth credential");
      return { ...current, access: "rotated" };
    }),
  ).toEqual({ ...oauth, access: "rotated" });
  await store.delete("example");
  await store.delete("missing");
  expect(await store.read("example")).toBeUndefined();
  expect(await store.list()).toEqual([]);
});

test("FileCredentialStore provider IDs are literal map keys, including prototype names", async () => {
  const store = new FileCredentialStore(await directory());
  expect(await store.read("constructor")).toBeUndefined();
  await store.modify("__proto__", async () => ({
    type: "api_key",
    key: "artificial",
  }));
  expect(await store.read("__proto__")).toEqual({
    type: "api_key",
    key: "artificial",
  });
});

test("FileCredentialStore serializes whole-file updates across instances and atomic replacements", async () => {
  const path = await directory();
  const stores = [new FileCredentialStore(path), new FileCredentialStore(path)];
  const update = async (round: number) => {
    await Promise.all(
      stores.map((store, index) =>
        store.modify(`provider-${index}`, async () => {
          await new Promise((resolve) => setTimeout(resolve, 40));
          return { type: "api_key", key: `artificial-${round}-${index}` };
        }),
      ),
    );
    expect(await stores[0].read("provider-0")).toEqual({
      type: "api_key",
      key: `artificial-${round}-0`,
    });
    expect(await stores[1].read("provider-1")).toEqual({
      type: "api_key",
      key: `artificial-${round}-1`,
    });
  };
  await update(0);
  await update(1);
  await update(2);
});

test("FileCredentialStore serializes logout after an in-progress refresh", async () => {
  const path = await directory();
  const store = new FileCredentialStore(path);
  await store.modify("example", async () => ({ type: "api_key", key: "old" }));
  const started = Promise.withResolvers<void>();
  const refresh = store.modify("example", async () => {
    started.resolve();
    await new Promise((resolve) => setTimeout(resolve, 100));
    return { type: "api_key", key: "rotated" };
  });
  await started.promise;
  const logout = new FileCredentialStore(path).delete("example");
  await Promise.all([refresh, logout]);
  expect(await store.read("example")).toBeUndefined();
});

test("FileCredentialStore cancels lock waiting without running its callback", async () => {
  const path = await directory();
  const store = new FileCredentialStore(path);
  const started = Promise.withResolvers<void>();
  const finish = Promise.withResolvers<void>();
  const holder = store.modify("one", async () => {
    started.resolve();
    await finish.promise;
    return { type: "api_key", key: "artificial" };
  });
  await started.promise;
  const controller = new AbortController();
  let called = false;
  const waiting = new FileCredentialStore(path).modify(
    "two",
    async () => {
      called = true;
      return undefined;
    },
    { signal: controller.signal },
  );
  controller.abort("artificial-secret-reason");
  try {
    await expect(waiting).rejects.toMatchObject({ name: "AbortError" });
    expect(called).toBe(false);
  } finally {
    finish.resolve();
    await holder;
  }
});

test("FileCredentialStore rejects an already cancelled operation even with a free lock", async () => {
  const store = new FileCredentialStore(await directory());
  const signal = AbortSignal.abort("artificial-secret-reason");
  await expect(
    store.modify("example", async () => ({ type: "api_key", key: "no" }), {
      signal,
    }),
  ).rejects.toMatchObject({ name: "AbortError" });
});

test("FileCredentialStore drains an active callback and persists rotated credentials despite caller cancellation", async () => {
  const path = await directory();
  const store = new FileCredentialStore(path);
  const started = Promise.withResolvers<void>();
  const finish = Promise.withResolvers<void>();
  const controller = new AbortController();
  const modifying = store.modify(
    "example",
    async () => {
      started.resolve();
      await finish.promise;
      return {
        type: "oauth",
        access: "rotated",
        refresh: "rotated-refresh",
        expires: 123,
      };
    },
    { signal: controller.signal },
  );
  await started.promise;
  controller.abort();
  let drained = false;
  const draining = store.drain().then(() => {
    drained = true;
  });
  await new Promise((resolve) => setTimeout(resolve, 20));
  expect(drained).toBe(false);
  finish.resolve();
  await Promise.all([modifying, draining]);
  expect(await new FileCredentialStore(path).read("example")).toMatchObject({
    access: "rotated",
    refresh: "rotated-refresh",
  });
});

test.each([
  '{"secret":"artificial-secret",',
  "null",
  "[]",
  '{"example":{"type":"oauth","refresh":"artificial-secret"}}',
  '{"example":{"type":"api_key","key":123}}',
])(
  "FileCredentialStore rejects corrupt storage without exposing its contents: %s",
  async (contents) => {
    const path = await directory();
    await mkdir(path, { mode: 0o700 });
    await writeFile(join(path, "credentials.json"), contents, { mode: 0o600 });
    const store = new FileCredentialStore(path);
    await Promise.all(
      [
        () => store.read("example"),
        () => store.list(),
        () =>
          store.modify("example", async () => ({
            type: "api_key",
            key: "overwrite",
          })),
        () => store.delete("example"),
      ].map(async (operation) => {
        const error = await operation().then(
          () => undefined,
          (reason: unknown) => reason,
        );
        expect(error).toBeInstanceOf(Error);
        expect(String(error)).not.toContain("artificial-secret");
        expect(await readFile(join(path, "credentials.json"), "utf8")).toBe(
          contents,
        );
      }),
    );
  },
);

test("FileCredentialStore save failure leaves old data intact, rejects drain, and cleans temporary files", async () => {
  const path = await directory();
  const store = new FileCredentialStore(path);
  await store.modify("example", async () => ({
    type: "api_key",
    key: "old-artificial",
  }));
  const before = await readFile(join(path, "credentials.json"), "utf8");
  const started = Promise.withResolvers<void>();
  const finish = Promise.withResolvers<void>();
  const operation = store.modify("example", async () => {
    started.resolve();
    await finish.promise;
    await chmod(path, 0o500);
    return { type: "api_key", key: "new-artificial" };
  });
  await started.promise;
  const drain = store.drain();
  const rejected = Promise.all([
    expect(operation).rejects.toThrow("Credential storage"),
    expect(drain).rejects.toThrow("Credential storage"),
  ]);
  finish.resolve();
  try {
    await rejected;
  } finally {
    await chmod(path, 0o700);
  }
  expect(await readFile(join(path, "credentials.json"), "utf8")).toBe(before);
  expect((await readdir(path)).filter((name) => name.endsWith(".tmp"))).toEqual(
    [],
  );
});

test("FileCredentialStore uses private directory and file permissions on first use and replacement", async () => {
  const path = await directory();
  const store = new FileCredentialStore(path);
  const update = async () => {
    await store.modify("example", async () => ({
      type: "api_key",
      key: "artificial",
    }));
    expect((await stat(path)).mode & 0o777).toBe(0o700);
    expect((await stat(join(path, "credentials.json"))).mode & 0o777).toBe(
      0o600,
    );
  };
  await update();
  await update();
});

test("FileCredentialStore rejects a credential symlink rather than reading another auth file", async () => {
  const path = await directory();
  await mkdir(path, { mode: 0o700 });
  const foreign = join(path, "foreign.json");
  await writeFile(
    foreign,
    '{"example":{"type":"api_key","key":"foreign-secret"}}',
    { mode: 0o600 },
  );
  await symlink(foreign, join(path, "credentials.json"));
  await expect(new FileCredentialStore(path).read("example")).rejects.toThrow(
    "Credential storage",
  );
  expect(await readFile(foreign, "utf8")).toContain("foreign-secret");
});

test("FileCredentialStore reuses one private installation UUID across instances and concurrent first use", async () => {
  const path = await directory();
  const stores = Array.from({ length: 4 }, () => new FileCredentialStore(path));
  const ids = await Promise.all(stores.map((store) => store.getDeviceId()));
  expect(ids[0]).toMatch(
    /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
  );
  expect(new Set(ids).size).toBe(1);
  expect(await new FileCredentialStore(path).getDeviceId()).toBe(ids[0]);
  expect((await stat(join(path, "installation-id"))).mode & 0o777).toBe(0o600);
});

test("FileCredentialStore does not replace a corrupt installation UUID", async () => {
  const path = await directory();
  await mkdir(path, { mode: 0o700 });
  await writeFile(
    join(path, "installation-id"),
    "artificial-secret-invalid-id",
    { mode: 0o600 },
  );
  await expect(new FileCredentialStore(path).getDeviceId()).rejects.toThrow(
    "Credential storage",
  );
  expect(await readFile(join(path, "installation-id"), "utf8")).toBe(
    "artificial-secret-invalid-id",
  );
});

const packageRoot = fileURLToPath(new URL("../", import.meta.url));
const processEntry = fileURLToPath(
  new URL("./credentials-process.mjs", import.meta.url),
);
const networkGuard = fileURLToPath(
  new URL("./no-network.mjs", import.meta.url),
);
beforeAll(() => {
  execFileSync(
    process.execPath,
    ["node_modules/typescript/bin/tsc", "-p", "tsconfig.build.json"],
    { cwd: packageRoot },
  );
});

function childStore(path: string, mode: string, providerId = "example") {
  const child = spawn(
    process.execPath,
    ["--import", networkGuard, processEntry, path, mode, providerId],
    { stdio: ["ignore", "pipe", "pipe"] },
  );
  let stdout = "";
  let stderr = "";
  const started = Promise.withResolvers<void>();
  child.stdout.on("data", (data: Buffer) => {
    stdout += data.toString();
    if (stdout.includes("started\n")) started.resolve();
  });
  child.stderr.on("data", (data: Buffer) => {
    stderr += data.toString();
  });
  const done = new Promise<string>((resolve, reject) => {
    child.on("error", reject);
    child.on("exit", (code) => {
      if (code === 0) resolve(stdout.trim());
      else reject(new Error(`Credential fixture exited ${code}: ${stderr}`));
    });
  });
  return { started: started.promise, done };
}

test("FileCredentialStore serializes first creation and repeated replacement across processes", async () => {
  const path = await directory();
  await Promise.all([
    childStore(path, "update", "one").done,
    childStore(path, "update", "two").done,
    childStore(path, "update", "one").done,
  ]);
  const store = new FileCredentialStore(path);
  expect(await store.read("one")).toEqual({ type: "api_key", key: "6" });
  expect(await store.read("two")).toEqual({ type: "api_key", key: "3" });
});

test("FileCredentialStore shares one installation UUID across processes", async () => {
  const path = await directory();
  const ids = await Promise.all([
    childStore(path, "device").done,
    childStore(path, "device").done,
  ]);
  expect(ids[0]).toBe(ids[1]);
  expect(await new FileCredentialStore(path).getDeviceId()).toBe(ids[0]);
});

test("FileCredentialStore heartbeat keeps a long callback exclusive beyond the stale interval", async () => {
  const path = await directory();
  const holder = childStore(path, "hold");
  await holder.started;
  const initial = (await stat(join(path, "credentials.json.lock"))).mtimeMs;
  let completed = false;
  const contender = childStore(path, "update", "other").done.then(() => {
    completed = true;
  });
  await new Promise((resolve) => setTimeout(resolve, 10_500));
  expect(completed).toBe(false);
  expect(
    (await stat(join(path, "credentials.json.lock"))).mtimeMs,
  ).toBeGreaterThan(initial);
  await Promise.all([holder.done, contender]);
  expect(await new FileCredentialStore(path).read("other")).toEqual({
    type: "api_key",
    key: "3",
  });
}, 20_000);

function unused(): never {
  throw new Error("No model generation in credential tests");
}

function oauthModels(
  store: FileCredentialStore,
  refresh: (
    credential: OAuthCredential,
    signal: AbortSignal,
  ) => Promise<OAuthCredential>,
) {
  const models = createModels({ credentials: store });
  models.setProvider(
    createProvider({
      id: "example",
      models: [],
      api: { stream: unused, streamSimple: unused },
      auth: {
        oauth: {
          name: "Artificial OAuth",
          login: unused,
          refresh,
          toAuth: async (credential) => ({ apiKey: credential.access }),
        },
      },
    }),
  );
  return models;
}

const expired: OAuthCredential = {
  type: "oauth",
  refresh: "artificial-refresh",
  access: "artificial-access",
  expires: 0,
  clientId: "retained-client",
  scopes: ["retained-scope"],
};

test("public Models.getAuth refreshes a rotated token once across independent FileCredentialStores", async () => {
  const path = await directory();
  await new FileCredentialStore(path).modify("example", async () => expired);
  let calls = 0;
  const refresh = async (credential: OAuthCredential) => {
    calls++;
    expect(credential).toEqual(expired);
    await new Promise((resolve) => setTimeout(resolve, 60));
    return {
      ...credential,
      access: "rotated",
      refresh: "rotated-refresh",
      expires: Date.now() + 3_600_000,
    };
  };
  const models = [
    oauthModels(new FileCredentialStore(path), refresh),
    oauthModels(new FileCredentialStore(path), refresh),
  ];
  const results = await Promise.all(
    models.map((collection) => collection.getAuth("example")),
  );
  expect(calls).toBe(1);
  expect(results.map((result) => result?.auth.apiKey)).toEqual([
    "rotated",
    "rotated",
  ]);
  expect(await new FileCredentialStore(path).read("example")).toMatchObject({
    refresh: "rotated-refresh",
    clientId: "retained-client",
    scopes: ["retained-scope"],
  });
});

test("public Models.getAuth rejects failed OAuth refresh without ambient API key fallback or credential loss", async () => {
  const store = new FileCredentialStore(await directory());
  await store.modify("example", async () => expired);
  const authContext = {
    env: async (name: string) =>
      name === "ARTIFICIAL_API_KEY" ? "available-artificial-key" : undefined,
    fileExists: async () => false,
  };
  const resolve = vi.fn<ApiKeyAuth["resolve"]>(async ({ ctx }) => ({
    auth: { apiKey: await ctx.env("ARTIFICIAL_API_KEY") },
  }));
  const models = createModels({ credentials: store, authContext });
  models.setProvider(
    createProvider({
      id: "example",
      models: [],
      api: { stream: unused, streamSimple: unused },
      auth: {
        apiKey: { name: "Artificial ambient API key", resolve },
        oauth: {
          name: "Artificial OAuth",
          login: unused,
          refresh: async () => {
            throw new Error("Artificial expired OAuth failure");
          },
          toAuth: async (credential) => ({ apiKey: credential.access }),
        },
      },
    }),
  );
  // Prove the alternative is usable, not merely a missing-key failure.
  expect(await authContext.env("ARTIFICIAL_API_KEY")).toBe(
    "available-artificial-key",
  );
  await expect(models.getAuth("example")).rejects.toMatchObject({
    code: "oauth",
  });
  expect(resolve).not.toHaveBeenCalled();
  expect(await store.read("example")).toEqual(expired);
});

test("public Models.getAuth cancellation rejects promptly while FileCredentialStore.drain saves refresh", async () => {
  const path = await directory();
  const store = new FileCredentialStore(path);
  await store.modify("example", async () => expired);
  const started = Promise.withResolvers<void>();
  const finish = Promise.withResolvers<void>();
  const models = oauthModels(store, async (credential, signal) => {
    started.resolve();
    await finish.promise;
    expect(signal.aborted).toBe(false);
    return {
      ...credential,
      access: "rotated",
      refresh: "rotated-refresh",
      expires: Date.now() + 3_600_000,
    };
  });
  const controller = new AbortController();
  const auth = models.getAuth("example", { signal: controller.signal });
  await started.promise;
  controller.abort();
  await expect(auth).rejects.toBeDefined();
  let drained = false;
  const draining = store.drain().then(() => {
    drained = true;
  });
  await new Promise((resolve) => setTimeout(resolve, 20));
  expect(drained).toBe(false);
  finish.resolve();
  await draining;
  expect(await new FileCredentialStore(path).read("example")).toMatchObject({
    refresh: "rotated-refresh",
    access: "rotated",
  });
});

test("FileCredentialStore callback undefined leaves even a mutated callback input unchanged", async () => {
  const store = new FileCredentialStore(await directory());
  await store.modify("example", async () => expired);
  expect(
    await store.modify("example", async (current) => {
      if (current?.type !== "oauth")
        throw new Error("Expected OAuth credential");
      current.refresh = "discarded";
      return undefined;
    }),
  ).toEqual(expired);
  expect(await store.read("example")).toEqual(expired);
});

test("FileCredentialStore propagates callback rejection, preserves data and releases the lock", async () => {
  const store = new FileCredentialStore(await directory());
  await store.modify("example", async () => expired);
  const error = new Error("Artificial callback failure");
  await expect(
    store.modify("example", async () => {
      throw error;
    }),
  ).rejects.toBe(error);
  expect(await store.read("example")).toEqual(expired);
  await store.delete("example");
  expect(await store.list()).toEqual([]);
});

test("FileCredentialStore canonical directory aliases share the same lock", async () => {
  const path = await directory();
  await mkdir(path, { mode: 0o700 });
  const alias = `${path}-alias`;
  await symlink(path, alias);
  const first = new FileCredentialStore(path);
  const second = new FileCredentialStore(alias);
  await Promise.all([
    first.modify("one", async () => {
      await new Promise((resolve) => setTimeout(resolve, 40));
      return { type: "api_key", key: "one" };
    }),
    second.modify("two", async () => ({ type: "api_key", key: "two" })),
  ]);
  expect(await first.list()).toEqual(
    expect.arrayContaining([
      { providerId: "one", type: "api_key" },
      { providerId: "two", type: "api_key" },
    ]),
  );
});

test("FileCredentialStore enforces private accessible modes even under a restrictive umask", async () => {
  const path = await directory();
  expect(JSON.parse(await childStore(path, "private").done)).toEqual([
    0o700, 0o600, 0o600,
  ]);
});

test.each([true, false])(
  "FileCredentialStore defaults to the app directory using XDG when set (%s)",
  async (xdg) => {
    const path = await directory();
    const home = join(path, "home");
    execFileSync(
      process.execPath,
      ["--import", networkGuard, processEntry, path, "default"],
      {
        env: { ...process.env, HOME: home, XDG_CONFIG_HOME: xdg ? path : "" },
      },
    );
    const expected = join(xdg ? path : join(home, ".config"), "doc-translate");
    expect(await new FileCredentialStore(expected).getDeviceId()).toBe(
      await readFile(join(expected, "installation-id"), "utf8"),
    );
  },
);

test("FileCredentialStore failed atomic replacement preserves old credentials and removes staged secrets", async () => {
  const path = await directory();
  const store = new FileCredentialStore(path);
  await store.modify("example", async () => expired);
  const before = await readFile(join(path, "credentials.json"), "utf8");
  const rename = vi
    .spyOn(filesystem, "rename")
    .mockRejectedValueOnce(new Error("artificial-secret-filesystem-error"));
  syncBuiltinESMExports();
  try {
    const error = await store
      .modify("example", async () => ({
        ...expired,
        refresh: "new-artificial-refresh",
      }))
      .then(
        () => undefined,
        (reason: unknown) => reason,
      );
    expect(error).toBeInstanceOf(Error);
    expect(String(error)).toContain("Credential storage");
    expect(String(error)).not.toContain("artificial-secret");
    expect(error).not.toHaveProperty("cause");
  } finally {
    rename.mockRestore();
    syncBuiltinESMExports();
  }
  expect(await readFile(join(path, "credentials.json"), "utf8")).toBe(before);
  expect(await store.read("example")).toEqual(expired);
  expect(await readdir(path)).toEqual(["credentials.json"]);
});

test("public Models.getAuth abort cannot hide a background save failure that finished before drain", async () => {
  const store = new FileCredentialStore(await directory());
  await store.modify("example", async () => expired);
  const started = Promise.withResolvers<void>();
  const finish = Promise.withResolvers<void>();
  const models = oauthModels(store, async (credential) => {
    started.resolve();
    await finish.promise;
    return {
      ...credential,
      refresh: "rotated-refresh",
      expires: Date.now() + 3_600_000,
    };
  });
  const controller = new AbortController();
  const auth = models.getAuth("example", { signal: controller.signal });
  await started.promise;
  controller.abort();
  await expect(auth).rejects.toBeDefined();
  const rename = vi
    .spyOn(filesystem, "rename")
    .mockRejectedValueOnce(new Error("artificial-secret-save-failure"));
  syncBuiltinESMExports();
  try {
    finish.resolve();
    // A successful read waits for the refresh lock to be released after the save failure.
    expect(await store.read("example")).toEqual(expired);
    await expect(store.drain()).rejects.toThrow("Credential storage");
    await expect(store.drain()).resolves.toBeUndefined();
  } finally {
    rename.mockRestore();
    syncBuiltinESMExports();
  }
});

test("FileCredentialStore drain does not re-report caller cancellation as a storage failure", async () => {
  const store = new FileCredentialStore(await directory());
  const operation = store.read("example", {
    signal: AbortSignal.abort("artificial-secret-reason"),
  });
  const error = await operation.then(
    () => undefined,
    (reason: unknown) => reason,
  );
  expect(error).toMatchObject({ name: "AbortError" });
  expect(error).not.toHaveProperty("cause");
  expect(String(error)).not.toContain("artificial-secret");
  await expect(store.drain()).resolves.toBeUndefined();
});
