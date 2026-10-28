"""Explicit CLI review scope through the real helper/public offline faux provider."""

import base64
import hashlib
import json

import pytest
from test_epub import make_epub
from test_run import helper, run_args

from doc_translate.cli import main
from doc_translate.pdf import PDFPageImage
from doc_translate.snapshot import load_source_snapshot

pytest_plugins = ["test_pdf", "test_pdf_correction", "test_run"]


def epub_workspace(tmp_path, capsys):
    source = make_epub(
        tmp_path,
        extra={
            "OPS/text/ch.xhtml": b'<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Own</title></head><body><h1>UNSELECTED HEADING</h1><p id="chosen">SELECTED TEXT</p><p>SECRET OTHER UNIT</p></body></html>'
        },
    )
    workspace = tmp_path / "new"
    main(run_args(source, workspace), process_command=helper(tmp_path))
    capsys.readouterr()
    return workspace


def requests(tmp_path):
    return [
        json.loads(line)
        for line in (tmp_path / "requests.jsonl").read_text().splitlines()
    ]


def test_review_units_saves_scoped_findings_without_changing_snapshots(
    tmp_path, capsys, monkeypatch
):
    workspace = epub_workspace(tmp_path, capsys)
    before = {
        name: (workspace / name).read_bytes()
        for name in (
            "source-snapshot.json",
            "translation-snapshot.json",
            "preparation-snapshot.json",
            "run.json",
        )
    }
    original_artifacts = {
        p: p.read_bytes() for p in workspace.glob("snapshots/*/*.json")
    }
    count = len(requests(tmp_path))
    monkeypatch.setenv("PI_PROVIDER", "must-not-use-ambient")
    main(
        ["review", str(workspace), "--units", "unit-2"],
        process_command=helper(
            tmp_path,
            review_findings=[
                {
                    "target_kind": "unit",
                    "target_id": "unit-2",
                    "reason": "Artificial meaning concern",
                }
            ],
        ),
    )
    result = json.loads(capsys.readouterr().out)
    assert result["review"]["status"] == "validated"
    assert len(requests(tmp_path)) == count + 1
    sent = requests(tmp_path)[-1]
    serialized = json.dumps(sent)
    assert "SELECTED TEXT" in serialized
    assert "SECRET OTHER UNIT" not in serialized
    assert "UNSELECTED HEADING" not in serialized
    assert '"image"' not in serialized
    assert sent["selection"] == {"provider": "faux", "model": "text"}
    assert {name: (workspace / name).read_bytes() for name in before} == before
    assert all(p.read_bytes() == data for p, data in original_artifacts.items())
    record = json.loads((workspace / result["review"]["artifact"]).read_bytes())
    assert (
        hashlib.sha256(
            (workspace / result["review"]["artifact"]).read_bytes()
        ).hexdigest()
        == result["review"]["sha256"]
    )
    assert record["reviewer"] == "faux/text"
    assert record["method"] == "independent_llm"
    assert record["unit_ids"] == ["unit-2"]
    assert record["findings"][0]["unit_ids"] == ["unit-2"]
    assert record["findings"][0]["locations"][0]["resource_path"] == "OPS/text/ch.xhtml"
    monkeypatch.setenv("PATH", "")
    main(["status", str(workspace)])
    status = json.loads(capsys.readouterr().out)
    assert status["render_current"] is False
    assert status["content_review"]["unreviewed_translation_unit_ids"] == [
        "unit-1",
        "unit-3",
    ]
    assert status["content_review"]["meaning_quality_approved"] is False
    assert status["usage"]["attempts_by_stage"]["review"] == 1
    main(["render", str(workspace)])
    rendered = json.loads(capsys.readouterr().out)
    qa = json.loads((workspace / rendered["artifacts"]["qa"]).read_bytes())
    assert qa["content_review"] == status["content_review"]
    assert all(check["passed"] for check in qa["checks"])
    concern = next(
        c for c in qa["uncertainties"] if c["reason"] == "Artificial meaning concern"
    )
    assert concern["stage"] == "review"
    assert concern["unit_ids"] == ["unit-2"]
    assert concern["output_anchors"]
    assert qa["content_review"]["unreviewed_source_unit_ids"] == ["unit-1", "unit-3"]
    main(["status", str(workspace)])
    assert json.loads(capsys.readouterr().out)["render_current"] is True


