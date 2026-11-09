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


@pytest.fixture(scope="module")
def prepared_seed(tmp_path_factory):
    root = tmp_path_factory.mktemp("translation-prepared")
    workspace, candidates = source(root)
    prepare(root, workspace, candidates)
    before = {
        path.relative_to(workspace): path.read_bytes()
        for path in workspace.rglob("*")
        if path.is_file()
    }
    yield workspace
    assert {
        path.relative_to(workspace): path.read_bytes()
        for path in workspace.rglob("*")
        if path.is_file()
    } == before


@pytest.fixture
def prepared(tmp_path, prepared_seed):
    workspace = shutil.copytree(prepared_seed, tmp_path / "workspace")
    load_source_snapshot(workspace)
    load_preparation(workspace)
    return workspace


def test_prepared_copies_load_independently_without_changing_seed(
    tmp_path, prepared, prepared_seed
):
    other = shutil.copytree(prepared_seed, tmp_path / "other")
    assert load_source_snapshot(prepared) == load_source_snapshot(other)
    assert load_preparation(prepared) == load_preparation(other)
    pointer = "source-snapshot.json"
    original = (prepared_seed / pointer).read_bytes()
    (prepared / pointer).write_bytes(b"changed copy")
    assert (other / pointer).read_bytes() == original
    assert (prepared_seed / pointer).read_bytes() == original
    assert load_preparation(other) == load_preparation(prepared_seed)


def translate(tmp_path, workspace, response=None, **kwargs):
    answers = tmp_path / "translation-answers.json"
    answers.write_text(json.dumps(response or {}))
    from doc_translate.translation import _projection

    units = generate_translation_units(
        load_source_snapshot(workspace, kwargs.get("source_sha256"))
    )
    answers.with_suffix(".bindings.json").write_text(
        json.dumps(
            {
                "units": _projection(units, 2),
                "unit_ids": [unit.unit_id for unit in units],
            }
        )
    )
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
    workspace, candidates = source(tmp_path)
    prepare(tmp_path, workspace, candidates)
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
    events = []
    assert translate(tmp_path, workspace, observer=events.append) == digest
    saved = next(e for e in reversed(events) if e.kind == "adopted")
    assert saved.succeeded == saved.reused == saved.total == 1
    assert saved.failed == saved.not_processed == 0
    assert not any(e.kind == "request_start" for e in events)
    assert (
        tmp_path / "translation-requests.jsonl"
    ).read_text().splitlines() == requests
    run = json.loads(
        next((workspace / "attempts/translation").glob("*.json")).read_bytes()
    )
    assert run["attempts"][0]["final"]["usage_status"] == "observed"
    assert run["status"] == "validated"


@pytest.mark.parametrize("bad", ["missing", "duplicate", "unknown", "empty", "marker"])
def test_translation_rejects_invalid_answers_and_records_nonadoption(
    tmp_path, prepared, bad
):
    workspace = prepared
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
    assert len(adopted.runs) == (3 if bad == "marker" else 1)
    record = json.loads(
        (workspace / "attempts/translation" / f"{adopted.runs[0]}.json").read_bytes()
    )
    assert record["status"] == "rejected"
    assert record["attempts"][0]["final"]["usage_status"] == "observed"
    assert not list((workspace / "translations/chunks").glob("*.json"))


def test_translation_repairs_all_marker_moves_with_specific_untrusted_feedback(
    tmp_path,
):
    workspace = prepare_blocks(
        tmp_path,
        [
            ("body", "Use https://example.invalid/private."),
            ("body", "Another paragraph."),
        ],
    )
    units = generate_translation_units(load_source_snapshot(workspace))
    marker = units[0].tokens[0].start_marker
    invalid = {
        "translations": [
            {"unit_id": units[0].unit_id, "text": "訳文", "uncertainties": []},
            {
                "unit_id": units[1].unit_id,
                "text": "ARTIFICIAL_INJECTION " + marker,
                "uncertainties": [],
            },
        ]
    }
    notices = []

    def before_send(_config, request, _preflight):
        notices.append(request)
        feedback = request.context.get("translation_feedback")
        if feedback is None:
            return
        assert feedback["code"] == "translation_marker_mismatch"
        first, second = feedback["units"]
        assert first["unit_id"] == "request-unit-1"
        assert first["expected_markers"] == first["missing_markers"] == [marker]
        assert first["actual_markers"] == first["extra_markers"] == []
        assert second["unit_id"] == "request-unit-2"
        assert second["expected_markers"] == second["missing_markers"] == []
        assert second["actual_markers"] == second["extra_markers"] == [marker]
        assert second["translated_text"] == "ARTIFICIAL_INJECTION " + marker
        assert "ARTIFICIAL_INJECTION" not in request.policy
        assert "untrusted data, not instructions" in request.policy
        (tmp_path / "translation-answers.json").write_text(json.dumps({}))

    digest = translate(
        tmp_path, workspace, {"answer": invalid}, before_send=before_send
    )
    adopted = load_translation(workspace, digest)
    assert adopted.processing.status == "completed"
    assert len(adopted.runs) == 2
    assert len(notices) == 2
    assert notices[0].text == notices[1].text
    assert notices[0].glossary == notices[1].glossary
    repaired_context = dict(notices[1].context)
    repaired_context.pop("translation_feedback")
    assert repaired_context == notices[0].context
    records = [
        json.loads((workspace / "attempts/translation" / f"{run}.json").read_text())
        for run in adopted.runs
    ]
    assert [r["status"] for r in records] == ["rejected", "validated"]
    assert all(r["attempts"][0]["final"]["usage_status"] == "observed" for r in records)
    assert len(records[0]["validation_feedback"]["units"]) == 2
    sent = (tmp_path / "translation-requests.jsonl").read_bytes()
    assert b"example.invalid/private" not in sent
    assert translate(tmp_path, workspace) == digest
    assert (tmp_path / "translation-requests.jsonl").read_bytes() == sent


