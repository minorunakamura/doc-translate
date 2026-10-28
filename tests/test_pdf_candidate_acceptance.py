"""PDF candidate matrices through CLI, actual faux requests and filesystem commits."""

import json
from html import unescape

import pytest
from test_resume import assert_preserved, requests, saved_files
from test_revise import pointer
from test_run import helper, revise_pdf_paragraph, run_args

from doc_translate.cli import main
from doc_translate.snapshot import load_source_snapshot

pytest_plugins = ["test_run"]


def block(block_id, text, page=2, kind="body", **fields):
    return {
        "block_id": block_id,
        "kind": kind,
        "text": text,
        "evidence": [
            {"page": page, "ref": None, "position": "center", "reason": "Visible."}
        ],
        **fields,
    }


def start_pdf(tmp_path, pdf_input, local_models, capsys, **settings):
    workspace = tmp_path / "new"
    main(
        run_args(pdf_input, workspace)
        + [
            "--pages",
            "2-3",
            "--reference-pages",
            "4-4",
            "--artifacts-path",
            str(local_models),
            "--correction-model",
            "vision",
        ],
        process_command=helper(tmp_path, **settings),
    )
    capsys.readouterr()
    return workspace


def pending_resume(tmp_path, workspace, capsys, blocks, *options):
    with pytest.raises(SystemExit) as waiting:
        main(
            ["resume", str(workspace), *options],
            process_command=helper(tmp_path, correction_blocks=blocks),
        )
    assert waiting.value.code == 1
    return json.loads(capsys.readouterr().out)["source_adoption"]


@pytest.mark.parametrize(
    "option,value,provider,model,reasoning",
    [
        ("--correction-provider", "faux-alt", "faux-alt", "vision", None),
        ("--correction-model", "vision-alt", "faux", "vision-alt", None),
        ("--correction-thinking", "minimal", "faux", "vision", "minimal"),
    ],
)
def test_explicit_correction_settings_reach_actual_request_without_reacquisition_or_scope_expansion(
    tmp_path,
    pdf_input,
    local_models,
    capsys,
    monkeypatch,
    option,
    value,
    provider,
    model,
    reasoning,
):
    from docling.document_converter import DocumentConverter

    workspace = start_pdf(tmp_path, pdf_input, local_models, capsys)
    head = revise_pdf_paragraph(tmp_path, workspace, capsys)
    before = saved_files(workspace)
    count = len(requests(tmp_path))
    monkeypatch.setattr(
        DocumentConverter, "convert", lambda *a, **kw: pytest.fail("reacquired PDF")
    )
    command = helper(
        tmp_path,
        provider=provider,
        correction_blocks=[
            block("new", "New correction.", 2),
            block("tail", "Tail.", 3),
        ],
    )
    with pytest.raises(SystemExit) as waiting:
        main(["resume", str(workspace), option, value], process_command=command)
    assert waiting.value.code == 1
    summary = json.loads(capsys.readouterr().out)
    raw = [
        json.loads(line)
        for line in (tmp_path / "requests.jsonl").read_text().splitlines()
    ][count:]
    assert len(raw) == 1  # no old successful chunk reuse, preparation or translation
    assert raw[0]["selection"] == {"provider": provider, "model": model}
    assert raw[0]["options"].get("reasoning") == reasoning
    sent = requests(tmp_path)[count:]
    assert sent[0]["context"]["owned_pages"] == [2, 3]
    assert sent[0]["context"]["reference_pages"] == [4]
    assert "ONLY_PAGE_1_TEXT" not in sent[0]["text"]
    assert "ONLY_PAGE_5_TEXT" not in sent[0]["text"]
    assert (
        sum(part["type"] == "image" for part in raw[0]["messages"][1]["content"]) == 3
    )
    assert pointer(workspace, "source") == head
    assert summary["source_adoption"]["status"] == "pending"
    with pytest.raises(SystemExit):
        main(["resume", str(workspace)], process_command=command)
    capsys.readouterr()
    assert len(requests(tmp_path)) == count + 1
    assert_preserved(before)


def downstream_pointers(workspace):
    return {
        name: (workspace / name).read_bytes()
        for name in (
            "source-snapshot.json",
            "preparation-snapshot.json",
            "translation-snapshot.json",
            "render.json",
        )
    }


