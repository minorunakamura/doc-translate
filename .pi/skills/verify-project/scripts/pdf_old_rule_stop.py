"""Issue #117 only: current-rule PDF workspaces still resume; old-rule ones stop.

Real CLI (scripts/pdf_progress_fixture.py: public Docling converter/page backend
replaced by the self-authored 9-page fixture) -> real Node ai/tests/run-process.mjs
faux. The pre-#117 code is `git archive <base> src` in an owned temp, placed first on
the child PYTHONPATH. Run from the repository root with the existing .venv Python.
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
FIXTURE_ENTRY = ROOT / ".pi/skills/verify-project/scripts/pdf_progress_fixture.py"
OLD_RULE_GUIDANCE = (
    "このworkspaceのPDF補正は現在と異なるルールで保存されています。"
    "新しいworkspaceで `run` し直してください。"
)
# Same rewrites as tests/test_run.py::test_resume_old_rule_pdf_workspace_...
OLD_RULES = {"v16": (17, "drawn-table-listing-v16"), "v1": (1, None)}


def files(workspace):
    return {
        str(p.relative_to(workspace)): (
            hashlib.sha256(p.read_bytes()).hexdigest(),
            p.stat().st_mtime_ns,
        )
        for p in sorted(workspace.rglob("*"))
        if p.is_file()
    }


def digests(workspace, keep=lambda name: True):
    return {k: v[0] for k, v in files(workspace).items() if keep(k)}


def stored(workspace, directory, data):
    digest = hashlib.sha256(data).hexdigest()
    path = workspace / directory / f"{digest}.json"
    path.write_bytes(data)
    return {"artifact": f"{directory}/{digest}.json", "sha256": digest}


def rewrite_old_rule(workspace, schema, rule):
    """Model a workspace corrected under an earlier adoption rule, re-hashed."""
    runs = {}
    for path in sorted((workspace / "attempts/pdf-correction").glob("*.json")):
        record = json.loads(path.read_bytes())
        if rule is None:
            del record["request"]["context"]["adoption_rule"]
        else:
            record["request"]["context"]["adoption_rule"] = rule
        runs[path.stem] = stored(
            workspace, "attempts/pdf-correction", json.dumps(record).encode()
        )
        path.unlink()
    chunks = {}
    for path in sorted((workspace / "corrections/pdf").glob("*.json")):
        chunk = json.loads(path.read_bytes())
        chunk["schema_version"] = schema
        chunk["run"] = runs[chunk["run"]["sha256"]]
        data = json.dumps(chunk, ensure_ascii=False, separators=(",", ":")).encode()
        chunks[path.stem] = stored(workspace, "corrections/pdf", data)["sha256"]
        path.unlink()
    manifest_path = workspace / "run.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["chunk_sha256s"] = [chunks[d] for d in manifest["chunk_sha256s"]]
    manifest_path.write_text(json.dumps(manifest))
    return len(runs), len(chunks)


class Runner:
    def __init__(self, evidence, owned, old_src):
        self.evidence, self.owned, self.old_src = evidence, owned, old_src
        self.python = str(Path(sys.executable).absolute())
        self.node = shutil.which("node")
        assert self.node, "existing Node required; no install"
        self.observations = []

    def helper(self, case):
        settings = case / "faux.json"
        if not settings.exists():
            settings.write_text("{}")
        return [
            self.node,
            str(ROOT / "ai/tests/run-process.mjs"),
            str(case / "credentials"),
            str(settings),
            str(case / "requests.jsonl"),
        ]

    def env(self, code, empty_path):
        source = self.old_src if code == "old" else ROOT / "src"
        env = {
            **os.environ,
            "PYTHONPATH": str(source),
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
        }
        if empty_path:
            env["PATH"] = ""
        return env

    def imported(self, code):
        result = subprocess.run(
            [
                self.python,
                "-c",
                "import doc_translate.pdf_correction as m; print(m.__file__)",
            ],
            env=self.env(code, False),
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()

    def sent(self, case):
        path = case / "requests.jsonl"
        return path.read_bytes() if path.exists() else b""

    def invoke(self, name, code, case, arguments, expected, *, helper=None):
        command = [
            self.python,
            str(FIXTURE_ENTRY),
            "--fixture-dir",
            str(case / "fixture"),
            "--helper",
            json.dumps(helper or self.helper(case)),
            "--",
            *arguments,
        ]
        empty_path = arguments[0] != "run"
        before = self.sent(case)
        result = subprocess.run(
            command,
            env=self.env(code, empty_path),
            capture_output=True,
            text=True,
            check=False,
        )
        (self.evidence / f"{name}.stdout").write_text(result.stdout)
        (self.evidence / f"{name}.stderr").write_text(result.stderr)
        record = {
            "name": name,
            "code": code,
            "empty_path": empty_path,
            "command": [str(a).replace(str(self.owned), "<owned>") for a in arguments],
            "expected_exit": expected,
            "exit": result.returncode,
            "requests_unchanged": self.sent(case) == before,
        }
        self.observations.append(record)
        assert result.returncode == expected, (name, result.returncode, result.stderr)
        return result, record

    def run_pdf(self, name, code, case):
        case.mkdir()
        workspace = case / "workspace"
        fixture = case / "fixture"
        result, record = self.invoke(
            name,
            code,
            case,
            [
                "run",
                str(fixture / "own.pdf"),
                "--workspace",
                str(workspace),
                "--pages",
                "2-7",
                "--artifacts-path",
                str(fixture / "models"),
                "--provider",
                "faux",
                "--model",
                "text",
                "--thinking",
                "off",
                "--correction-model",
                "vision",
                "--json",
            ],
            0,
        )
        assert json.loads(result.stdout)["processing"]["status"] == "completed"
        record["requests_sent"] = len(self.sent(case).splitlines())
        return workspace


def current_rule_case(runner):
    """(a) pre-#117 run -> current resume sends 0; current run saves the same chunks."""
    case = runner.owned / "before-117"
    workspace = runner.run_pdf("current-rule-run-before-117", "old", case)

    # resume re-renders: renders/ and the render.json pointer may change. run.json
    # was observed byte-identical, so it is held to the same check as every file.
    def kept(name):
        return not name.startswith("renders/") and name != "render.json"

    preserved = digests(workspace, kept)
    result, record = runner.invoke(
        "current-rule-resume-current",
        "current",
        case,
        ["resume", "--json", str(workspace)],
        0,
    )
    assert record["requests_unchanged"], "resume sent a request"
    assert json.loads(result.stdout)["processing"]["status"] == "completed"
    assert digests(workspace, kept) == preserved
    record.update(
        {
            "saved_bytes_unchanged_except": ["render.json", "renders/"],
            "run_json_bytes_unchanged": True,
        }
    )

    fresh_case = runner.owned / "current"
    fresh = runner.run_pdf("current-rule-run-current", "current", fresh_case)
    before = digests(workspace, lambda n: n.startswith("corrections/pdf/"))
    after = digests(fresh, lambda n: n.startswith("corrections/pdf/"))
    (old_draft,), (new_draft,) = (
        [json.loads(p.read_bytes()) for p in (w / "drafts/pdf").glob("*.json")]
        for w in (workspace, fresh)
    )
    # The draft records the absolute --artifacts-path/model file paths and a
    # hash-seed-ordered engine list; its digest feeds the attempt and the chunk.
    assert draft_without_run_paths(old_draft) == draft_without_run_paths(new_draft)
    old_chunks, new_chunks = (
        sorted(
            (chunk_without_run_links(w / name) for name in found),
            key=lambda c: c["owned_pages"],
        )
        for w, found in ((workspace, before), (fresh, after))
    )
    runner.observations[-1].update(
        {
            "chunks_before_117": len(before),
            "chunks_current": len(after),
            "chunk_bytes_equal_to_before_117": sorted(before.values())
            == sorted(after.values()),
            "chunk_bytes_differ_because": "draft_sha256 differs (draft records the "
            "absolute --artifacts-path and model file paths, and a hash-seed-ordered "
            "supported_engines list); run differs (attempt has draft_sha256, "
            "request_id, elapsed_seconds)",
            "drafts_equal_without_paths_and_engine_order": True,
            "chunks_equal_without_draft_sha256_and_run": old_chunks == new_chunks,
        }
    )
    assert old_chunks == new_chunks, "current run adopted different chunk content"
    return fresh_case


