"""Issue 80 explicit contracts; artificial answers do not test semantic accuracy."""

import json
import os
import subprocess
import tempfile
from importlib import import_module
from pathlib import Path

import pytest
from pdf_source_feasibility import inventory, sha, write_json
from pdf_source_offline import CONFIG, ArtificialProcess, offline
from pdf_source_trial import (
    Inspection,
    SendBudget,
    copy_dependencies,
    decide,
    readback,
    trial,
    validate_inspection,
)

from doc_translate.inference import InferenceError
from doc_translate.pdf_correction import load_pdf_correction
from doc_translate.snapshot import SourceSnapshot, load_source_snapshot

pytest_plugins = ["test_pdf_source_feasibility"]


def inputs(samples, name):
    workspace = samples / "synthetic-inputs" / name
    source = SourceSnapshot.model_validate_json(
        (workspace / "current-source.json").read_bytes()
    )
    digests = list(
        dict.fromkeys(
            f["chunk_sha256"]
            for b in source.blocks
            for f in b.model_dump(mode="json")["structure"]["fragments"]
        )
    )
    pages = {
        p.page: p
        for d in digests
        for p in load_pdf_correction(workspace, d).page_images
    }
    return workspace, digests, pages


@pytest.fixture(scope="module")
def completed(samples, tmp_path_factory):
    output = tmp_path_factory.mktemp("trial") / "offline"
    offline(
        output,
        samples,
        sha((samples / "hashes.json").read_bytes()),
        subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        80,
    )
    return output


def test_pdf_source_trial_public_pipeline_and_independent_readback(completed):
    rows = json.loads((completed / "cases.json").read_bytes())
    assert [r["adopt"] for r in rows] == [
        False,
        True,
        False,
        True,
        False,
        True,
        False,
        True,
    ]
    assert [r["whole_sample_clean"] for r in rows] == [
        False,
        False,
        True,
        True,
        True,
        True,
        True,
        True,
    ]
    assert all(
        r["baseline_hash_mtime_unchanged"] and r["input_hash_mtime_unchanged"]
        for r in rows
    )
    sends = json.loads((completed / "sends.json").read_bytes())
    assert len(sends) == 18
    assert len({s["label"] for s in sends}) == 18
    for row in rows:
        case = completed / row["sample"]
        adopted = load_source_snapshot(case / "adopted")
        proof = json.loads((case / "adopted/readback.json").read_bytes())
        assert (
            proof["source_sha256"]
            == row["selected_sha256"]
            == sha(adopted.model_dump_json().encode())
        )
        assert proof["source_only_partial"] == bool(proof["units"])
        assert not (case / "candidate/source-snapshot.json").exists()
        assert not list(case.rglob("run.json"))
        if row["sample"] in {"sample-03", "sample-04"}:
            assert len(proof["assets"]) == 1 and proof["units"] == 0
        if row["sample"] == "sample-02":
            assert adopted.blocks[0].text == "Keep the blue valve closed."
            assert adopted.blocks[1].text == "Record the pressure before opening."
            assert adopted.blocks[-1].text == "Measurement:"
            assert row["remaining_gaps"] == 1


def reports(completed, name):
    before = load_source_snapshot(completed / name / "baseline")
    after = SourceSnapshot.model_validate_json(
        (completed / name / "candidate-source.json").read_bytes()
    )
    first = Inspection.model_validate_json(
        (completed / f"{name}-review-inspection.json").read_bytes()
    )
    second = Inspection.model_validate_json(
        (completed / f"{name}-reinspect-inspection.json").read_bytes()
    )
    pages = {
        p["page"]: p
        for b in before.blocks
        for p in b.model_dump(mode="json")["structure"]["page_images"]
    }
    from doc_translate.pdf import PDFPageImage

    return (
        before,
        after,
        first,
        second,
        {k: PDFPageImage.model_validate(v) for k, v in pages.items()},
    )


