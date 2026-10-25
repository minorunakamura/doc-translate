import { constants } from "node:fs";
import {
  chmod,
  lstat,
  mkdir,
  open,
  realpath,
  rename,
  rm,
} from "node:fs/promises";
import { randomUUID } from "node:crypto";
import { homedir } from "node:os";
import { join } from "node:path";
import { setTimeout as delay } from "node:timers/promises";
import type {
  AuthOperationOptions,
  Credential,
  CredentialInfo,
  CredentialStore,
} from "@earendil-works/pi-ai";
import lockfile from "proper-lockfile";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function validateCredential(value: unknown): asserts value is Credential {
  if (!isRecord(value))
    throw new Error("Credential storage contains invalid credential data");
  if (
    value.type === "oauth" &&
    typeof value.refresh === "string" &&
    typeof value.access === "string" &&
    typeof value.expires === "number" &&
    Number.isFinite(value.expires)
  )
    return;
  if (
    value.type === "api_key" &&
    (value.key === undefined || typeof value.key === "string") &&
    (value.env === undefined ||
      (isRecord(value.env) &&
        Object.values(value.env).every((item) => typeof item === "string")))
  )
    return;
  throw new Error("Credential storage contains invalid credential data");
}

async function storage<T>(operation: () => Promise<T>): Promise<T> {
  try {
    return await operation();
  } catch {
    // Filesystem/JSON errors can contain secrets, including in their causes.
    throw new Error(
      "Credential storage failed; check permissions and storage, and re-login if necessary",
    );
  }
}

export class FileCredentialStore implements CredentialStore {
  private readonly directory: string;
  private readonly pending = new Set<Promise<unknown>>();
  private failure: { reason: unknown } | undefined;

  constructor(
    directory = join(
      process.env.XDG_CONFIG_HOME || join(homedir(), ".config"),
      "doc-translate",
    ),
  ) {
    this.directory = directory;
  }

  private locked<T>(
    fn: (directory: string, check: () => void) => Promise<T>,
    options?: AuthOperationOptions,
  ): Promise<T> {
    const operation = this.runLocked(fn, options);
    this.pending.add(operation);
    void operation.then(
      () => this.pending.delete(operation),
      (reason: unknown) => {
        this.pending.delete(operation);
        if (!(isRecord(reason) && reason.name === "AbortError"))
          this.failure ??= { reason };
      },
    );
    return operation;
  }

  async drain(): Promise<void> {
    await Promise.allSettled(this.pending);
    const failure = this.failure;
    this.failure = undefined;
    if (failure) throw failure.reason;
  }

  private checkAbort(options?: AuthOperationOptions): void {
    if (options?.signal?.aborted)
      throw new DOMException("Credential operation cancelled", "AbortError");
  }

  private async runLocked<T>(
    fn: (directory: string, check: () => void) => Promise<T>,
    options?: AuthOperationOptions,
  ): Promise<T> {
    this.checkAbort(options);
    const directory = await storage(async () => {
      await mkdir(this.directory, { recursive: true, mode: 0o700 });
      const canonical = await realpath(this.directory);
      const info = await lstat(canonical);
      if (!info.isDirectory() || info.uid !== process.getuid?.())
        throw new Error("Invalid credential directory");
      await chmod(canonical, 0o700);
      return canonical;
    });
    // The path identity survives both first use and atomic file replacement.
    const identity = join(directory, "credentials.json");
    let compromised = false;
    const acquire = async (): Promise<() => Promise<void>> => {
      this.checkAbort(options);
      const release = await storage(async () => {
        try {
          return await lockfile.lock(identity, {
            realpath: false,
            retries: 0,
            onCompromised: () => {
              compromised = true;
            },
          });
        } catch (error) {
          if (isRecord(error) && error.code === "ELOCKED") return undefined;
          throw error;
        }
      });
      if (release) return release;
      try {
        await delay(50, undefined, { signal: options?.signal });
      } catch {
        this.checkAbort(options);
        throw new Error("Credential lock wait failed");
      }
      return acquire();
    };
    const release = await acquire();
    const check = () => {
      if (compromised) throw new Error("Credential storage lock lost");
    };
    try {
      this.checkAbort(options);
      check();
      return await fn(directory, check);
    } finally {
      await storage(release);
    }
  }

