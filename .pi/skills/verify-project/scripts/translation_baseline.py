"""Issue #57: fixed translation pre-job search boundary, with a retained synthetic seed."""

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections import Counter
from contextlib import ExitStack, contextmanager
from importlib import import_module
from importlib.metadata import version
from pathlib import Path
from typing import Any
from unittest.mock import patch

from performance_baseline import ROOT, digest, distribution, immutable, invoke
from preparation_baseline import create_seed as prepare_seed
from preparation_baseline import files

OPERATION = Path(__file__).with_name("performance_operation.py")


def create_seed(owned, destination, size):
    setup, preparation_log = prepare_seed(owned, destination, size)
    started = time.perf_counter()
    import_module("test_translation").translate(owned, destination)
    return setup + time.perf_counter() - started, (
        preparation_log,
        owned / "translation-requests.jsonl",
    )


def check_translation(snapshot, size):
    assert snapshot.processing.status == "completed"
    assert len(snapshot.runs) == len(snapshot.chunks) == size
    assert len(snapshot.translations) == size * 2
    for chunk in snapshot.chunks:
        assert chunk.answer is not None and chunk.error is None
        assert [item.unit_id for item in chunk.answer.translations] == [
            unit.unit_id for unit in chunk.units
        ]
        assert [item.text for item in chunk.answer.translations] == [
            "訳:" + unit.source_text for unit in chunk.units
        ]