@pytest.mark.parametrize(
    "fault",
    [
        "body-quote",
        "code-quote",
        "cell-quote",
        "page-coverage",
        "block-coverage",
        "image-hash",
        "outside",
        "unreadable",
        "mapping",
        "normal-body",
        "normal-cell",
        "local-gap",
        "missing-order",
        "invent-value",
    ],
)
def test_pdf_source_trial_rejects_false_evidence_and_unrelated_changes(
    completed, fault
):
    before, after, first, second, pages = reports(completed, "sample-02")
    if fault.endswith("quote"):
        index = {"body-quote": 0, "code-quote": 3, "cell-quote": 4}[fault]
        target = list(second.blocks[index].quotes)[-1]
        second.blocks[index].quotes[target] = "fabricated quote"
    elif fault == "page-coverage":
        second.pages.pop()
    elif fault == "block-coverage":
        second.blocks.pop()
    elif fault == "image-hash":
        second.blocks[0].evidence[0].image_sha256 = "0" * 64
    elif fault == "outside":
        second.blocks[0].evidence[0].page = 99
    elif fault == "unreadable":
        second.pages[0].status = "unreadable"
    elif fault == "mapping":
        second.correspondence.pop()
    elif fault == "normal-body":
        after.blocks[0].text = "unrelated change"
        second.blocks[0].quotes[after.blocks[0].block_id] = after.blocks[0].text
    elif fault == "normal-cell":
        cell = after.blocks[4].structure["cells"][0]
        cell["text"] = "unrelated cell change"
        second.blocks[4].quotes[cell["cell_id"]] = cell["text"]
    elif fault == "local-gap":
        second.gaps.clear()
    elif fault == "missing-order":
        after.blocks[1], after.blocks[2] = after.blocks[2], after.blocks[1]
    else:
        after.blocks[-1].text = "Measurement: 999"
        second.blocks[-1].quotes[after.blocks[-1].block_id] = after.blocks[-1].text
    with pytest.raises(ValueError):
        decide(before, after, first, second, pages)


@pytest.mark.parametrize(
    "fault",
    ["crop", "components", "false-quote", "connection-side", "connection-explanation"],
)
def test_pdf_source_trial_figure_and_connection_evidence(completed, fault):
    name = (
        "sample-04" if fault in {"crop", "components", "false-quote"} else "sample-06"
    )
    before, after, first, second, pages = reports(completed, name)
    assert decide(before, after, first, second, pages)["adopt"]
    if fault == "crop":
        second.blocks[0].picture_region["right"] = 850
    elif fault == "components":
        second.blocks[0].components = ""
    elif fault == "false-quote":
        second.blocks[0].quotes[after.blocks[0].block_id] = "OUTLET"
    elif fault == "connection-side":
        second.blocks[0].evidence.pop()
    else:
        second.blocks[0].connection = ""
    with pytest.raises(ValueError):
        validate_inspection(second, after, pages)


