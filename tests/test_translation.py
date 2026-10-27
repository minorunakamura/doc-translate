"""Saved source and fixed preparation -> offline helper -> adopted translations."""

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
from test_preparation import CONFIG, rewrite_artifact, source
from test_preparation import run as prepare

pytest_plugins = ["test_preparation"]

from doc_translate.inference import GenerationOptions
from doc_translate.preparation import StylePolicy, load_preparation
from doc_translate.snapshot import (
    SourceSnapshot,
    load_source_snapshot,
    save_source_snapshot,
)
from doc_translate.translation import load_translation, translate_document
from doc_translate.units import generate_translation_units

ROOT = Path(__file__).resolve().parents[1]


def prepared(tmp_path):
    workspace, candidates = source(tmp_path)
    prepare(tmp_path, workspace, candidates)
    return workspace


def translate(tmp_path, workspace, response=None, **kwargs):
    answers = tmp_path / "translation-answers.json"
    answers.write_text(json.dumps(response or {}))
    node = shutil.which("node")
    assert node
    return translate_document(
        workspace=workspace,
        config=kwargs.pop("config", CONFIG),
        process_command=[
            node,
            str(ROOT / "ai/tests/translation-process.mjs"),
            str(tmp_path / "credentials"),
            str(answers),
            str(tmp_path / "translation-requests.jsonl"),
        ],
        **kwargs,
    )


def test_translation_adopts_and_reuses_fixed_inputs_without_sending_local_payload(
    tmp_path,
):
    workspace = prepared(tmp_path)
    digest = translate(tmp_path, workspace)
    adopted = load_translation(workspace)
    assert adopted == load_translation(workspace, digest)
    assert adopted.processing.status == "completed"
    assert [item.text for item in adopted.translations] == [
        "訳:A transaction log records changes. Keep Nimbus unchanged."
    ]
    assert adopted.uncertainties == []
    assert adopted.content_reviews == []
    requests = (tmp_path / "translation-requests.jsonl").read_text().splitlines()
    assert len(requests) == 1
    sent = json.loads(json.loads(requests[0])["messages"][1]["content"][0]["text"])
    assert sent["glossary"] == load_preparation(workspace).glossary.model_dump()
    assert "tokens" not in sent["text"]
    assert translate(tmp_path, workspace) == digest
    assert (
        tmp_path / "translation-requests.jsonl"
    ).read_text().splitlines() == requests
    run = json.loads(
        next((workspace / "attempts/translation").glob("*.json")).read_bytes()
    )
    assert run["attempts"][0]["final"]["usage_status"] == "observed"
    assert run["status"] == "validated"


@pytest.mark.parametrize("bad", ["missing", "duplicate", "unknown", "empty", "marker"])
def test_translation_rejects_invalid_answers_and_records_nonadoption(tmp_path, bad):
    workspace = prepared(tmp_path)
    unit = generate_translation_units(load_source_snapshot(workspace))[0]
    item = {"unit_id": unit.unit_id, "text": "訳文", "uncertainties": []}
    items = [item]
    if bad == "missing":
        items = []
    elif bad == "duplicate":
        items = [item, item]
    elif bad == "unknown":
        item["unit_id"] = "unknown"
    elif bad == "empty":
        item["text"] = "  \n"
    else:
        item["text"] = "未知⟦=99⟧"
    digest = translate(tmp_path, workspace, {"answer": {"translations": items}})
    adopted = load_translation(workspace, digest)
    assert adopted.processing.status == "failed"
    assert adopted.translations == []
    assert adopted.chunks[0].error
    assert adopted.chunks[0].artifact_sha256 is None
    assert len(adopted.runs) == 1
    record = json.loads(
        (workspace / "attempts/translation" / f"{adopted.runs[0]}.json").read_bytes()
    )
    assert record["status"] == "rejected"
    assert record["attempts"][0]["final"]["usage_status"] == "observed"
    assert not list((workspace / "translations/chunks").glob("*.json"))