@contextmanager
def translation_measurement(workspace, diagnostic=False):
    """Delegate both fixed endpoints; public load/store scopes override pre-job search."""
    from doc_translate import translation

    metrics: dict[str, Any] = {
        "pre_job_search_inclusive_seconds": 0.0,
        "search_intervals": 0,
        "public_load_inclusive_seconds": 0.0,
        "public_load_calls": 0,
        "worker_submissions": 0,
        "snapshot_save_calls": 0,
        "snapshot_save_bytes": 0,
    }
    state = threading.local()
    counts = Counter()
    started = None
    glob, executor = Path.glob, translation.ThreadPoolExecutor
    load, read, store = (
        translation.load_translation,
        translation._read,
        translation._store,
    )
    read_bytes, sha256, loads, replace = (
        Path.read_bytes,
        hashlib.sha256,
        json.loads,
        os.replace,
    )

    @contextmanager
    def scope(label):
        previous = getattr(state, "scope", "operation_other")
        state.scope = label
        try:
            yield
        finally:
            state.scope = previous

    def search_glob(path, pattern, *args, **kwargs):
        nonlocal started
        if (
            path == workspace / "attempts/translation"
            and pattern == "*.json"
            and metrics["search_intervals"] == 0
        ):
            started = time.perf_counter()
            metrics["search_intervals"] += 1
            state.scope = "operation_search"
        return glob(path, pattern, *args, **kwargs)

    class MeasuredExecutor(executor):
        def __enter__(self):
            nonlocal started
            assert started is not None, "pre-job search start boundary missing"
            metrics["pre_job_search_inclusive_seconds"] = time.perf_counter() - started
            started = None
            state.scope = "operation_other"
            return super().__enter__()

        def submit(self, *args, **kwargs):
            metrics["worker_submissions"] += 1
            return super().submit(*args, **kwargs)

    def public_load(*args, **kwargs):
        begin = time.perf_counter()
        try:
            with scope("public_load"):
                return load(*args, **kwargs)
        finally:
            metrics["public_load_calls"] += 1
            metrics["public_load_inclusive_seconds"] += time.perf_counter() - begin

    def artifact_read(workspace, directory, digest):
        previous = getattr(state, "attempt", None)
        state.attempt = str(digest) if directory == "attempts/translation" else None
        try:
            return read(workspace, directory, digest)
        finally:
            state.attempt = previous

    def actual_read(path):
        data = read_bytes(path)
        attempt = getattr(state, "attempt", None)
        if attempt is not None:
            label = getattr(state, "scope", "operation_other")
            counts[f"{label}/read/{attempt}"] += 1
            counts[f"{label}/logical_bytes"] += len(data)
        return data

    def actual_hash(*args, **kwargs):
        attempt = getattr(state, "attempt", None)
        if attempt is not None:
            counts[f"{state.scope}/hash/{attempt}"] += 1
        return sha256(*args, **kwargs)

    def actual_parse(*args, **kwargs):
        label = getattr(state, "scope", "operation_other")
        attempt = getattr(state, "attempt", None)
        if attempt is not None:
            counts[f"{label}/parse/{attempt}"] += 1
        elif label == "operation_search":
            counts[f"{label}/json_loads_outside_attempt_read"] += 1
        return loads(*args, **kwargs)

    def stored(*args, **kwargs):
        with scope("store"):
            return store(*args, **kwargs)

    def counted(original, name):
        def wrapped(*args, **kwargs):
            label = getattr(state, "scope", "operation_other")
            counts[f"{label}/{name}"] += 1
            result = original(*args, **kwargs)
            if name == "matches_request":
                counts[f"{label}/{'match' if result else 'nonmatch'}"] += 1
            return result

        return wrapped

    def snapshot_replace(src, dst, *args, **kwargs):
        path = Path(dst)
        snapshot = path.parent == workspace / "snapshots/translation"
        size = Path(src).stat().st_size if snapshot else 0
        result = replace(src, dst, *args, **kwargs)
        if snapshot:
            metrics["snapshot_save_calls"] += 1
            metrics["snapshot_save_bytes"] += size
        return result

    with ExitStack() as stack:
        for target, name, wrapper in (
            (Path, "glob", search_glob),
            (translation, "ThreadPoolExecutor", MeasuredExecutor),
            (translation, "load_translation", public_load),
            (os, "replace", snapshot_replace),
        ):
            stack.enter_context(patch.object(target, name, wrapper))
        if diagnostic:
            for target, name, wrapper in (
                (translation, "_read", artifact_read),
                (translation, "_store", stored),
                (translation, "_request", counted(translation._request, "request")),
                (
                    translation,
                    "_matches_request",
                    counted(translation._matches_request, "matches_request"),
                ),
                (Path, "read_bytes", actual_read),
                (hashlib, "sha256", actual_hash),
                (json, "loads", actual_parse),
            ):
                stack.enter_context(patch.object(target, name, wrapper))
        yield metrics
        assert started is None, "pre-job search end boundary missing"
    if diagnostic:
        metrics["attempt_diagnostic_counts"] = dict(counts)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument(
        "--seed", type=Path, help="retained before seed; never regenerate after"
    )
    parser.add_argument("--size", type=int, choices=(2, 100), default=100)
    args = parser.parse_args()
    assert Path.cwd() == ROOT
    evidence = args.evidence.absolute()
    assert evidence.parent == ROOT / "work"
    assert (
        subprocess.run(
            ["git", "check-ignore", "--quiet", str(evidence)], check=False
        ).returncode
        == 0
    )
    evidence.mkdir(exist_ok=False)
    node = shutil.which("node")
    assert node, "existing Node required; no install/build"
    from doc_translate.ai_runtime import _resources
    from doc_translate.translation import TranslationSnapshot, load_translation

    build = files(ROOT / "ai/dist")
    assert build and build == files(_resources() / "dist"), (
        "helper resource mismatch; stop"
    )
    seed = evidence / "seed"
    if args.seed:
        original = args.seed.resolve()
        assert original.parent.parent == ROOT / "work" and original.name == "seed"
        expected = json.loads((original.parent / "seed-hashes.json").read_text())
        assert files(original) == expected, "retained seed byte mismatch"
        shutil.copytree(original, seed)
    sources = [
        *sorted((ROOT / "src/doc_translate").glob("*.py")),
        OPERATION,
        Path(__file__).resolve(),
        Path(__file__).with_name("preparation_baseline.py"),
        Path(__file__).with_name("performance_baseline.py"),
        ROOT / "tests/test_preparation.py",
        ROOT / "tests/test_translation.py",
        ROOT / "ai/tests/preparation-process.mjs",
        ROOT / "ai/tests/translation-process.mjs",
        ROOT / "ai/tests/no-network.mjs",
    ]
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
            name: version(name)
            for name in ("pydantic", "Pillow", "docling", "docling-core")
        },
        "helper_dist_sha256": build,
        "source_sha256": {str(p.relative_to(ROOT)): digest(p) for p in sources},
        "size": args.size,
        "conditions": "fresh Python; warm filesystem; no cache flush; sequential/nonparallel; faux/no-network; artificial credentials; translation concurrency=2, worker submissions=0 on reuse",
        "interval": "first Path.glob('*.json') on attempts/translation through translation.ThreadPoolExecutor.__enter__; inclusive pre-job proxy (saved_runs read, orphan matching, job construction), not public translate wall; same endpoints before/after",
        "budget": "child 120s, invocation 900s checked between children; seed uses existing finite generate timeout/retry, not a hard 900s interrupt; owned-group SIGINT, drain 45s, SIGKILL/wait if needed",
        "unknown": [
            "physical I/O",
            "simultaneous aggregate RSS",
            "provider performance",
            "pure interpreter startup",
            "cache residency",
            "seed helper peak RSS",
        ],
        "retention": "current translation saved_runs holds all size attempt records; previous adopted snapshot at most one; historical snapshots not loaded en masse; diagnostic counters hold digests/counts, not records",
    }
    (evidence / "manifest.json").write_text(json.dumps(manifest, indent=2))
    (evidence / "dirty.txt").write_bytes(
        subprocess.check_output(["git", "status", "--short"])
    )
    (evidence / "scope.diff").write_bytes(subprocess.check_output(["git", "diff"]))
    saved = evidence / "verification-sources"
    saved.mkdir()
    for path in sources:
        if path.parent == OPERATION.parent:
            shutil.copyfile(path, saved / path.name)
    observations, temporary = [], None
    started = time.perf_counter()
    try:
        with tempfile.TemporaryDirectory(prefix="issue57-owned-") as name:
            temporary = Path(name)
            if not args.seed:
                setup, logs = create_seed(temporary, seed, args.size)
                manifest["seed_generation_wall_seconds"] = setup
                for stage, log in zip(
                    ("preparation", "translation"), logs, strict=True
                ):
                    (evidence / f"seed-{stage}-requests.jsonl").write_bytes(
                        log.read_bytes()
                    )
                    assert len(log.read_text().splitlines()) == args.size + (
                        stage == "preparation"
                    )
            hashes = files(seed)
            (evidence / "seed-hashes.json").write_text(json.dumps(hashes, indent=2))
            expected_snapshot = load_translation(seed)
            check_translation(expected_snapshot, args.size)
            attempts = list((seed / "attempts/translation").glob("*.json"))
            assert len(attempts) == args.size
            manifest["translation_attempt_records"] = len(attempts)
            manifest["translation_attempt_file_bytes"] = sum(
                p.stat().st_size for p in attempts
            )
            for mode in ("adopted", "unadopted", "load"):
                for repeat in range(4):
                    diagnostic = repeat == 3
                    label = f"{mode}-{'diagnostic' if diagnostic else repeat + 1}"
                    output, case = evidence / label, temporary / label
                    output.mkdir()
                    case.mkdir()
                    workspace = case / "workspace"
                    shutil.copytree(seed, workspace)
                    assert files(workspace) == hashes
                    if mode == "unadopted":
                        (workspace / "translation-snapshot.json").unlink()
                    old = immutable(workspace)
                    answers, log = case / "answers.json", case / "requests.jsonl"
                    answers.write_text(
                        "{}"
                    )  # Faux can succeed on a miss: log zero is mandatory.
                    helper = [
                        node,
                        str(ROOT / "ai/tests/translation-process.mjs"),
                        str(case / "credentials"),
                        str(answers),
                        str(log),
                    ]
                    command = [
                        sys.executable,
                        str(OPERATION),
                        "--format",
                        "pdf",
                        "--size",
                        str(args.size),
                        "--operation",
                        "load-translation" if mode == "load" else "translate",
                        "--workspace",
                        str(workspace),
                        "--helper",
                        json.dumps(helper),
                        "--metrics",
                        str(output / "metrics.json"),
                    ]
                    if diagnostic:
                        command += ["--observe", str(output / "diagnostic.json")]
                    remaining = 900 - (time.perf_counter() - started)
                    assert remaining > 0, "whole-invocation budget reached"
                    record, result = invoke(
                        command,
                        {
                            **os.environ,
                            "HF_HUB_OFFLINE": "1",
                            "TRANSFORMERS_OFFLINE": "1",
                        },
                        output,
                        min(120, remaining),
                    )
                    assert not log.exists() or log.read_bytes() == b"", (
                        "unexpected actual faux request"
                    )
                    if mode == "load":
                        snapshot = TranslationSnapshot.model_validate(result)
                    else:
                        pointer = json.loads(
                            (workspace / "translation-snapshot.json").read_bytes()
                        )["sha256"]
                        assert (
                            result["sha256"] == pointer and result["completed"] is True
                        )
                        snapshot = load_translation(
                            workspace
                        )  # Outside child timer/counters.
                    check_translation(snapshot, args.size)
                    # Orphan recovery restores source order, not concurrent completion order.
                    assert sorted(snapshot.runs) == sorted(expected_snapshot.runs)
                    assert snapshot.model_dump(
                        exclude={"runs"}
                    ) == expected_snapshot.model_dump(exclude={"runs"})
                    assert all(digest(workspace / p) == h for p, h in old.items())
                    assert files(seed) == hashes
                    metrics = json.loads((output / "metrics.json").read_text())
                    assert metrics["worker_submissions"] == 0
                    assert metrics["search_intervals"] == int(mode != "load")
                    assert metrics["public_load_calls"] == int(mode != "unadopted")
                    observations.append(
                        {
                            "case": label,
                            "mode": mode,
                            "diagnostic": diagnostic,
                            **record,
                            **metrics,
                            "actual_new_requests": 0,
                            "old_immutable_unchanged": True,
                            "seed_copy_all_bytes_identical": True,
                            "retained_attempt_records": args.size
                            if mode != "load"
                            else 0,
                        }
                    )
                    (evidence / "observations.json").write_text(
                        json.dumps(observations, indent=2)
                    )
    finally:
        (evidence / "manifest.json").write_text(json.dumps(manifest, indent=2))
        (evidence / "observations.json").write_text(json.dumps(observations, indent=2))
        removed = temporary is None or not temporary.exists()
        (evidence / "cleanup.txt").write_text(
            f"owned temporary workspaces/credentials/answers removed={removed}; owned subprocess groups waited; retained synthetic seed and evidence only\n"
        )
        assert removed
        for path in evidence.rglob("*"):
            if path.is_file():
                path.read_bytes()
    statistics = {}
    for mode in ("adopted", "unadopted", "load"):
        rows = [r for r in observations if r["mode"] == mode and not r["diagnostic"]]
        statistics[mode] = {
            key: distribution([r[key] for r in rows])
            for key in (
                "operation_wall_seconds",
                "pre_job_search_inclusive_seconds",
                "public_load_inclusive_seconds",
                "operation_python_cpu_seconds",
                "parent_ru_maxrss",
                "reaped_child_ru_maxrss",
                "external_process_wall_seconds",
            )
        }
        if mode != "load":
            statistics[mode]["search_fraction_of_operation"] = distribution(
                [
                    r["pre_job_search_inclusive_seconds"] / r["operation_wall_seconds"]
                    for r in rows
                ]
            )
    (evidence / "statistics.json").write_text(json.dumps(statistics, indent=2))
    for path in evidence.rglob("*"):
        if path.is_file():
            path.read_bytes()
    print(
        evidence,
        "public translate/load reuse; no requests/workers; byte identity; cleanup and all evidence readback matched",
    )


if __name__ == "__main__":
    main()