def adopt_candidate(workspace, candidate, capsys):
    main(
        [
            "revise",
            str(workspace),
            "--source",
            str(workspace / candidate["envelope"]),
            "--reason",
            "Compared whole candidate with original",
        ]
    )
    capsys.readouterr()


def joined_prose():
    first = block("first", "A sentence", 2)
    second = block("second", "continues.", 3)
    first["outgoing"] = {"target": second["evidence"][0], "mode": "space"}
    second["incoming"] = {"target": first["evidence"][0], "mode": "space"}
    return [first, second]


@pytest.mark.parametrize(
    "old,new,expected",
    [
        pytest.param(
            [block("first", "A sentence", 2), block("second", "continues.", 3)],
            joined_prose(),
            ["A sentence continues."],
            id="page-boundary-merge",
        ),
        pytest.param(
            joined_prose(),
            [block("first", "A sentence", 2), block("second", "continues.", 3)],
            ["A sentence", "continues."],
            id="page-boundary-split",
        ),
        pytest.param(
            [
                block("first", "Deleted.", 2),
                block("kept", "Kept.", 2),
                block("second", "Second.", 3),
                block("third", "Third.", 3),
            ],
            [
                block("kept", "Kept.", 2),
                block("third", "Third.", 3),
                block("second", "Second.", 3),
            ],
            ["Kept.", "Third.", "Second."],
            id="delete-and-reorder",
        ),
    ],
)
def test_structural_candidate_never_transfers_human_edits_by_reused_ids(
    tmp_path,
    pdf_input,
    local_models,
    capsys,
    old,
    new,
    expected,
):
    workspace = start_pdf(
        tmp_path, pdf_input, local_models, capsys, correction_blocks=old
    )
    head = revise_pdf_paragraph(tmp_path, workspace, capsys)
    before = saved_files(workspace)
    pointers = downstream_pointers(workspace)
    count = len(requests(tmp_path))
    candidate = pending_resume(
        tmp_path, workspace, capsys, new, "--correction-model", "vision-alt"
    )
    assert downstream_pointers(workspace) == pointers
    assert load_source_snapshot(workspace).blocks[0].text == "Human correction."
    assert [item["context"]["owned_pages"] for item in requests(tmp_path)[count:]] == [
        [2, 3]
    ]
    assert "IDs are not content correspondence" in candidate["page_changes"]["note"]
    assert [
        b.text for b in load_source_snapshot(workspace, candidate["C"]["sha256"]).blocks
    ] == expected
    adopt_candidate(workspace, candidate, capsys)
    adopted = load_source_snapshot(workspace)
    assert adopted.previous_snapshot_sha256 == head
    assert [b.text for b in adopted.blocks] == expected
    assert (
        adopted.blocks[0].block_id == "pdf-1"
    )  # same ID, deliberately different content
    main(["resume", str(workspace)], process_command=helper(tmp_path))
    result = json.loads(capsys.readouterr().out)
    english = unescape((workspace / result["artifacts"]["english"]).read_text())
    assert "Human correction." not in english
    assert all(text in english for text in expected)
    assert_preserved(before)


