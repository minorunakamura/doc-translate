"""One fresh-process Issue #54 operation; only PDF's external converter is replaced."""

import argparse
import cProfile
import hashlib
import json
import os
import pstats
import resource
import sys
import time
from collections import Counter
from contextlib import contextmanager
from importlib import import_module
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "tests"))


def render_source(format, size):
    from doc_translate.snapshot import SourceSnapshot

    location = (
        {"format": "pdf", "page": 1, "evidence": "self-authored"}
        if format == "pdf"
        else {"format": "epub", "resource_path": "own.xhtml", "element_id": "own"}
    )
    return SourceSnapshot.model_validate(
        {
            "schema_version": 1,
            "snapshot_id": "own-source",
            "document": {
                "format": format,
                "sha256": "a" * 64,
                "artifact": "own.pdf" if format == "pdf" else "own.epub",
            },
            "blocks": [
                {
                    "block_id": f"body-{i}",
                    "kind": "body",
                    "text": f"Self-authored queue record {i}.",
                    "locations": [location],
                }
                for i in range(size)
            ],
            "uncertainties": [
                {
                    "reason": "Self-authored uncertainty.",
                    "target_ids": [f"body-{i}"],
                    "locations": [location],
                    "stage": "source",
                    "origin": "original",
                }
                for i in range(size)
            ],
            "processing": {"stage": "source", "status": "completed"},
        }
    )


def pdf_fixture(directory, size, patch):
    pdf_fixtures = import_module("test_pdf")
    run_fixtures = import_module("test_run")

    directory.mkdir(exist_ok=False)
    converted = pdf_fixtures.conversion.__wrapped__(patch)
    run_fixtures.pdf_input.__wrapped__(directory, converted, patch)
    if size == 35:
        from docling_core.types.doc import (
            BoundingBox,
            DocItemLabel,
            ImageRef,
            ProvenanceItem,
            Size,
        )
        from PIL import Image

        for page in range(10, 37):
            converted.document.add_page(
                page_no=page,
                size=Size(width=200, height=100),
                image=ImageRef.from_pil(Image.new("RGB", (400, 200), "white"), dpi=144),
            )
            text = f"ONLY_PAGE_{page}_TEXT"
            converted.document.add_text(
                label=DocItemLabel.TEXT,
                text=text,
                orig=text,
                prov=ProvenanceItem(
                    page_no=page,
                    charspan=(0, len(text)),
                    bbox=BoundingBox(
                        l=10, t=80, r=100, b=60, coord_origin="BOTTOMLEFT"
                    ),
                ),
            )
        converted.input.page_count = 36
    pdf_fixtures.local_models.__wrapped__(directory)
    return converted