@pytest.mark.parametrize("text", ["marker消失", "⟦1⟧訳⟦/1⟧⟦1⟧重複⟦/1⟧", "⟦/1⟧訳⟦1⟧"])
def test_translation_rejects_protected_marker_inventory_and_structure_changes(
    tmp_path, emphasized_source, text
):
    workspace, unit = emphasized_source
    prepare(
        tmp_path,
        workspace,
        [],
        answers_override={
            "collect": {"context": "Records", "candidates": []},
            "organize": {"context": "Records", "terms": {}},
        },
    )
    digest = translate(
        tmp_path,
        workspace,
        {
            "answer": {
                "translations": [
                    {"unit_id": unit.unit_id, "text": text, "uncertainties": []}
                ]
            }
        },
    )
    adopted = load_translation(workspace, digest)
    assert adopted.processing.status == "failed"
    assert adopted.translations == []


def prepare_blocks(tmp_path, blocks):
    workspace, _ = source(tmp_path)
    data = load_source_snapshot(workspace).model_dump(mode="json")
    data["blocks"] = [
        {
            "block_id": f"block-{index}",
            "kind": kind,
            "text": text,
            "locations": [{"format": "pdf", "page": 1, "evidence": "artificial"}],
        }
        for index, (kind, text) in enumerate(blocks)
    ]
    save_source_snapshot(workspace, SourceSnapshot.model_validate(data))
    prepare(
        tmp_path,
        workspace,
        [],
        answers_override={
            "collect": {"context": "Records", "candidates": []},
            "organize": {"context": "Records", "terms": {}},
        },
    )
    return workspace


def requests(tmp_path):
    return [
        json.loads(json.loads(line)["messages"][1]["content"][0]["text"])
        for line in (tmp_path / "translation-requests.jsonl").read_text().splitlines()
    ]


def test_translation_chunks_respect_sections_and_retain_success_on_partial_failure(
    tmp_path,
):
    workspace = prepare_blocks(
        tmp_path, [("body", "first"), ("heading", "Second"), ("body", "failure")]
    )
    digest = translate(tmp_path, workspace, {"fail": "failure"})
    partial = load_translation(workspace, digest)
    assert partial.processing.status == "failed"
    assert [item.text for item in partial.translations] == ["訳:first"]
    assert len(requests(tmp_path)) == 2
    assert [len(json.loads(r["text"])) for r in requests(tmp_path)] == [1, 2] or [
        len(json.loads(r["text"])) for r in requests(tmp_path)
    ] == [2, 1]
    assert sorted(
        tuple(unit["kind"] for unit in json.loads(request["text"]))
        for request in requests(tmp_path)
    ) == [("body",), ("heading", "body")]
    accepted = partial.chunks[0].artifact_sha256
    digest = translate(tmp_path, workspace)
    completed = load_translation(workspace, digest)
    assert completed.processing.status == "completed"
    assert completed.chunks[0].artifact_sha256 == accepted
    assert [item.text for item in completed.translations] == [
        "訳:first",
        "訳:Second",
        "訳:failure",
    ]
    assert len(requests(tmp_path)) == 3
    assert len(completed.runs) == 3


def test_translation_defaults_to_twelve_thousand_source_characters_and_two_active_calls(
    tmp_path,
):
    workspace = prepare_blocks(
        tmp_path, [("body", "a" * 6500), ("body", "b" * 6500), ("body", "c" * 6500)]
    )
    digest = translate(tmp_path, workspace, {"barrier": True})
    adopted = load_translation(workspace, digest)
    assert adopted.processing.status == "completed"
    assert len(adopted.chunks) == 3
    assert [len(chunk.units[0].source_text) for chunk in adopted.chunks] == [
        6500,
        6500,
        6500,
    ]
    assert all(len(json.loads(r["text"])) == 1 for r in requests(tmp_path))


@pytest.mark.parametrize(
    "blocks",
    [[("body", "word " * 40)], [("body", "alpha " * 10), ("body", "beta " * 10)]],
)
def test_translation_reduces_length_without_summarizing_and_reuses_split_results(
    tmp_path, blocks
):
    workspace = prepare_blocks(tmp_path, blocks)
    digest = translate(tmp_path, workspace, {"length_above": 80})
    adopted = load_translation(workspace, digest)
    assert adopted.processing.status == "completed"
    assert "".join(
        unit.source_text for chunk in adopted.chunks for unit in chunk.units
    ) == "".join(text for _, text in blocks)
    assert all(
        sum(len(u.source_text) for u in chunk.units) <= 80 for chunk in adopted.chunks
    )
    assert len(adopted.runs) > len(adopted.chunks)
    count = len(requests(tmp_path))
    assert translate(tmp_path, workspace, {"length_above": 80}) == digest
    assert len(requests(tmp_path)) == count