  private async readText(
    directory: string,
    name: string,
  ): Promise<string | undefined> {
    return storage(async () => {
      let file;
      try {
        file = await open(
          join(directory, name),
          constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK,
        );
      } catch (error) {
        if (isRecord(error) && error.code === "ENOENT") return undefined;
        throw error;
      }
      try {
        const info = await file.stat();
        if (
          !info.isFile() ||
          info.nlink !== 1 ||
          info.uid !== process.getuid?.()
        )
          throw new Error("Invalid credential file");
        await file.chmod(0o600);
        return await file.readFile("utf8");
      } finally {
        await file.close();
      }
    });
  }

  private async load(directory: string): Promise<Map<string, Credential>> {
    return storage(async () => {
      const text = await this.readText(directory, "credentials.json");
      if (text === undefined) return new Map<string, Credential>();
      const data: unknown = JSON.parse(text);
      if (!isRecord(data)) throw new Error("Invalid credential map");
      const credentials = new Map<string, Credential>();
      for (const [providerId, credential] of Object.entries(data)) {
        validateCredential(credential);
        credentials.set(providerId, credential);
      }
      return credentials;
    });
  }

  private async save(
    directory: string,
    name: string,
    contents: string,
    check: () => void,
  ): Promise<void> {
    await storage(async () => {
      const temporary = join(directory, `.credentials-${randomUUID()}.tmp`);
      try {
        const file = await open(temporary, "wx", 0o600);
        try {
          await file.chmod(0o600);
          await file.writeFile(contents);
          await file.sync();
        } finally {
          await file.close();
        }
        check();
        await rename(temporary, join(directory, name));
      } finally {
        await rm(temporary, { force: true });
      }
    });
  }

  async getDeviceId(): Promise<string> {
    return this.locked(async (directory, check) => {
      const saved = await this.readText(directory, "installation-id");
      if (saved !== undefined) {
        if (
          !/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(
            saved,
          )
        )
          throw new Error(
            "Credential storage contains invalid installation ID",
          );
        return saved;
      }
      const id = randomUUID();
      await this.save(directory, "installation-id", id, check);
      return id;
    });
  }

  async read(
    providerId: string,
    options?: AuthOperationOptions,
  ): Promise<Credential | undefined> {
    return this.locked(
      async (directory) => (await this.load(directory)).get(providerId),
      options,
    );
  }

  async list(
    options?: AuthOperationOptions,
  ): Promise<readonly CredentialInfo[]> {
    return this.locked(
      async (directory) =>
        Array.from(await this.load(directory)).map(
          ([providerId, credential]) => ({ providerId, type: credential.type }),
        ),
      options,
    );
  }

  async modify(
    providerId: string,
    fn: (current: Credential | undefined) => Promise<Credential | undefined>,
    options?: AuthOperationOptions,
  ): Promise<Credential | undefined> {
    return this.locked(async (directory, check) => {
      const data = await this.load(directory);
      this.checkAbort(options);
      const next = await fn(structuredClone(data.get(providerId)));
      check();
      if (next !== undefined) {
        validateCredential(next);
        data.set(providerId, next);
        await storage(async () =>
          this.save(
            directory,
            "credentials.json",
            JSON.stringify(Object.fromEntries(data)),
            check,
          ),
        );
      }
      return data.get(providerId);
    }, options);
  }

  async delete(
    providerId: string,
    options?: AuthOperationOptions,
  ): Promise<void> {
    return this.locked(async (directory, check) => {
      const data = await this.load(directory);
      data.delete(providerId);
      await storage(async () =>
        this.save(
          directory,
          "credentials.json",
          JSON.stringify(Object.fromEntries(data)),
          check,
        ),
      );
    }, options);
  }
}