def test_review_pdf_pages_sends_only_selected_image_and_block_with_verified_positions(
    tmp_path, pdf_input, local_models, capsys
):
    workspace = tmp_path / "new"
    main(
        run_args(pdf_input, workspace)
        + [
            "--pages",
            "2-4",
            "--correction-model",
            "vision",
            "--artifacts-path",
            str(local_models),
        ],
        process_command=helper(tmp_path),
    )
    capsys.readouterr()
    main(
        ["review", str(workspace), "--pages", "3-3"],
        process_command=helper(
            tmp_path,
            review_findings=[
                {
                    "target_kind": "block",
                    "target_id": "pdf-2",
                    "reason": "Compare visible paragraph",
                },
                {"target_kind": "page", "target_id": "3", "reason": "Image concern"},
            ],
        ),
    )
    output = capsys.readouterr()
    result = json.loads(output.out)
    notice = json.loads(output.err)
    assert notice["transmission"]["pages"] == [3]
    assert notice["transmission"]["images"] == [3]
    assert notice["model"] == "vision"
    assert notice["auth"] == {"source": None, "type": "api_key"}
    sent = requests(tmp_path)[-1]
    content = sent["messages"][1]["content"]
    inputs = json.loads(content[0]["text"])
    assert inputs["context"]["pages"] == [3]
    assert "Page 3 source." in inputs["text"]
    assert "Page 2 source." not in inputs["text"]
    assert "Page 4 source." not in inputs["text"]
    assert "ONLY_PAGE_" not in json.dumps(sent)
    images = [part for part in content if part["type"] == "image"]
    assert len(images) == 1
    source = load_source_snapshot(workspace)
    pages = source.blocks[0].structure["page_images"]
    assert isinstance(pages, list)
    page = PDFPageImage.model_validate(pages[1])
    assert (
        base64.b64decode(images[0]["data"])
        == (workspace / page.image.artifact).read_bytes()
    )
    assert "page 3; origin top-left; pixels; scale_x=" in content[1]["text"]
    record = json.loads((workspace / result["review"]["artifact"]).read_bytes())
    assert record["pages"] == [3]
    assert record["block_ids"] == ["pdf-2"]
    assert record["unit_ids"] == ["unit-2"]
    assert record["findings"][0]["locations"][0]["page"] == 3
    assert record["findings"][1]["locations"][0]["page"] == 3
    assert record["findings"][1]["block_ids"] == ["pdf-2"]
    assert record["findings"][1]["unit_ids"] == ["unit-2"]
    attempt = json.loads(
        next((workspace / "attempts/review").glob("*.json")).read_bytes()
    )
    assert attempt["attempts"][0]["images"][0]["sha256"] == page.image.sha256


@pytest.mark.parametrize(
    "arguments,expected",
    [
        ([], "required"),
        (["--units", "unit-2", "--pages", "1-1"], "not allowed"),
        (["--units", "missing"], "Unknown review unit"),
        (["--units", ""], "explicit unit IDs"),
        (["--units", "unit-2,"], "explicit unit IDs"),
        (["--pages", "1-1"], "PDF-only"),
        (["--pages", "0-1"], "ascending positive"),
    ],
)
def test_review_invalid_scope_never_sends(tmp_path, capsys, arguments, expected):
    workspace = epub_workspace(tmp_path, capsys)
    count = len(requests(tmp_path))
    with pytest.raises(SystemExit) as stopped:
        main(["review", str(workspace), *arguments], process_command=helper(tmp_path))
    assert stopped.value.code == 2
    assert expected in capsys.readouterr().err
    assert len(requests(tmp_path)) == count
    assert not (workspace / "reviews").exists()


@pytest.mark.parametrize(
    "findings,fail_stage,error",
    [
        ([], None, None),
        (
            [{"target_kind": "unit", "target_id": "unit-3", "reason": "Unselected"}],
            None,
            "out-of-scope",
        ),
        (
            [{"target_kind": "block", "target_id": "unknown", "reason": "Invented"}],
            None,
            "out-of-scope",
        ),
        ([], "review", "generation_failed"),
    ],
)
def test_review_empty_and_rejected_answers_keep_usage_without_quality_approval(
    tmp_path, capsys, findings, fail_stage, error
):
    workspace = epub_workspace(tmp_path, capsys)
    args = ["review", str(workspace), "--units", "unit-2"]
    command = helper(tmp_path, review_findings=findings, fail_stage=fail_stage)
    if error:
        with pytest.raises(SystemExit) as stopped:
            main(args, process_command=command)
        assert stopped.value.code == 1
    else:
        main(args, process_command=command)
    result = json.loads(capsys.readouterr().out)
    assert result["content_review"]["meaning_quality_approved"] is False
    assert result["usage"]["attempts_by_stage"]["review"] == 1
    run = json.loads(
        (workspace / f"attempts/review/{result['review']['run']}.json").read_bytes()
    )
    final = run["attempts"][0]["final"]
    assert final["usage_status"] == "observed"
    assert final["usage"]["input"] > 0
    if error:
        assert error in result["review"]["error"]
        assert result["content_review"]["translation_records"] == []
        assert result["content_review"]["unreviewed_translation_unit_ids"] == [
            "unit-1",
            "unit-2",
            "unit-3",
        ]
        assert not (workspace / "reviews").exists()
    else:
        assert result["content_review"]["translation_records"][0]["findings"] == []
        assert result["content_review"]["unreviewed_translation_unit_ids"] == [
            "unit-1",
            "unit-3",
        ]