def test_front_page_recovery_shifts_block_and_unit_ids_without_transferring_manual_edits(
    tmp_path,
    pdf_input,
    local_models,
    capsys,
):
    from doc_translate.translation import load_translation

    workspace = tmp_path / "new"
    with pytest.raises(SystemExit) as partial:
        main(
            run_args(pdf_input, workspace)
            + [
                "--pages",
                "2-7",
                "--artifacts-path",
                str(local_models),
                "--correction-model",
                "vision",
            ],
            process_command=helper(tmp_path, correction_fail_page=2),
        )
    assert partial.value.code == 1
    capsys.readouterr()
    original = load_source_snapshot(workspace)
    assert [(b.block_id, b.text) for b in original.blocks] == [
        ("pdf-1", "Page 7 source.")
    ]
    head = revise_pdf_paragraph(tmp_path, workspace, capsys)
    count = len(requests(tmp_path))
    before = saved_files(workspace)
    pointers = downstream_pointers(workspace)
    with pytest.raises(SystemExit) as waiting:
        main(["resume", str(workspace)], process_command=helper(tmp_path))
    assert waiting.value.code == 1
    candidate = json.loads(capsys.readouterr().out)["source_adoption"]
    assert downstream_pointers(workspace) == pointers
    assert [item["context"]["owned_pages"] for item in requests(tmp_path)[count:]] == [
        [2, 3, 4, 5, 6]
    ]
    adopt_candidate(workspace, candidate, capsys)
    adopted = load_source_snapshot(workspace)
    assert adopted.previous_snapshot_sha256 == head
    assert [(b.block_id, b.text) for b in adopted.blocks] == [
        ("pdf-1", "Page 2 source."),
        ("pdf-2", "Page 3 source."),
        ("pdf-3", "Page 4 source."),
        ("pdf-4", "Page 5 source."),
        ("pdf-5", "Page 6 source."),
        ("pdf-6", "Page 7 source."),
    ]
    main(["resume", str(workspace)], process_command=helper(tmp_path))
    result = json.loads(capsys.readouterr().out)
    assert [
        (unit.unit_id, unit.block_id, unit.locations[0].model_dump()["page"])
        for chunk in load_translation(workspace).chunks
        for unit in chunk.units
    ] == [
        ("unit-1", "pdf-1", 2),
        ("unit-2", "pdf-2", 3),
        ("unit-3", "pdf-3", 4),
        ("unit-4", "pdf-4", 5),
        ("unit-5", "pdf-5", 6),
        ("unit-6", "pdf-6", 7),
    ]
    assert (
        "Human correction"
        not in (workspace / result["artifacts"]["english"]).read_text()
    )
    assert_preserved(before)


def structured_blocks(changed):
    table = block(
        "table",
        "",
        3,
        "table",
        rows=1,
        columns=2,
        cells=[
            {
                "cell_id": name.lower(),
                "row": 0,
                "column": column,
                "row_span": 1,
                "column_span": 1,
                "text": name,
                "heading": False,
                "evidence": block("e", "", 3)["evidence"],
            }
            for column, name in enumerate(
                ["Value", "Name"] if changed else ["Name", "Value"]
            )
        ],
    )
    return [
        block("anchor", "Anchor."),
        block("parent-a", "Parent A", kind="list", depth=1, marker="-"),
        block("parent-b", "Parent B", kind="list", depth=1, marker="-"),
        block(
            "child",
            "Child",
            kind="list",
            depth=2,
            marker="-",
            parent_id="parent-b" if changed else "parent-a",
        ),
        table,
        block("caption-a", "Caption A", 3, "caption"),
        block("caption-b", "Caption B", 3, "caption"),
        block(
            "figure",
            "",
            3,
            "picture",
            region={"page": 3, "left": 1, "top": 1, "right": 100, "bottom": 100},
            caption_ids=["caption-b" if changed else "caption-a"],
            members=[
                {
                    "target_id": "value" if changed else "name",
                    "status": "confirmed",
                    "reason": "Inside figure",
                }
            ],
        ),
    ]