def test_translation_splits_oversized_units_before_sending_and_retains_indivisible_span(
    tmp_path, emphasized_source
):
    workspace, unit = emphasized_source
    prepare(
        tmp_path,
        workspace,
        [],
        answers_override={
            "collect": {"context": "Records", "candidates": []},
            "organize": {"context": "Records", "terms": {}},
        },
    )
    digest = translate(tmp_path, workspace, max_source_chars=10)
    adopted = load_translation(workspace, digest)
    assert adopted.processing.status == "failed"
    assert adopted.chunks[0].error is not None
    assert "Cannot split" in adopted.chunks[0].error
    assert adopted.chunks[0].units[0] == unit
    assert not (tmp_path / "translation-requests.jsonl").exists()
    assert len(adopted.runs) == 1


def test_translation_maps_uncertainty_to_source_without_content_approval(
    tmp_path, emphasized_source
):
    workspace, unit = emphasized_source
    prepare(
        tmp_path,
        workspace,
        [],
        answers_override={
            "collect": {"context": "Records", "candidates": []},
            "organize": {"context": "Records", "terms": {}},
        },
    )
    digest = translate(
        tmp_path,
        workspace,
        {
            "answer": {
                "translations": [
                    {
                        "unit_id": unit.unit_id,
                        "text": "⟦1⟧取引⟦/1⟧ログです。",
                        "uncertainties": [
                            {"reason": "Meaning is ambiguous", "origin": "undetermined"}
                        ],
                    }
                ]
            }
        },
    )
    adopted = load_translation(workspace, digest)
    assert adopted.processing.status == "completed"
    assert adopted.content_reviews == []
    assert adopted.uncertainties[0].model_dump(mode="json") == {
        "reason": "Meaning is ambiguous",
        "origin": "undetermined",
        "stage": "translation",
        "target_ids": [unit.unit_id],
        "locations": [location.model_dump(mode="json") for location in unit.locations],
    }


def test_translation_does_not_reuse_reordered_ids(tmp_path):
    workspace = prepare_blocks(tmp_path, [("body", "first"), ("body", "second")])
    units = generate_translation_units(load_source_snapshot(workspace))
    digest = translate(
        tmp_path,
        workspace,
        {
            "answer": {
                "translations": [
                    {"unit_id": unit.unit_id, "text": "訳", "uncertainties": []}
                    for unit in reversed(units)
                ]
            }
        },
    )
    assert load_translation(workspace, digest).translations == []


def test_translation_reuses_same_marker_input_after_code_and_preparation_metadata_changes(
    tmp_path,
):
    workspace = prepare_blocks(tmp_path, [("body", "Before SECRET_CODE after.")])
    data = load_source_snapshot(workspace).model_dump(mode="json")
    data["blocks"][0]["structure"] = {
        "inline": [
            {"kind": "text", "text": "Before "},
            {"kind": "code", "text": "SECRET_CODE"},
            {"kind": "text", "text": " after."},
        ]
    }
    save_source_snapshot(workspace, SourceSnapshot.model_validate(data))
    digest = translate(tmp_path, workspace)
    adopted = load_translation(workspace, digest)
    assert "SECRET_CODE" not in json.dumps(requests(tmp_path))
    data["blocks"][0]["text"] = "Before CHANGED_CODE after."
    data["blocks"][0]["structure"]["inline"][1]["text"] = "CHANGED_CODE"
    new_source = save_source_snapshot(workspace, SourceSnapshot.model_validate(data))
    preparation = load_preparation(workspace).model_dump(mode="json")
    changed_runs = []
    for run_digest in preparation["runs"]:
        record = json.loads(
            (workspace / "attempts/preparation" / f"{run_digest}.json").read_bytes()
        )
        record["method"] = "other-preparation-metadata"
        changed_runs.append(rewrite_artifact(workspace, "attempts/preparation", record))
    preparation["runs"] = changed_runs
    changed_preparation = rewrite_artifact(
        workspace, "snapshots/preparation", preparation
    )
    second = translate(tmp_path, workspace, preparation_sha256=changed_preparation)
    rebound = load_translation(workspace, second)
    assert len(requests(tmp_path)) == 1
    assert second != digest
    assert rebound.source_snapshot_sha256 == new_source
    assert rebound.preparation_sha256 == changed_preparation
    assert rebound.chunks[0].artifact_sha256 == adopted.chunks[0].artifact_sha256
    assert (
        rebound.chunks[0].units[0].source_hash != adopted.chunks[0].units[0].source_hash
    )


