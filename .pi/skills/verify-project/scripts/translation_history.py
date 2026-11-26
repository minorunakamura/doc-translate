"""Issue #105: translation snapshot history growth per initial / unchanged / recovery run."""

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from contextlib import ExitStack, contextmanager
from importlib import import_module
from importlib.metadata import version
from pathlib import Path
from typing import Any
from unittest.mock import patch

from performance_baseline import ROOT, digest, distribution
from preparation_baseline import create_seed, files

SIZES = (10, 100)
REPEATS = 3
SCENARIOS = ("initial", "unchanged", "failed_initial", "recovery")
DIRECTORIES = {
    "snapshot": "snapshots/translation",
    "attempt": "attempts/translation",
    "chunk": "translations/chunks",
}
POINTER = "translation-snapshot.json"


def failing_text(size):
    # Body numbers become protected markers and the next group's heading_path repeats
    # this heading, so match only the middle heading's own source_text.
    return f'"source_text":"Section group{size // 2}"'


def inventory(workspace):
    return {
        kind: {p.name: p.stat().st_size for p in (workspace / d).glob("*.json")}
        for kind, d in DIRECTORIES.items()
    }


@contextmanager
def history_measurement(workspace):
    """Delegate os.replace/submit; file increments come from directory diffs."""
    from doc_translate import translation

    metrics: dict[str, Any] = {
        "pointer_writes": 0,
        "snapshot_saves": [],
        "jobs": 0,
    }
    replace, executor = os.replace, translation.ThreadPoolExecutor

    def counted_replace(src, dst, *args, **kwargs):
        path = Path(dst)
        size = Path(src).stat().st_size
        result = replace(src, dst, *args, **kwargs)
        if path == workspace / POINTER:
            metrics["pointer_writes"] += 1
        elif path.parent == workspace / DIRECTORIES["snapshot"]:
            metrics["snapshot_saves"].append(size)
        return result

    class CountedExecutor(executor):
        def submit(self, *args, **kwargs):
            metrics["jobs"] += 1
            return super().submit(*args, **kwargs)

    with ExitStack() as stack:
        for target, name, wrapper in (
            (os, "replace", counted_replace),
            (translation, "ThreadPoolExecutor", CountedExecutor),
        ):
            stack.enter_context(patch.object(target, name, wrapper))
        yield metrics


def measure(owned, workspace, response=None):
    """One public translate through the existing faux/no-network fixture."""
    from doc_translate.translation import load_translation

    log = owned / "translation-requests.jsonl"
    sent = len(log.read_text().splitlines()) if log.exists() else 0
    pointer = workspace / POINTER
    old_pointer = digest(pointer) if pointer.exists() else None
    before = inventory(workspace)
    started = time.perf_counter()
    with history_measurement(workspace) as metrics:
        result = import_module("test_translation").translate(owned, workspace, response)
    elapsed = time.perf_counter() - started
    after = inventory(workspace)
    snapshot = load_translation(workspace)
    assert json.loads(pointer.read_bytes())["sha256"] == result
    row = {
        "status": snapshot.processing.status,
        "failed_chunks": sum(c.error is not None for c in snapshot.chunks),
        "requests": len(log.read_text().splitlines()) - sent,
        "jobs": metrics["jobs"],
        "pointer_writes": metrics["pointer_writes"],
        "pointer_changed": old_pointer != digest(pointer),
        "snapshot_save_bytes_in_order": metrics["snapshot_saves"],
        "final_snapshot_bytes": after["snapshot"][f"{result}.json"],
        "translate_seconds": elapsed,
    }
    for kind in DIRECTORIES:
        added = [size for name, size in after[kind].items() if name not in before[kind]]
        assert all(after[kind][n] == s for n, s in before[kind].items())
        row[f"{kind}_added_files"] = len(added)
        row[f"{kind}_added_bytes"] = sum(added)
        row[f"{kind}_added_max_bytes"] = max(added, default=0)
    assert row["snapshot_added_files"] == len(metrics["snapshot_saves"])
    return row