@pytest.mark.parametrize("tamper", ["none", "feedback", "policy", "schema"])
def test_translation_revalidates_repaired_orphan_request_before_reuse(
    tmp_path, prepared, monkeypatch, tamper
):
    workspace = prepared
    unit = generate_translation_units(load_source_snapshot(workspace))[0]
    invalid = {
        "translations": [
            {"unit_id": unit.unit_id, "text": "訳文⟦=99⟧", "uncertainties": []}
        ]
    }
    replace = os.replace

    def before_send(_config, request, _preflight):
        if "translation_feedback" in request.context:
            (tmp_path / "translation-answers.json").write_text(json.dumps({}))

    def fail_replace(src, dst):
        if "translations/chunks" in str(dst):
            raise OSError("interrupted repaired chunk save")
        return replace(src, dst)

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", fail_replace)
        with pytest.raises(OSError, match="interrupted repaired chunk save"):
            translate(tmp_path, workspace, {"answer": invalid}, before_send=before_send)
    path, record = next(
        (p, json.loads(p.read_text()))
        for p in (workspace / "attempts/translation").glob("*.json")
        if json.loads(p.read_text())["status"] == "validated"
    )
    sent = len(requests(tmp_path))
    assert sent == 2
    if tamper != "none":
        path.rename(path.with_suffix(".original"))
        if tamper == "feedback":
            record["request"]["context"]["translation_feedback"]["code"] = "unapproved"
        elif tamper == "policy":
            record["request"]["policy"] = "unapproved policy"
        else:
            record["request"]["context"]["response_schema"] = {}
        rewrite_artifact(workspace, "attempts/translation", record)
    digest = translate(tmp_path, workspace)
    assert load_translation(workspace, digest).processing.status == "completed"
    assert len(requests(tmp_path)) == sent + (tamper != "none")


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
    assert len(requests(tmp_path)) == len(adopted.runs) == 3


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
    events = []
    digest = translate(tmp_path, workspace, {"barrier": True}, observer=events.append)
    waiting, peak = set(), 0
    for event in events:
        if event.kind == "waiting":
            waiting.add(event.request_id)
            peak = max(peak, len(waiting))
        elif event.kind == "request_end":
            waiting.discard(event.request_id)
    assert peak == 2 and not waiting
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
    events = []

    def observe(event):
        events.append(event)
        if event.kind == "adopted":
            saved = load_translation(workspace)
            assert event.total == sum(len(c.units) for c in saved.chunks)
            assert event.succeeded == len(saved.translations)

    digest = translate(tmp_path, workspace, {"length_above": 80}, observer=observe)
    assert any(e.kind == "split" for e in events)
    assert all(e.job_id for e in events if e.kind == "request_start")
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
    tmp_path, emphasized_source, catalog_publications
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
    assert len(catalog_publications) == 1
    assert catalog_publications[0]["status"] == "rejected"
    assert catalog_publications[0]["attempts"] == []


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


@pytest.mark.parametrize("temperature", [-0.0, 0.0], ids=["negative", "positive"])
def test_translation_recovers_orphan_with_equal_signed_zero_config(
    tmp_path, prepared, temperature
):
    saved_config = CONFIG.model_copy(
        update={"options": GenerationOptions(temperature=temperature)}
    )
    current_config = CONFIG.model_copy(
        update={"options": GenerationOptions(temperature=-temperature)}
    )
    assert saved_config == current_config
    first = translate(tmp_path, prepared, config=saved_config)
    initial = load_translation(prepared, first)
    assert initial.processing.status == "completed"
    immutable = {
        path: path.read_bytes()
        for path in prepared.rglob("*")
        if path.is_file() and path.name != "translation-snapshot.json"
    }
    log = tmp_path / "translation-requests.jsonl"
    sent = log.read_bytes()
    (prepared / "translation-snapshot.json").unlink()
    digest = translate(tmp_path, prepared, config=current_config)
    adopted = load_translation(prepared, digest)
    assert adopted == load_translation(prepared)
    assert adopted.processing.status == "completed"
    assert adopted.config.model_dump_json() == current_config.model_dump_json()
    assert adopted.runs == initial.runs
    assert adopted.translations == initial.translations
    assert log.read_bytes() == sent
    assert all(path.read_bytes() == data for path, data in immutable.items())
    assert load_translation(prepared, first) == initial