def test_review_after_translation_revision_keeps_history_but_does_not_count_old_hash(
    tmp_path, capsys
):
    from doc_translate.translation import load_translation

    workspace = epub_workspace(tmp_path, capsys)
    main(
        ["review", str(workspace), "--units", "unit-2"],
        process_command=helper(tmp_path),
    )
    old = json.loads(capsys.readouterr().out)
    review_file = workspace / old["review"]["artifact"]
    review_bytes = review_file.read_bytes()
    patch = tmp_path / "patch.json"
    item = load_translation(workspace).translations[1].model_dump()
    item["text"] = "Revised selected translation"
    patch.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "base_source_sha256": old["source_snapshot_sha256"],
                "base_translation_sha256": old["translation_snapshot_sha256"],
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
            "Artificial revision",
        ]
    )
    capsys.readouterr()
    main(["status", str(workspace)])
    status = json.loads(capsys.readouterr().out)
    assert status["content_review"]["translation_records"] == []
    assert (
        status["content_review"]["review_history"][0]["translation_snapshot_sha256"]
        == old["translation_snapshot_sha256"]
    )
    assert status["content_review"]["unreviewed_translation_unit_ids"] == [
        "unit-1",
        "unit-2",
        "unit-3",
    ]
    assert review_file.read_bytes() == review_bytes


def test_review_stale_or_missing_target_translation_never_sends(tmp_path, capsys):
    workspace = epub_workspace(tmp_path, capsys)
    count = len(requests(tmp_path))
    manifest = json.loads((workspace / "run.json").read_bytes())
    manifest["policy"]["text"] += " Explicit changed policy."
    (workspace / "run.json").write_text(json.dumps(manifest))
    with pytest.raises(SystemExit) as stopped:
        main(
            ["review", str(workspace), "--units", "unit-2"],
            process_command=helper(tmp_path),
        )
    assert stopped.value.code == 2
    assert (
        "matching the adopted source, preparation and settings"
        in capsys.readouterr().err
    )
    assert len(requests(tmp_path)) == count


def test_review_unit_selection_uses_explicit_override_without_changing_saved_settings(
    tmp_path, capsys
):
    workspace = epub_workspace(tmp_path, capsys)
    before = (workspace / "run.json").read_bytes()
    main(
        [
            "review",
            str(workspace),
            "--units",
            "unit-3,unit-2",
            "--model",
            "vision-alt",
            "--thinking",
            "low",
        ],
        process_command=helper(tmp_path),
    )
    result = json.loads(capsys.readouterr().out)
    assert requests(tmp_path)[-1]["selection"] == {
        "provider": "faux",
        "model": "vision-alt",
    }
    assert result["content_review"]["translation_records"][0]["unit_ids"] == [
        "unit-2",
        "unit-3",
    ]
    assert (workspace / "run.json").read_bytes() == before


def saved_pdf_workspace(tmp_path, draft, blocks, owned, reference):
    from test_pdf_source import accept, assemble

    from doc_translate.inference import GenerationConfig
    from doc_translate.preparation import StylePolicy, prepare_document
    from doc_translate.snapshot import save_source_snapshot
    from doc_translate.translation import translate_document

    digest = accept(tmp_path, draft, blocks, owned, reference)
    source = assemble(draft, [digest])
    workspace = draft[0]
    save_source_snapshot(workspace, source)
    config = GenerationConfig(provider="faux", model="text", thinking="off")
    command = helper(tmp_path)
    prepare_document(
        workspace=workspace,
        book="Own artificial PDF",
        policy=StylePolicy(version="1", text="Use polite Japanese."),
        config=config,
        process_command=command,
    )
    translate_document(workspace=workspace, config=config, process_command=command)
    return workspace