def measure_scenarios(owned, seed, size):
    """Fresh copies: initial -> unchanged, and failed initial -> recovery."""
    rows = {}
    for name, first, second in (
        ("complete", "initial", "unchanged"),
        ("recover", "failed_initial", "recovery"),
    ):
        case = owned / name
        case.mkdir()
        workspace = shutil.copytree(seed, case / "workspace")
        response = {"fail": failing_text(size)} if first == "failed_initial" else None
        rows[first] = measure(case, workspace, response)
        rows[second] = measure(case, workspace)
    assert rows["initial"]["status"] == rows["recovery"]["status"] == "completed"
    assert rows["unchanged"]["status"] == "completed"
    assert rows["failed_initial"]["status"] == "failed"
    assert rows["failed_initial"]["failed_chunks"] == 1
    unchanged = rows["unchanged"]
    assert unchanged["requests"] == unchanged["jobs"] == 0
    assert unchanged["snapshot_added_files"] == unchanged["snapshot_added_bytes"] == 0
    assert unchanged["attempt_added_files"] == unchanged["chunk_added_files"] == 0
    assert not unchanged["pointer_changed"]
    assert rows["recovery"]["jobs"] == 1
    assert rows["recovery"]["snapshot_added_files"] == 1
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--size", type=int, action="append", choices=(2, *SIZES))
    args = parser.parse_args()
    sizes = args.size or list(SIZES)
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

    build = files(ROOT / "ai/dist")
    assert build and build == files(_resources() / "dist"), (
        "helper resource mismatch; stop"
    )
    fixture = import_module("test_translation")
    sources = [
        *sorted((ROOT / "src/doc_translate").glob("*.py")),
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
        "dependencies": {name: version(name) for name in ("pydantic",)},
        "helper_dist_sha256": build,
        "source_sha256": {str(p.relative_to(ROOT)): digest(p) for p in sources},
        "sizes": sizes,
        "repeats": REPEATS,
        "conditions": {
            "input": "preparation_baseline.seed_source: N heading+body groups, 2N units, PDF-type common source, real prepare collect N + organize 1",
            "config": fixture.CONFIG.model_dump(mode="json"),
            "concurrency": 2,
            "max_source_chars": 12000,
            "helper": "ai/tests/translation-process.mjs compiled Node faux + no-network, artificial credentials",
            "failure": "faux {'fail': failing_text(size)} makes exactly the middle group's request fail permanently",
            "isolation": "prepared seed made once per size in its own TemporaryDirectory; every repeat copies it into a new owned TemporaryDirectory; complete and recover chains use separate copies",
            "timing": "in-process translate wall seconds, secondary only; warm filesystem",
        },
        "counting": {
            "files": "directory diff of snapshots/translation, attempts/translation, translations/chunks; logical bytes = file sizes",
            "pointer_writes": "delegated os.replace whose destination is translation-snapshot.json",
            "snapshot_saves": "delegated os.replace into snapshots/translation (existing digest is not rewritten)",
            "jobs": "delegated translation.ThreadPoolExecutor.submit",
            "requests": "new lines in the faux requests.jsonl",
        },
        "unknown": ["physical I/O", "filesystem block overhead", "provider behaviour"],
    }
    (evidence / "manifest.json").write_text(json.dumps(manifest, indent=2))
    (evidence / "dirty.txt").write_bytes(
        subprocess.check_output(["git", "status", "--short"])
    )
    (evidence / "scope.diff").write_bytes(subprocess.check_output(["git", "diff"]))
    observations, owned = [], []
    try:
        for size in sizes:
            with tempfile.TemporaryDirectory(prefix="issue105-seed-") as name:
                temporary = Path(name)
                owned.append(temporary)
                seed = temporary / "seed"
                create_seed(temporary, seed, size)
                hashes = files(seed)
                (evidence / f"seed-{size}-hashes.json").write_text(
                    json.dumps(hashes, indent=2)
                )
                for repeat in range(REPEATS):
                    with tempfile.TemporaryDirectory(prefix="issue105-case-") as case:
                        owned.append(Path(case))
                        rows = measure_scenarios(Path(case), seed, size)
                    assert files(seed) == hashes
                    for scenario in SCENARIOS:
                        observations.append(
                            {
                                "size": size,
                                "repeat": repeat + 1,
                                "scenario": scenario,
                                **rows[scenario],
                            }
                        )
    finally:
        (evidence / "observations.json").write_text(json.dumps(observations, indent=2))
        removed = all(not path.exists() for path in owned)
        (evidence / "cleanup.txt").write_text(
            f"owned temporary seeds/workspaces/credentials/answers removed={removed}; in-process translate children waited by translate_document; evidence keeps hashes and metrics only\n"
        )
        assert removed
    statistics = {
        str(size): {
            scenario: {
                key: distribution(
                    [
                        r[key]
                        for r in observations
                        if r["size"] == size and r["scenario"] == scenario
                    ]
                )
                for key in (
                    "snapshot_added_files",
                    "snapshot_added_bytes",
                    "snapshot_added_max_bytes",
                    "final_snapshot_bytes",
                    "pointer_writes",
                    "jobs",
                    "requests",
                    "attempt_added_files",
                    "attempt_added_bytes",
                    "chunk_added_files",
                    "chunk_added_bytes",
                    "translate_seconds",
                )
            }
            for scenario in SCENARIOS
        }
        for size in sizes
    }
    (evidence / "statistics.json").write_text(json.dumps(statistics, indent=2))
    for path in evidence.rglob("*"):
        if path.is_file():
            path.read_bytes()
    print(
        evidence,
        "unchanged resume added 0 snapshots; cleanup and all evidence readback matched",
    )


if __name__ == "__main__":
    main()
