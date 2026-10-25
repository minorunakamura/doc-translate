import {
  getSupportedThinkingLevels,
  ModelsError,
  type AssistantMessage,
  type Context,
  type Model,
  type Models,
  type ModelThinkingLevel,
  type SimpleStreamOptions,
  type Usage,
} from "@earendil-works/pi-ai";
import type { JSONLRecord, JSONValue } from "./jsonl.js";
import { isRetryableAssistantError } from "@earendil-works/pi-ai/utils/retry";

type Payload = JSONLRecord["payload"];

function object(value: JSONValue | undefined): Payload {
  if (!value || typeof value !== "object" || Array.isArray(value))
    throw new Error("invalid_request");
  return value;
}

function keys(value: Payload, allowed: string[]): void {
  if (Object.keys(value).some((key) => !allowed.includes(key)))
    throw new Error("invalid_request");
}

function positive(value: JSONValue | undefined): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value > 0;
}

export interface GenerationConfig {
  model: Model<string>;
  thinking: ModelThinkingLevel;
  imageInput: boolean;
  options: SimpleStreamOptions;
}

export function generationConfig(
  payload: Payload,
  models: Models,
): GenerationConfig {
  keys(payload, ["provider", "model", "thinking", "image_input", "options"]);
  if (
    typeof payload.provider !== "string" ||
    typeof payload.model !== "string" ||
    typeof payload.thinking !== "string" ||
    typeof payload.image_input !== "boolean"
  )
    throw new Error("invalid_request");
  if (!models.getProvider(payload.provider))
    throw new Error("unknown_provider");
  const model = models.getModel(payload.provider, payload.model);
  if (
    !model ||
    model.provider !== payload.provider ||
    model.id !== payload.model
  )
    throw new Error("unknown_model");
  const thinking = getSupportedThinkingLevels(model).find(
    (level) => level === payload.thinking,
  );
  if (!thinking) throw new Error("unsupported_thinking");
  if (payload.image_input && !model.input.includes("image"))
    throw new Error("unsupported_image");
  const input = object(payload.options);
  keys(input, [
    "temperature",
    "maxTokens",
    "transport",
    "cacheRetention",
    "thinkingBudgets",
    "timeoutMs",
  ]);
  const options: SimpleStreamOptions = { maxRetries: 0, timeoutMs: 1_200_000 };
  if (thinking !== "off") options.reasoning = thinking;
  if (input.temperature !== undefined) {
    if (
      typeof input.temperature !== "number" ||
      !Number.isFinite(input.temperature)
    )
      throw new Error("invalid_request");
    options.temperature = input.temperature;
  }
  if (input.maxTokens !== undefined) {
    if (!positive(input.maxTokens) || input.maxTokens > model.maxTokens)
      throw new Error("invalid_request");
    options.maxTokens = input.maxTokens;
  }
  if (input.timeoutMs !== undefined) {
    if (!positive(input.timeoutMs) || input.timeoutMs > 2_147_483_647)
      throw new Error("invalid_request");
    options.timeoutMs = input.timeoutMs;
  }
  if (input.transport !== undefined) {
    if (
      input.transport !== "sse" &&
      input.transport !== "websocket" &&
      input.transport !== "websocket-cached" &&
      input.transport !== "auto"
    )
      throw new Error("invalid_request");
    options.transport = input.transport;
  }
  if (input.cacheRetention !== undefined) {
    if (
      input.cacheRetention !== "none" &&
      input.cacheRetention !== "short" &&
      input.cacheRetention !== "long"
    )
      throw new Error("invalid_request");
    options.cacheRetention = input.cacheRetention;
  }
  if (input.thinkingBudgets !== undefined) {
    const budgets = object(input.thinkingBudgets);
    keys(budgets, ["minimal", "low", "medium", "high"]);
    options.thinkingBudgets = {};
    for (const level of ["minimal", "low", "medium", "high"] as const) {
      if (budgets[level] !== undefined) {
        const budget = budgets[level];
        if (!positive(budget)) throw new Error("invalid_request");
        options.thinkingBudgets[level] = budget;
      }
    }
  }
  return { model, thinking, imageInput: payload.image_input, options };
}

export async function preflight(
  config: GenerationConfig,
  models: Models,
  signal: AbortSignal,
): Promise<Payload> {
  const auth = await models.checkAuth(config.model.provider, { signal });
  if (!auth) throw new Error("auth_unavailable");
  const limits = config.model.inputLimits;
  return {
    operation: "preflight",
    provider: config.model.provider,
    model: config.model.id,
    api: config.model.api,
    image_input: config.model.input.includes("image"),
    thinking_levels: getSupportedThinkingLevels(config.model),
    context_window: config.model.contextWindow,
    max_tokens: config.model.maxTokens,
    auth: { source: auth.source ?? null, type: auth.type },
    input_limits: limits
      ? {
          maxRequestBytes: limits.maxRequestBytes ?? null,
          images: limits.images
            ? {
                maxPerMessage: limits.images.maxPerMessage ?? null,
                maxPerRequest: limits.images.maxPerRequest ?? null,
                resize: limits.images.resize
                  ? {
                      maxWidth: limits.images.resize.maxWidth ?? null,
                      maxHeight: limits.images.resize.maxHeight ?? null,
                      maxBytes: limits.images.resize.maxBytes ?? null,
                      jpegQuality: limits.images.resize.jpegQuality ?? null,
                    }
                  : null,
              }
            : null,
        }
      : null,
  };
}