def test_review_nontranslation_pdf_blocks_and_ordered_images_need_no_other_pages(
    tmp_path, draft, capsys
):
    from test_pdf_source import fragment

    workspace = saved_pdf_workspace(
        tmp_path,
        draft,
        [
            fragment("body", "Outside text", 1),
            fragment("code", "print('visible')", 2, kind="code", language="python")
            | {
                "evidence": [
                    {
                        "page": 2,
                        "ref": None,
                        "position": "center",
                        "reason": "visible code",
                    }
                ]
            },
        ],
        [1, 2],
        [3],
    )
    main(
        [
            "review",
            str(workspace),
            "--pages",
            "2-2",
            "--provider",
            "faux",
            "--model",
            "vision",
            "--thinking",
            "off",
        ],
        process_command=helper(
            tmp_path,
            review_findings=[
                {
                    "target_kind": "block",
                    "target_id": "pdf-2",
                    "reason": "Visible code is incomplete",
                }
            ],
        ),
    )
    result = json.loads(capsys.readouterr().out)
    assert result["review"]["status"] == "validated"
    assert result["content_review"]["translation_records"][0]["unit_ids"] == []
    assert result["content_review"]["unreviewed_source_block_ids"] == ["pdf-1"]
    content = requests(tmp_path)[-1]["messages"][1]["content"]
    assert "print('visible')" in content[0]["text"]
    assert "Outside text" not in content[0]["text"]
    assert "SECRET PAGE" not in json.dumps(content)
    assert len([p for p in content if p["type"] == "image"]) == 1
    assert (
        base64.b64decode(content[2]["data"])
        == (workspace / draft[2].pages[1].image.artifact).read_bytes()
    )
    main(
        [
            "review",
            str(workspace),
            "--pages",
            "1-2",
            "--provider",
            "faux",
            "--model",
            "vision",
            "--thinking",
            "off",
        ],
        process_command=helper(tmp_path),
    )
    result = json.loads(capsys.readouterr().out)
    content = requests(tmp_path)[-1]["messages"][1]["content"]
    assert [base64.b64decode(p["data"]) for p in content if p["type"] == "image"] == [
        (workspace / p.image.artifact).read_bytes() for p in draft[2].pages[:2]
    ]
    assert result["content_review"]["meaning_quality_approved"] is False


def test_review_split_table_and_attribute_units_keep_exact_ids_and_locations(
    tmp_path, capsys
):
    from doc_translate.translation import load_translation

    source = make_epub(
        tmp_path,
        extra={
            "OPS/text/ch.xhtml": (
                '<html xmlns="http://www.w3.org/1999/xhtml"><body><p>'
                + "Selected prose. " * 1000
                + '</p><table><tr><td>CELL SECRET OTHER</td><td>CELL SELECTED</td></tr></table><p title="TITLE SELECTED">ATTRIBUTE OWNER SECRET</p></body></html>'
            ).encode()
        },
    )
    workspace = tmp_path / "new"
    main(run_args(source, workspace), process_command=helper(tmp_path))
    capsys.readouterr()
    units = [u for c in load_translation(workspace).chunks for u in c.units]
    split = next(u for u in units if u.parent_unit_id)
    cell = next(u for u in units if u.source_text == "CELL SELECTED")
    attribute = next(u for u in units if u.attribute == "title")
    ids = [split.unit_id, cell.unit_id, attribute.unit_id]
    main(
        ["review", str(workspace), "--units", ",".join(ids)],
        process_command=helper(
            tmp_path,
            review_findings=[
                {
                    "target_kind": "unit",
                    "target_id": uid,
                    "reason": "Artificial concern",
                }
                for uid in ids
            ],
        ),
    )
    result = json.loads(capsys.readouterr().out)
    sent = json.loads(requests(tmp_path)[-1]["messages"][1]["content"][0]["text"])
    projection = json.loads(sent["text"])["units"]
    assert [u["unit_id"] for u in projection] == ids
    assert projection[0]["parent_unit_id"] == "unit-1"
    assert projection[0]["source_range"]["start"] == 0
    assert projection[1]["context"]["table"]["column"] == 1
    assert projection[2]["attribute"] == "title"
    assert "CELL SECRET OTHER" not in json.dumps(sent)
    assert "ATTRIBUTE OWNER SECRET" not in json.dumps(sent)
    record = json.loads((workspace / result["review"]["artifact"]).read_bytes())
    assert [f["unit_ids"] for f in record["findings"]] == [[uid] for uid in ids]
    assert [f["locations"] for f in record["findings"]] == [
        [p.model_dump(mode="json") for p in u.locations]
        for u in (split, cell, attribute)
    ]


