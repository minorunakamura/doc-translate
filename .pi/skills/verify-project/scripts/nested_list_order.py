"""Issue #103 only: nested list order, old-split stop, and unchanged non-target workspaces.

Real CLI (tests/fixtures/resume_process.py; PDF run via scripts/pdf_progress_fixture.py)
-> real Node ai/tests/run-process.mjs faux, empty PATH. The pre-#103 code is `git archive <base> src` in an owned temp, placed first
on the child PYTHONPATH. Run from the repository root with the existing .venv Python.
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from importlib import import_module
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "tests"))

make_epub = import_module("test_epub").make_epub
_resume_tests = import_module("test_resume")
_run_tests = import_module("test_run")
helper, run_args = _run_tests.helper, _run_tests.run_args
SPLIT_UNITS_GUIDANCE = _resume_tests.SPLIT_UNITS_GUIDANCE
nested_list_epub = _resume_tests.nested_list_epub

XHTML = '<html xmlns="http://www.w3.org/1999/xhtml"><head/><body>{}</body></html>'
# Before/after differ; inline; two child lists; lead-only child; tail-only child;
# a formatted non-target item whose text after the child list is whitespace only.
TARGET = XHTML.format(
    '<ol start="3"><li id="p">Before <em>x</em><ul><li>Child</li></ul>'
    'After <em title="Tip">y</em><ol><li>Second</li></ol>Last</li>'
    "<li><ul><li>Lead child</li></ul>Only after</li>"
    "<li>Only before<ul><li>Tail child</li></ul> </li>"
    "<li>Formatted\n<ul>\n<li>Inner</li>\n</ul>\n</li></ol>"
)
NON_TARGET = XHTML.format(
    "<ol><li>Outer\n<ul>\n<li>Inner</li>\n</ul>\n</li>"
    "<li><p>Para</p>\n<ul><li>Deep</li></ul>\n</li>"
    "<li>Only before<ul><li>Tail child</li></ul> </li></ol>"
)
INTERNAL = ("correspondence mismatch", "Traceback")


def files(workspace):
    return {
        str(p.relative_to(workspace)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(workspace.rglob("*"))
        if p.is_file()
    }


class Runner:
    def __init__(self, evidence, owned, old_src):
        self.evidence, self.owned, self.old_src = evidence, owned, old_src
        self.python = str(Path(sys.executable).absolute())
        self.observations = []

    def entry(self, case, pdf_fixture=None):
        if pdf_fixture is None:
            script = [str(ROOT / "tests/fixtures/resume_process.py")]
        else:
            script = [
                str(ROOT / ".pi/skills/verify-project/scripts/pdf_progress_fixture.py"),
                "--fixture-dir",
                str(pdf_fixture),
            ]
        return [
            self.python,
            *script,
            "--helper",
            json.dumps(helper(case)),
            "--",
        ]

    def env(self, code):
        source = self.old_src if code == "old" else ROOT / "src"
        return {
            **os.environ,
            "PATH": "",
            "PYTHONPATH": str(source),
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
        }

    def imported(self, code):
        result = subprocess.run(
            [self.python, "-c", "import doc_translate.units as u; print(u.__file__)"],
            env=self.env(code),
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()

    def sent(self, case):
        path = case / "requests.jsonl"
        return len(path.read_text().splitlines()) if path.exists() else 0

    def invoke(self, name, code, case, arguments, expected, pdf_fixture=None):
        command = [*self.entry(case, pdf_fixture), *arguments]
        before = self.sent(case)
        result = subprocess.run(
            command, env=self.env(code), capture_output=True, text=True, check=False
        )
        (self.evidence / f"{name}.stdout").write_text(result.stdout)
        (self.evidence / f"{name}.stderr").write_text(result.stderr)
        record = {
            "name": name,
            "code": code,
            "command": [str(a).replace(str(self.owned), "<owned>") for a in arguments],
            "expected_exit": expected,
            "exit": result.returncode,
            "requests_added": self.sent(case) - before,
        }
        self.observations.append(record)
        assert result.returncode == expected, (name, result.returncode, result.stderr)
        return result, record


def translation_inputs(case):
    sent = []
    for line in (case / "requests.jsonl").read_text().splitlines():
        payload = json.loads(json.loads(line)["messages"][1]["content"][0]["text"])
        if not payload["context"].get("phase"):
            sent.extend(unit["source_text"] for unit in json.loads(payload["text"]))
    return sent


def order_case(runner):
    """(a) current code: run -> request/English/Japanese/source-map order."""
    case = runner.owned / "order"
    case.mkdir()
    source = make_epub(case, extra={"OPS/text/ch.xhtml": TARGET.encode()})
    workspace = case / "workspace"
    result, record = runner.invoke(
        "order-run",
        "current",
        case,
        run_args(source, workspace),
        0,
    )
    summary = json.loads(result.stdout)
    expected_sources = [
        "Before ⟦1⟧x⟦/1⟧",
        "Child",
        "After ⟦1⟧y⟦/1⟧",
        "Tip",
        "Second",
        "Last",
        "Lead child",
        "Only after",
        "Only before ",
        "Tail child",
        "Formatted\n\n",
        "Inner",
    ]
    sent = translation_inputs(case)
    assert sent == expected_sources, sent
    english = (workspace / summary["artifacts"]["english"]).read_text()
    japanese = (workspace / summary["artifacts"]["japanese"]).read_text()
    item = (
        '3. <a id="node-1-5"></a>{}Before <em>x</em>\n\n'
        '    - <a id="node-1-8"></a>{}Child\n\n'
        '    {}After <em title="{}">y</em>\n\n'
        '    1. <a id="node-1-11"></a>{}Second\n\n'
        "    {}Last\n\n"
        '4. <a id="node-1-12"></a>\n\n'
        '    - <a id="node-1-14"></a>{}Lead child\n\n'
        "    {}Only after\n\n"
        '5. <a id="node-1-15"></a>{}Only before \n\n'
        '    - <a id="node-1-17"></a>{}Tail child\n\n'
        '6. <a id="node-1-18"></a>{}Formatted\n\n\n\n'
        '    - <a id="node-1-20"></a>{}Inner'
    )
    for document, mark in ((english, ""), (japanese, "訳:")):
        expected = item.format(mark, mark, mark, f"{mark}Tip", *([mark] * 8))
        assert document.count(expected) == 1, document
        assert document.count('<a id="node-1-5"') == 1
        assert document.count("3. ") == 1
    mapping = json.loads((workspace / summary["artifacts"]["source_map"]).read_text())
    mapped = [
        (u["source_text"], u["output_anchor"])
        for u in mapping["units"]
        if not u["attribute"]
    ]
    assert [m[0] for m in mapped] == [s for s in expected_sources if s != "Tip"]
    assert [m[1] for m in mapped][:6] == [
        "node-1-5",
        "node-1-8",
        "node-1-5",
        "node-1-11",
        "node-1-5",
        "node-1-14",
    ]
    (runner.evidence / "order-requests-translation-inputs.json").write_text(
        json.dumps(sent, ensure_ascii=False, indent=2)
    )
    (runner.evidence / "order-english.md").write_text(english)
    (runner.evidence / "order-japanese.md").write_text(japanese)
    (runner.evidence / "order-source-map-units.json").write_text(
        json.dumps(mapped, ensure_ascii=False, indent=2)
    )
    record.update(
        {
            "translation_inputs": len(sent),
            "observed": "request/English/Japanese/source-map: before -> child -> after "
            "once each; marker 3. and parent anchor once; formatted non-target one unit",
        }
    )


def old_split_case(runner):
    """(b) old code run, current resume/render/status --verify stop with guidance."""
    case = runner.owned / "old-split"
    case.mkdir()
    source = nested_list_epub(case / "book.epub")
    workspace = case / "workspace"
    runner.invoke(
        "old-split-run-before-103",
        "old",
        case,
        run_args(source, workspace),
        0,
    )
    saved = files(workspace)
    for operation in (["resume", "--json"], ["render"], ["status", "--verify"]):
        name = "old-split-" + operation[0]
        result, record = runner.invoke(
            name, "current", case, [*operation, str(workspace)], 2
        )
        assert SPLIT_UNITS_GUIDANCE in result.stderr, result.stderr
        assert not any(text in result.stderr for text in INTERNAL), result.stderr
        assert record["requests_added"] == 0
        assert files(workspace) == saved
        record.update(
            {"guidance": True, "internal_text": False, "saved_bytes_unchanged": True}
        )


def comparable_status(stdout, workspace):
    """status --verify --json with only the elapsed-derived render digest/time removed."""
    status = json.loads(stdout)
    outputs = {
        name: hashlib.sha256((workspace / path).read_bytes()).hexdigest()
        for name, path in status.pop("artifacts").items()
        if name in {"english", "japanese", "source_map", "qa"}
    }
    status.pop("render_elapsed_seconds")
    return {"status": status, "outputs": outputs}


def unchanged_case(runner, name, source_factory=None):
    """(c) old-code workspace without target lists: resume sends 0, same result.

    EPUB comes from source_factory; without it, PDF runs through the public Docling
    converter/page backend fixture (scripts/pdf_progress_fixture.py) under the old code.
    resume re-renders (render_elapsed_seconds feeds the render digest), so the control
    is the pre-#103 resume on an identical copy of the same workspace.
    """
    case = runner.owned / name
    case.mkdir()
    workspace = case / "workspace"
    if source_factory is None:
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
        arguments = run_args(source_factory(case), workspace)
    runner.invoke(f"{name}-run-before-103", "old", case, arguments, 0, fixture)
    control = case / "control"
    shutil.copytree(workspace, control)
    preserved = {
        k: v
        for k, v in files(workspace).items()
        if not k.startswith("renders/") and k != "render.json"
    }
    results = {}
    for code, target in (("old", control), ("current", workspace)):
        label = "before-103" if code == "old" else "current"
        resumed, record = runner.invoke(
            f"{name}-resume-{label}", code, case, ["resume", str(target), "--json"], 0
        )
        assert record["requests_added"] == 0
        assert json.loads(resumed.stdout)["processing"]["status"] == "completed"
        status, _ = runner.invoke(
            f"{name}-status-{label}",
            code,
            case,
            ["status", str(target), "--verify", "--json"],
            0,
        )
        results[code] = comparable_status(status.stdout, target)
    assert results["current"] == results["old"], results
    assert {
        k: v
        for k, v in files(workspace).items()
        if not k.startswith("renders/") and k != "render.json"
    } == preserved
    record.update(
        {
            "status_verify_json_equal_to_before_103": "yes (render digest/elapsed masked)",
            "english_japanese_source_map_qa_bytes_equal": True,
            "saved_bytes_outside_renders_unchanged": True,
        }
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--base", default="8d4988b")
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
        with tempfile.TemporaryDirectory(prefix="nested-list-owned-") as temporary:
            owned = Path(temporary)
            archive = subprocess.run(
                ["git", "archive", args.base, "src"],
                cwd=ROOT,
                capture_output=True,
                check=True,
            )
            subprocess.run(
                ["tar", "-x", "-C", str(owned)], input=archive.stdout, check=True
            )
            runner = Runner(evidence, owned, owned / "src")
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
                    },
                    indent=2,
                )
            )
            observations = runner.observations
            order_case(runner)
            old_split_case(runner)
            unchanged_case(
                runner,
                "epub-formatted-non-target",
                lambda case: make_epub(
                    case, extra={"OPS/text/ch.xhtml": NON_TARGET.encode()}
                ),
            )
            unchanged_case(runner, "epub-default", make_epub)
            unchanged_case(runner, "pdf")
    finally:
        (evidence / "observations.json").write_text(
            json.dumps(observations, ensure_ascii=False, indent=2)
        )
        (evidence / "cleanup.txt").write_text(
            "only the owned TemporaryDirectory (old src, workspaces, artificial "
            "credentials) removed; children reaped by subprocess.run\n"
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
