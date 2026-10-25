"""Check explicit helper build freshness and wheel resources, then clean installs.

Run after pnpm --dir ai build && uv build. Registry access is only for the
explicit npm/pnpm installs; CLI catalog and codec smoke send no requests.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
RESOURCE = "doc_translate/ai/"
SMOKE = """
import assert from 'node:assert/strict';
import { encodeRecord, readRecords } from '@doc-translate/ai/jsonl';
import { builtinModels } from '@earendil-works/pi-ai/providers/all';
import lockfile from 'proper-lockfile';
assert.equal(typeof builtinModels, 'function');
assert.equal(typeof lockfile.lock, 'function');
const record = { schema_version: 1, request_id: 'offline', type: 'echo',
    payload: { text: '日本語\\u2028\\u2029' } };
const bytes = encodeRecord(record);
const output = [];
for await (const value of readRecords([...bytes].map(b => Uint8Array.of(b)))) {
    output.push(value);
}
assert.deepEqual(output, [record]);
await assert.rejects(
    Array.fromAsync(readRecords([bytes.subarray(0, bytes.length - 1)])),
    /missing final LF/,
);
console.log('offline codec + pi-ai/proper-lockfile imports: OK');
"""


def main() -> None:
    wheel = Path(sys.argv[1]).resolve()
    node = shutil.which("node")
    assert node is not None, "Node must already be installed"
    with tempfile.TemporaryDirectory(prefix="doc-translate-distribution-") as directory:
        temporary = Path(directory)
        compiled = temporary / "compiled"
        subprocess.run(
            [
                "pnpm",
                "--dir",
                str(ROOT / "ai"),
                "exec",
                "tsc",
                "-p",
                "tsconfig.build.json",
                "--outDir",
                str(compiled),
            ],
            check=True,
        )
        built = {
            p.relative_to(compiled): p.read_bytes()
            for p in compiled.rglob("*")
            if p.is_file()
        }
        dist = ROOT / "ai/dist"
        actual = {
            p.relative_to(dist): p.read_bytes() for p in dist.rglob("*") if p.is_file()
        }
        assert built and built == actual, "ai/dist is stale; explicitly rebuild it"
        expected = {f"dist/{path.as_posix()}": data for path, data in built.items()}
        for name in ("package.json", "pnpm-lock.yaml", "package-lock.json"):
            expected[name] = (ROOT / "ai" / name).read_bytes()
        with ZipFile(wheel) as archive:
            resources = {
                name.removeprefix(RESOURCE): archive.read(name)
                for name in archive.namelist()
                if name.startswith(RESOURCE) and not name.endswith("/")
            }
        assert resources == expected, "wheel helper resources are missing or stale"
        print("helper source/build/manifest/locks and wheel resources: OK", flush=True)
        # Keep the smoke PATH free of Pi CLI and source-tree node_modules.
        binaries = temporary / "bin"
        binaries.mkdir()
        (binaries / "node").symlink_to(node)
        core = temporary / "core"
        subprocess.run(
            ["uv", "venv", "--python", sys.executable, str(core)], check=True
        )
        python = core / "bin/python"
        cli = core / "bin/doc-translate"
        subprocess.run(
            ["uv", "pip", "install", "--python", str(python), str(wheel)], check=True
        )
        subprocess.run(
            [
                str(python),
                "-c",
                "import importlib.util; assert importlib.util.find_spec('docling') is None",
            ],
            cwd=temporary,
            check=True,
        )
        for manager in ("npm", "pnpm"):
            home = temporary / manager
            home.mkdir()
            environment = {
                **os.environ,
                "HOME": str(home),
                "XDG_DATA_HOME": str(home / "data"),
            }
            environment.pop("NODE_PATH", None)
            environment.pop("NODE_OPTIONS", None)
            installed = subprocess.run(
                [str(cli), "setup-ai", "--package-manager", manager],
                cwd=temporary,
                env=environment,
                capture_output=True,
                text=True,
                check=True,
            )
            record = json.loads(installed.stdout)
            assert record["package_manager"] == manager
            assert record["library_version"] == "1.4.0"
            runtime = Path(record["runtime"])
            assert runtime.is_relative_to(home / "data/doc-translate/ai")
            assert (
                json.loads((runtime / "runtime.json").read_text())["lock_sha256"]
                == record["lock_sha256"]
            )
            environment["PATH"] = str(binaries)
            subprocess.run(
                [str(cli), "--help"], cwd=temporary, env=environment, check=True
            )
            for arguments, key in (
                (["providers"], "providers"),
                (["models", "--provider", "anthropic"], "models"),
                (["models", "--provider", "google"], "models"),
            ):
                result = subprocess.run(
                    [str(cli), *arguments],
                    cwd=temporary,
                    env=environment,
                    capture_output=True,
                    text=True,
                    check=True,
                )
                payload = json.loads(result.stdout)
                assert payload[key]
                if key == "providers":
                    assert {provider["id"] for provider in payload[key]} >= {
                        "openai",
                        "anthropic",
                        "google",
                    }
                else:
                    assert any(
                        model["image_input"] and model["reasoning"]
                        for model in payload[key]
                    )
            (runtime / "smoke.mjs").write_text(SMOKE, encoding="utf-8")
            subprocess.run(
                [node, "smoke.mjs"], cwd=runtime, env=environment, check=True
            )
            print(
                f"{manager}: wheel CLI setup + Node-only catalog + offline codec/imports OK ({record['runtime']})",
                flush=True,
            )


if __name__ == "__main__":
    main()