@pytest.mark.parametrize(
    "range_,expected",
    [("1-1", "crosses selected pages"), ("1-3", "outside saved scope")],
)
def test_review_cross_page_block_or_unavailable_page_never_sends(
    tmp_path, draft, capsys, range_, expected
):
    from test_pdf_source import continuation, fragment

    workspace = saved_pdf_workspace(
        tmp_path,
        draft,
        [
            fragment("start", "Outside text", 1, outgoing=continuation(2)),
            fragment("end", "REFERENCE ONLY", 2, incoming=continuation(1)),
        ],
        [1, 2],
        [],
    )
    count = len(requests(tmp_path))
    with pytest.raises(SystemExit) as stopped:
        main(
            [
                "review",
                str(workspace),
                "--pages",
                range_,
                "--provider",
                "faux",
                "--model",
                "vision",
                "--thinking",
                "off",
            ],
            process_command=helper(tmp_path),
        )
    assert stopped.value.code == 2
    assert expected in capsys.readouterr().err
    assert len(requests(tmp_path)) == count


def test_review_requires_target_translation_and_does_not_translate_it(tmp_path, capsys):
    workspace = tmp_path / "new"
    with pytest.raises(SystemExit):
        main(
            run_args(make_epub(tmp_path), workspace),
            process_command=helper(tmp_path, fail_stage="translation"),
        )
    capsys.readouterr()
    count = len(requests(tmp_path))
    with pytest.raises(SystemExit) as stopped:
        main(
            ["review", str(workspace), "--units", "unit-1"],
            process_command=helper(tmp_path),
        )
    assert stopped.value.code == 2
    assert "valid saved translations for: unit-1" in capsys.readouterr().err
    assert len(requests(tmp_path)) == count


def test_review_writer_lock_prevents_sending(tmp_path, capsys):
    from doc_translate.workspace import workspace_lock

    workspace = epub_workspace(tmp_path, capsys)
    count = len(requests(tmp_path))
    with workspace_lock(workspace), pytest.raises(SystemExit) as stopped:
        main(
            ["review", str(workspace), "--units", "unit-2"],
            process_command=helper(tmp_path),
        )
    assert stopped.value.code == 1
    assert "active writer" in capsys.readouterr().err
    assert len(requests(tmp_path)) == count


def test_review_atomic_save_failure_keeps_old_review_snapshots_and_observed_usage(
    tmp_path, capsys, monkeypatch
):
    import os
    from pathlib import Path

    workspace = epub_workspace(tmp_path, capsys)
    main(
        ["review", str(workspace), "--units", "unit-2"],
        process_command=helper(tmp_path),
    )
    result = json.loads(capsys.readouterr().out)
    old = workspace / result["review"]["artifact"]
    old_bytes = old.read_bytes()
    pointers = {p: p.read_bytes() for p in workspace.glob("*-snapshot.json")}
    replace = os.replace

    def failed_replace(source, destination):
        if Path(destination).parent == workspace / "reviews":
            raise OSError("artificial review artifact save failure")
        return replace(source, destination)

    monkeypatch.setattr(os, "replace", failed_replace)
    with pytest.raises(SystemExit) as stopped:
        main(
            ["review", str(workspace), "--units", "unit-3"],
            process_command=helper(tmp_path),
        )
    assert stopped.value.code == 1
    assert "artifact save failure" in capsys.readouterr().err
    assert old.read_bytes() == old_bytes
    assert all(p.read_bytes() == data for p, data in pointers.items())
    assert list((workspace / "reviews").iterdir()) == [old]
    main(["status", str(workspace)])
    status = json.loads(capsys.readouterr().out)
    assert status["usage"]["attempts_by_stage"]["review"] == 2
    assert status["content_review"]["unreviewed_translation_unit_ids"] == [
        "unit-1",
        "unit-3",
    ]


def test_review_saved_hash_mismatch_is_not_treated_as_current_review(tmp_path, capsys):
    workspace = epub_workspace(tmp_path, capsys)
    main(
        ["review", str(workspace), "--units", "unit-2"],
        process_command=helper(tmp_path),
    )
    result = json.loads(capsys.readouterr().out)
    record = workspace / result["review"]["artifact"]
    record.write_bytes(record.read_bytes() + b" ")
    with pytest.raises(SystemExit) as stopped:
        main(["status", str(workspace)])
    assert stopped.value.code == 2
    assert "SHA-256 mismatch" in capsys.readouterr().err
    count = len(requests(tmp_path))
    with pytest.raises(SystemExit) as stopped:
        main(
            ["review", str(workspace), "--units", "unit-3"],
            process_command=helper(tmp_path),
        )
    assert stopped.value.code == 2
    assert len(requests(tmp_path)) == count


