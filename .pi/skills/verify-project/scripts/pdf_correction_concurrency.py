"""Issue #61 sample: real run/resume with twelve independent five-page jobs."""

import json
import os
import shutil
import signal
import tempfile
import time
from importlib import import_module
from pathlib import Path

import pytest
from performance_baseline import immutable, requests


def seed(evidence):
    from doc_translate.inference import GenerationConfig
    from doc_translate.run import run_document

    fixtures = import_module("test_pdf")
    runs = import_module("test_run")
    with tempfile.TemporaryDirectory(prefix="issue61-seed-owned-") as temporary:
        directory = Path(temporary)
        with pytest.MonkeyPatch.context() as patch:
            converted = fixtures.conversion.__wrapped__(patch)
            source = runs.pdf_input.__wrapped__(directory, converted, patch)
            runs.extend_pdf_input(converted, 61)
            models = fixtures.local_models.__wrapped__(directory)

            def stop_before_correction(event):
                if event.stage == "correction" and event.kind == "start":
                    os.kill(os.getpid(), signal.SIGTERM)

            result = run_document(
                source=source,
                workspace=directory / "workspace",
                translation=GenerationConfig(
                    provider="faux", model="text", thinking="off"
                ),
                correction=GenerationConfig(
                    provider="faux", model="vision", thinking="off"
                ),
                pages=(2, 61),
                artifacts_path=models,
                process_command=runs.helper(directory),
                observer=stop_before_correction,
            )
            assert result["processing"]["status"] == "partial"
            assert result["processing"]["source"]["status"] == "failed"
            assert not (directory / "requests.jsonl").exists()
            shutil.copytree(directory / "workspace", evidence / "seed")
    assert not directory.exists()


def case(concurrency, evidence):
    from doc_translate.pdf_correction import load_pdf_correction
    from doc_translate.run import resume_document
    from doc_translate.snapshot import PDFLocation, load_source_snapshot

    runs = import_module("test_run")
    owned_path = None
    try:
        with tempfile.TemporaryDirectory(prefix="issue61-owned-") as temporary:
            owned_path = Path(temporary)
            (evidence / "owned-path.txt").write_text(str(owned_path))
            workspace = owned_path / "workspace"
            shutil.copytree(evidence.parent / "seed", workspace)
            timeline = evidence / "timeline.jsonl"
            command = runs.helper(
                owned_path,
                correction_delay_ms=500,
                correction_timeline=str(timeline),
            )
            stages = {}

            def observe(event):
                if event.stage == "correction" and event.kind in {
                    "start",
                    "finished",
                }:
                    stages[event.kind] = time.perf_counter()

            started = time.perf_counter()
            result = resume_document(
                workspace=workspace,
                correction_concurrency=concurrency,
                process_command=command,
                observer=observe,
            )
            run_elapsed = time.perf_counter() - started
            assert result["processing"]["status"] == "completed"
            elapsed = stages["finished"] - stages["start"]
            manifest = json.loads((workspace / "run.json").read_bytes())
            chunks = [
                load_pdf_correction(workspace, digest)
                for digest in manifest["chunk_sha256s"]
            ]
            ownership = [page for chunk in chunks for page in chunk.owned_pages]
            assert ownership == list(range(2, 62))
            assert len(chunks) == 12 and all(
                len(chunk.owned_pages) == 5 for chunk in chunks
            )
            records = [
                json.loads((workspace / chunk.run.artifact).read_bytes())
                for chunk in chunks
            ]
            assert all(
                record["status"] == "validated" and len(record["attempts"]) == 1
                for record in records
            )
            events, peak = runs.pdf_timeline(timeline, concurrency)
            assert len(events) == 24
            log = owned_path / "requests.jsonl"
            # Keep only correction metrics in the stage comparison (pipeline is real).
            rows = [
                line
                for line in log.read_text().splitlines()
                if "owned_pages"
                in json.loads(json.loads(line)["messages"][1]["content"][0]["text"])[
                    "context"
                ]
            ]
            stage_log = evidence / "requests.jsonl"
            stage_log.write_text("\n".join(rows) + "\n")
            metrics = requests(stage_log)
            metrics["correction_scopes"].sort(key=lambda scope: scope["owned_pages"][0])
            assert metrics["phases"] == {"correction": 12}
            old = immutable(workspace)
            sent = log.read_bytes()
            started = time.perf_counter()
            resumed = resume_document(workspace=workspace, process_command=command)
            resume_elapsed = time.perf_counter() - started
            assert resumed["processing"]["status"] == "completed"
            assert immutable(workspace) == old and log.read_bytes() == sent
            snapshot = load_source_snapshot(workspace)
            source_order = []
            for block in snapshot.blocks:
                pages = []
                for location in block.locations:
                    assert isinstance(location, PDFLocation)
                    pages.append(location.page)
                source_order.append({"text": block.text, "pages": pages})
            # All input/config/raw answers are compared; elapsed and artifact digests are not answers.
            comparable = {
                "inputs": [
                    {"request": record["request"], "config": record["config"]}
                    for record in records
                ],
                "answers": [
                    record["attempts"][0]["final"]["message"]["content"]
                    for record in records
                ],
                "source_order": source_order,
                "metrics": metrics,
            }
            (evidence / "comparable.json").write_text(
                json.dumps(comparable, ensure_ascii=False, indent=2)
            )
            (evidence / "immutable-hashes.json").write_text(json.dumps(old, indent=2))
            (evidence / "config.json").write_text(json.dumps(manifest, indent=2))
            return {
                "format": "pdf-correction",
                "concurrency": concurrency,
                "stage_seconds": elapsed,
                "public_resume_from_draft_seconds": run_elapsed,
                "no_change_resume_seconds": resume_elapsed,
                "peak_actual_faux_calls": peak,
                "metrics": metrics,
                "calls_on_no_change": 0,
                "immutable_unchanged": True,
                "input_sha256": manifest["document"]["sha256"],
            }
    finally:
        assert owned_path is None or not owned_path.exists()
        (evidence / "cleanup.txt").write_text(
            "owned external TemporaryDirectory removed; generate children drained/reaped\n"
        )