def test_candidate_adoption_uses_new_table_list_caption_and_figure_relationships(
    tmp_path,
    pdf_input,
    local_models,
    capsys,
):
    workspace = start_pdf(
        tmp_path,
        pdf_input,
        local_models,
        capsys,
        correction_blocks=structured_blocks(False),
    )
    revise_pdf_paragraph(tmp_path, workspace, capsys)
    before = saved_files(workspace)
    pointers = downstream_pointers(workspace)
    count = len(requests(tmp_path))
    candidate = pending_resume(
        tmp_path,
        workspace,
        capsys,
        structured_blocks(True),
        "--correction-model",
        "vision-alt",
    )
    assert downstream_pointers(workspace) == pointers
    assert [item["context"]["owned_pages"] for item in requests(tmp_path)[count:]] == [
        [2, 3]
    ]
    adopt_candidate(workspace, candidate, capsys)
    source = load_source_snapshot(workspace)
    assert source.blocks[0].text == "Anchor."
    child = next(b for b in source.blocks if b.text == "Child")
    assert child.structure["parent_id"] == next(
        b.block_id for b in source.blocks if b.text == "Parent B"
    )
    table = next(
        b.model_dump()["structure"] for b in source.blocks if b.kind == "table"
    )
    assert [(c["column"], c["text"]) for c in table["cells"]] == [(1, "Name")]
    assert [(c["column"], c["text"]) for c in table["retained_cells"]] == [(0, "Value")]
    picture = next(
        b.model_dump()["structure"] for b in source.blocks if b.kind == "picture"
    )
    assert picture["caption_ids"] == [
        next(b.block_id for b in source.blocks if b.text == "Caption B")
    ]
    assert picture["members"][0]["target_id"] == table["retained_cells"][0]["cell_id"]
    head = pointer(workspace, "source")
    invalid = source.model_dump(mode="json")
    invalid_table = next(b for b in invalid["blocks"] if b["kind"] == "table")
    invalid_table["structure"]["cells"].append(invalid_table["structure"]["cells"][0])
    replacement = tmp_path / "duplicate-cell.json"
    replacement.write_text(
        json.dumps({"schema_version": 1, "base_source_sha256": head, "source": invalid})
    )
    with pytest.raises(SystemExit) as duplicate:
        main(
            [
                "revise",
                str(workspace),
                "--source",
                str(replacement),
                "--reason",
                "Invalid duplicate",
            ]
        )
    assert duplicate.value.code == 2
    assert "Duplicate source cell ID" in capsys.readouterr().err
    assert pointer(workspace, "source") == head
    main(["resume", str(workspace)], process_command=helper(tmp_path))
    result = json.loads(capsys.readouterr().out)
    assert result["processing"]["status"] == "completed"
    assert (
        "Human correction"
        not in (workspace / result["artifacts"]["english"]).read_text()
    )
    assert_preserved(before)


@pytest.mark.parametrize("change", ["code-only", "body", "id", "context", "policy"])
def test_adopted_candidate_reuses_manual_translation_only_for_exact_actual_request(
    tmp_path,
    pdf_input,
    local_models,
    capsys,
    change,
):
    from doc_translate.translation import load_translation

    old_blocks = [
        block("heading", "Chapter", kind="heading", level=1),
        block("body", "Original paragraph."),
        block("code", "old_code()", 3, "code", language="python"),
    ]
    workspace = start_pdf(
        tmp_path, pdf_input, local_models, capsys, correction_blocks=old_blocks
    )
    original_request = next(
        item
        for item in requests(tmp_path)
        if not item["context"].get("phase") and "owned_pages" not in item["context"]
    )
    source = load_source_snapshot(workspace).model_dump(mode="json")
    source["blocks"][2]["text"] = "human_code()"
    replacement = tmp_path / "source.json"
    replacement.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "base_source_sha256": pointer(workspace, "source"),
                "source": source,
            }
        )
    )
    main(
        [
            "revise",
            str(workspace),
            "--source",
            str(replacement),
            "--reason",
            "Code checked",
        ]
    )
    capsys.readouterr()
    main(["resume", str(workspace)], process_command=helper(tmp_path))
    capsys.readouterr()
    item = load_translation(workspace).translations[1].model_dump(mode="json")
    item["text"] = "手動確認済み本文"
    patch = tmp_path / "translation.json"
    patch.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "base_source_sha256": pointer(workspace, "source"),
                "base_translation_sha256": pointer(workspace, "translation"),
                "translations": [item],
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
            "Manual translation checked",
        ]
    )
    capsys.readouterr()
    manual = pointer(workspace, "translation")
    before = saved_files(workspace)
    pointers = downstream_pointers(workspace)
    new = [
        block(
            "heading",
            "Changed chapter" if change == "context" else "Chapter",
            kind="heading",
            level=1,
        ),
        block(
            "body", "Changed paragraph." if change == "body" else "Original paragraph."
        ),
        block("code", "new_code()", 3, "code", language="python"),
    ]
    if change == "id":
        new.insert(1, block("inserted", "New preceding paragraph."))
    count = len(requests(tmp_path))
    candidate = pending_resume(
        tmp_path, workspace, capsys, new, "--correction-model", "vision-alt"
    )
    assert downstream_pointers(workspace) == pointers
    assert [r["context"]["owned_pages"] for r in requests(tmp_path)[count:]] == [[2, 3]]
    adopt_candidate(workspace, candidate, capsys)
    main(["status", str(workspace)])
    assert json.loads(capsys.readouterr().out)["stale_translation"] is True
    with pytest.raises(SystemExit) as stale:
        main(["render", str(workspace)])
    assert stale.value.code == 1
    assert json.loads(capsys.readouterr().out)["render_current"] is True
    resume = ["resume", str(workspace)]
    if change == "policy":
        policy = tmp_path / "policy.json"
        policy.write_text(json.dumps({"version": "2", "text": "本文は常体。"}))
        resume += ["--policy", str(policy)]
    count = len(requests(tmp_path))
    main(resume, process_command=helper(tmp_path))
    result = json.loads(capsys.readouterr().out)
    sent = requests(tmp_path)[count:]
    translations = [
        r
        for r in sent
        if not r["context"].get("phase") and "owned_pages" not in r["context"]
    ]
    assert all("owned_pages" not in r["context"] for r in sent)
    assert len(translations) == (0 if change == "code-only" else 1)
    if translations:
        assert translations[0] != original_request
        units = json.loads(translations[0]["text"])
        body = next(
            u
            for u in units
            if u["kind"] == "body" and u["source_text"] != "New preceding paragraph."
        )
        assert body["source_text"] == (
            "Changed paragraph." if change == "body" else "Original paragraph."
        )
        assert body["unit_id"] == ("unit-3" if change == "id" else "unit-2")
        if change == "context":
            assert body["context"]["heading_path"][0]["text"] == "Changed chapter"
        if change == "policy":
            assert translations[0]["context"]["style_policy"] == {
                "version": "2",
                "text": "本文は常体。",
            }
    assert result["processing"]["status"] == "completed"
    assert result["stale_translation"] is False
    assert result["render_current"] is True
    japanese = unescape((workspace / result["artifacts"]["japanese"]).read_text())
    assert ("手動確認済み本文" in japanese) == (change == "code-only")
    assert "new_code()" in japanese and "human_code()" not in japanese
    assert (
        load_translation(workspace, manual).translations[1].text == "手動確認済み本文"
    )
    assert result["artifacts"]["qa"] not in json.loads(pointers["render.json"])["files"]
    qa = json.loads((workspace / result["artifacts"]["qa"]).read_bytes())
    assert (
        next(c for c in qa["checks"] if c["name"] == "render_unit_coverage")["passed"]
        is True
    )
    assert_preserved(before)