def test_review_preflight_config_failure_preserves_unknown_usage_without_sending(
    tmp_path, capsys
):
    workspace = epub_workspace(tmp_path, capsys)
    count = len(requests(tmp_path))
    with pytest.raises(SystemExit) as stopped:
        main(
            ["review", str(workspace), "--units", "unit-2", "--model", "unknown"],
            process_command=helper(tmp_path),
        )
    assert stopped.value.code == 2
    result = json.loads(capsys.readouterr().out)
    assert result["review"]["error"] == "unknown_model"
    assert len(requests(tmp_path)) == count
    attempt = json.loads(
        next((workspace / "attempts/review").glob("*.json")).read_bytes()
    )
    assert attempt["attempts"][0]["final"] is None
    assert attempt["attempts"][0]["code"] == "unknown_model"
    assert result["usage"]["attempts_by_stage"]["review"] == 1
    assert result["content_review"]["translation_records"] == []


def test_review_selected_inline_literals_are_visible_but_other_units_and_attribute_tokens_are_not(
    tmp_path, capsys
):
    source = make_epub(
        tmp_path,
        extra={
            "OPS/text/ch.xhtml": b'<html xmlns="http://www.w3.org/1999/xhtml"><body><p>Use <code title="OTHER ATTRIBUTE SECRET">ChosenToken</code> with 42 items.</p><p>OtherToken</p></body></html>'
        },
    )
    workspace = tmp_path / "new"
    main(run_args(source, workspace), process_command=helper(tmp_path))
    capsys.readouterr()
    main(
        ["review", str(workspace), "--units", "unit-1"],
        process_command=helper(tmp_path),
    )
    capsys.readouterr()
    sent = json.dumps(requests(tmp_path)[-1])
    assert "ChosenToken" in sent
    assert "42" in sent
    assert "OtherToken" not in sent
    assert "OTHER ATTRIBUTE SECRET" not in sent


@pytest.mark.parametrize(
    "target_kind,second_cell_text",
    [
        ("unit", "SECRET OTHER PAGE CELL"),
        ("page", "SECRET OTHER PAGE CELL"),
        ("page", ""),
    ],
    ids=["unit", "page-body-cell", "page-empty-cell"],
)
def test_review_table_with_cells_outside_block_location_refuses_instead_of_leaking(
    tmp_path, draft, capsys, monkeypatch, target_kind, second_cell_text
):
    from test_pdf_correction import evidence
    from test_pdf_source import fragment

    workspace = saved_pdf_workspace(
        tmp_path,
        draft,
        [
            fragment(
                "table",
                "",
                1,
                kind="table",
                rows=1,
                columns=2,
                cells=[
                    {
                        "cell_id": "selected",
                        "row": 0,
                        "column": 0,
                        "row_span": 1,
                        "column_span": 1,
                        "text": "Outside text",
                        "heading": False,
                        "evidence": [evidence(1, None)],
                    },
                    {
                        "cell_id": "other",
                        "row": 0,
                        "column": 1,
                        "row_span": 1,
                        "column_span": 1,
                        "text": second_cell_text,
                        "heading": False,
                        "evidence": [evidence(2, None)],
                    },
                ],
            )
        ],
        [1, 2],
        [],
    )
    count = len(requests(tmp_path))
    with pytest.raises(SystemExit) as stopped:
        main(
            [
                "review",
                str(workspace),
                "--pages",
                "1-1",
                "--provider",
                "faux",
                "--model",
                "vision",
                "--thinking",
                "off",
            ],
            process_command=helper(tmp_path),
        )
    assert stopped.value.code == 2
    assert "crosses selected pages" in capsys.readouterr().err
    assert len(requests(tmp_path)) == count
    before = {
        p: p.read_bytes()
        for p in [
            *workspace.glob("*-snapshot.json"),
            *workspace.glob("snapshots/*/*.json"),
        ]
    }
    scope = (
        ["--units", "unit-2"]
        if target_kind == "unit"
        else [
            "--pages",
            "1-2",
            "--provider",
            "faux",
            "--model",
            "vision",
            "--thinking",
            "off",
        ]
    )
    main(
        ["review", str(workspace), *scope],
        process_command=helper(
            tmp_path,
            review_findings=[
                {
                    "target_kind": target_kind,
                    "target_id": "unit-2" if target_kind == "unit" else "2",
                    "reason": "Check second cell",
                }
            ],
        ),
    )
    result = json.loads(capsys.readouterr().out)
    record = json.loads((workspace / result["review"]["artifact"]).read_bytes())
    finding = record["findings"][0]
    expected_units = ["unit-2"] if second_cell_text else []
    assert finding["block_ids"] == ["pdf-1"]
    assert finding["unit_ids"] == expected_units
    assert finding["locations"][0]["page"] == 2
    if target_kind == "unit":
        assert "Outside text" not in json.dumps(requests(tmp_path)[-1])
    monkeypatch.setenv("PATH", "")
    main(["render", str(workspace)])
    rendered = json.loads(capsys.readouterr().out)
    qa = json.loads((workspace / rendered["artifacts"]["qa"]).read_bytes())
    concern = next(
        item for item in qa["uncertainties"] if item["reason"] == "Check second cell"
    )
    assert concern["stage"] == "review"
    assert concern["origin"] == "undetermined"
    assert concern["target_ids"] == (expected_units or ["pdf-1"])
    assert concern["block_ids"] == ["pdf-1"]
    assert concern["unit_ids"] == expected_units
    assert concern["locations"] == finding["locations"]
    assert concern["output_anchors"] == ["block-1"]
    for language, prefix in (("japanese", "訳:"), ("english", "")):
        document = (workspace / rendered["artifacts"][language]).read_text()
        assert 'id="block-1"' in document
        assert f"<td>{prefix}Outside text</td>" in document
        if second_cell_text:
            assert f"{prefix}SECRET OTHER PAGE CELL※" in document
        else:
            assert "<td></td>" in document
            assert "</table>\n\n※" in document
    main(["status", str(workspace)])
    status = json.loads(capsys.readouterr().out)
    assert status["render_current"] is True
    assert status["content_review"] == qa["content_review"]
    assert status["content_review"]["meaning_quality_approved"] is False
    assert status["content_review"]["viewer_display_checked"] is False
    assert all(p.read_bytes() == data for p, data in before.items())


