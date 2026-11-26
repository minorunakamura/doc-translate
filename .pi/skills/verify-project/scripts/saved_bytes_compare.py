"""Issue #121 only: saved workspace bytes are identical before/after the store refactor.

Real CLI (tests/fixtures/resume_process.py; PDF run via scripts/pdf_progress_fixture.py)
-> real Node ai/tests/run-process.mjs faux, empty PATH. The base code is `git archive <base> src`
in an owned temp, placed first on the child PYTHONPATH. Each child replaces only
doc_translate.inference.uuid4 (counter UUIDs), time.monotonic (constant) and the Node
reported generate elapsed_ms (constant), with PYTHONHASHSEED=0, so that two runs of the same input save the same
bytes. Old and current run at the same absolute case
path in turn (the old case is moved aside first) because saved manifests keep input paths.
Run from the repository root with the existing .venv Python.
"""

import argparse
import json
import runpy
import subprocess
import sys
import tempfile
from pathlib import Path

from nested_list_order import Runner as BaseRunner
from nested_list_order import files, helper, make_epub, run_args

ROOT = Path(__file__).resolve().parents[4]
SCRIPTS = ROOT / ".pi/skills/verify-project/scripts"


def child():
    """Deterministic child: `saved_bytes_compare.py --child ENTRY ARGS...`."""
    import itertools
    import threading
    import time
    import uuid
    from unittest.mock import patch

    from doc_translate import inference

    counter, lock = itertools.count(1), threading.Lock()

    def counted_uuid4():
        with lock:
            return uuid.UUID(int=next(counter))

    read_records = inference.read_records

    def fixed_elapsed_records(stream):
        # Node measures elapsed_ms itself (performance.now); fix it like monotonic.
        for record in read_records(stream):
            if "elapsed_ms" in record.payload:
                record.payload["elapsed_ms"] = 1
            yield record

    entry = sys.argv[2]
    sys.argv = [entry, *sys.argv[3:]]
    with (
        patch.object(inference, "uuid4", counted_uuid4),
        patch.object(inference, "read_records", fixed_elapsed_records),
        patch.object(time, "monotonic", lambda: 1000.0),
    ):
        runpy.run_path(entry, run_name="__main__")


class Runner(BaseRunner):
    def __init__(self, evidence, owned, old_src):
        super().__init__(evidence, owned, old_src)
        self.hashes = {}

    def env(self, code):
        # Docling saves set-valued settings (supported_engines) in hash order.
        return {**super().env(code), "PYTHONHASHSEED": "0"}

    def invoke_step(self, flow, step, code, case, arguments, pdf_fixture=None):
        if pdf_fixture is None:
            entry = [str(ROOT / "tests/fixtures/resume_process.py")]
        else:
            entry = [
                str(SCRIPTS / "pdf_progress_fixture.py"),
                "--fixture-dir",
                str(pdf_fixture),
            ]
        command = [
            self.python,
            str(Path(__file__).resolve()),
            "--child",
            *entry,
            "--helper",
            json.dumps(helper(case)),
            "--",
            *arguments,
        ]
        before = self.sent(case)
        result = subprocess.run(
            command, env=self.env(code), capture_output=True, text=True, check=False
        )
        name = f"{flow}-{step}-{code}"
        (self.evidence / f"{name}.stdout").write_text(result.stdout)
        (self.evidence / f"{name}.stderr").write_text(result.stderr)
        hashes = files(case / "workspace")
        self.hashes.setdefault(flow, {}).setdefault(step, {})[code] = hashes
        record = {
            "flow": flow,
            "step": step,
            "code": code,
            "command": [str(a).replace(str(self.owned), "<owned>") for a in arguments],
            "exit": result.returncode,
            "requests_added": self.sent(case) - before,
            "workspace_files": len(hashes),
        }
        self.observations.append(record)
        assert result.returncode == 0, (name, result.returncode, result.stderr)
        return result

    def compare(self, flow):
        """Every step: same request count, same relative path -> sha256."""
        mismatches = {}
        for step, codes in self.hashes[flow].items():
            old, current = codes["old"], codes["current"]
            differ = sorted(
                path
                for path in old.keys() | current.keys()
                if old.get(path) != current.get(path)
            )
            if differ:
                mismatches[step] = {p: [old.get(p), current.get(p)] for p in differ}
        steps = [r for r in self.observations if r["flow"] == flow]
        for step in self.hashes[flow]:
            pair = {r["code"]: r for r in steps if r["step"] == step}
            for key in ("requests_added",):
                if pair["old"][key] != pair["current"][key]:
                    mismatches.setdefault(step, {})[key] = [
                        pair["old"][key],
                        pair["current"][key],
                    ]
        (self.evidence / f"{flow}-mismatches.json").write_text(
            json.dumps(mismatches, indent=2)
        )
        assert not mismatches, (flow, mismatches)


