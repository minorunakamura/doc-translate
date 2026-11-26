"""Issue #118 only: workspaces saved with the removed generation options stop.

Real CLI (tests/fixtures/resume_process.py; PDF run via scripts/pdf_progress_fixture.py)
-> real Node ai/tests/run-process.mjs faux, empty PATH, reusing the Runner of
scripts/nested_list_order.py. The pre-#118 code is `git archive <base> src ai` in an
owned temp: src first on the child PYTHONPATH, and ai/src built there with the existing
ai/node_modules (symlinked, no install) because the old Python checks the old helper's
`submitted` shape. Run from the repository root with the existing .venv Python.
"""

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from importlib import import_module
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
nested_list_order = import_module("nested_list_order")
ROOT = nested_list_order.ROOT
Runner = nested_list_order.Runner
make_epub, run_args = nested_list_order.make_epub, nested_list_order.run_args
OLD_WORKSPACE_GUIDANCE = import_module("test_resume").OLD_WORKSPACE_GUIDANCE
REMOVED = ("temperature", "maxTokens", "transport", "cacheRetention", "thinkingBudgets")
INTERNAL = ("validation error", "extra_forbidden", "Traceback")
OPERATIONS = (["resume", "--json"], ["render"], ["status", "--verify"])


class OptionsRunner(Runner):
    """Old runs use the pre-#118 helper; every other operation the current one."""

    def __init__(self, evidence, owned, old_src, old_ai):
        super().__init__(evidence, owned, old_src)
        self.old_ai, self.code = old_ai, "current"

    def entry(self, case, pdf_fixture=None):
        command = super().entry(case, pdf_fixture)
        if self.code == "old":
            helper = json.loads(command[-2])
            helper[1] = str(self.old_ai / "tests/run-process.mjs")
            command[-2] = json.dumps(helper)
        return command

    def invoke(self, name, code, case, arguments, expected, pdf_fixture=None):
        self.code = code
        return super().invoke(name, code, case, arguments, expected, pdf_fixture)


def build_old_helper(owned):
    ai = owned / "ai"
    (ai / "node_modules").symlink_to(ROOT / "ai/node_modules")
    subprocess.run(
        [str(ROOT / "ai/node_modules/.bin/tsc"), "-p", str(ai / "tsconfig.build.json")],
        check=True,
        capture_output=True,
    )
    assert (ai / "dist/main.js").is_file()
    return ai


def files(workspace):
    return {
        str(p.relative_to(workspace)): (
            hashlib.sha256(p.read_bytes()).hexdigest(),
            p.stat().st_mtime_ns,
        )
        for p in sorted(workspace.rglob("*"))
        if p.is_file()
    }


def _names_removed(value):
    if isinstance(value, dict):
        return any(
            (
                key in ("options", "submitted")
                and isinstance(item, dict)
                and set(item) & set(REMOVED)
            )
            or _names_removed(item)
            for key, item in value.items()
        )
    return isinstance(value, list) and any(_names_removed(item) for item in value)


def removed_keys(workspace):
    """Saved JSON files whose options/submitted objects still name a removed option."""
    return sorted(
        name
        for name in files(workspace)
        if name.endswith(".json")
        and _names_removed(json.loads((workspace / name).read_bytes()))
    )


def run_workspace(runner, name, code, case, fmt):
    case.mkdir()
    workspace = case / "workspace"
    if fmt == "pdf":
        fixture = case / "fixture"
        arguments = [
            *run_args(fixture / "own.pdf", workspace),
            "--pages",
            "2-7",
            "--artifacts-path",
            str(fixture / "models"),
            "--correction-model",
            "vision",
        ]
    else:
        fixture = None
        arguments = run_args(make_epub(case), workspace)
    result, record = runner.invoke(name, code, case, arguments, 0, fixture)
    assert json.loads(result.stdout)["processing"]["status"] == "completed"
    record["requests_sent"] = runner.sent(case)
    return workspace


def old_case(runner, fmt):
    """(a) pre-#118 run -> current resume/render/status --verify stop with guidance."""
    case = runner.owned / f"{fmt}-before-118"
    workspace = run_workspace(runner, f"{fmt}-run-before-118", "old", case, fmt)
    named = removed_keys(workspace)
    assert named, "the pre-#118 workspace did not save the removed options"
    runner.observations[-1]["files_naming_removed_options"] = len(named)
    saved = files(workspace)
    for operation in OPERATIONS:
        result, record = runner.invoke(
            f"{fmt}-old-{operation[0]}-{'-'.join(operation[1:]) or 'plain'}",
            "current",
            case,
            [*operation, str(workspace)],
            2,
        )
        assert f"doc-translate: error: {OLD_WORKSPACE_GUIDANCE}" in result.stderr, (
            result.stderr
        )
        assert not any(text in result.stderr for text in INTERNAL), result.stderr
        assert record["requests_added"] == 0
        assert files(workspace) == saved, "the old workspace changed"
        record.update(
            {
                "guidance": True,
                "internal_text": False,
                "all_bytes_and_mtimes_unchanged": True,
            }
        )


def current_case(runner, fmt):
    """(b) control: a current run -> the same operations succeed without sending."""
    case = runner.owned / f"{fmt}-current"
    workspace = run_workspace(runner, f"{fmt}-run-current", "current", case, fmt)
    assert removed_keys(workspace) == []
    for operation in OPERATIONS:
        result, record = runner.invoke(
            f"{fmt}-current-{operation[0]}-{'-'.join(operation[1:]) or 'plain'}",
            "current",
            case,
            [*operation, str(workspace)],
            0,
        )
        assert record["requests_added"] == 0
        assert "Traceback" not in result.stderr
        record["files_naming_removed_options"] = 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--base", default="4aae54e")
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
    observations = []
    try:
        with tempfile.TemporaryDirectory(prefix="options-old-owned-") as temporary:
            owned = Path(temporary)
            archive = subprocess.run(
                ["git", "archive", args.base, "src", "ai"],
                cwd=ROOT,
                capture_output=True,
                check=True,
            )
            subprocess.run(
                ["tar", "-x", "-C", str(owned)], input=archive.stdout, check=True
            )
            runner = OptionsRunner(
                evidence, owned, owned / "src", build_old_helper(owned)
            )
            observations = runner.observations
            imports = {
                "old": runner.imported("old"),
                "current": runner.imported("current"),
            }
            assert imports["old"].startswith(str(owned / "src"))
            assert imports["current"].startswith(str(ROOT / "src"))
            (evidence / "imports.json").write_text(
                json.dumps(
                    {
                        "base": args.base,
                        "old": imports["old"].replace(str(owned), "<owned>"),
                        "current": imports["current"],
                        "old_helper": "<owned>/ai/tests/run-process.mjs",
                        "current_helper": str(ROOT / "ai/tests/run-process.mjs"),
                    },
                    indent=2,
                )
            )
            for fmt in ("epub", "pdf"):
                old_case(runner, fmt)
                current_case(runner, fmt)
    finally:
        (evidence / "observations.json").write_text(
            json.dumps(observations, ensure_ascii=False, indent=2)
        )
        (evidence / "cleanup.txt").write_text(
            "only the owned TemporaryDirectory (old src, old helper build and its "
            "node_modules symlink, fixtures, workspaces, artificial credentials) removed; children reaped by subprocess.run\n"
        )
    for path in evidence.rglob("*"):
        if path.is_file():
            path.read_bytes()
    print(
        evidence,
        f"{len(observations)} CLI operations matched; cleanup/readback complete",
    )


if __name__ == "__main__":
    main()