@pytest.mark.parametrize(
    "fault",
    [
        "cap-retry",
        "coverage",
        "cancel",
        "auth",
        "save-before",
        "save-after",
        "reinspection",
    ],
)
def test_pdf_source_trial_stops_at_public_boundaries(
    samples, tmp_path, monkeypatch, fault
):
    workspace, digests, pages = inputs(samples, "sample-04")
    evidence = tmp_path / "evidence"
    evidence.mkdir(mode=0o700)
    budget = SendBudget(CONFIG, 1 if fault == "cap-retry" else 40, evidence)
    before = inventory(workspace)
    with tempfile.TemporaryDirectory(prefix="issue80-selfcheck-owned-") as temporary:
        process = ArtificialProcess(
            Path(temporary),
            "figure",
            True,
            pages,
            retry=fault == "cap-retry",
            coverage=fault == "coverage",
        )
        real_command = process
        if fault == "cancel":
            original_callback = budget.before_send

            def cancel(*args):
                original_callback(*args)
                budget.cancel.set()

            monkeypatch.setattr(budget, "before_send", cancel)
        if fault.startswith("save-"):
            replace = os.replace

            def fail(source, destination):
                if Path(destination) == evidence / "case/saving/source-snapshot.json":
                    if fault == "save-after":
                        replace(source, destination)
                    raise OSError("artificial storage failure")
                return replace(source, destination)

            monkeypatch.setattr(os, "replace", fail)
        if fault == "reinspection":

            def command(label, *args):
                argv = real_command(label, *args)
                if label.endswith("reinspect"):
                    answer = json.loads(Path(argv[3]).read_bytes())
                    answer["blocks"].clear()
                    write_json(Path(argv[3]), answer)
                return argv
        elif fault == "auth":
            controlled_helper = import_module("test_inference").controlled_helper

            def command(*_args):
                return controlled_helper(Path(temporary), "refresh-failure")
        else:
            command = real_command
        if fault == "coverage":
            assert trial(workspace, evidence / "case", digests, budget, command)[
                "adopt"
            ]
            assert len(budget.sends) == 4
            assert "correction_feedback" in budget.sends[2]["request"]["context"]
        else:
            with pytest.raises((ValueError, OSError, InferenceError)):
                trial(workspace, evidence / "case", digests, budget, command)
            result = json.loads((evidence / "case/result.json").read_bytes())
            assert not result["adopt"] and result["status"] == "stopped"
            assert result["baseline_hash_mtime_unchanged"] and budget.cancel.is_set()
            assert not (evidence / "case/adopted").exists()
            if fault == "save-after":
                assert (
                    result["failed_save_readback_sha256"] == result["selected_sha256"]
                )
            if fault == "save-before":
                assert result["failed_save_readback_sha256"] is None
            if fault in {"cap-retry", "cancel"}:
                assert len(budget.sends) == 1
            if fault == "auth":
                assert len(budget.sends) == 1  # Faux refresh fails after authorization.
                attempts = json.loads(
                    (evidence / "case-review-attempts.json").read_bytes()
                )
                assert attempts[-1]["code"] == "auth_failed"
    assert inventory(workspace) == before


def test_pdf_source_trial_dependency_copy_is_independent_and_rejects_damage(
    samples, tmp_path
):
    workspace, digests, _ = inputs(samples, "sample-04")
    copy = tmp_path / "copy"
    chunks = copy_dependencies(workspace, copy, digests)
    for path in copy.rglob("*"):
        if path.is_file():
            assert (
                path.stat().st_ino != (workspace / path.relative_to(copy)).stat().st_ino
            )
    image = copy / chunks[0].page_images[0].image.artifact
    image.write_bytes(b"damage")
    with pytest.raises(ValueError):
        copy_dependencies(copy, tmp_path / "rejected", digests)


def test_pdf_source_trial_unknown_history_cannot_pass(completed):
    before, _, first, _, pages = reports(completed, "sample-02")
    before.uncertainties[0].locations.clear()
    before.uncertainties[0].target_ids.clear()
    with pytest.raises(ValueError, match="Unmapped historical scope"):
        validate_inspection(first, before, pages)


@pytest.mark.parametrize("fault", ["config", "scope", "policy", "stage"])
def test_pdf_source_trial_send_guard_matches_complete_authorized_request(
    completed, tmp_path, fault
):
    from types import SimpleNamespace

    from doc_translate.inference import GenerationInput

    authorization = json.loads(
        (completed / "sample-04-review-authorization.json").read_bytes()
    )
    request = GenerationInput.model_validate(authorization["request"])
    budget = SendBudget(CONFIG, 1, tmp_path)
    budget.request = request.model_copy(deep=True)
    config = CONFIG
    if fault == "config":
        config = config.model_copy(update={"model": "other"})
    elif fault == "scope":
        assert isinstance(request.context, dict)
        pages = request.context["pages"]
        assert isinstance(pages, list)
        pages.append(999)
    elif fault == "policy":
        request.policy = "different"
    else:
        request.stage = "translation"
    with pytest.raises(ValueError):
        budget.before_send(
            config, request, SimpleNamespace(auth=SimpleNamespace(type="api_key"))
        )
    assert budget.cancel.is_set() and not budget.sends


