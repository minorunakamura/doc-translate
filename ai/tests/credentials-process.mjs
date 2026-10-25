import { setTimeout as delay } from "node:timers/promises";
import { stat } from "node:fs/promises";
import { join } from "node:path";
import { FileCredentialStore } from "../dist/credentials.js";

const [directory, mode, providerId] = process.argv.slice(2);
const store = new FileCredentialStore(
  mode === "default" ? undefined : directory,
);
if (mode === "private") {
  process.umask(0o777);
  await store.modify(providerId, async () => ({
    type: "api_key",
    key: "artificial",
  }));
  await store.getDeviceId();
  const modes = await Promise.all(
    [
      directory,
      join(directory, "credentials.json"),
      join(directory, "installation-id"),
    ].map(async (path) => (await stat(path)).mode & 0o777),
  );
  process.stdout.write(`${JSON.stringify(modes)}\n`);
} else if (mode === "device" || mode === "default") {
  process.stdout.write(`${await store.getDeviceId()}\n`);
} else if (mode === "hold") {
  await store.modify(providerId, async () => {
    process.stdout.write("started\n");
    await delay(11_500);
    return { type: "api_key", key: "artificial" };
  });
} else if (mode === "update") {
  await Promise.all(
    Array.from({ length: 3 }, () =>
      store.modify(providerId, async (current) => {
        await delay(40);
        return {
          type: "api_key",
          key: String(Number(current?.key ?? "0") + 1),
        };
      }),
    ),
  );
} else {
  throw new Error("Unknown credential test fixture mode");
}
await store.drain();