@pytest.mark.parametrize(
    "change", ["source", "context", "glossary", "policy", "options", "thinking"]
)
def test_translation_resends_when_actual_input_changes(tmp_path, prepared, change):

    workspace = prepared
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
def test_translation_load_rejects_rehashed_unvalidated_attempt(
    tmp_path, prepared, tamper
):
    workspace = prepared
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
@pytest.mark.parametrize("shared", [False, True])
def test_translation_cancels_parallel_children_and_drains_before_return(
    tmp_path, signum, shared
):
    workspace = prepare_blocks(
        tmp_path, [("body", letter * 6500) for letter in ("aaaa" if shared else "abcd")]
    )
    active = 1 if shared else 2
    script = (
        "from pathlib import Path; from test_translation import translate; "
        f"translate(Path({str(tmp_path)!r}), Path({str(workspace)!r}), {{'wait': True}})"
    )
    child = subprocess.Popen(
        [sys.executable, "-c", script],
        env={
            **os.environ,
            "PYTHONPATH": os.pathsep.join((str(ROOT / "src"), str(ROOT / "tests"))),
        },
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        recording = tmp_path / "translation-requests.jsonl"
        deadline = time.monotonic() + 10
        while (
            not recording.exists() or len(recording.read_text().splitlines()) < active
        ):
            assert child.poll() is None
            assert time.monotonic() < deadline
            time.sleep(0.01)
        child.send_signal(signum)
        stdout, stderr = child.communicate(timeout=10)
        assert child.returncode == 0, (stdout, stderr)
        adopted = load_translation(workspace)
        assert adopted.processing.status == "failed"
        assert adopted.translations == []
        assert len(requests(tmp_path)) == active
        assert (
            tmp_path / "translation-requests.jsonl.drained"
        ).read_text().splitlines() == ["drained"] * active
        assert len(adopted.runs) == active
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
def test_translation_preserves_finite_adapter_retry_and_all_usage(
    tmp_path, prepared, retry
):
    workspace = prepared
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
def test_translation_does_not_adopt_or_reuse_corrupt_artifacts(
    tmp_path, prepared, directory
):
    workspace = prepared
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
    tmp_path, prepared, monkeypatch, target
):

    workspace = prepared
    previous = translate(tmp_path, workspace)
    reference = (workspace / "translation-snapshot.json").read_bytes()
    events = []
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
                observer=events.append,
                config=CONFIG.model_copy(
                    update={"options": GenerationOptions(maxTokens=512)}
                ),
            )
    assert (workspace / "translation-snapshot.json").read_bytes() == reference
    assert load_translation(workspace) == load_translation(workspace, previous)
    assert len(list((workspace / "attempts/translation").glob("*.json"))) == 2

    assert not any(e.kind in {"adopted", "done"} for e in events)


def test_translation_respects_workspace_writer_lock(tmp_path, prepared):
    from doc_translate.workspace import workspace_lock

    workspace = prepared
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


@pytest.mark.parametrize(
    "target",
    ["translations/chunks", "snapshots/translation", "translation-snapshot.json"],
)
def test_translation_recovers_validated_calls_before_adoption_without_resending(
    tmp_path, prepared, monkeypatch, target
):
    workspace = prepared
    replace = os.replace

    def fail_replace(src, dst):
        if target in str(dst):
            raise OSError("interrupted translation save")
        return replace(src, dst)

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", fail_replace)
        with pytest.raises(OSError, match="interrupted translation save"):
            translate(tmp_path, workspace)
    sent = requests(tmp_path)
    digest = translate(tmp_path, workspace)
    assert load_translation(workspace, digest).processing.status == "completed"
    assert requests(tmp_path) == sent


