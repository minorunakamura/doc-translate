"""Issue #60 only: public preparation, 12 independent 500ms collects, 1/2/4 x3."""

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
from pathlib import Path

from performance_baseline import ROOT, digest, distribution, immutable, invoke, requests

sys.path.insert(0, str(ROOT / "tests"))


def case(source_format, concurrency, evidence):
    fixture = import_module("test_preparation")

    from doc_translate.preparation import (
        _groups,
        _projection,
        load_preparation,
        prepare_document,
    )
    from doc_translate.snapshot import save_source_snapshot
    from doc_translate.units import generate_translation_units

    owned_path = None
    try:
        with tempfile.TemporaryDirectory(prefix="owned-", dir=evidence) as owned:
            owned_path = Path(owned)
            workspace = owned_path / "workspace"
            if source_format == "epub":
                source = import_module("test_epub_source").extract(
                    evidence.parent / "input.epub"
                )
            else:
                source = fixture.parallel_source(owned_path, source_format, size=12)
            source_digest = save_source_snapshot(workspace, source)
            groups = _groups(generate_translation_units(source))
            assert len(groups) == 12
            wires = [_projection(group, 2) for group in groups]
            assert len({json.dumps(wire, sort_keys=True) for wire in wires}) == 12
            answers = fixture.id_answers(source)
            answers["observation"] = {
                "delay_ms": 500,
                "timeline": str(evidence / "timeline.jsonl"),
            }
            answer_path = owned_path / "answers.json"
            answer_path.write_text(json.dumps(answers))
            answer_path.with_suffix(".bindings.json").write_text(
                json.dumps(
                    [
                        {"wire": wire, "unit_ids": [unit.unit_id for unit in group]}
                        for wire, group in zip(wires, groups, strict=True)
                    ]
                )
            )
            log = evidence / "requests.jsonl"
            node = shutil.which("node")
            assert node
            command = [
                node,
                str(ROOT / "ai/tests/preparation-process.mjs"),
                str(owned_path / "credentials"),
                str(answer_path),
                str(log),
            ]

            def prepare():
                return prepare_document(
                    workspace=workspace,
                    book="Artificial concurrency document",
                    policy=fixture.POLICY,
                    config=fixture.CONFIG,
                    concurrency=concurrency,
                    process_command=command,
                )

            started = time.perf_counter()
            adopted_digest = prepare()
            elapsed = time.perf_counter() - started
            adopted = load_preparation(workspace, adopted_digest)
            evidence_value = json.loads(
                (
                    workspace
                    / "preparation/evidence"
                    / f"{adopted.evidence_sha256}.json"
                ).read_text()
            )
            records = [
                json.loads(
                    (workspace / "attempts/preparation" / f"{run}.json").read_text()
                )
                for run in adopted.runs
            ]
            actual_wires = [
                json.loads(record["request"]["text"]) for record in records[:-1]
            ]
            assert actual_wires == wires
            assert len(records) == 13
            assert records[-1]["request"]["context"]["phase"] == "organize"
            assert all(
                record["status"] == "validated" and len(record["attempts"]) == 1
                for record in records
            )
            metrics = requests(log)
            assert metrics["phases"] == {"collect": 12, "organize": 1}
            events = sorted(
                [
                    json.loads(line)
                    for line in (evidence / "timeline.jsonl").read_text().splitlines()
                ],
                key=lambda event: int(event["ns"]),
            )
            active = peak = 0
            for event in events:
                active += 1 if event["event"] == "start" else -1
                assert 0 <= active <= concurrency
                peak = max(peak, active)
            assert active == 0
            assert events[-2]["phase"] == events[-1]["phase"] == "organize"
            old = immutable(workspace)
            sent = log.read_bytes()
            resume_started = time.perf_counter()
            prepare()
            resume_elapsed = time.perf_counter() - resume_started
            assert immutable(workspace) == old and log.read_bytes() == sent
            # Compare actual input/config/raw answers, not digest-bearing elapsed metadata.
            comparable = {
                "source_sha256": source_digest,
                "inputs": [
                    {"request": record["request"], "config": record["config"]}
                    for record in records
                ],
                "answers": [
                    record["attempts"][0]["final"]["message"]["content"]
                    for record in records
                ],
                "collections": evidence_value["collections"],
                "organization": evidence_value["organization"],
                "metrics": metrics,
            }
            (evidence / "comparable.json").write_text(
                json.dumps(comparable, ensure_ascii=False, indent=2)
            )
            (evidence / "immutable-hashes.json").write_text(json.dumps(old, indent=2))
            return {
                "format": source_format,
                "concurrency": concurrency,
                "stage_seconds": elapsed,
                "no_change_prepare_seconds": resume_elapsed,
                "peak_actual_faux_calls": peak,
                "metrics": metrics,
                "calls_on_no_change": 0,
                "immutable_unchanged": True,
            }
    finally:
        assert owned_path is None or not owned_path.exists()
        (evidence / "cleanup.txt").write_text(
            "owned TemporaryDirectory removed; generate children drained/reaped\n"
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--case", choices=("pdf", "epub"), help=argparse.SUPPRESS)
    parser.add_argument(
        "--concurrency", type=int, choices=(1, 2, 4), help=argparse.SUPPRESS
    )
    args = parser.parse_args()
    if args.case:
        print(json.dumps(case(args.case, args.concurrency, args.evidence)))
        return
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
    assert shutil.which("node"), "existing Node required; do not install/build"
    from doc_translate.ai_runtime import _resources

    build = {
        path.name: digest(path)
        for path in (ROOT / "ai/dist").iterdir()
        if path.is_file()
    }
    assert len(build) == 10 and build == {
        path.name: digest(path)
        for path in (_resources() / "dist").iterdir()
        if path.is_file()
    }, "helper resource mismatch; stop, do not build"
    (evidence / "revision.txt").write_text(
        subprocess.check_output(["git", "rev-parse", "HEAD"], text=True)
    )
    (evidence / "dirty.txt").write_text(
        subprocess.check_output(["git", "status", "--short"], text=True)
    )
    (evidence / "scope.diff").write_text(
        subprocess.check_output(["git", "diff"], text=True)
    )
    fixture = import_module("test_preparation")

    with tempfile.TemporaryDirectory(prefix="issue60-input-owned-") as temporary:
        fixture.parallel_source(Path(temporary), "epub", size=12)
        (evidence / "input.epub").write_bytes(
            (Path(temporary) / "book.epub").read_bytes()
        )
    manifest = {
        "input_epub_sha256": digest(evidence / "input.epub"),
        "command": sys.argv,
        "python": sys.version,
        "executable": sys.executable,
        "platform": platform.platform(),
        "node": subprocess.check_output(["node", "--version"], text=True).strip(),
        "helper_dist_sha256": build,
        "conditions": "fresh Python per sample; warm filesystem; no cache flush; sequential; synthetic PDF type and real EPUB reader/extractor; faux/no-network/artificial credential",
        "interval": "public prepare_document wall, including helper process startup, validation, immutable saves and pointer; source generation/save and separate load excluded; external Python wall recorded separately",
        "active": "cross-process start/end monotonic ns at actual faux callbacks, not process-local counters or pre-send notices",
        "unknown": [
            "real provider speed/rate limits/quality",
            "physical I/O",
            "simultaneous aggregate RSS",
        ],
        "budget": "60 seconds per owned process group; SIGINT then 45s drain then SIGKILL/wait; no concurrent benchmark",
        "source_sha256": {
            str(path.relative_to(ROOT)): digest(path)
            for path in [
                Path(__file__).resolve(),
                ROOT / "tests/test_preparation.py",
                ROOT / "ai/tests/preparation-process.mjs",
                ROOT / "ai/tests/no-network.mjs",
                *sorted((ROOT / "src/doc_translate").glob("*.py")),
            ]
        },
    }
    (evidence / "manifest.json").write_text(json.dumps(manifest, indent=2))
    saved_sources = evidence / "verification-sources"
    for relative in manifest["source_sha256"]:
        path = saved_sources / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, path)
    env = {**os.environ, "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"}
    results = []
    summary = {}
    try:
        for source_format in ("pdf", "epub"):
            expected = None
            summary[source_format] = {}
            for concurrency in (1, 2, 4):
                values = []
                for repeat in range(3):
                    directory = evidence / f"{source_format}-c{concurrency}-{repeat}"
                    directory.mkdir()
                    command = [
                        sys.executable,
                        str(Path(__file__).resolve()),
                        "--case",
                        source_format,
                        "--concurrency",
                        str(concurrency),
                        "--evidence",
                        str(directory),
                    ]
                    try:
                        _, value = invoke(command, env, directory, 60)
                    finally:
                        # Only this new sample's child-owned temp dirs, after group reap.
                        for owned in directory.glob("owned-*"):
                            shutil.rmtree(owned)
                        (directory / "parent-cleanup.txt").write_text(
                            "owned temp dirs absent after child/group reap; evidence retained\n"
                        )
                    comparable = json.loads((directory / "comparable.json").read_text())
                    if expected is None:
                        expected = comparable
                    assert comparable == expected, (
                        "calls/total input/config/raw answer/order changed"
                    )
                    results.append(value)
                    values.append(value["stage_seconds"])
                summary[source_format][str(concurrency)] = distribution(values)
            baseline = summary[source_format]["1"]["median"]
            for concurrency, limit in ((2, 0.75), (4, 0.55)):
                ratio = summary[source_format][str(concurrency)]["median"] / baseline
                summary[source_format][str(concurrency)]["ratio_to_1"] = ratio
                assert ratio <= limit, (source_format, concurrency, ratio)
        (evidence / "summary.json").write_text(json.dumps(summary, indent=2))
        print(json.dumps(summary, indent=2))
    finally:
        (evidence / "results.json").write_text(json.dumps(results, indent=2))
        (evidence / "cleanup.txt").write_text(
            "owned child groups reaped; each sample owns/removes only its TemporaryDirectory; evidence retained\n"
        )
        for path in evidence.rglob("*"):
            if path.is_file():
                path.read_bytes()


if __name__ == "__main__":
    main()