@pytest.mark.parametrize("termination", ["SIGINT", "SIGKILL"])
@pytest.mark.parametrize("edge", ["before", "after"])
@pytest.mark.parametrize(
    "boundary",
    ["corrections/pdf/*.json", "candidates/source/*.json", "source-candidate.json"],
)
def test_interrupted_candidate_publication_preserves_head_and_republishes_without_correction_resend(
    tmp_path,
    pdf_input,
    local_models,
    capsys,
    termination,
    edge,
    boundary,
):
    import signal

    from test_resume import await_boundary, external_cli

    workspace = start_pdf(tmp_path, pdf_input, local_models, capsys)
    revise_pdf_paragraph(tmp_path, workspace, capsys)
    pointers = downstream_pointers(workspace)
    before = saved_files(workspace)
    count = len(requests(tmp_path))
    command = helper(
        tmp_path,
        correction_blocks=[block("first", "New paragraph."), block("last", "Tail.", 3)],
    )
    process = external_cli(
        tmp_path,
        command,
        ["resume", str(workspace), "--correction-model", "vision-alt"],
        boundary=boundary,
        edge=edge,
    )
    try:
        await_boundary(tmp_path, process)
        assert downstream_pointers(workspace) == pointers
        for operation in ("resume", "render"):
            with pytest.raises(SystemExit) as locked:
                main([operation, str(workspace)], process_command=command)
            assert locked.value.code == 1
        capsys.readouterr()
        process.send_signal(getattr(signal, termination))
        (tmp_path / "boundary.release").touch()
        code = process.wait(timeout=15)
        assert code == (1 if termination == "SIGINT" else -signal.SIGKILL)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
    assert downstream_pointers(workspace) == pointers
    assert len(requests(tmp_path)) == count + 1
    assert_preserved(before)
    resumed = external_cli(tmp_path, command, ["resume", str(workspace)])
    assert resumed.wait(timeout=15) == 1, (tmp_path / "process.err").read_text()
    result = json.loads((tmp_path / "process.out").read_text())
    candidate = result["source_adoption"]
    assert candidate["status"] == "pending"
    assert downstream_pointers(workspace) == pointers
    assert len(requests(tmp_path)) == count + 1
    # Re-publication must retain a usable, verified envelope and not wait twice after adoption.
    adopt_candidate(workspace, candidate, capsys)
    main(["resume", str(workspace)], process_command=command)
    result = json.loads(capsys.readouterr().out)
    assert result["processing"]["status"] == "completed"
    assert "source_adoption" not in result
    assert all(
        "owned_pages" not in r["context"] for r in requests(tmp_path)[count + 1 :]
    )
    assert_preserved(before)