@pytest.mark.parametrize(
    "change", ["source", "context", "glossary", "policy", "options", "thinking"]
)
def test_translation_resends_when_actual_input_changes(tmp_path, change):

    workspace = prepared(tmp_path)
    first = translate(tmp_path, workspace)
    kwargs = {}
    if change == "source":
        data = load_source_snapshot(workspace).model_dump(mode="json")
        data["blocks"][0]["text"] += " New prose."
        save_source_snapshot(workspace, SourceSnapshot.model_validate(data))
    elif change in {"context", "glossary", "policy"}:
        workspace, candidates = source(tmp_path)
        prepare(
            tmp_path,
            workspace,
            candidates,
            version="2",
            terms={"New": "新語"} if change == "glossary" else None,
            policy=StylePolicy(version="2", text="常体で訳す。")
            if change == "policy"
            else load_preparation(workspace).policy,
            answers_override={
                "collect": {"context": "Records", "candidates": candidates},
                "organize": {
                    "context": "New context"
                    if change == "context"
                    else load_preparation(workspace).context,
                    "terms": {"Nimbus": "Nimbus"},
                },
            },
        )
    elif change == "options":
        kwargs["config"] = CONFIG.model_copy(
            update={"options": GenerationOptions(maxTokens=512)}
        )
    else:
        kwargs["config"] = CONFIG.model_copy(update={"thinking": "high"})
    second = translate(tmp_path, workspace, **kwargs)
    assert second != first
    assert len(requests(tmp_path)) == (1 if change == "thinking" else 2)
    assert load_translation(workspace, second).processing.status == (
        "failed" if change == "thinking" else "completed"
    )


def test_translation_reduces_published_input_limit_and_records_unknown_usage(tmp_path):
    workspace = prepare_blocks(tmp_path, [("body", "word " * 40)])
    digest = translate(tmp_path, workspace, {"input_limit_bytes": 100})
    adopted = load_translation(workspace, digest)
    assert adopted.processing.status == "completed"
    records = [
        json.loads((workspace / "attempts/translation" / f"{run}.json").read_bytes())
        for run in adopted.runs
    ]
    failures = [record for record in records if record["error"] == "input_limit"]
    assert failures
    assert failures[0]["attempts"][0]["final"]["usage_status"] == "unknown"
    assert failures[0]["attempts"][0]["final"]["usage"] is None


@pytest.mark.parametrize(
    "tamper", ["terminal", "exit", "model", "submitted", "run-list"]
)
def test_translation_load_rejects_rehashed_unvalidated_attempt(tmp_path, tamper):
    workspace = prepared(tmp_path)
    digest = translate(tmp_path, workspace)
    data = load_translation(workspace, digest).model_dump(mode="json")
    chunk = data["chunks"][0]
    artifact = json.loads(
        (
            workspace / "translations/chunks" / f"{chunk['artifact_sha256']}.json"
        ).read_bytes()
    )
    run = json.loads(
        (workspace / "attempts/translation" / f"{artifact['run']}.json").read_bytes()
    )
    attempt = run["attempts"][-1]
    if tamper == "terminal":
        attempt["final"]["terminal"]["reason"] = "length"
    elif tamper == "exit":
        attempt["exit_code"] = 1
    elif tamper == "model":
        attempt["final"]["message"]["model"] = "wrong"
    elif tamper == "submitted":
        attempt["final"]["submitted"]["maxTokens"] = 1
    if tamper == "run-list":
        data["runs"] = []
    else:
        new_run = rewrite_artifact(workspace, "attempts/translation", run)
        data["runs"] = [new_run]
        artifact["run"] = new_run
        chunk["artifact_sha256"] = rewrite_artifact(
            workspace, "translations/chunks", artifact
        )
    changed = rewrite_artifact(workspace, "snapshots/translation", data)
    with pytest.raises(ValueError):
        load_translation(workspace, changed)