@pytest.mark.parametrize(
    "blocks",
    [
        [("body", "word " * 40)],
        [("body", "alpha " * 10), ("body", "beta " * 10)],
        [("body", "alpha " * 40), ("body", "beta " * 40)],
    ],
)
@pytest.mark.parametrize(
    "target", ["snapshots/translation", "translation-snapshot.json"]
)
def test_translation_recovers_successful_split_subcalls_before_snapshot_save(
    tmp_path, monkeypatch, blocks, target
):
    workspace = prepare_blocks(tmp_path, blocks)
    replace = os.replace
    events = []

    def fail_replace(src, dst):
        if target in str(dst):
            raise OSError("interrupted split adoption")
        return replace(src, dst)

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", fail_replace)
        with pytest.raises(OSError, match="interrupted split adoption"):
            translate(tmp_path, workspace, {"length_above": 80}, observer=events.append)
    assert not any(e.kind in {"adopted", "done"} for e in events)
    assert not (workspace / "translation-snapshot.json").exists()
    sent = requests(tmp_path)
    immutable = {
        path: path.read_bytes()
        for directory in (
            "attempts/translation",
            "translations/chunks",
            "snapshots/translation",
        )
        for path in (workspace / directory).glob("*.json")
    }
    children = {
        path.stem
        for path in immutable
        if path.parent == workspace / "translations/chunks"
    }
    assert children
    events.clear()
    digest = translate(
        tmp_path, workspace, {"length_above": 80}, observer=events.append
    )
    adopted = load_translation(workspace, digest)
    assert all(path.read_bytes() == data for path, data in immutable.items())
    assert {chunk.artifact_sha256 for chunk in adopted.chunks} == children
    saved = next(e for e in reversed(events) if e.kind == "adopted")
    assert saved.succeeded == saved.reused == saved.total == len(adopted.translations)
    assert not any(e.kind == "request_start" for e in events)
    if blocks == [("body", "word " * 40)]:
        assert len(sent) == 7
        assert saved.succeeded == saved.reused == saved.total == 4
    assert adopted.processing.status == "completed"
    assert len(adopted.chunks) > 1
    assert "".join(u.source_text for c in adopted.chunks for u in c.units) == "".join(
        text for _, text in blocks
    )
    assert requests(tmp_path) == sent


@pytest.mark.parametrize("tamper", ["terminal", "incomplete", "domain", "hash"])
def test_translation_revalidates_orphan_runs_before_reusing(
    tmp_path, prepared, monkeypatch, tamper
):
    workspace = prepared
    replace = os.replace

    def fail_replace(src, dst):
        if "translations/chunks" in str(dst):
            raise OSError("interrupted chunk save")
        return replace(src, dst)

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", fail_replace)
        with pytest.raises(OSError):
            translate(tmp_path, workspace)
    path = next((workspace / "attempts/translation").glob("*.json"))
    record = json.loads(path.read_bytes())
    sent = requests(tmp_path)
    if tamper == "hash":
        path.write_bytes(path.read_bytes() + b" ")
        corrupt = path.read_bytes()
        with pytest.raises(ValueError, match="SHA-256"):
            translate(tmp_path, workspace)
        assert path.read_bytes() == corrupt
        assert requests(tmp_path) == sent
        return
    path.rename(path.with_suffix(".original"))
    if tamper == "terminal":
        record["attempts"][-1]["final"]["terminal"]["reason"] = "length"
    elif tamper == "incomplete":
        record["attempts"] = []
    else:
        record["attempts"][-1]["final"]["message"]["content"] = [
            {"type": "text", "text": '{"translations":[]}'}
        ]
    rewrite_artifact(workspace, "attempts/translation", record)
    digest = translate(tmp_path, workspace)
    assert load_translation(workspace, digest).processing.status == "completed"
    assert len(requests(tmp_path)) == len(sent) + 1


@pytest.mark.parametrize(
    "corrupt_directory",
    ["snapshots/translation", "translations/chunks", "attempts/translation"],
)
def test_manual_history_load_verifies_each_snapshot_once_per_call(
    tmp_path, monkeypatch, capsys, corrupt_directory
):
    from collections import Counter

    from doc_translate.cli import main

    workspace, candidates = source(tmp_path)
    original = load_source_snapshot(workspace)
    original.blocks.append(
        original.blocks[0].model_copy(
            update={"block_id": "second", "text": "Another paragraph."}
        )
    )
    save_source_snapshot(workspace, original)
    prepare(tmp_path, workspace, candidates)
    translate(tmp_path, workspace, max_source_chars=60)
    loaded = load_translation(workspace)
    assert len(loaded.chunks) == 2
    historical_digest = {
        "snapshots/translation": json.loads(
            (workspace / "translation-snapshot.json").read_bytes()
        )["sha256"],
        "translations/chunks": loaded.chunks[0].artifact_sha256,
        "attempts/translation": loaded.runs[0],
    }[corrupt_directory]
    historical = workspace / corrupt_directory / f"{historical_digest}.json"
    for revision in range(5):
        changes = [item.model_dump(mode="json") for item in loaded.translations]
        for item in changes:
            item["text"] = f"修正{revision} " + item["text"]
        patch = tmp_path / "patch.json"
        patch.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "base_source_sha256": loaded.source_snapshot_sha256,
                    "base_translation_sha256": json.loads(
                        (workspace / "translation-snapshot.json").read_bytes()
                    )["sha256"],
                    "translations": changes,
                }
            )
        )
        main(
            [
                "revise",
                str(workspace),
                "--translations",
                str(patch),
                "--reason",
                "Compared",
            ]
        )
        capsys.readouterr()
        loaded = load_translation(workspace)

    reads = Counter()
    read_bytes = Path.read_bytes

    def observe_read(path):
        if path.parent == workspace / "snapshots/translation":
            reads[path] += 1
        return read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", observe_read)
    assert load_translation(workspace) == loaded
    assert len(reads) == 6
    assert set(reads.values()) == {1}
    reads.clear()
    assert load_translation(workspace) == loaded
    assert len(reads) == 6
    assert set(reads.values()) == {1}
    historical.write_bytes(historical.read_bytes() + b" ")
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        load_translation(workspace)


