import "./no-network.mjs";
import { appendFile, readFile } from "node:fs/promises";
import { createModels } from "@earendil-works/pi-ai";
import {
  fauxAssistantMessage,
  fauxProvider,
} from "@earendil-works/pi-ai/providers/faux";
import { FileCredentialStore } from "../dist/credentials.js";
import { runHelper } from "../dist/main.js";

const [directory, settingsPath, recordingPath] = process.argv.slice(2);
const settings = JSON.parse(await readFile(settingsPath, "utf8"));
const store = new FileCredentialStore(directory);
const models = createModels({ credentials: store });
const faux = fauxProvider({
  api: "faux-api",
  provider: settings.provider ?? "faux",
  models: [
    { id: "text", input: ["text"] },
    {
      id: "vision",
      reasoning: true,
      input: ["text", "image"],
      ...(settings.image_limit
        ? { inputLimits: { images: { maxPerMessage: settings.image_limit } } }
        : {}),
    },
    { id: "vision-alt", reasoning: true, input: ["text", "image"] },
  ],
});
faux.setResponses([
  async (context, options, _state, model) => {
    if (settings.notices) {
      const notices = (await readFile(settings.notices, "utf8"))
        .trim()
        .split("\n");
      const notice = JSON.parse(notices.at(-1));
      if (notice.provider !== "faux" || notice.auth.source !== null)
        throw new Error("Missing before-send notice");
    }
    await appendFile(
      recordingPath,
      `${JSON.stringify({ ...context, selection: { provider: model.provider, model: model.id }, options })}\n`,
    );
    const input = JSON.parse(context.messages[1].content[0].text);
    if (settings.delay_phase === input.context.phase) {
      await new Promise((resolve) => setTimeout(resolve, settings.delay_ms));
    }
    const answer =
      input.context.phase === "review"
        ? { findings: settings.review_findings ?? [] }
        : input.context.owned_pages
          ? {
              schema_version: 1,
              draft_sha256: input.context.draft_sha256,
              owned_pages: input.context.owned_pages,
              blocks:
                settings.correction_blocks ??
                input.context.owned_pages.map((page) => ({
                  block_id: `page-${page}`,
                  kind: "body",
                  text: `Page ${page} source.`,
                  evidence: [
                    {
                      page,
                      ref: null,
                      position: "center",
                      reason: "Synthetic visible paragraph.",
                    },
                  ],
                })),
              exclusions: settings.correction_exclusions ?? [],
              uncertainties: [],
            }
          : input.context.phase === "collect"
            ? {
                context: "Artificial section.",
                candidates: settings.candidate
                  ? [
                      {
                        term: "Original",
                        translation: "自動",
                        evidence: [{ unit_id: "unit-1", quote: "Original" }],
                      },
                    ]
                  : [],
              }
            : input.context.phase === "organize"
              ? {
                  context: "Artificial document.",
                  terms: settings.candidate ? { Original: "自動" } : {},
                }
              : {
                  translations: JSON.parse(input.text).map((unit) => ({
                    unit_id: unit.unit_id,
                    text: `訳:${unit.source_text}`,
                    uncertainties: settings.attention
                      ? [
                          {
                            reason: "Artificial ambiguity.",
                            origin: "undetermined",
                          },
                        ]
                      : [],
                  })),
                };
    const message = fauxAssistantMessage(
      JSON.stringify(answer),
      input.context.owned_pages?.includes(settings.correction_fail_page) ||
        (settings.fail_stage === "review" &&
          input.context.phase === "review") ||
        (settings.fail_stage === "prepare" &&
          ["collect", "organize"].includes(input.context.phase)) ||
        (settings.fail_stage === "translation" &&
          !input.context.phase &&
          !input.context.owned_pages)
        ? {
            stopReason: "error",
            errorMessage: "artificial permanent correction failure",
          }
        : input.context.owned_pages?.length > settings.correction_length_above
          ? { stopReason: "length" }
          : {},
    );
    message.provider = model.provider;
    message.model = model.id;
    return message;
  },
]);
models.setProvider(faux.provider);
await runHelper(models, store);