def test_review_pending_candidate_reviews_adopted_hash_without_adopting_candidate(
    tmp_path, pdf_input, local_models, capsys
):
    from test_pdf_candidate_acceptance import block, pending_resume, start_pdf
    from test_run import revise_pdf_paragraph

    workspace = start_pdf(tmp_path, pdf_input, local_models, capsys)
    adopted = revise_pdf_paragraph(tmp_path, workspace, capsys)
    main(["resume", str(workspace)], process_command=helper(tmp_path))
    capsys.readouterr()
    pending_resume(
        tmp_path,
        workspace,
        capsys,
        [
            block("new", "Candidate-only secret."),
            block("third", "Page three candidate.", page=3),
        ],
        "--correction-model",
        "vision-alt",
    )
    before = {p: p.read_bytes() for p in workspace.glob("*-snapshot.json")}
    main(
        ["review", str(workspace), "--units", "unit-1"],
        process_command=helper(tmp_path),
    )
    result = json.loads(capsys.readouterr().out)
    assert result["source_adoption"]["status"] == "pending"
    assert result["review"]["status"] == "validated"
    assert (
        result["content_review"]["translation_records"][0]["source_snapshot_sha256"]
        == adopted
    )
    assert "Candidate-only secret" not in json.dumps(requests(tmp_path)[-1])
    assert all(p.read_bytes() == data for p, data in before.items())