def test_legacy_joined_machine_baseline_is_proved_without_rewriting_its_bytes(
    tmp_path,
    pdf_input,
    local_models,
    capsys,
):
    import hashlib

    workspace = start_pdf(
        tmp_path, pdf_input, local_models, capsys, correction_blocks=joined_prose()
    )
    legacy = load_source_snapshot(workspace).model_dump(mode="json")
    legacy.pop("machine_inputs")
    # Before alias repair, ownership checks retained both original fragment IDs.
    legacy["checks"][0]["target_ids"] = ["pdf-1", "pdf-2"]
    raw = json.dumps(legacy, indent=3).encode()
    digest = hashlib.sha256(raw).hexdigest()
    artifact = workspace / f"snapshots/source/{digest}.json"
    artifact.write_bytes(raw)
    (workspace / "source-snapshot.json").write_text(
        json.dumps({"schema_version": 1, "sha256": digest})
    )
    revised = json.loads(raw)
    revised["blocks"][0]["text"] = "Human checked joined paragraph."
    revised["checks"][0]["target_ids"] = ["pdf-1"]
    replacement = tmp_path / "legacy-revision.json"
    replacement.write_text(
        json.dumps(
            {"schema_version": 1, "base_source_sha256": digest, "source": revised}
        )
    )
    forged = json.loads(raw)
    forged["checks"][0]["target_ids"] = ["pdf-1", "pdf-999"]
    forged_bytes = json.dumps(forged).encode()
    forged_digest = hashlib.sha256(forged_bytes).hexdigest()
    (workspace / f"snapshots/source/{forged_digest}.json").write_bytes(forged_bytes)
    (workspace / "source-snapshot.json").write_text(
        json.dumps({"schema_version": 1, "sha256": forged_digest})
    )
    replacement.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "base_source_sha256": forged_digest,
                "source": revised,
            }
        )
    )
    count = len(requests(tmp_path))
    with pytest.raises(SystemExit) as unknown:
        main(
            [
                "revise",
                str(workspace),
                "--source",
                str(replacement),
                "--reason",
                "Unknown QA target",
            ]
        )
    assert unknown.value.code == 2
    assert "differs from immutable acquisition inputs" in capsys.readouterr().err
    assert pointer(workspace, "source") == forged_digest
    assert len(requests(tmp_path)) == count
    (workspace / "source-snapshot.json").write_text(
        json.dumps({"schema_version": 1, "sha256": digest})
    )
    replacement.write_text(
        json.dumps(
            {"schema_version": 1, "base_source_sha256": digest, "source": revised}
        )
    )
    main(
        [
            "revise",
            str(workspace),
            "--source",
            str(replacement),
            "--reason",
            "Joined paragraph checked",
        ]
    )
    capsys.readouterr()
    count = len(requests(tmp_path))
    main(["resume", str(workspace)], process_command=helper(tmp_path))
    result = json.loads(capsys.readouterr().out)
    assert result["processing"]["status"] == "completed"
    assert "source_adoption" not in result
    assert load_source_snapshot(workspace).machine_base_sha256 == digest
    assert (
        load_source_snapshot(workspace).blocks[0].text
        == "Human checked joined paragraph."
    )
    assert all("owned_pages" not in r["context"] for r in requests(tmp_path)[count:])
    assert artifact.read_bytes() == raw