@pytest.fixture
def catalog_publications(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    from doc_translate import translation
    from doc_translate.inference import GenerationInput

    stored = translation._AttemptCatalog.store
    records = []

    def observe(catalog, value):
        digest = stored(catalog, value)
        path = catalog.workspace / "attempts/translation" / f"{digest}.json"
        record = json.loads(path.read_bytes())
        request = GenerationInput.model_validate(record["request"])
        # Another thread sees a persisted, complete, digest-ordered snapshot.
        with ThreadPoolExecutor(max_workers=1) as executor:
            snapshot = executor.submit(catalog.candidates, request).result(timeout=5)
        assert isinstance(snapshot, tuple)
        assert [run for run, _ in snapshot] == sorted(run for run, _ in snapshot)
        assert (digest, record) in snapshot
        records.append(record)
        return digest

    monkeypatch.setattr(translation._AttemptCatalog, "store", observe)
    return records


@pytest.mark.parametrize("mode", ["normal", "repair", "failed", "split"])
def test_translation_catalog_publishes_every_saved_attempt(
    tmp_path, catalog_publications, mode
):
    workspace = prepare_blocks(
        tmp_path,
        [("heading", "Section"), ("body", "word " * (40 if mode == "split" else 2))],
    )
    response = {}
    if mode in {"repair", "failed"}:
        units = generate_translation_units(load_source_snapshot(workspace))
        response = {
            "answer": {
                "translations": [
                    {"unit_id": u.unit_id, "text": "bad ⟦=99⟧", "uncertainties": []}
                    for u in units
                ]
            }
        }
    elif mode == "split":
        response = {"length_above": 80}

    def before_send(_config, request, _preflight):
        if mode == "repair" and "translation_feedback" in request.context:
            (tmp_path / "translation-answers.json").write_text("{}")

    digest = translate(tmp_path, workspace, response, before_send=before_send)
    adopted = load_translation(workspace, digest)
    records = catalog_publications
    assert len(records) == len(adopted.runs)
    assert {r["status"] for r in records} == {
        "normal": {"validated"},
        "repair": {"rejected", "validated"},
        "failed": {"rejected"},
        "split": {"rejected", "adjusted", "validated"},
    }[mode]
    if mode == "repair":
        assert len(records) == 2
    if mode == "failed":
        assert len(records) == 3


def test_translation_catalog_indexes_orphans_in_digest_order_once_per_operation(
    tmp_path, monkeypatch
):
    from collections import Counter

    from doc_translate import translation

    workspace = prepare_blocks(
        tmp_path,
        [
            (kind, text)
            for i in range(4)
            for kind, text in [("heading", f"Section {i}"), ("body", f"Record {i}.")]
        ],
    )
    translate(tmp_path, workspace)
    initial = load_translation(workspace)
    # Two genuine successes for the same input: digest order, not insertion order.
    for run in initial.runs:
        record = json.loads(
            (workspace / "attempts/translation" / f"{run}.json").read_bytes()
        )
        record["elapsed_seconds"] += 1
        rewrite_artifact(workspace, "attempts/translation", record)
    paths = sorted((workspace / "attempts/translation").glob("*.json"))
    expected = {}
    for path in paths:
        record = json.loads(path.read_bytes())
        expected.setdefault(record["request"]["text"], path.stem)
    (workspace / "translation-snapshot.json").unlink()
    reads = Counter()
    request_count = 0
    read, request = translation._read, translation._request

    def observe_read(workspace, directory, digest):
        if directory == "attempts/translation":
            reads[digest] += 1
        return read(workspace, directory, digest)

    def observe_request(*args):
        nonlocal request_count
        request_count += 1
        return request(*args)

    sent = requests(tmp_path)
    with monkeypatch.context() as patch:
        patch.setattr(translation, "_read", observe_read)
        patch.setattr(translation, "_request", observe_request)
        digest = translate(tmp_path, workspace)
    assert reads == Counter({p.stem: 1 for p in paths})
    assert request_count == 4
    adopted = load_translation(workspace, digest)
    for chunk in adopted.chunks:
        artifact = read(workspace, "translations/chunks", chunk.artifact_sha256)
        assert artifact["run"] == expected[artifact["request"]["text"]]
    assert requests(tmp_path) == sent
    # An unrelated corrupt record must still fail before even adopted reuse.
    corrupt = workspace / "attempts/translation" / f"{'0' * 64}.json"
    corrupt.write_text("{}")
    with pytest.raises(ValueError, match="SHA-256"):
        translate(tmp_path, workspace)
    assert requests(tmp_path) == sent


@pytest.mark.parametrize("source_format", ["pdf", "epub"])
def test_translation_v2_rebinds_prefix_without_erasing_real_context(
    tmp_path, source_format
):
    from test_preparation import id_answers, id_source

    workspace = tmp_path / "workspace"
    original = id_source(tmp_path, source_format)
    save_source_snapshot(workspace, original)
    prepare(tmp_path, workspace, [], answers_override=id_answers(original))
    first = translate(tmp_path, workspace)
    old = load_translation(workspace, first)
    immutable = {
        p: p.read_bytes()
        for d in ["snapshots", "attempts", "translations"]
        for p in (workspace / d).rglob("*.json")
    }
    shifted = id_source(tmp_path, source_format, prefix=True)
    save_source_snapshot(workspace, shifted)
    sent = len(requests(tmp_path))
    digest = translate(tmp_path, workspace)
    current = load_translation(workspace, digest)
    assert len(requests(tmp_path)) == sent + 2  # Preface and changed Alpha context.
    assert current.chunks[-1].units[-1].unit_id == "unit-6"
    artifact = json.loads(
        (
            workspace
            / "translations/chunks"
            / f"{current.chunks[-1].artifact_sha256}.json"
        ).read_bytes()
    )
    assert artifact["run"] in old.runs
    assert artifact["wire_version"] == 2
    assert artifact["bindings"] == ["unit-5", "unit-6"]
    raw = json.loads(
        (workspace / "attempts/translation" / f"{artifact['run']}.json").read_bytes()
    )
    assert raw["wire_version"] == raw["request"]["context"]["wire_version"] == 2
    raw_answer = json.loads(
        raw["attempts"][-1]["final"]["message"]["content"][0]["text"]
    )
    assert [u["unit_id"] for u in raw_answer["translations"]] == [
        "request-unit-1",
        "request-unit-2",
    ]
    assert current.chunks[-1].answer is not None
    assert [u.unit_id for u in current.chunks[-1].answer.translations] == artifact[
        "bindings"
    ]
    assert all(p.read_bytes() == data for p, data in immutable.items())
    assert load_translation(workspace, first) == old
    for heading, body in [
        ("Beta changed", "queue queue."),
        ("Beta changed", "queue changed."),
    ]:
        changed = id_source(
            tmp_path, source_format, prefix=True, heading=heading, body=body
        )
        save_source_snapshot(workspace, changed)
        count = len(requests(tmp_path))
        translate(tmp_path, workspace)
        assert len(requests(tmp_path)) == count + 1


@pytest.mark.parametrize("failure", [False, True])
def test_translation_v2_single_flight_binds_independent_jobs_and_counts_usage_once(
    tmp_path, failure
):
    # Distinct tracking IDs, identical complete wire input, two concurrent jobs.
    workspace = prepare_blocks(tmp_path, [("body", "same queue ")] * 2)
    events = []
    digest = translate(
        tmp_path,
        workspace,
        {"fail": "same"} if failure else {},
        max_source_chars=11,
        observer=events.append,
    )
    adopted = load_translation(workspace, digest)
    assert len(requests(tmp_path)) == 1
    assert len(adopted.chunks) == 2
    assert len(adopted.runs) == len(set(adopted.runs)) == 1
    assert adopted.processing.status == ("failed" if failure else "completed")
    assert len([e for e in events if e.kind == "request_start"]) == 1
    if not failure:
        assert [item.unit_id for item in adopted.translations] == ["unit-1", "unit-2"]
        assert len({c.artifact_sha256 for c in adopted.chunks}) == 2
        assert translate(tmp_path, workspace, max_source_chars=11) == digest
        assert len(requests(tmp_path)) == 1


def test_translation_v2_single_flight_persistence_exception_completes_followers(
    tmp_path, monkeypatch
):
    workspace = prepare_blocks(tmp_path, [("body", "same queue ")] * 2)
    replace = os.replace

    def fail_replace(src, dst):
        if "attempts/translation" in str(dst):
            raise OSError("shared attempt save failed")
        return replace(src, dst)

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", fail_replace)
        with pytest.raises(OSError, match="shared attempt save failed"):
            translate(tmp_path, workspace, max_source_chars=11)
    assert len(requests(tmp_path)) == 1
    assert not (workspace / "translation-snapshot.json").exists()
    translate(tmp_path, workspace, max_source_chars=11)
    assert load_translation(workspace).processing.status == "completed"
    assert len(requests(tmp_path)) == 2


@pytest.mark.parametrize(
    "tamper",
    [
        "binding-order",
        "binding-duplicate",
        "version",
        "request-version",
        "raw-order",
        "raw-duplicate",
        "raw-tracking",
        "split-range",
        "split-parent",
    ],
)
def test_translation_v2_load_rejects_rehashed_binding_raw_version_and_split_changes(
    tmp_path, tamper
):
    split = tamper.startswith("split-")
    workspace = prepare_blocks(
        tmp_path,
        [("body", "word " * 10)] if split else [("body", "first"), ("body", "second")],
    )
    digest = translate(
        tmp_path, workspace, **({"max_source_chars": 30} if split else {})
    )
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
    if tamper == "binding-order":
        artifact["bindings"].reverse()
    elif tamper == "binding-duplicate":
        artifact["bindings"][1] = artifact["bindings"][0]
    elif tamper == "version":
        artifact["wire_version"] = 99
    elif tamper == "request-version":
        artifact["request"]["context"]["wire_version"] = 99
    elif split:
        field = artifact["request"]["context"]["split_inputs"][0]
        if tamper == "split-range":
            field["source_range"]["start"] += 1
        else:
            field["parent_source_hash"] = "a" * 64
        run["request"] = artifact["request"]
    else:
        raw = json.loads(run["attempts"][-1]["final"]["message"]["content"][0]["text"])
        if tamper == "raw-order":
            raw["translations"].reverse()
        elif tamper == "raw-duplicate":
            raw["translations"][1]["unit_id"] = "request-unit-1"
        else:
            raw["translations"][0]["unit_id"] = "unit-1"
        run["attempts"][-1]["final"]["message"]["content"][0]["text"] = json.dumps(raw)
    new_run = rewrite_artifact(workspace, "attempts/translation", run)
    data["runs"] = [new_run if r == artifact["run"] else r for r in data["runs"]]
    artifact["run"] = new_run
    chunk["artifact_sha256"] = rewrite_artifact(
        workspace, "translations/chunks", artifact
    )
    changed = rewrite_artifact(workspace, "snapshots/translation", data)
    with pytest.raises(ValueError):
        load_translation(workspace, changed)


def test_translation_byte_original_v1_reuse_mixed_v2_and_manual_history(tmp_path):
    from doc_translate.revise import revise_document

    workspace = tmp_path / "workspace"
    frozen = json.loads((ROOT / "tests/fixtures/translation-v1.json").read_bytes())
    for name, text in frozen.items():
        path = workspace / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    old = load_translation(workspace)
    before = {p: p.read_bytes() for p in workspace.rglob("*.json")}
    digest = translate_document(
        workspace=workspace, config=CONFIG, process_command=["must-not-launch"]
    )
    assert digest == json.loads(frozen["translation-snapshot.json"])["sha256"]
    assert all(p.read_bytes() == value for p, value in before.items())
    original = load_source_snapshot(workspace)
    original.blocks.append(
        original.blocks[0].model_copy(
            update={"block_id": "new-heading", "kind": "heading", "text": "New section"}
        )
    )
    save_source_snapshot(workspace, original)
    translate(tmp_path, workspace)
    mixed = load_translation(workspace)
    artifacts = [
        json.loads(
            (
                workspace / "translations/chunks" / f"{c.artifact_sha256}.json"
            ).read_bytes()
        )
        for c in mixed.chunks
    ]
    assert [a.get("wire_version", 1) for a in artifacts] == [1, 2]
    for index in [0, 1, 1]:
        current = load_translation(workspace)
        answer = current.chunks[index].answer
        assert answer is not None
        item = answer.translations[0].model_dump(mode="json")
        item["text"] = "手修正 " + item["text"]
        patch = tmp_path / "manual.json"
        patch.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "base_source_sha256": current.source_snapshot_sha256,
                    "base_translation_sha256": json.loads(
                        (workspace / "translation-snapshot.json").read_bytes()
                    )["sha256"],
                    "translations": [item],
                }
            )
        )
        revise_document(
            workspace=workspace, translations=patch, reason="Self-authored manual check"
        )
    manual = load_translation(workspace)
    sent = requests(tmp_path)
    translate(tmp_path, workspace)
    assert load_translation(workspace).translations == manual.translations
    assert requests(tmp_path) == sent
    assert load_translation(workspace, digest) == old
    assert all(
        p.read_bytes() == value
        for p, value in before.items()
        if p.name != "translation-snapshot.json" and p.name != "source-snapshot.json"
    )


