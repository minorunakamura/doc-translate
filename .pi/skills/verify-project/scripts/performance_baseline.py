"""Bounded, offline Issue #54 recipes (not a general benchmark runner). Run at repo root."""

import argparse
import base64
import hashlib
import importlib.metadata
import json
import os
import platform
import shutil
import signal
import statistics
import subprocess
import sys
import tempfile
import time
from importlib import import_module
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
OPERATION = Path(__file__).with_name("performance_operation.py")
sys.path.insert(0, str(ROOT / "tests"))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def immutable(workspace):
    return {
        str(path.relative_to(workspace)): digest(path)
        for directory in ("originals", "drafts", "corrections", "attempts", "snapshots")
        for path in (workspace / directory).rglob("*")
        if path.is_file()
    }


def distribution(values):
    return {
        "raw": values,
        "median": statistics.median(values),
        "min": min(values),
        "max": max(values),
    }


def epub_source(directory, sections, multiple=False):
    epub_fixtures = import_module("test_epub")

    directory.mkdir()
    resources = 2 if multiple else 1
    manifest, spine, extra = [], [], {}
    for resource in range(resources):
        name = f"section-{resource}.xhtml"
        manifest.append(
            f'<item id="ch{resource}" href="{name}" media-type="application/xhtml+xml"/>'
        )
        spine.append(f'<itemref idref="ch{resource}"/>')
        body = "".join(
            f"<h1>Section {i}</h1><p>Original queue record.</p>"
            for i in range(
                resource * sections // resources, (resource + 1) * sections // resources
            )
        )
        extra[f"OPS/{name}"] = (
            f'<html xmlns="http://www.w3.org/1999/xhtml"><body>{body}</body></html>'
        ).encode()
    return epub_fixtures.make_epub(
        directory, manifest="".join(manifest), spine="".join(spine), extra=extra
    )


def requests(log):
    rows = [json.loads(line) for line in log.read_text().splitlines()]
    text_bytes = image_bytes = images = 0
    phases = {}
    scopes = []
    for row in rows:
        content = row["messages"][1]["content"]
        request = json.loads(content[0]["text"])
        context = request["context"]
        phase = context.get(
            "phase", "correction" if "owned_pages" in context else "translation"
        )
        phases[phase] = phases.get(phase, 0) + 1
        if phase == "correction":
            scopes.append(
                {key: context[key] for key in ("owned_pages", "reference_pages")}
            )
        for message in row["messages"]:
            for part in (
                message["content"] if isinstance(message["content"], list) else []
            ):
                if part["type"] == "text":
                    text_bytes += len(part["text"].encode())
                elif part["type"] == "image":
                    images += 1
                    image_bytes += len(base64.b64decode(part["data"], validate=True))
    return {
        "actual_faux_calls": len(rows),
        "phases": phases,
        "sent_message_text_utf8_bytes": text_bytes,
        "decoded_image_bytes": image_bytes,
        "images": images,
        "correction_scopes": scopes,
    }


def interrupt_and_wait(process):
    # The process group belongs to this invocation, never a name-selected process.
    try:
        os.killpg(process.pid, signal.SIGINT)
    except ProcessLookupError:
        pass
    try:
        return process.communicate(timeout=45)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        return process.communicate()