function generationContext(
  payload: Payload,
  config: GenerationConfig,
): Context {
  keys(payload, ["policy", "text", "context", "glossary", "images"]);
  if (
    typeof payload.policy !== "string" ||
    typeof payload.text !== "string" ||
    payload.context === undefined ||
    payload.glossary === undefined ||
    !Array.isArray(payload.images)
  )
    throw new Error("invalid_request");
  const images = payload.images;
  const limits = config.model.inputLimits?.images;
  if (images.length && !config.imageInput) throw new Error("unsupported_image");
  if (
    (limits?.maxPerMessage !== undefined &&
      images.length > limits.maxPerMessage) ||
    (limits?.maxPerRequest !== undefined &&
      images.length > limits.maxPerRequest)
  )
    throw new Error("input_limit");
  const content: (
    | { type: "text"; text: string }
    | { type: "image"; data: string; mimeType: string }
  )[] = [
    {
      type: "text",
      text: JSON.stringify({
        text: payload.text,
        context: payload.context,
        glossary: payload.glossary,
      }),
    },
  ];
  for (const item of images) {
    const image = object(item);
    keys(image, ["data", "mimeType", "width", "height", "coordinates"]);
    if (
      typeof image.data !== "string" ||
      image.mimeType !== "image/png" ||
      !positive(image.width) ||
      !positive(image.height) ||
      typeof image.coordinates !== "string" ||
      !image.coordinates
    )
      throw new Error("invalid_image");
    const bytes = Buffer.from(image.data, "base64");
    // Python validates full PNG decoding/hash; this guard prevents wire metadata drift.
    if (
      bytes.toString("base64") !== image.data ||
      bytes.length < 24 ||
      !bytes
        .subarray(0, 8)
        .equals(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10])) ||
      bytes.toString("ascii", 12, 16) !== "IHDR" ||
      bytes.readUInt32BE(16) !== image.width ||
      bytes.readUInt32BE(20) !== image.height
    )
      throw new Error("invalid_image");
    if (
      (limits?.resize?.maxWidth !== undefined &&
        image.width > limits.resize.maxWidth) ||
      (limits?.resize?.maxHeight !== undefined &&
        image.height > limits.resize.maxHeight) ||
      (limits?.resize?.maxBytes !== undefined &&
        image.data.length > limits.resize.maxBytes)
    )
      throw new Error("input_limit");
    content.push({
      type: "text",
      text: JSON.stringify({
        width: image.width,
        height: image.height,
        coordinates: image.coordinates,
      }),
    });
    content.push({ type: "image", data: image.data, mimeType: image.mimeType });
  }
  return {
    systemPrompt: payload.policy,
    messages: [{ role: "user", content, timestamp: Date.now() }],
  };
}

function usageRecord(usage: Usage): Payload {
  return {
    input: usage.input,
    output: usage.output,
    cacheRead: usage.cacheRead,
    cacheWrite: usage.cacheWrite,
    totalTokens: usage.totalTokens,
    ...(usage.reasoning !== undefined ? { reasoning: usage.reasoning } : {}),
    ...(usage.cacheWrite1h !== undefined
      ? { cacheWrite1h: usage.cacheWrite1h }
      : {}),
    cost: { ...usage.cost },
  };
}

function observedUsage(message: AssistantMessage): boolean {
  return [
    message.usage.input,
    message.usage.output,
    message.usage.cacheRead,
    message.usage.cacheWrite,
    message.usage.totalTokens,
  ].some((value) => value > 0);
}

function safeMessage(message: AssistantMessage): Payload {
  return {
    role: message.role,
    provider: message.provider,
    model: message.model,
    api: message.api,
    stopReason: message.stopReason,
    content: message.content.map(
      (part): JSONValue =>
        part.type === "text"
          ? { type: "text", text: part.text }
          : { type: part.type },
    ),
    provider_thinking_level: message.providerThinkingLevel ?? null,
    response_model: message.responseModel ?? null,
  };
}

function retryClassification(
  code: string | null,
  terminal: Payload | null,
  message: AssistantMessage | undefined,
): Payload {
  if (code === "generation_failed" && terminal?.type === "error" && message)
    return {
      retryable: isRetryableAssistantError(message),
      classification: "library_heuristic",
    };
  return { retryable: false, classification: null };
}