def test_translation_v2_does_not_transfer_manual_edits_after_prefix_shift(tmp_path):
    from test_preparation import id_answers, id_source

    from doc_translate.revise import revise_document

    workspace = tmp_path / "workspace"
    original = id_source(tmp_path, "pdf")
    save_source_snapshot(workspace, original)
    prepare(tmp_path, workspace, [], answers_override=id_answers(original))
    translate(tmp_path, workspace)
    current = load_translation(workspace)
    patch = tmp_path / "manual.json"
    item = current.translations[-1].model_dump(mode="json")
    item["text"] = "手修正限定"
    patch.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "base_source_sha256": current.source_snapshot_sha256,
                "base_translation_sha256": json.loads(
                    (workspace / "translation-snapshot.json").read_bytes()
                )["sha256"],
                "translations": [item],
            }
        )
    )
    revised = revise_document(
        workspace=workspace, translations=patch, reason="Self-authored check"
    )
    manual = load_translation(workspace)
    translate(tmp_path, workspace)
    assert load_translation(workspace).translations == manual.translations
    save_source_snapshot(workspace, id_source(tmp_path, "pdf", prefix=True))
    count = len(requests(tmp_path))
    translate(tmp_path, workspace)
    assert len(requests(tmp_path)) == count + 2
    assert "手修正限定" not in [
        u.text for u in load_translation(workspace).translations
    ]
    assert load_translation(workspace, revised["translation_snapshot_sha256"]) == manual