def draft_without_run_paths(draft):
    options = draft["settings"]["pipeline_options"]
    options.pop("artifacts_path")
    spec = options["chart_extraction_options"]["model_spec"]
    spec["supported_engines"] = sorted(spec["supported_engines"])
    for model in draft["models"]:
        for file in model["files"]:
            file["artifact"] = file["artifact"].rsplit("/fixture/models/", 1)[1]
    return draft


def chunk_without_run_links(path):
    chunk = json.loads(path.read_bytes())
    del chunk["draft_sha256"], chunk["run"]
    return chunk


def old_rule_case(runner, fresh_case, label, schema, rule):
    """(b) a current workspace rewritten to an older rule: resume stops, render/status work."""
    case = runner.owned / f"old-rule-{label}"
    case.mkdir()
    shutil.copy2(fresh_case / "requests.jsonl", case / "requests.jsonl")
    workspace = case / "workspace"
    shutil.copytree(fresh_case / "workspace", workspace)
    attempts, chunks = rewrite_old_rule(workspace, schema, rule)
    saved = files(workspace)
    never = [str(case / "must-not-launch")]
    result, record = runner.invoke(
        f"old-rule-{label}-resume",
        "current",
        case,
        ["resume", "--json", str(workspace)],
        2,
        helper=never,
    )
    assert f"doc-translate: error: {OLD_RULE_GUIDANCE}" in result.stderr, result.stderr
    assert "Traceback" not in result.stderr
    assert "validation error" not in result.stderr
    assert result.stdout == ""
    assert record["requests_unchanged"]
    assert files(workspace) == saved, "resume changed the old-rule workspace"
    record.update(
        {
            "rewritten": {"schema_version": schema, "adoption_rule": rule},
            "rewritten_attempts": attempts,
            "rewritten_chunks": chunks,
            "guidance": True,
            "internal_text": False,
            "stdout_empty": True,
            "all_bytes_and_mtimes_unchanged": True,
        }
    )
    rendered, record = runner.invoke(
        f"old-rule-{label}-render",
        "current",
        case,
        ["render", str(workspace)],
        0,
        helper=never,
    )
    assert json.loads(rendered.stdout)["processing"]["status"] == "completed"
    assert record["requests_unchanged"]
    _, record = runner.invoke(
        f"old-rule-{label}-status",
        "current",
        case,
        ["status", str(workspace)],
        0,
        helper=never,
    )
    assert record["requests_unchanged"]
    verified, record = runner.invoke(
        f"old-rule-{label}-status-verify",
        "current",
        case,
        ["status", "--verify", "--json", str(workspace)],
        0,
        helper=never,
    )
    status = json.loads(verified.stdout)
    assert status["processing"]["status"] == "completed"
    assert status["render_current"] is True
    assert record["requests_unchanged"]
    record.update({"processing": "completed", "render_current": True})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--base", default="f9afb23")
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
        with tempfile.TemporaryDirectory(prefix="pdf-old-rule-owned-") as temporary:
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
                    },
                    indent=2,
                )
            )
            fresh_case = current_rule_case(runner)
            for label, (schema, rule) in OLD_RULES.items():
                old_rule_case(runner, fresh_case, label, schema, rule)
    finally:
        (evidence / "observations.json").write_text(
            json.dumps(observations, ensure_ascii=False, indent=2)
        )
        (evidence / "cleanup.txt").write_text(
            "only the owned TemporaryDirectory (old src, fixtures, workspaces, "
            "artificial credentials) removed; children reaped by subprocess.run\n"
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
