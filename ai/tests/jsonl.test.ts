import { execFileSync, spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { beforeAll, expect, test } from "vitest";

const packageRoot = fileURLToPath(new URL("../", import.meta.url));
const processEntry = fileURLToPath(
  new URL("./jsonl-process.mjs", import.meta.url),
);

beforeAll(() => {
  execFileSync(
    process.execPath,
    ["node_modules/typescript/bin/tsc", "-p", "tsconfig.build.json"],
    {
      cwd: packageRoot,
    },
  );
});

test.each([{ args: [] }, { args: ["--byte-chunks"] }])(
  "public compiled codec round-trips LF records without splitting Unicode line separators (%j)",
  ({ args }) => {
    const wire =
      '{"schema_version":1,"request_id":"r-1","type":"example","payload":{"text":"first\u2028second\u2029third\\n日本語🌸"}}\n' +
      '{"schema_version":1,"request_id":"r-2","type":"unlisted","payload":{"nested":[null,true,1,{"x":"y"}]}}\n';
    const result = spawnSync(process.execPath, [processEntry, ...args], {
      input: wire,
      encoding: "utf8",
    });
    expect(result.stderr).toBe("");
    expect(result.status).toBe(0);
    expect(result.stdout).toBe(wire);
  },
);

test.each([
  "NaN",
  "Infinity",
  "-Infinity",
  "9007199254740992",
  "-9007199254740992",
  "9007199254740993",
  "-9007199254740993",
  "9007199254740992.0",
  "-9007199254740992.0",
  "9.007199254740992e15",
  "-9.007199254740992e15",
])(
  "public encoder rejects non-finite or unsafe integer JSON numbers: %s",
  (number) => {
    const result = spawnSync(
      process.execPath,
      [
        "--input-type=module",
        "-e",
        `
		import { encodeRecord } from '@doc-translate/ai/jsonl';
		process.stdout.write(encodeRecord({ schema_version: 1, request_id: 'r', type: 'example', payload: { nested: [{ cost: ${number} }] } }));
	`,
      ],
      { cwd: packageRoot, encoding: "utf8" },
    );
    expect(result.status).toBe(1);
    expect(result.stdout).toBe("");
    expect(result.stderr).toContain("Invalid JSONL envelope");
  },
);

test.each([
  "9007199254740991",
  "-9007199254740991",
  "9007199254740991.0",
  "-9007199254740991.0",
])(
  "public encoder preserves safe integer boundaries and other JSON values: %s",
  (number) => {
    const result = spawnSync(
      process.execPath,
      [
        "--input-type=module",
        "-e",
        `
		import { encodeRecord } from '@doc-translate/ai/jsonl';
		process.stdout.write(encodeRecord({ schema_version: 1, request_id: 'r', type: 'example', payload: { count: ${number}, nested: [{ count: ${number} }], other: [0, 1.25, -1.25, 5e-324, true, false, null, '9007199254740993'] } }));
	`,
      ],
      { cwd: packageRoot, encoding: "utf8" },
    );
    expect(result.status).toBe(0);
    expect(result.stderr).toBe("");
    expect(JSON.parse(result.stdout)).toEqual({
      schema_version: 1,
      request_id: "r",
      type: "example",
      payload: {
        count: number.startsWith("-") ? -9007199254740991 : 9007199254740991,
        nested: [
          {
            count: number.startsWith("-")
              ? -9007199254740991
              : 9007199254740991,
          },
        ],
        other: [0, 1.25, -1.25, 5e-324, true, false, null, "9007199254740993"],
      },
    });
  },
);

test.each([
  "9007199254740992",
  "-9007199254740992",
  "9007199254740993",
  "-9007199254740993",
  "9007199254740992.0",
  "-9007199254740992.0",
  "9.007199254740992e15",
  "-9.007199254740992e15",
])(
  "public decoder rejects unsafe integer wire values before returning a record: %s",
  (number) => {
    for (const payload of [
      `{"count":${number}}`,
      `{"nested":[{"count":${number}}]}`,
    ]) {
      const result = spawnSync(
        process.execPath,
        [processEntry, "--byte-chunks"],
        {
          input: `{"schema_version":1,"request_id":"r","type":"example","payload":${payload}}\n`,
          encoding: "utf8",
        },
      );
      expect(result.status).toBe(1);
      expect(result.stdout).toBe("");
      expect(result.stderr).toContain("Invalid JSONL envelope");
    }
  },
);

test.each([
  "null",
  "[]",
  '{"schema_version":2,"request_id":"r","type":"example","payload":{}}',
  '{"schema_version":true,"request_id":"r","type":"example","payload":{}}',
  '{"schema_version":"1","request_id":"r","type":"example","payload":{}}',
  '{"schema_version":1,"type":"example","payload":{}}',
  '{"schema_version":1,"request_id":"","type":"example","payload":{}}',
  '{"schema_version":1,"request_id":1,"type":"example","payload":{}}',
  '{"schema_version":1,"request_id":"r","type":"","payload":{}}',
  '{"schema_version":1,"request_id":"r","type":true,"payload":{}}',
  '{"schema_version":1,"request_id":"r","type":"example","payload":[]}',
  '{"schema_version":1,"request_id":"r","type":"example","payload":null}',
  '{"schema_version":1,"request_id":"r","type":"example","payload":{},"extra":0}',
  '{"schema_version":1,"request_id":"r","type":"example","payload":{"nested":[{"cost":1e400}]}}',
])("public codec rejects an invalid envelope: %s", (json) => {
  const result = spawnSync(process.execPath, [processEntry], {
    input: `${json}\n`,
    encoding: "utf8",
  });
  expect(result.status).toBe(1);
  expect(result.stdout).toBe("");
  expect(result.stderr).toContain("Invalid JSONL envelope");
});

test.each([
  Buffer.from("not json\n"),
  Buffer.from("\n"),
  Buffer.from(
    '{"schema_version":1,"request_id":"r","type":"example","payload":{"cost":NaN}}\n',
  ),
  Buffer.from(
    '{"schema_version":1,"request_id":"r","type":"example","payload":{"cost":Infinity}}\n',
  ),
  Buffer.concat([
    Buffer.from(
      '{"schema_version":1,"request_id":"r","type":"example","payload":{"text":"',
    ),
    Buffer.from([0xff]),
    Buffer.from('"}}\n'),
  ]),
])("public codec rejects malformed JSON or UTF-8: %s", (wire) => {
  const result = spawnSync(process.execPath, [processEntry], {
    input: wire,
    encoding: "utf8",
  });
  expect(result.status).toBe(1);
  expect(result.stdout).toBe("");
});

test("public codec rejects an incomplete final record instead of accepting it at EOF", () => {
  const result = spawnSync(process.execPath, [processEntry], {
    input:
      '{"schema_version":1,"request_id":"r","type":"example","payload":{}}',
    encoding: "utf8",
  });
  expect(result.status).toBe(1);
  expect(result.stdout).toBe("");
  expect(result.stderr).toContain("Incomplete JSONL record");
});