def test_translation_v2_orphan_cannot_swallow_current_manual_subchunk(tmp_path):
    from doc_translate.revise import revise_document

    workspace = prepare_blocks(tmp_path, [("body", "first"), ("body", "second")])
    translate(tmp_path, workspace, {"fail": "first"})
    failed_pointer = (workspace / "translation-snapshot.json").read_bytes()
    translate(tmp_path, workspace)  # Genuine full-size success, later left orphaned.
    (workspace / "translation-snapshot.json").write_bytes(failed_pointer)
    failed = load_translation(workspace)
    patch = tmp_path / "manual.json"
    patch.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "base_source_sha256": failed.source_snapshot_sha256,
                "base_translation_sha256": json.loads(failed_pointer)["sha256"],
                "translations": [
                    {"unit_id": "unit-2", "text": "手修正優先", "uncertainties": []}
                ],
            }
        )
    )
    revise_document(
        workspace=workspace, translations=patch, reason="Self-authored partial check"
    )
    sent = len(requests(tmp_path))
    translate(tmp_path, workspace)
    assert [u.text for u in load_translation(workspace).translations] == [
        "訳:first",
        "手修正優先",
    ]
    assert len(requests(tmp_path)) == sent + 1
    assert len(json.loads(requests(tmp_path)[-1]["text"])) == 1