@pytest.mark.parametrize("field", ["cells", "retained_cells"])
@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize(
    "page,ref,accepted",
    [
        (2, "#/texts/unknown", False),
        (999, None, False),
        (4, "#/texts/3", False),
        (2, "#/texts/1", True),
        (2, None, True),
    ],
)
def test_source_revision_validates_table_cell_evidence_against_original_scope(
    tmp_path,
    pdf_input,
    local_models,
    capsys,
    monkeypatch,
    field,
    legacy,
    page,
    ref,
    accepted,
):
    import hashlib

    workspace = start_pdf(tmp_path, pdf_input, local_models, capsys)
    source = load_source_snapshot(workspace).model_dump(mode="json")
    if legacy:
        # Non-run snapshots predate machine_inputs; preserve their original bytes.
        source.pop("machine_inputs")
        raw = json.dumps(source, indent=3).encode()
        digest = hashlib.sha256(raw).hexdigest()
        (workspace / f"snapshots/source/{digest}.json").write_bytes(raw)
        (workspace / "source-snapshot.json").write_text(json.dumps({"sha256": digest}))
        (workspace / "run.json").unlink()
    source["blocks"][0].update(
        kind="table",
        text="",
        structure={
            "rows": 1,
            "columns": 1,
            "cells": [],
            field: [
                {
                    "cell_id": "human-cell",
                    "row": 0,
                    "column": 0,
                    "row_span": 1,
                    "column_span": 1,
                    "text": "Human corrected cell",
                    "heading": False,
                    "evidence": [
                        {
                            "page": page,
                            "ref": ref,
                            "position": "center",
                            "reason": "Visible",
                        }
                    ],
                }
            ],
        },
    )
    path = tmp_path / "source.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "base_source_sha256": pointer(workspace, "source"),
                "source": source,
            }
        )
    )
    before = {p: p.read_bytes() for p in workspace.rglob("*") if p.is_file()}
    monkeypatch.setenv("PATH", "")
    command = [
        "revise",
        str(workspace),
        "--source",
        str(path),
        "--reason",
        "Cell check",
    ]
    if accepted:
        main(command)
        assert (
            pointer(workspace, "source")
            != json.loads(before[workspace / "source-snapshot.json"])["sha256"]
        )
        assert (
            load_source_snapshot(workspace).model_dump(mode="json")["blocks"][0][
                "structure"
            ][field][0]["text"]
            == "Human corrected cell"
        )
        assert_preserved(
            {
                p: hashlib.sha256(value).hexdigest()
                for p, value in before.items()
                if p.parent.name == "source"
            }
        )
    else:
        with pytest.raises(SystemExit) as rejected:
            main(command)
        assert rejected.value.code == 2
        assert all(p.read_bytes() == value for p, value in before.items())


@pytest.mark.parametrize("depth", [1, 2])
def test_source_revision_validates_list_parent_depth_across_pages(
    tmp_path, pdf_input, local_models, capsys, monkeypatch, depth
):
    from copy import deepcopy

    workspace = start_pdf(tmp_path, pdf_input, local_models, capsys)
    source = load_source_snapshot(workspace).model_dump(mode="json")
    parent = source["blocks"][0]
    parent.update(kind="list", structure={"depth": 1, "marker": "-", "parent_id": None})
    child = deepcopy(parent)
    child.update(
        block_id="human-child",
        text="Child",
        structure={"depth": depth, "marker": "-", "parent_id": parent["block_id"]},
    )
    child["locations"] = source["blocks"][-1]["locations"]
    source["blocks"].insert(1, child)
    path = tmp_path / "source.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "base_source_sha256": pointer(workspace, "source"),
                "source": source,
            }
        )
    )
    before = {p: p.read_bytes() for p in workspace.rglob("*") if p.is_file()}
    monkeypatch.setenv("PATH", "")
    command = [
        "revise",
        str(workspace),
        "--source",
        str(path),
        "--reason",
        "List check",
    ]
    if depth == 1:
        with pytest.raises(SystemExit) as rejected:
            main(command)
        assert rejected.value.code == 2
        assert all(p.read_bytes() == value for p, value in before.items())
    else:
        main(command)
        adopted = load_source_snapshot(workspace)
        assert adopted.blocks[1].structure["parent_id"] == adopted.blocks[0].block_id
        assert adopted.blocks[1].locations[0].model_dump()["page"] == 3