def test_pdf_source_trial_selects_fixed_chunks_from_full_source_and_excludes_revision(
    samples, tmp_path
):
    from pdf_source_samples import response

    from doc_translate.pdf import load_pdf_draft
    from doc_translate.pdf_source import assemble_pdf_source
    from doc_translate.snapshot import save_source_snapshot

    run = import_module("test_pdf_correction").run
    original, digests, _ = inputs(samples, "sample-01")
    workspace = tmp_path / "full-source"
    chunk = copy_dependencies(original, workspace, digests)[0]
    draft = (
        workspace,
        chunk.draft_sha256,
        load_pdf_draft(workspace, chunk.draft_sha256),
    )
    selected = []
    with tempfile.TemporaryDirectory(prefix="issue80-subset-owned-") as temporary:
        owned = Path(temporary)
        for page in (1, 2):
            call = owned / f"split-{page}"
            call.mkdir()
            selected.append(
                run(
                    call,
                    draft,
                    response(chunk.draft_sha256, "history-layout", False, [page]),
                    owned_pages=[page],
                    reference_pages=[],
                )
            )
        full = assemble_pdf_source(
            workspace=workspace, chunk_sha256s=selected, snapshot_id="full"
        )
        digest = save_source_snapshot(workspace, full)
        before = inventory(workspace)
        pages = {
            p.page: p for p in load_pdf_correction(workspace, selected[0]).page_images
        }
        evidence = tmp_path / "subset"
        evidence.mkdir(mode=0o700)
        budget = SendBudget(CONFIG, 5, evidence)
        process = ArtificialProcess(owned, "figure", False, pages)
        result = trial(workspace, evidence / "normal", selected[:1], budget, process)
        assert result["status"] == "baseline-verified" and result["whole_sample_clean"]
        assert inventory(workspace) == before
        assert all(
            p.format == "pdf" and p.page == 1
            for b in load_source_snapshot(evidence / "normal/adopted").blocks
            for p in b.locations
        )
        revision = full.model_copy(
            update={
                "previous_snapshot_sha256": digest,
                "change_reason": "Artificial manual revision",
            }
        )
        save_source_snapshot(workspace, revision)
        with pytest.raises(ValueError, match="Manual revision excluded"):
            trial(workspace, evidence / "manual", selected[:1], budget, process)
        assert len(budget.sends) == 1


@pytest.mark.parametrize(
    "kind,text,rendered",
    [
        ("code", 'print("&amp;")', 'print("&amp;")'),
        ("code", r'pattern = r"\."', r'pattern = r"\."'),
        ("code", r'pattern = r"\*"', r'pattern = r"\*"'),
        ("body", r"A literal \. token", "A literal &#92;&#46; token"),
        ("formula", "x = &amp;", "x = &amp;"),
    ],
)
def test_pdf_source_trial_readback_preserves_raw_code_and_entity_escaped_text(
    tmp_path, kind, text, rendered
):
    save_source = import_module("test_render").save_source
    workspace = save_source(
        tmp_path,
        [{"block_id": "literal", "kind": kind, "text": text}],
    )
    source = load_source_snapshot(workspace)
    digest = sha(source.model_dump_json().encode())
    readback(workspace, digest)
    manifest = json.loads((workspace / "render.json").read_bytes())
    summary = json.loads((workspace / manifest["summary"]).read_bytes())
    english = (workspace / summary["artifacts"]["english"]).read_bytes()
    assert rendered in english.decode()
    proof = json.loads((workspace / "readback.json").read_bytes())
    assert proof["source_sha256"] == digest
    assert proof["english_sha256"] == sha(english)
    assert load_source_snapshot(workspace) == source