def test_translation_v2_split_reuse_checks_whole_parent_not_just_matching_parts(
    tmp_path,
):
    workspace = prepare_blocks(tmp_path, [("body", "word " * 20)])
    first = translate(tmp_path, workspace, max_source_chars=30)
    original = load_translation(workspace, first)
    assert len(original.chunks) == 4
    source = load_source_snapshot(workspace)
    source.blocks[0].text = "word " * 19 + "edit "
    save_source_snapshot(workspace, source)
    sent = len(requests(tmp_path))
    translate(tmp_path, workspace, max_source_chars=30)
    current = load_translation(workspace)
    assert (
        current.chunks[0].units[0].source_text
        == original.chunks[0].units[0].source_text
    )
    assert len(requests(tmp_path)) == sent + 4
    assert set(current.runs) > set(original.runs)
    assert load_translation(workspace, first) == original


def test_translation_v2_projection_keeps_nonheading_context_verbatim(tmp_path):
    from doc_translate.translation import _request

    workspace = prepare_blocks(tmp_path, [("body", "queue")])
    unit = generate_translation_units(load_source_snapshot(workspace))[0]
    context = {
        "heading_path": [
            {
                "block_id": "position",
                "node_path": "node",
                "level": 2,
                "text": "Heading",
                "other": "keep",
            }
        ],
        "list": {"parent_id": "position", "level": 1},
        "table": {"cell_id": "cell", "column": 1},
        "caption": {"block_id": "figure"},
        "arbitrary": {"node_path": "keep"},
    }
    unit = unit.model_copy(update={"context": context})
    projected = json.loads(_request([unit], load_preparation(workspace)).text)[0]
    expected = {
        **context,
        "heading_path": [{"level": 2, "text": "Heading", "other": "keep"}],
    }
    assert projected["context"] == expected
    assert unit.context == context


def test_translation_v2_requires_aliases_in_actual_raw_answer(tmp_path, prepared):
    response = {
        "wire_answer": True,
        "answer": {
            "translations": [{"unit_id": "unit-1", "text": "訳文", "uncertainties": []}]
        },
    }
    digest = translate(tmp_path, prepared, response)
    assert load_translation(prepared, digest).translations == []
    assert len(requests(tmp_path)) == 1