@pytest.mark.parametrize(
    "machine_inputs", [False, True], ids=["legacy", "machine-inputs"]
)
@pytest.mark.parametrize("with_other_block", [False, True], ids=["empty", "mixed"])
def test_review_exclusion_only_chunk_uses_adopted_images_and_revalidates_offline(
    tmp_path, draft, capsys, monkeypatch, machine_inputs, with_other_block
):
    from test_pdf_correction import CONFIG, answer, evidence, run
    from test_pdf_source import accept, assemble, fragment

    from doc_translate.inference import GenerationConfig
    from doc_translate.pdf_correction import load_pdf_correction
    from doc_translate.preparation import StylePolicy, prepare_document
    from doc_translate.snapshot import PDFSourceInputs, save_source_snapshot
    from doc_translate.translation import translate_document

    response = answer(draft[1])
    response["blocks"] = []
    response["exclusions"] = [
        {"text": "", "reason": "page visibly blank", "evidence": [evidence(ref=None)]}
    ]
    digest = run(tmp_path, draft, response)
    digests = [digest]
    if with_other_block:
        digests.append(
            accept(tmp_path, draft, [fragment("other", "REFERENCE ONLY", 2)], [2], [])
        )
    source = assemble(draft, digests)
    # A valid but unadopted chunk must not extend review scope.
    accept(tmp_path, draft, [fragment("unadopted", "SECRET PAGE", 3)], [3], [])
    workspace = draft[0]
    if machine_inputs:
        chunk = load_pdf_correction(workspace, digest)
        correction = json.loads((workspace / chunk.run.artifact).read_bytes())
        owned = [1, 2] if with_other_block else [1]
        source.machine_inputs = PDFSourceInputs(
            draft_sha256=draft[1],
            chunk_sha256s=digests,
            correction=CONFIG,
            correction_policy=correction["request"]["policy"],
            owned_pages=owned,
            reference_pages=[] if with_other_block else [2],
            corrected_pages=owned,
            failed_pages=[],
            not_processed_pages=[],
        )
    save_source_snapshot(workspace, source)
    config = GenerationConfig(provider="faux", model="text", thinking="off")
    prepare_document(
        workspace=workspace,
        book="Own artificial PDF",
        policy=StylePolicy(version="1", text="Use polite Japanese."),
        config=config,
        process_command=helper(tmp_path),
    )
    translate_document(
        workspace=workspace, config=config, process_command=helper(tmp_path)
    )
    capsys.readouterr()
    before = {
        p: p.read_bytes()
        for pattern in (
            "*-snapshot.json",
            "snapshots/*/*.json",
            "corrections/pdf/*.json",
        )
        for p in workspace.glob(pattern)
    }
    main(
        [
            "review",
            str(workspace),
            "--pages",
            "1-1",
            "--provider",
            "faux",
            "--model",
            "vision",
            "--thinking",
            "off",
        ],
        process_command=helper(
            tmp_path,
            review_findings=[
                {"target_kind": "page", "target_id": "1", "reason": "Check blank page"}
            ],
        ),
    )
    output = capsys.readouterr()
    result = json.loads(output.out)
    assert result["review"]["status"] == "validated"
    notice = json.loads(output.err)
    assert notice["transmission"]["pages"] == [1]
    assert notice["transmission"]["images"] == [1]
    content = requests(tmp_path)[-1]["messages"][1]["content"]
    projection = json.loads(content[0]["text"])
    assert projection["context"]["pages"] == [1]
    assert json.loads(projection["text"]) == {"blocks": [], "units": []}
    assert [base64.b64decode(p["data"]) for p in content if p["type"] == "image"] == [
        (workspace / draft[2].pages[0].image.artifact).read_bytes()
    ]
    record = json.loads((workspace / result["review"]["artifact"]).read_bytes())
    assert record["pages"] == [1]
    assert record["block_ids"] == record["unit_ids"] == []
    finding = record["findings"][0]
    assert finding["target_id"] == "1"
    assert finding["locations"][0]["page"] == 1
    assert finding["block_ids"] == finding["unit_ids"] == []
    assert record["reviewer"] == "faux/vision"
    count = len(requests(tmp_path))
    with pytest.raises(SystemExit) as stopped:
        main(
            ["review", str(workspace), "--pages", "3-3"],
            process_command=helper(tmp_path),
        )
    assert stopped.value.code == 2
    assert "outside saved scope" in capsys.readouterr().err
    assert len(requests(tmp_path)) == count
    monkeypatch.setenv("PATH", "")
    main(["status", str(workspace)])
    status = json.loads(capsys.readouterr().out)
    assert status["usage"]["attempts_by_stage"]["review"] == 1
    assert status["content_review"]["translation_records"][0]["findings"] == [finding]
    main(["render", str(workspace)])
    rendered = json.loads(capsys.readouterr().out)
    qa = json.loads((workspace / rendered["artifacts"]["qa"]).read_bytes())
    assert qa["content_review"] == status["content_review"]
    assert qa["content_review"]["meaning_quality_approved"] is False
    concern = next(c for c in qa["uncertainties"] if c["reason"] == "Check blank page")
    assert concern["stage"] == "review"
    assert concern["locations"] == finding["locations"]
    assert all(p.read_bytes() == data for p, data in before.items())
    chunk_path = workspace / f"corrections/pdf/{digest}.json"
    chunk_path.write_bytes(chunk_path.read_bytes() + b" ")
    with pytest.raises(SystemExit) as stopped:
        main(["status", str(workspace)])
    assert stopped.value.code == 2
    assert "SHA-256 mismatch" in capsys.readouterr().err