@contextmanager
def observe(workspace, output):
    """Separate diagnostic pass: delegate reads/replaces and retain inclusive profile."""
    from unittest.mock import patch

    counts = Counter()
    reads = Counter()
    writes = Counter()
    read_bytes, replace = Path.read_bytes, os.replace

    def read(path):
        data = read_bytes(path)
        if path.is_relative_to(workspace):
            reads[str(path.relative_to(workspace))] += 1
            counts["path_read_bytes_calls"] += 1
            counts["logical_read_bytes"] += len(data)
        return data

    def write(src, dst, *args, **kwargs):
        path = Path(dst)
        size = Path(src).stat().st_size
        result = replace(src, dst, *args, **kwargs)
        if path.is_relative_to(workspace):
            relative = str(path.relative_to(workspace))
            writes[relative] += 1
            category = relative.split("/")[0]
            counts[f"replace_{category}_calls"] += 1
            counts[f"replace_{category}_bytes"] += size
        return result

    from doc_translate import pdf_correction, preparation, translation

    collect_call = preparation._call
    phase_seconds: dict[str, float] = {}

    def preparation_call(*args, **kwargs):
        phase = args[2].context["phase"]
        started = time.perf_counter()
        try:
            return collect_call(*args, **kwargs)
        finally:
            phase_seconds[phase] = phase_seconds.get(phase, 0.0) + (
                time.perf_counter() - started
            )
            counts[f"preparation_{phase}_calls"] += 1

    def matching(original, stage):
        def match(*args, **kwargs):
            result = original(*args, **kwargs)
            counts[f"{stage}_candidate_comparisons"] += 1
            counts[
                f"{stage}_candidate_matches"
                if result
                else f"{stage}_candidate_nonmatches"
            ] += 1
            return result

        return match

    profiler = cProfile.Profile()
    try:
        with (
            patch.object(Path, "read_bytes", read),
            patch.object(os, "replace", write),
            patch.object(preparation, "_call", preparation_call),
            patch.object(
                translation,
                "_matches_request",
                matching(translation._matches_request, "translation"),
            ),
            patch.object(
                pdf_correction,
                "_matches_request",
                matching(pdf_correction._matches_request, "correction"),
            ),
        ):
            profiler.enable()
            yield
    finally:
        profiler.disable()
        stats = pstats.Stats(profiler)
        # Raw stats retain same-named functions and sub-millisecond timing precision.
        raw_stats = getattr(stats, "stats", None)
        assert raw_stats is not None
        functions = [
            {
                "file": filename,
                "line": line,
                "function": name,
                "primitive_calls": values[0],
                "calls": values[1],
                "self_seconds": values[2],
                "inclusive_seconds": values[3],
            }
            for (filename, line, name), values in raw_stats.items()
        ]
        output.write_text(
            json.dumps(
                {
                    "counts": counts,
                    "preparation_call_inclusive_seconds": phase_seconds,
                    "reads": reads,
                    "writes": writes,
                    "profile": functions,
                },
                indent=2,
            )
        )