def epub_flow(runner, code):
    """run -> review --units -> revise --translations -> render."""
    case = runner.owned / "case"
    case.mkdir()
    # One EPUB (zip entries carry timestamps) is copied byte-for-byte into both runs.
    built = runner.owned / "input/book.epub"
    if not built.exists():
        built.parent.mkdir()
        make_epub(built.parent)
    source = case / "book.epub"
    source.write_bytes(built.read_bytes())
    workspace = case / "workspace"
    runner.invoke_step("epub", "1-run", code, case, run_args(source, workspace))
    runner.invoke_step(
        "epub",
        "2-review",
        code,
        case,
        ["review", "--json", "--events-json", str(workspace), "--units", "unit-1"],
    )
    source_digest = json.loads((workspace / "source-snapshot.json").read_text())
    translation_digest = json.loads(
        (workspace / "translation-snapshot.json").read_text()
    )
    # Same edit as tests/test_revise.py: keep the saved protected markers.
    saved = next(
        item["text"]
        for path in (workspace / "translations/chunks").glob("*.json")
        for item in json.loads(path.read_bytes())["answer"]["translations"]
        if item["unit_id"] == "unit-1"
    )
    patch = case / "patch.json"
    patch.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "base_source_sha256": source_digest["sha256"],
                "base_translation_sha256": translation_digest["sha256"],
                "translations": [
                    {
                        "unit_id": "unit-1",
                        "text": saved.replace("Original", "手動修正"),
                        "uncertainties": [],
                    }
                ],
            },
            ensure_ascii=False,
        )
    )
    runner.invoke_step(
        "epub",
        "3-revise",
        code,
        case,
        [
            "revise",
            str(workspace),
            "--translations",
            str(patch),
            "--reason",
            "用語確認",
        ],
    )
    runner.invoke_step("epub", "4-render", code, case, ["render", str(workspace)])
    return case


def pdf_flow(runner, code):
    """Public PDF run through the self-authored Docling boundary fixture."""
    case = runner.owned / "case"
    case.mkdir()
    fixture = case / "fixture"
    workspace = case / "workspace"
    arguments = [
        *run_args(fixture / "own.pdf", workspace),
        "--pages",
        "2-7",
        "--artifacts-path",
        str(fixture / "models"),
        "--correction-model",
        "vision",
    ]
    runner.invoke_step("pdf", "1-run", code, case, arguments, fixture)
    return case


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--base", default="4065822")
    args = parser.parse_args()
    evidence = args.evidence.resolve()
    for name, command in (
        ("revision.txt", ["git", "rev-parse", "HEAD"]),
        ("dirty.txt", ["git", "status", "--short"]),
        ("scope.diff", ["git", "diff"]),
    ):
        (evidence / name).write_text(
            subprocess.check_output(command, text=True, cwd=ROOT)
        )
    observations, hashes = [], {}
    try:
        with tempfile.TemporaryDirectory(prefix="saved-bytes-owned-") as temporary:
            owned = Path(temporary)
            archive = subprocess.run(
                ["git", "archive", args.base, "src"],
                cwd=ROOT,
                capture_output=True,
                check=True,
            )
            (owned / "base").mkdir()
            subprocess.run(
                ["tar", "-x", "-C", str(owned / "base")],
                input=archive.stdout,
                check=True,
            )
            runner = Runner(evidence, owned, owned / "base/src")
            observations, hashes = runner.observations, runner.hashes
            imports = {
                "old": runner.imported("old"),
                "current": runner.imported("current"),
            }
            assert imports["old"].startswith(str(owned / "base/src"))
            assert imports["current"].startswith(str(ROOT / "src"))
            (evidence / "imports.json").write_text(
                json.dumps(
                    {
                        "base": args.base,
                        "old": imports["old"].replace(str(owned), "<owned>"),
                        "current": imports["current"],
                    },
                    indent=2,
                )
            )
            for flow, run_flow in (("epub", epub_flow), ("pdf", pdf_flow)):
                for code in ("old", "current"):
                    run_flow(runner, code).rename(owned / f"{flow}-{code}")
                runner.compare(flow)
    finally:
        (evidence / "observations.json").write_text(
            json.dumps(observations, ensure_ascii=False, indent=2)
        )
        (evidence / "file-hashes.json").write_text(json.dumps(hashes, indent=2))
        (evidence / "cleanup.txt").write_text(
            "only the owned TemporaryDirectory (base src, cases, workspaces, artificial "
            "credentials) removed; children reaped by subprocess.run\n"
        )
    for path in evidence.rglob("*"):
        if path.is_file():
            path.read_bytes()
    print(
        evidence,
        f"{len(observations)} CLI operations; old/current saved bytes identical; "
        "cleanup/readback complete",
    )


if __name__ == "__main__":
    if sys.argv[1:2] == ["--child"]:
        child()
    else:
        main()