export async function generate(
  payload: Payload,
  config: GenerationConfig,
  models: Models,
  signal: AbortSignal,
): Promise<Payload> {
  const context = generationContext(payload, config);
  const controller = new AbortController();
  let timedOut = false;
  const cancel = () => controller.abort();
  signal.addEventListener("abort", cancel, { once: true });
  if (signal.aborted) cancel();
  const timer = setTimeout(() => {
    timedOut = true;
    cancel();
  }, config.options.timeoutMs);
  const started = performance.now();
  let terminal: Payload | null = null;
  let message: AssistantMessage | undefined;
  let usage: Payload | null = null;
  let code: string | null = null;
  let payloadObserved = false;
  let responses = 0;
  let limitExceeded = false;
  let toolCall = false;
  const consume = async () => {
    if (controller.signal.aborted) throw new Error("cancelled");
    await preflight(config, models, controller.signal);
    if (!(await models.getAuth(config.model, { signal: controller.signal })))
      throw new Error("auth_unavailable");
    const stream = models.streamSimple(config.model, context, {
      ...config.options,
      signal: controller.signal,
      onPayload: (body) => {
        payloadObserved = true;
        const maximum = config.model.inputLimits?.maxRequestBytes;
        if (
          maximum !== undefined &&
          Buffer.byteLength(JSON.stringify(body), "utf8") > maximum
        ) {
          limitExceeded = true;
          throw new Error("input_limit");
        }
      },
      onResponse: () => {
        responses += 1;
      },
    });
    for await (const event of stream) {
      const partial =
        event.type === "done"
          ? event.message
          : event.type === "error"
            ? event.error
            : event.partial;
      if (observedUsage(partial)) usage = usageRecord(partial.usage);
      if (partial.content.some((part) => part.type === "toolCall"))
        toolCall = true;
      if (event.type === "done" || event.type === "error") {
        terminal = { type: event.type, reason: event.reason };
      }
    }
    message = await stream.result();
    if (observedUsage(message)) usage = usageRecord(message.usage);
    if (!terminal) code = "missing_terminal";
    else if (
      message.provider !== config.model.provider ||
      message.model !== config.model.id ||
      message.api !== config.model.api
    )
      code = "model_mismatch";
    else if (
      toolCall ||
      message.content.some((part) => part.type === "toolCall")
    )
      code = "tool_call";
    else if (
      terminal.type !== "done" ||
      terminal.reason !== "stop" ||
      message.stopReason !== "stop"
    )
      code = "generation_failed";
  };
  let onAbort: (() => void) | undefined;
  let graceTimer: ReturnType<typeof setTimeout> | undefined;
  try {
    const aborted = new Promise<void>((resolve) => {
      onAbort = resolve;
      controller.signal.addEventListener("abort", onAbort, { once: true });
      if (controller.signal.aborted) onAbort();
    });
    const consuming = consume().catch((error: unknown) => {
      code = limitExceeded
        ? "input_limit"
        : generationError(error, controller.signal);
    });
    await Promise.race([consuming, aborted]);
    clearTimeout(timer);
    // Abort terminals can carry usage; drain cooperatively within the kill grace.
    if (controller.signal.aborted)
      await Promise.race([
        consuming,
        new Promise<void>((resolve) => {
          graceTimer = setTimeout(resolve, 30_000);
        }),
      ]);
  } finally {
    clearTimeout(timer);
    clearTimeout(graceTimer);
    signal.removeEventListener("abort", cancel);
    if (onAbort) controller.signal.removeEventListener("abort", onAbort);
  }
  if (limitExceeded) code = "input_limit";
  if (controller.signal.aborted) code = timedOut ? "timeout" : "cancelled";
  return {
    operation: "generate",
    code,
    terminal,
    message: message ? safeMessage(message) : null,
    usage,
    usage_status: usage ? "observed" : "unknown",
    elapsed_ms: Math.round(performance.now() - started),
    requested: {
      provider: config.model.provider,
      model: config.model.id,
      thinking: config.thinking,
    },
    submitted: {
      reasoning: config.options.reasoning ?? null,
      maxRetries: 0,
      timeoutMs: config.options.timeoutMs ?? null,
      maxTokens: config.options.maxTokens ?? null,
      temperature: config.options.temperature ?? null,
      transport: config.options.transport ?? null,
      cacheRetention: config.options.cacheRetention ?? null,
      thinkingBudgets: config.options.thinkingBudgets
        ? { ...config.options.thinkingBudgets }
        : null,
    },
    provider_confirmed: { thinking: message?.providerThinkingLevel ?? null },
    retry: {
      helper_attempts: 1,
      maxRetries: 0,
      observed_responses: responses,
      internal_retries: "unknown",
      ...retryClassification(code, terminal, message),
    },
    request_bytes_checked:
      payloadObserved &&
      config.model.inputLimits?.maxRequestBytes !== undefined,
  };
}

export function generationError(error: unknown, signal: AbortSignal): string {
  if (signal.aborted) return "cancelled";
  if (
    error instanceof ModelsError &&
    (error.code === "auth" || error.code === "oauth")
  )
    return "auth_failed";
  const code = error instanceof Error ? error.message : "";
  return [
    "invalid_request",
    "unknown_provider",
    "unknown_model",
    "unsupported_thinking",
    "unsupported_image",
    "auth_unavailable",
    "invalid_image",
    "input_limit",
  ].includes(code)
    ? code
    : "generation_failed";
}