def invoke(command, env, directory, budget):
    started = time.perf_counter()
    process = subprocess.Popen(
        command,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    timed_out = False
    stdout, stderr = "", ""
    try:
        stdout, stderr = process.communicate(timeout=budget)
    except subprocess.TimeoutExpired:
        timed_out = True
        stdout, stderr = interrupt_and_wait(process)
    except BaseException:
        stdout, stderr = interrupt_and_wait(process)
        raise
    finally:
        record = {
            "command": command,
            "exit": process.returncode,
            "timed_out": timed_out,
            "external_process_wall_seconds": time.perf_counter() - started,
        }
        (directory / "stdout.json").write_text(stdout)
        (directory / "stderr.txt").write_text(stderr)
        (directory / "command.json").write_text(json.dumps(record, indent=2))
    assert not timed_out and process.returncode == 0, record
    return record, json.loads(stdout)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--evidence", type=Path, required=True, help="new ignored work/ directory"
    )
    parser.add_argument(
        "--feature", choices=("render", "run-resume", "all"), default="all"
    )
    parser.add_argument(
        "--diagnostics",
        action="store_true",
        help="separate representative profile/counter passes",
    )
    args = parser.parse_args()
    assert Path.cwd() == ROOT, "run from repository root"
    evidence = args.evidence.absolute()
    assert evidence.parent == ROOT / "work", (
        "evidence must be a new direct child of work/"
    )
    assert (
        subprocess.run(
            ["git", "check-ignore", "--quiet", str(evidence)], check=False
        ).returncode
        == 0
    )
    evidence.mkdir(exist_ok=False)
    node = shutil.which("node")
    assert node, "existing Node required; do not install"
    env = {**os.environ, "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"}
    from doc_translate.ai_runtime import _resources

    resource = _resources() / "dist"
    build = {
        str(p.relative_to(ROOT / "ai/dist")): digest(p)
        for p in (ROOT / "ai/dist").rglob("*")
        if p.is_file()
    }
    installed = {
        str(p.relative_to(resource)): digest(p)
        for p in resource.rglob("*")
        if p.is_file()
    }
    assert build and build == installed, (
        "existing installed helper resources must match; do not rebuild"
    )
    manifest = {
        "invocation": sys.argv,
        "revision": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True
        ).strip(),
        "python": sys.version,
        "executable": sys.executable,
        "platform": platform.platform(),
        "node": subprocess.check_output([node, "--version"], text=True).strip(),
        "dependencies": {
            name: importlib.metadata.version(name)
            for name in ("pydantic", "Pillow", "docling", "docling-core")
        },
        "helper_dist_sha256": build,
        "source_sha256": {
            str(p.relative_to(ROOT)): digest(p)
            for p in [
                *sorted((ROOT / "src/doc_translate").glob("*.py")),
                OPERATION,
                Path(__file__).resolve(),
                ROOT / "tests/test_pdf.py",
                ROOT / "tests/test_run.py",
                ROOT / "tests/test_epub.py",
                ROOT / "ai/tests/run-process.mjs",
                ROOT / "ai/tests/no-network.mjs",
            ]
        },
        "conditions": "fresh Python per operation; warm filesystem cache, not flushed; sequential; no network faux; empty settings; translation workers existing default 2, preparation/correction sequential; no production changes",
        "stop_budget": {
            "operation_seconds": 120,
            "growth_operation_seconds": 180,
            "whole_invocation_seconds": 1800,
            "interrupt_drain_seconds": 45,
        },
        "comparison": "before/after are repeatability controls, not speed improvement",
    }
    (evidence / "manifest.json").write_text(json.dumps(manifest, indent=2))
    (evidence / "dirty.txt").write_bytes(
        subprocess.check_output(["git", "status", "--short"])
    )
    (evidence / "scope.diff").write_bytes(subprocess.check_output(["git", "diff"]))
    observations = []
    started = time.perf_counter()
    temporary = None
    try:
        with tempfile.TemporaryDirectory(prefix="issue54-owned-") as owned:
            temporary = Path(owned)
            sources = {}
            if args.feature != "render":
                for size, multi in ((2, False), (20, False), (200, False), (2, True)):
                    setup = time.perf_counter()
                    source = epub_source(
                        temporary / f"input-{size}-{multi}", size, multi
                    )
                    sources[size, multi] = source
                    observations.append(
                        {
                            "input": f"epub-{size}-{multi}",
                            "sha256": digest(source),
                            "input_generation_wall_seconds": time.perf_counter()
                            - setup,
                        }
                    )
            cases = []
            if args.feature != "run-resume":
                cases += [
                    ("render", format, size, False)
                    for format in ("pdf", "epub")
                    for size in (50, 200, 500)
                ]
            if args.feature != "render":
                cases += [("run-resume", "pdf", size, False) for size in (6, 35)]
                cases += [("run-resume", "epub", size, False) for size in (2, 20, 200)]
                cases += [("run-resume", "epub", 2, True)]
            for feature, format, size, multi in cases:
                passes = (
                    ["growth"]
                    if size == 200 and feature == "run-resume"
                    else ["boundary"]
                    if multi
                    else ["before", "after"]
                )
                representative = size == (
                    500 if feature == "render" else 35 if format == "pdf" else 20
                )
                if args.diagnostics and representative:
                    passes += ["diagnostic"]
                for label in passes:
                    repeats = 1 if label in ("boundary", "diagnostic") else 3
                    for repeat in range(repeats):
                        assert time.perf_counter() - started < 1800, (
                            "whole-invocation stop budget reached"
                        )
                        name = f"{feature}-{format}-{size}{'-multi' if multi else ''}-{label}-{repeat + 1}"
                        directory = evidence / name
                        directory.mkdir()
                        case = temporary / name
                        case.mkdir()
                        workspace, log = case / "workspace", case / "requests.jsonl"
                        settings = case / "settings.json"
                        settings.write_text("{}")
                        helper = [
                            node,
                            str(ROOT / "ai/tests/run-process.mjs"),
                            str(case / "credentials"),
                            str(settings),
                            str(log),
                        ]
                        operations = (
                            ["render"] if feature == "render" else ["run", "resume"]
                        )
                        for operation in operations:
                            output = directory / operation
                            output.mkdir()
                            command = [
                                sys.executable,
                                str(OPERATION),
                                "--format",
                                format,
                                "--size",
                                str(size),
                                "--operation",
                                operation,
                                "--workspace",
                                str(workspace),
                                "--metrics",
                                str(output / "metrics.json"),
                            ]
                            if feature != "render":
                                command += [
                                    "--helper",
                                    json.dumps(helper),
                                    "--source",
                                    str(
                                        sources[size, multi]
                                        if format == "epub"
                                        else case / "fixture/own.pdf"
                                    ),
                                    "--fixture-dir",
                                    str(case / "fixture"),
                                ]
                            if label == "diagnostic":
                                command += [
                                    "--observe",
                                    str(output / "diagnostic.json"),
                                ]
                            previous = (
                                immutable(workspace) if operation == "resume" else None
                            )
                            previous_log = (
                                log.read_bytes() if operation == "resume" else None
                            )
                            remaining = 1800 - (time.perf_counter() - started)
                            assert remaining > 0, "whole-invocation stop budget reached"
                            record, summary = invoke(
                                command,
                                env,
                                output,
                                min(remaining, 180 if label == "growth" else 120),
                            )
                            assert summary["processing"]["status"] == (
                                "partial" if feature == "render" else "completed"
                            )
                            for artifact in summary["artifacts"].values():
                                assert (workspace / artifact).is_file()
                            qa = json.loads(
                                (workspace / summary["artifacts"]["qa"]).read_bytes()
                            )
                            mapping = json.loads(
                                (
                                    workspace / summary["artifacts"]["source_map"]
                                ).read_bytes()
                            )
                            assert (
                                "uncertainties" in qa
                                and "units" in mapping
                                and "blocks" in mapping
                            )
                            assert len(mapping["units"]) == (
                                size
                                if feature == "render" or format == "pdf"
                                else size * 2
                            )
                            if operation == "resume":
                                assert previous is not None
                                assert log.read_bytes() == previous_log, (
                                    "unchanged resume appended actual faux requests"
                                )
                                assert all(
                                    digest(workspace / path) == sha
                                    for path, sha in previous.items()
                                ), "old immutable bytes changed"
                                (output / "immutable-before.json").write_text(
                                    json.dumps(previous, indent=2)
                                )
                            saved = list((workspace / "attempts").rglob("*.json"))
                            attempts = [json.loads(p.read_bytes()) for p in saved]
                            metrics = json.loads((output / "metrics.json").read_bytes())
                            observation = {
                                "case": name,
                                "label": label,
                                "feature": feature,
                                "format": format,
                                "size": size,
                                "operation": operation,
                                **record,
                                **metrics,
                                "input_sha256": digest(case / "fixture/own.pdf")
                                if format == "pdf" and feature != "render"
                                else digest(sources[size, multi])
                                if feature != "render"
                                else digest(
                                    next(
                                        (workspace / "snapshots/source").glob("*.json")
                                    )
                                ),
                                "saved_render_build_seconds_excludes_writes": summary[
                                    "render_elapsed_seconds"
                                ],
                                "saved_attempt_records": len(saved),
                                "saved_attempt_record_bytes": sum(
                                    p.stat().st_size for p in saved
                                ),
                                "saved_underlying_attempts": sum(
                                    len(a.get("attempts", [])) for a in attempts
                                ),
                                "saved_snapshots": len(
                                    list((workspace / "snapshots").rglob("*.json"))
                                ),
                                "old_immutable_unchanged": True
                                if operation == "resume"
                                else None,
                                "resume_appended_actual_requests": 0
                                if operation == "resume"
                                else None,
                                "units": len(mapping["units"]),
                                "qa_uncertainties": len(qa["uncertainties"]),
                            }
                            if feature != "render":
                                observation["requests"] = requests(log)
                                if format == "pdf":
                                    assert summary["scope"]["run"][
                                        "owned_pages"
                                    ] == list(range(2, size + 2))
                                    for scope in observation["requests"][
                                        "correction_scopes"
                                    ]:
                                        assert set(scope["owned_pages"]).isdisjoint(
                                            scope["reference_pages"]
                                        )
                                        assert set(
                                            scope["owned_pages"]
                                            + scope["reference_pages"]
                                        ) <= set(range(2, size + 2))
                                (output / "requests.jsonl").write_bytes(
                                    log.read_bytes()
                                )
                            for key in ("qa", "source_map", "summary"):
                                (output / f"saved-{key}.json").write_bytes(
                                    (workspace / summary["artifacts"][key]).read_bytes()
                                )
                            (output / "saved-attempts.json").write_text(
                                json.dumps(attempts, indent=2)
                            )
                            observations.append(observation)
                            (evidence / "observations.json").write_text(
                                json.dumps(observations, indent=2)
                            )
    finally:
        (evidence / "observations.json").write_text(json.dumps(observations, indent=2))
        removed = temporary is None or not temporary.exists()
        (evidence / "cleanup.txt").write_text(
            "Owned TemporaryDirectory removed (inputs/models/artificial credentials/workspaces); subprocess handles waited; evidence retained.\n"
            if removed
            else f"Cleanup incomplete: {temporary}\n"
        )
        for path in evidence.rglob("*"):
            if path.is_file():
                path.read_bytes()
        assert removed, "owned temporary state remains"
    grouped = {}
    for row in observations:
        if row.get("label") not in ("before", "after", "growth"):
            continue
        key = f"{row['feature']}-{row['format']}-{row['size']}-{row['operation']}-{row['label']}"
        grouped.setdefault(key, []).append(row["operation_wall_seconds"])
    (evidence / "statistics.json").write_text(
        json.dumps({k: distribution(v) for k, v in grouped.items()}, indent=2)
    )
    for path in evidence.rglob("*"):
        if path.is_file():
            path.read_bytes()
    print(evidence)
    print(
        "Expected public operation/status/artifacts/provenance/QA and unchanged-resume checks matched; cleanup and evidence reread complete."
    )


if __name__ == "__main__":
    main()
