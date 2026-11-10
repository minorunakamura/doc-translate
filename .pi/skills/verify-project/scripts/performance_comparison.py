"""Issue #62 fixed old/current public-operation comparison; no checkout or provider changes."""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from importlib.metadata import version
from pathlib import Path

from performance_baseline import (
    ROOT,
    digest,
    distribution,
    epub_source,
    immutable,
    invoke,
    requests,
    save_render_artifacts,
)
from preparation_baseline import files

SCRIPTS = Path(__file__).resolve().parent
REVISION = "8f19b4b"


def compare_saved_outputs(before, after, independent=False):
    excluded = []
    for name in (
        "saved-japanese.md",
        "saved-english.md",
        "saved-source_map.json",
        "saved-qa.json",
    ):
        left, right = (before / name).read_bytes(), (after / name).read_bytes()
        if independent and name in ("saved-source_map.json", "saved-qa.json"):
            left, right = json.loads(left), json.loads(right)
            for value in (left, right):
                if name == "saved-source_map.json":
                    for unit in value["units"]:
                        if "source_snapshot_sha256" in unit:
                            excluded.append(unit["source_snapshot_sha256"])
                            unit["source_snapshot_sha256"] = (
                                "independently-generated-source"
                            )
                else:
                    for check in value["checks"]:
                        if check["name"] == "pdf_chunk_ownership":
                            check["details"] = re.sub(
                                r"^chunk=[0-9a-f]{64};",
                                "chunk=independently-generated-chunk;",
                                check["details"],
                            )
        assert left == right, (before, name)
    assert files(before / "saved-assets") == files(after / "saved-assets")
    return {
        "equal": True,
        "markdown_assets": "all bytes",
        "qa_map": "all targets/order/checks and fields",
        "independent_generation_digest_fields": independent,
        "source_snapshot_digests": sorted(set(excluded)),
        "excluded": "summary elapsed/derived directory; independent run only: PDF ownership chunk digest and unit source_snapshot_sha256 (retained in original outputs)",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument(
        "--feature", choices=("render", "run-resume", "attempts"), required=True
    )
    parser.add_argument(
        "--smoke", action="store_true", help="small connection check, not acceptance"
    )
    args = parser.parse_args()
    evidence = args.evidence.absolute()
    assert Path.cwd() == ROOT and evidence.parent == ROOT / "work"
    assert (
        subprocess.run(
            ["git", "check-ignore", "--quiet", str(evidence)], check=False
        ).returncode
        == 0
    )
    evidence.mkdir(exist_ok=False)
    from doc_translate.ai_runtime import _resources

    build = files(ROOT / "ai/dist")
    assert len(build) == 10 and build == files(_resources() / "dist")
    for name, command in (
        ("revision.txt", ["git", "rev-parse", "HEAD"]),
        ("dirty.txt", ["git", "status", "--short"]),
        ("scope.diff", ["git", "diff"]),
    ):
        (evidence / name).write_bytes(subprocess.check_output(command))
    saved = evidence / "verification-sources"
    saved.mkdir()
    for name in (
        "performance_comparison",
        "performance_revision",
        "performance_baseline",
        "performance_operation",
        "preparation_baseline",
        "translation_baseline",
    ):
        shutil.copyfile(SCRIPTS / f"{name}.py", saved / f"{name}.py")
    (evidence / "manifest.json").write_text(
        json.dumps(
            {
                "command": sys.argv,
                "before_revision": subprocess.check_output(
                    ["git", "rev-parse", REVISION], text=True
                ).strip(),
                "python": sys.version,
                "node": subprocess.check_output(
                    ["node", "--version"], text=True
                ).strip(),
                "helper_dist_sha256": build,
                "script_sha256": files(saved),
                "dependencies": {
                    name: version(name)
                    for name in ("pydantic", "Pillow", "docling", "docling-core")
                },
                "fixture_sha256": {
                    str(path.relative_to(ROOT)): digest(path)
                    for path in [
                        *(
                            ROOT / "tests" / f"test_{name}.py"
                            for name in (
                                "pdf",
                                "run",
                                "epub",
                                "preparation",
                                "translation",
                            )
                        ),
                        *(
                            ROOT / "ai/tests" / f"{name}-process.mjs"
                            for name in ("run", "preparation", "translation")
                        ),
                        ROOT / "ai/tests/no-network.mjs",
                    ]
                },
                "conditions": "fresh Python per operation; warm filesystem/no flush; sequential foreground; same generated EPUB bytes and v1 seed; real Node/faux/no-network; preparation/PDF workers 1, translation 2",
                "series": "before v1 fresh and current v2 fresh are distinct cold request caches; unchanged resumes warm saved-result reuse",
                "budget": "existing invoke: operation 120s (EPUB200 180s), invocation 1800s checked between children, own group SIGINT/drain45s/SIGKILL/wait",
                "unknown": [
                    "real provider quality/rate limits/cost",
                    "real OCR/layout",
                    "physical IO",
                    "simultaneous aggregate RSS",
                ],
                "smoke_only": args.smoke,
            },
            indent=2,
        )
    )
    rows = []
    started = time.perf_counter()
    temporary = None
    try:
        with tempfile.TemporaryDirectory(
            prefix="issue62-comparison-owned-"
        ) as temporary_name:
            temporary = Path(temporary_name)
            archive = temporary / "old.tar"
            archive.write_bytes(
                subprocess.check_output(["git", "archive", REVISION, "src"])
            )
            with tarfile.open(archive) as archived:
                archived.extractall(temporary / "old", filter="data")
            sources = {"before": temporary / "old/src", "after": ROOT / "src"}
            (evidence / "source-hashes.json").write_text(
                json.dumps(
                    {side: files(source) for side, source in sources.items()}, indent=2
                )
            )

            def child(side, output, script, arguments):
                env = {
                    **os.environ,
                    "HF_HUB_OFFLINE": "1",
                    "TRANSFORMERS_OFFLINE": "1",
                    "PYTHONPATH": os.pathsep.join(
                        (str(sources[side]), str(SCRIPTS), str(ROOT / "tests"))
                    ),
                }
                command = [
                    sys.executable,
                    str(SCRIPTS / "performance_revision.py"),
                    "--source-root",
                    str(sources[side]),
                    "--imports",
                    str(output / "imports.json"),
                    *([str(script), *arguments] if script else arguments),
                ]
                remaining = 1800 - (time.perf_counter() - started)
                assert remaining > 0, "whole-invocation stop budget reached"
                return invoke(
                    command,
                    env,
                    output,
                    min(remaining, 180 if "200" in arguments else 120),
                )

            seed = evidence / "seed"
            if args.feature == "attempts":
                output = evidence / "seed-generation"
                output.mkdir()
                owned = temporary / "seed-owned"
                owned.mkdir()
                child(
                    "before",
                    output,
                    None,
                    [
                        "--v1-seed",
                        str(seed),
                        "--owned",
                        str(owned),
                        "--size",
                        "2" if args.smoke else "100",
                    ],
                )
                for log in owned.glob("*-requests.jsonl"):
                    shutil.copyfile(log, output / log.name)
                (evidence / "seed-hashes.json").write_text(
                    json.dumps(files(seed), indent=2)
                )
                cases = [
                    (stage, "pdf", 2 if args.smoke else 100, False)
                    for stage in ("prepare", "translate")
                ]
            elif args.feature == "render":
                cases = [
                    ("render", format, size, False)
                    for format in ("pdf", "epub")
                    for size in ((50,) if args.smoke else (50, 200, 500))
                ]
            else:
                cases = [
                    ("run", "pdf", size, False)
                    for size in ((6,) if args.smoke else (6, 35))
                ]
                cases += [
                    ("run", "epub", size, False)
                    for size in ((2,) if args.smoke else (2, 20, 200))
                ]
                cases += [("run", "epub", 2, True)]
            for stage, format, size, multi in cases:
                input_source = None
                if stage == "run" and format == "epub":
                    input_source = epub_source(
                        temporary / f"input-{size}-{multi}", size, multi
                    )
                    shutil.copyfile(
                        input_source, evidence / f"input-{size}-{multi}.epub"
                    )
                representative = size == (
                    500 if stage == "render" else 35 if format == "pdf" else 20
                )
                repeats = (
                    1
                    if args.smoke or multi
                    else 4
                    if representative and stage in ("render", "run")
                    else 3
                )
                for repeat in range(repeats):
                    diagnostic = repeat == 3
                    for side in ("before", "after"):
                        label = f"{stage}-{format}-{size}{'-multi' if multi else ''}-{repeat}-{side}"
                        case = temporary / label
                        case.mkdir()
                        workspace, log = case / "workspace", case / "requests.jsonl"
                        settings = case / "settings.json"
                        settings.write_text("{}")
                        helper_name = (
                            "run"
                            if stage == "run"
                            else "preparation"
                            if stage == "prepare"
                            else "translation"
                        )
                        helper = [
                            shutil.which("node"),
                            str(ROOT / f"ai/tests/{helper_name}-process.mjs"),
                            str(case / "credentials"),
                            str(settings),
                            str(log),
                        ]
                        operations = ("run", "resume") if stage == "run" else (stage,)
                        if stage in ("prepare", "translate"):
                            shutil.copytree(seed, workspace)
                            assert files(workspace) == files(seed)
                            (
                                workspace
                                / (
                                    "preparation-snapshot.json"
                                    if stage == "prepare"
                                    else "translation-snapshot.json"
                                )
                            ).unlink()
                        for operation in operations:
                            output = evidence / label / operation
                            output.mkdir(parents=True)
                            old = immutable(workspace)
                            sent = log.read_bytes() if log.exists() else b""
                            arguments = [
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
                                "--helper",
                                json.dumps(helper),
                            ]
                            if stage == "run":
                                arguments += [
                                    "--source",
                                    str(input_source or case / "fixture/own.pdf"),
                                    "--fixture-dir",
                                    str(case / "fixture"),
                                ]
                            if diagnostic:
                                arguments += [
                                    "--observe",
                                    str(output / "diagnostic.json"),
                                ]
                            record, result = child(
                                side,
                                output,
                                SCRIPTS / "performance_operation.py",
                                arguments,
                            )
                            metrics = json.loads((output / "metrics.json").read_text())
                            if operation == "resume" or stage in (
                                "prepare",
                                "translate",
                            ):
                                assert (
                                    log.read_bytes() if log.exists() else b""
                                ) == sent
                                assert immutable(workspace).items() >= old.items()
                                (output / "immutable-before.json").write_text(
                                    json.dumps(old, indent=2)
                                )
                            if stage == "prepare":
                                assert (
                                    result["sha256"]
                                    == json.loads(
                                        (seed / "preparation-snapshot.json").read_text()
                                    )["sha256"]
                                )
                            elif stage == "translate":
                                from translation_baseline import check_translation

                                from doc_translate.translation import load_translation

                                check_translation(load_translation(workspace), size)
                                assert (
                                    result["completed"]
                                    and metrics["worker_submissions"] == 0
                                )
                                assert files(seed) == json.loads(
                                    (evidence / "seed-hashes.json").read_text()
                                )
                            else:
                                assert result["processing"]["status"] == (
                                    "partial" if stage == "render" else "completed"
                                )
                                save_render_artifacts(workspace, result, output)
                                qa = json.loads((output / "saved-qa.json").read_text())
                                assert all(check["passed"] for check in qa["checks"])
                                mapping = json.loads(
                                    (output / "saved-source_map.json").read_text()
                                )
                                assert len(mapping["units"]) == (
                                    size * 2
                                    if format == "epub" and stage == "run"
                                    else size
                                )
                            row = {
                                "case": label,
                                "stage": stage,
                                "format": format,
                                "size": size,
                                "multi": multi,
                                "repeat": repeat,
                                "diagnostic": diagnostic,
                                "side": side,
                                "operation": operation,
                                **record,
                                **metrics,
                                "immutable_retained": True,
                                "new_requests_on_reuse": 0
                                if operation == "resume"
                                or stage in ("prepare", "translate")
                                else None,
                                "input_sha256": digest(input_source)
                                if input_source
                                else digest(case / "fixture/own.pdf")
                                if stage == "run"
                                else digest(
                                    next(
                                        (workspace / "snapshots/source").glob("*.json")
                                    )
                                ),
                            }
                            if stage == "run":
                                row["requests"] = requests(log)
                                if format == "pdf":
                                    assert result["scope"]["run"][
                                        "owned_pages"
                                    ] == list(range(2, size + 2))
                                    for scope in row["requests"]["correction_scopes"]:
                                        assert set(scope["owned_pages"]).isdisjoint(
                                            scope["reference_pages"]
                                        )
                                        assert set(
                                            scope["owned_pages"]
                                            + scope["reference_pages"]
                                        ) <= set(range(2, size + 2))
                                shutil.copyfile(log, output / "requests.jsonl")
                            attempts = [
                                json.loads(p.read_bytes())
                                for p in (workspace / "attempts").rglob("*.json")
                            ]
                            (output / "saved-attempts.json").write_text(
                                json.dumps(attempts, indent=2)
                            )
                            row["saved_attempt_records"] = len(attempts)
                            row["saved_attempt_bytes"] = sum(
                                p.stat().st_size
                                for p in (workspace / "attempts").rglob("*.json")
                            )
                            rows.append(row)
                            (evidence / "observations.json").write_text(
                                json.dumps(rows, indent=2)
                            )
                    if stage in ("render", "run"):
                        for operation in operations:
                            before = (
                                evidence
                                / f"{stage}-{format}-{size}{'-multi' if multi else ''}-{repeat}-before"
                                / operation
                            )
                            after = (
                                evidence
                                / f"{stage}-{format}-{size}{'-multi' if multi else ''}-{repeat}-after"
                                / operation
                            )
                            comparison = compare_saved_outputs(
                                before,
                                after,
                                independent=stage == "run" and format == "pdf",
                            )
                            (after / "equivalence.json").write_text(
                                json.dumps(comparison, indent=2)
                            )
                if stage in ("prepare", "translate"):
                    # Separate counter/profile pass, never included in the three normal timings.
                    for side in sources:
                        output = evidence / f"{stage}-{side}-diagnostic"
                        output.mkdir()
                        workspace = temporary / f"{stage}-{side}-diagnostic"
                        shutil.copytree(seed, workspace)
                        (
                            workspace
                            / f"{'preparation' if stage == 'prepare' else 'translation'}-snapshot.json"
                        ).unlink()
                        child(
                            side,
                            output,
                            SCRIPTS / "performance_operation.py",
                            [
                                "--format",
                                "pdf",
                                "--size",
                                str(size),
                                "--operation",
                                stage,
                                "--workspace",
                                str(workspace),
                                "--metrics",
                                str(output / "metrics.json"),
                                "--observe",
                                str(output / "diagnostic.json"),
                                "--helper",
                                '["must-not-send"]',
                            ],
                        )
    finally:
        removed = temporary is None or not temporary.exists()
        (evidence / "cleanup.txt").write_text(
            f"owned archive/src/input/credentials/workspaces removed={removed}; child groups drained/waited; evidence and synthetic seed retained\n"
        )
        assert removed
        for path in evidence.rglob("*"):
            if path.is_file():
                path.read_bytes()
    grouped = {}
    for row in rows:
        if row["diagnostic"]:
            continue
        key = f"{row['stage']}-{row['format']}-{row['size']}{'-multi' if row['multi'] else ''}-{row['operation']}-{row['side']}"
        grouped.setdefault(key, []).append(row)
    statistics = {
        key: {
            metric: distribution([row[metric] for row in values])
            for metric in (
                "operation_wall_seconds",
                "operation_python_cpu_seconds",
                "parent_ru_maxrss",
                "reaped_child_ru_maxrss",
                *(
                    ("search_call_inclusive_seconds",)
                    if values[0]["stage"] == "prepare"
                    else ()
                ),
                *(
                    ("pre_job_search_inclusive_seconds",)
                    if values[0]["stage"] == "translate"
                    else ()
                ),
            )
        }
        for key, values in grouped.items()
    }
    (evidence / "statistics.json").write_text(json.dumps(statistics, indent=2))
    (evidence / "statistics.json").read_bytes()
    print(evidence)


if __name__ == "__main__":
    main()
