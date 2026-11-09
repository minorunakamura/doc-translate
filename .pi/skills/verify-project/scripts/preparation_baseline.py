"""Issue #56: fixed public prepare/load recipe; retain a byte-identical synthetic seed."""

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from importlib import import_module
from importlib.metadata import version
from pathlib import Path

from performance_baseline import ROOT, digest, distribution, immutable, invoke
from performance_operation import render_source

OPERATION = Path(__file__).with_name("performance_operation.py")


def files(directory):
    return {
        str(p.relative_to(directory)): digest(p)
        for p in sorted(directory.rglob("*"))
        if p.is_file()
    }


def seed_source(size):
    from doc_translate.snapshot import SourceSnapshot

    data = render_source("pdf", size * 2).model_dump(mode="json")
    data["uncertainties"] = []
    for index, block in enumerate(data["blocks"]):
        if index % 2 == 0:
            block.update(
                kind="heading", text=f"Section {index // 2}", structure={"level": 1}
            )
    return SourceSnapshot.model_validate(data)


def create_seed(owned, destination, size):
    from doc_translate.snapshot import save_source_snapshot
    from doc_translate.units import generate_translation_units

    workspace = owned / "workspace"
    source = seed_source(size)
    save_source_snapshot(workspace, source)
    units = generate_translation_units(source)
    answers = {
        "collect": [
            {
                "context": f"人工節 {i}。",
                "candidates": [
                    {
                        "term": "queue",
                        "translation": "キュー",
                        "evidence": [
                            {
                                "unit_id": units[2 * i + 1].unit_id,
                                "quote": units[2 * i + 1].source_text,
                            }
                        ],
                    }
                ],
            }
            for i in range(size)
        ],
        "organize": {"context": "キューの人工文書です。", "terms": {"queue": "キュー"}},
    }
    started = time.perf_counter()
    import_module("test_preparation").run(
        owned, workspace, [], answers_override=answers
    )
    elapsed = time.perf_counter() - started
    shutil.copytree(workspace, destination)
    return elapsed, owned / "requests.jsonl"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument(
        "--seed", type=Path, help="retained synthetic seed from the before invocation"
    )
    parser.add_argument("--size", type=int, choices=(2, 100), default=100)
    args = parser.parse_args()
    assert Path.cwd() == ROOT, "run at repository root"
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
        "helper resource hash mismatch; stop, do not build"
    )
    if args.seed:
        seed = args.seed.resolve()
        assert seed.parent.parent == ROOT / "work" and seed.name == "seed", (
            "only this recipe's synthetic seed"
        )
        expected = json.loads((seed.parent / "seed-hashes.json").read_text())
        assert files(seed) == expected, "retained seed byte mismatch"
        shutil.copytree(seed, evidence / "seed")
    seed = evidence / "seed"
    env = {**os.environ, "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"}
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
        "source_sha256": {
            str(p.relative_to(ROOT)): digest(p)
            for p in [
                *sorted((ROOT / "src/doc_translate").glob("*.py")),
                OPERATION,
                Path(__file__).resolve(),
                Path(__file__).with_name("performance_baseline.py"),
                ROOT / "tests/test_preparation.py",
                ROOT / "ai/tests/preparation-process.mjs",
                ROOT / "ai/tests/no-network.mjs",
            ]
        },
        "conditions": "fresh Python; warm filesystem, no cache flush; sequential; faux/no-network; preparation sequential; artificial credentials only",
        "interval": "public prepare_document wall and sum of inclusive _call wrappers (lookup proxy: reuse-only, no generate); internal public load_preparation timed separately; standalone load is a separate operation; diagnostics excluded from normal statistics",
        "budget": "120 seconds per child, 900 seconds total, owned-group SIGINT then 45-second drain then SIGKILL/wait",
        "unknown": [
            "physical I/O",
            "aggregate simultaneous RSS",
            "provider performance",
            "pure interpreter startup",
            "cache residency",
        ],
        "size": args.size,
    }
    (evidence / "manifest.json").write_text(json.dumps(manifest, indent=2))
    saved_scripts = evidence / "verification-sources"
    saved_scripts.mkdir()
    for path in (OPERATION, Path(__file__).resolve()):
        shutil.copyfile(path, saved_scripts / path.name)
    (evidence / "dirty.txt").write_bytes(
        subprocess.check_output(["git", "status", "--short"])
    )
    (evidence / "scope.diff").write_bytes(subprocess.check_output(["git", "diff"]))
    observations, temporary = [], None
    started = time.perf_counter()
    try:
        with tempfile.TemporaryDirectory(prefix="issue56-owned-") as name:
            temporary = Path(name)
            if not args.seed:
                setup, log = create_seed(temporary, seed, args.size)
                manifest["seed_generation_wall_seconds"] = setup
                (evidence / "seed-requests.jsonl").write_bytes(log.read_bytes())
            hashes = files(seed)
            (evidence / "seed-hashes.json").write_text(json.dumps(hashes, indent=2))
            pointer = json.loads((seed / "preparation-snapshot.json").read_text())[
                "sha256"
            ]
            assert (
                len(list((seed / "attempts/preparation").glob("*.json")))
                == args.size + 1
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
                    assert files(workspace) == hashes, "copy is not byte-identical"
                    if mode == "unadopted":
                        (workspace / "preparation-snapshot.json").unlink()
                    old = immutable(workspace)
                    answers = case / "answers.json"
                    answers.write_text(
                        "{}"
                    )  # A miss must fail rather than produce a fake success.
                    log = case / "requests.jsonl"
                    helper = [
                        node,
                        str(ROOT / "ai/tests/preparation-process.mjs"),
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
                        "load-preparation" if mode == "load" else "prepare",
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
                    record, result = invoke(command, env, output, min(120, remaining))
                    assert not log.exists(), (
                        "unexpected actual faux request on saved reuse"
                    )
                    if mode != "load":
                        assert result["sha256"] == pointer
                    else:
                        assert len(result["runs"]) == args.size + 1 and result[
                            "glossary"
                        ]["terms"] == {"queue": "キュー"}
                    assert immutable(workspace) == old and files(seed) == hashes, (
                        "immutable bytes or seed changed"
                    )
                    metrics = json.loads((output / "metrics.json").read_text())
                    assert metrics["search_calls"] == (
                        0 if mode == "load" else args.size + 1
                    )
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
                "search_call_inclusive_seconds",
                "public_load_inclusive_seconds",
                "operation_python_cpu_seconds",
                "parent_ru_maxrss",
                "reaped_child_ru_maxrss",
                "external_process_wall_seconds",
            )
        }
    (evidence / "statistics.json").write_text(json.dumps(statistics, indent=2))
    for path in evidence.rglob("*"):
        if path.is_file():
            path.read_bytes()
    print(
        evidence,
        "public prepare/load reuse, byte identity, cleanup and evidence readback matched",
    )


if __name__ == "__main__":
    main()