@pytest.mark.parametrize("signum", [signal.SIGINT, signal.SIGTERM])
def test_translation_cancels_parallel_children_and_drains_before_return(
    tmp_path, signum
):
    workspace = prepare_blocks(tmp_path, [("body", letter * 6500) for letter in "abcd"])
    script = (
        "from pathlib import Path; from test_translation import translate; "
        f"translate(Path({str(tmp_path)!r}), Path({str(workspace)!r}), {{'wait': True}})"
    )
    child = subprocess.Popen(
        [sys.executable, "-c", script],
        env={**os.environ, "PYTHONPATH": str(ROOT / "tests")},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        recording = tmp_path / "translation-requests.jsonl"
        deadline = time.monotonic() + 10
        while not recording.exists() or len(recording.read_text().splitlines()) < 2:
            assert child.poll() is None
            assert time.monotonic() < deadline
            time.sleep(0.01)
        child.send_signal(signum)
        stdout, stderr = child.communicate(timeout=10)
        assert child.returncode == 0, (stdout, stderr)
        adopted = load_translation(workspace)
        assert adopted.processing.status == "failed"
        assert adopted.translations == []
        assert len(requests(tmp_path)) == 2
        assert (
            tmp_path / "translation-requests.jsonl.drained"
        ).read_text().splitlines() == ["drained", "drained"]
        assert len(adopted.runs) == 2
        records = [
            json.loads(
                (workspace / "attempts/translation" / f"{run}.json").read_bytes()
            )
            for run in adopted.runs
        ]
        assert all(record["error"] == "cancelled" for record in records)
        assert all(
            record["attempts"][0]["final"]["usage"]["input"] == 17 for record in records
        )
    finally:
        if child.poll() is None:
            child.kill()
            child.communicate()


@pytest.mark.parametrize("retry", [True, "always"])
def test_translation_preserves_finite_adapter_retry_and_all_usage(tmp_path, retry):
    workspace = prepared(tmp_path)
    digest = translate(tmp_path, workspace, {"retry": retry})
    adopted = load_translation(workspace, digest)
    record = json.loads(
        (workspace / "attempts/translation" / f"{adopted.runs[0]}.json").read_bytes()
    )
    assert len(adopted.runs) == 1
    assert [attempt["wait_seconds"] for attempt in record["attempts"]] == (
        [3, 6, 0] if retry == "always" else [3, 0]
    )
    assert len(requests(tmp_path)) == (3 if retry == "always" else 2)
    assert all(
        attempt["final"]["usage_status"] == "observed" for attempt in record["attempts"]
    )
    assert adopted.processing.status == ("failed" if retry == "always" else "completed")


@pytest.mark.parametrize(
    "directory",
    ["snapshots/translation", "translations/chunks", "attempts/translation"],
)
def test_translation_does_not_adopt_or_reuse_corrupt_artifacts(tmp_path, directory):
    workspace = prepared(tmp_path)
    translate(tmp_path, workspace)
    artifact = next((workspace / directory).glob("*.json"))
    artifact.write_bytes(artifact.read_bytes() + b" ")
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        load_translation(workspace)
    before = len(requests(tmp_path))
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        translate(tmp_path, workspace)
    assert len(requests(tmp_path)) == before


@pytest.mark.parametrize(
    "target",
    ["translations/chunks", "snapshots/translation", "translation-snapshot.json"],
)
def test_translation_save_failure_preserves_previous_adoption_and_attempts(
    tmp_path, monkeypatch, target
):

    workspace = prepared(tmp_path)
    previous = translate(tmp_path, workspace)
    reference = (workspace / "translation-snapshot.json").read_bytes()
    real_replace = os.replace

    def fail_replace(src, dst):
        if target in str(dst):
            raise OSError("injected replace failure")
        return real_replace(src, dst)

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", fail_replace)
        with pytest.raises(OSError, match="injected replace failure"):
            translate(
                tmp_path,
                workspace,
                config=CONFIG.model_copy(
                    update={"options": GenerationOptions(maxTokens=512)}
                ),
            )
    assert (workspace / "translation-snapshot.json").read_bytes() == reference
    assert load_translation(workspace) == load_translation(workspace, previous)
    assert len(list((workspace / "attempts/translation").glob("*.json"))) == 2


def test_translation_respects_workspace_writer_lock(tmp_path):
    from doc_translate.workspace import workspace_lock

    workspace = prepared(tmp_path)
    with workspace_lock(workspace), pytest.raises(RuntimeError, match="writer"):
        translate(tmp_path, workspace)
    assert not (tmp_path / "translation-requests.jsonl").exists()


def test_translation_splits_initial_long_unit_losslessly_before_inference(tmp_path):
    workspace = prepare_blocks(tmp_path, [("body", "word " * 20)])
    digest = translate(tmp_path, workspace, max_source_chars=30)
    adopted = load_translation(workspace, digest)
    assert adopted.processing.status == "completed"
    assert (
        "".join(chunk.units[0].source_text for chunk in adopted.chunks) == "word " * 20
    )
    assert all(
        sum(len(u["source_text"]) for u in json.loads(request["text"])) <= 30
        for request in requests(tmp_path)
    )
    assert all(
        chunk.units[0].parent_unit_id == "unit-1"
        and chunk.units[0].source_range is not None
        for chunk in adopted.chunks
    )


def test_translation_epub_reader_source_never_sends_code_formula_or_xhtml(tmp_path):
    from test_epub import make_epub
    from test_epub_source import extract

    xhtml = b'<html xmlns="http://www.w3.org/1999/xhtml"><body><p>Before <code>SECRET_CODE</code><math xmlns="http://www.w3.org/1998/Math/MathML"><mi>SECRET_FORMULA</mi></math> after.</p><pre>SECRET_BLOCK</pre></body></html>'
    snapshot = extract(make_epub(tmp_path, extra={"OPS/text/ch.xhtml": xhtml}))
    workspace = tmp_path / "workspace"
    save_source_snapshot(workspace, snapshot)
    prepare(
        tmp_path,
        workspace,
        [],
        answers_override={
            "collect": {"context": "Records", "candidates": []},
            "organize": {"context": "Records", "terms": {}},
        },
    )
    digest = translate(tmp_path, workspace)
    adopted = load_translation(workspace, digest)
    assert adopted.processing.status == "completed"
    assert [item.text for item in adopted.translations] == ["訳:Before ⟦=1⟧⟦=2⟧ after."]
    sent = json.dumps(requests(tmp_path))
    assert all(
        secret not in sent
        for secret in [
            "SECRET_CODE",
            "SECRET_FORMULA",
            "SECRET_BLOCK",
            "<html",
            "<math",
        ]
    )


def test_translation_keeps_split_successes_when_another_split_fails(tmp_path):
    workspace = prepare_blocks(tmp_path, [("body", "alpha " * 12 + "failure " * 9)])
    digest = translate(tmp_path, workspace, {"length_above": 80, "fail": "failure"})
    partial = load_translation(workspace, digest)
    assert partial.processing.status == "failed"
    assert partial.translations
    accepted = [chunk.artifact_sha256 for chunk in partial.chunks if chunk.answer]
    sent = len(requests(tmp_path))
    second = translate(tmp_path, workspace)
    completed = load_translation(workspace, second)
    assert completed.processing.status == "completed"
    assert [
        chunk.artifact_sha256
        for chunk in completed.chunks
        if chunk.artifact_sha256 in accepted
    ] == accepted
    assert len(requests(tmp_path)) > sent
    assert all(
        json.loads(request["text"])[0]["unit_id"]
        not in {item.unit_id for item in partial.translations}
        for request in requests(tmp_path)[sent:]
    )
