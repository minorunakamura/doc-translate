import "./no-network.mjs";
import { readFile, writeFile } from "node:fs/promises";
import { createModels } from "@earendil-works/pi-ai";
import {
  fauxAssistantMessage,
  fauxProvider,
} from "@earendil-works/pi-ai/providers/faux";
import { FileCredentialStore } from "../dist/credentials.js";
import { runHelper } from "../dist/main.js";

const [directory, answerPath, recordingPath] = process.argv.slice(2);
const store = new FileCredentialStore(directory);
const models = createModels({ credentials: store });
const faux = fauxProvider({
  api: "faux-api",
  models: [{ id: "vision", input: ["text", "image"] }],
});
faux.setResponses([
  async (context, _options, _state, model) => {
    await writeFile(recordingPath, JSON.stringify(context));
    const message = fauxAssistantMessage(await readFile(answerPath, "utf8"));
    message.provider = model.provider;
    message.model = model.id;
    return message;
  },
]);
models.setProvider(faux.provider);
await runHelper(models, store);