@contextmanager
def preparation_measurement(workspace, diagnostic=False):
    """Time inclusive lookup proxies; diagnostics delegate actual read/hash boundaries."""
    from contextlib import ExitStack
    from unittest.mock import patch

    from doc_translate import preparation

    metrics: dict[str, Any] = {
        "search_call_inclusive_seconds": 0.0,
        "search_calls": 0,
        "public_load_inclusive_seconds": 0.0,
        "public_load_calls": 0,
    }
    counts = Counter()
    scope, attempt = "operation_other", None
    call, load, read = (
        preparation._call,
        preparation.load_preparation,
        preparation._read,
    )
    read_bytes, sha256 = Path.read_bytes, hashlib.sha256

    def timed(original, label):
        def wrapped(*args, **kwargs):
            nonlocal scope
            previous, scope = scope, label
            started = time.perf_counter()
            try:
                return original(*args, **kwargs)
            finally:
                metrics[f"{label}_inclusive_seconds"] += time.perf_counter() - started
                metrics[
                    "search_calls" if label == "search_call" else "public_load_calls"
                ] += 1
                scope = previous

        return wrapped

    def artifact_read(workspace, directory, digest):
        nonlocal attempt
        previous = attempt
        attempt = str(digest) if directory == "attempts/preparation" else None
        try:
            return read(workspace, directory, digest)
        finally:
            attempt = previous

    def actual_read(path):
        data = read_bytes(path)
        if attempt is not None:
            counts[f"{scope}/read/{attempt}"] += 1
            counts[f"{scope}/logical_bytes"] += len(data)
        return data

    def actual_hash(*args, **kwargs):
        if attempt is not None:
            counts[f"{scope}/hash/{attempt}"] += 1
        return sha256(*args, **kwargs)

    with ExitStack() as stack:
        stack.enter_context(
            patch.object(preparation, "_call", timed(call, "search_call"))
        )
        stack.enter_context(
            patch.object(preparation, "load_preparation", timed(load, "public_load"))
        )
        if diagnostic:
            stack.enter_context(patch.object(preparation, "_read", artifact_read))
            stack.enter_context(patch.object(Path, "read_bytes", actual_read))
            stack.enter_context(patch.object(hashlib, "sha256", actual_hash))
        yield metrics
    if diagnostic:
        metrics["attempt_read_hash_counts"] = dict(counts)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--format", choices=("pdf", "epub"), required=True)
    parser.add_argument("--size", type=int, required=True)
    parser.add_argument(
        "--operation",
        choices=("run", "resume", "render", "prepare", "load-preparation"),
        required=True,
    )
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--fixture-dir", type=Path)
    parser.add_argument("--helper", help="JSON process_command array")
    parser.add_argument("--metrics", type=Path, required=True)
    parser.add_argument("--observe", type=Path)
    args = parser.parse_args()
    if args.operation in ("prepare", "load-preparation"):
        assert args.size in (2, 100)
    elif args.operation == "render":
        assert args.size in (50, 200, 500)
    else:
        assert args.size in ((6, 35) if args.format == "pdf" else (2, 20, 200))
    setup = time.perf_counter()
    import pytest

    with pytest.MonkeyPatch.context() as patch:
        converted = None
        if args.operation == "run" and args.format == "pdf":
            converted = pdf_fixture(args.fixture_dir, args.size, patch)
        from doc_translate.cli import main as cli_main
        from doc_translate.render import render_document
        from doc_translate.snapshot import save_source_snapshot

        if args.operation in ("prepare", "load-preparation"):
            fixture = import_module("test_preparation")
            import_module("unittest.mock")
            arguments = []
        elif args.operation == "render":
            source = render_source(args.format, args.size)
            save_source_snapshot(args.workspace, source)
            arguments = []
        elif args.operation == "resume":
            arguments = ["resume", str(args.workspace), "--json", "--events-json"]
        else:
            arguments = [
                "run",
                str(args.source),
                "--workspace",
                str(args.workspace),
                "--provider",
                "faux",
                "--model",
                "text",
                "--thinking",
                "off",
                "--json",
                "--events-json",
            ]
            if args.format == "pdf":
                arguments += [
                    "--pages",
                    f"2-{args.size + 1}",
                    "--artifacts-path",
                    str(args.fixture_dir / "models"),
                    "--correction-model",
                    "vision",
                ]
        setup_wall = time.perf_counter() - setup
        from contextlib import nullcontext

        exit_code = 0
        preparation_metrics = {}
        wall, cpu = time.perf_counter(), time.process_time()
        try:
            with (
                observe(args.workspace, args.observe) if args.observe else nullcontext()
            ):
                if args.operation in ("prepare", "load-preparation"):
                    from doc_translate import preparation

                    with preparation_measurement(
                        args.workspace, bool(args.observe)
                    ) as preparation_metrics:
                        if args.operation == "prepare":
                            digest = preparation.prepare_document(
                                workspace=args.workspace,
                                book="Artificial book",
                                policy=fixture.POLICY,
                                config=fixture.CONFIG,
                                process_command=json.loads(args.helper),
                            )
                            print(json.dumps({"sha256": digest}))
                        else:
                            print(
                                preparation.load_preparation(
                                    args.workspace
                                ).model_dump_json()
                            )
                elif args.operation == "render":
                    print(
                        json.dumps(render_document(args.workspace), ensure_ascii=False)
                    )
                else:
                    cli_main(arguments, process_command=json.loads(args.helper))
        except SystemExit as error:
            exit_code = error.code or 0
            raise
        except BaseException:
            exit_code = 1
            raise
        finally:
            args.metrics.write_text(
                json.dumps(
                    {
                        "exit": exit_code,
                        **preparation_metrics,
                        "setup_import_fixture_save_wall_seconds": setup_wall,
                        "operation_wall_seconds": time.perf_counter() - wall,
                        "operation_python_cpu_seconds": time.process_time() - cpu,
                        "parent_ru_maxrss": resource.getrusage(
                            resource.RUSAGE_SELF
                        ).ru_maxrss,
                        "reaped_child_ru_maxrss": resource.getrusage(
                            resource.RUSAGE_CHILDREN
                        ).ru_maxrss,
                        "reaped_child_cpu_seconds": sum(
                            resource.getrusage(resource.RUSAGE_CHILDREN)[i]
                            for i in (0, 1)
                        ),
                        "image_backend_events": converted.rendering_events
                        if converted
                        else [],
                    },
                    indent=2,
                )
            )


if __name__ == "__main__":
    main()
