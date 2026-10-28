"""Offline revision adoption through the public CLI and saved snapshots."""

import hashlib
import json

import pytest
from test_epub import make_epub
from test_resume import assert_preserved, requests, saved_files
from test_run import helper, run_args

from doc_translate.cli import main
from doc_translate.translation import load_translation


def pointer(workspace, name):
    return json.loads((workspace / f"{name}-snapshot.json").read_bytes())["sha256"]


def test_translation_patch_keeps_history_and_survives_resume_without_node(
    tmp_path, capsys, monkeypatch
):
    workspace = tmp_path / "run"
    main(run_args(make_epub(tmp_path), workspace), process_command=helper(tmp_path))
    capsys.readouterr()
    before = saved_files(workspace)
    raw = {
        path: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (workspace / "attempts").rglob("*.json")
    }
    base = pointer(workspace, "translation")
    patch = tmp_path / "patch.json"
    patch.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "base_source_sha256": pointer(workspace, "source"),
                "base_translation_sha256": base,
                "translations": [
                    {
                        "unit_id": "unit-1",
                        "text": load_translation(workspace)
                        .translations[0]
                        .text.replace("Original", "手動修正"),
                        "uncertainties": [],
                    }
                ],
            }
        )
    )
    monkeypatch.setenv("PATH", "")
    main(
        ["revise", str(workspace), "--translations", str(patch), "--reason", "用語確認"]
    )
    capsys.readouterr()
    assert "手動修正" in load_translation(workspace).translations[0].text
    assert "手動修正" not in load_translation(workspace, base).translations[0].text
    assert "用語確認" in "".join(
        p.read_text() for p in (workspace / "translations").rglob("*.json")
    )
    main(["resume", str(workspace)])
    result = json.loads(capsys.readouterr().out)
    assert result["processing"]["status"] == "completed"
    assert "手動修正" in (workspace / result["artifacts"]["japanese"]).read_text()
    assert len(requests(tmp_path)) == 3
    assert_preserved(before | raw)


@pytest.mark.parametrize("manual_patch", [False, True])
def test_source_code_revision_survives_resume_without_resending_body(
    tmp_path, capsys, monkeypatch, manual_patch
):
    from doc_translate.snapshot import load_source_snapshot

    workspace = tmp_path / "run"
    source_file = make_epub(
        tmp_path,
        extra={
            "OPS/text/ch.xhtml": b'<html xmlns="http://www.w3.org/1999/xhtml"><body><p>Original text.</p><pre><code>old_code()</code></pre></body></html>'
        },
    )
    main(run_args(source_file, workspace), process_command=helper(tmp_path))
    capsys.readouterr()
    if manual_patch:
        item = load_translation(workspace).translations[0].model_dump(mode="json")
        item["text"] = item["text"].replace("Original", "手動修正")
        patch = tmp_path / "patch.json"
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
                "訳文確認",
            ]
        )
        capsys.readouterr()
    before = saved_files(workspace)
    base = pointer(workspace, "source")
    source = load_source_snapshot(workspace).model_dump(mode="json")
    nodes = source["blocks"][0]["structure"]["tree"]["nodes"]
    code = next(
        n for n in nodes.values() if n["tag"] == "code" or n["tag"].endswith("}code")
    )
    code["content"] = ["new_code()"]
    revision = tmp_path / "source.json"
    revision.write_text(
        json.dumps({"schema_version": 1, "base_source_sha256": base, "source": source})
    )
    command = helper(tmp_path)
    monkeypatch.setenv("PATH", "")
    main(
        ["revise", str(workspace), "--source", str(revision), "--reason", "コード照合"]
    )
    capsys.readouterr()
    adopted = load_source_snapshot(workspace)
    assert adopted.previous_snapshot_sha256 == base
    assert adopted.change_reason == "コード照合"
    with pytest.raises(SystemExit) as partial:
        main(["render", str(workspace)])
    assert partial.value.code == 1
    assert json.loads(capsys.readouterr().out)["stale_translation"] is True
    main(["resume", str(workspace)], process_command=command)
    result = json.loads(capsys.readouterr().out)
    assert result["processing"]["status"] == "completed"
    assert "new_code()" in (workspace / result["artifacts"]["japanese"]).read_text()
    assert len(requests(tmp_path)) == 3
    assert load_source_snapshot(workspace) == adopted
    if manual_patch:
        assert "手動修正" in (workspace / result["artifacts"]["japanese"]).read_text()
    assert_preserved(before)


@pytest.mark.parametrize(
    "damage", ["unknown_target", "inactive_schema", "tree", "envelope", "stale"]
)
def test_source_replacement_rejects_malformed_whole_snapshot_before_adoption(
    tmp_path, capsys, damage
):
    from doc_translate.snapshot import load_source_snapshot

    workspace = tmp_path / "run"
    main(run_args(make_epub(tmp_path), workspace), process_command=helper(tmp_path))
    capsys.readouterr()
    source = load_source_snapshot(workspace).model_dump(mode="json")
    data = {
        "schema_version": 1,
        "base_source_sha256": pointer(workspace, "source"),
        "source": source,
    }
    nodes = source["blocks"][0]["structure"]["tree"]["nodes"]
    node = next(iter(nodes.values()))
    if damage == "unknown_target":
        node["references"] = [
            {
                "attribute": "href",
                "uri": "#missing",
                "resource_path": "OPS/text/ch.xhtml",
                "fragment": "missing",
                "query": "",
                "external_uri": None,
                "status": "resolved",
                "targets": [
                    {
                        "location": {
                            "format": "epub",
                            "resource_path": "OPS/text/ch.xhtml",
                            "node_path": "/missing",
                            "node_snapshot": "source-1",
                        }
                    }
                ],
            }
        ]
    elif damage == "inactive_schema":
        node["disposition"] = "inert"
        node["attributes"] = []
    elif damage == "tree":
        node["content"] = [{"node_path": "/missing"}]
    elif damage == "envelope":
        data["unexpected"] = True
    else:
        data["base_source_sha256"] = "0" * 64
    path = tmp_path / "source.json"
    path.write_text(json.dumps(data))
    before = {p: p.read_bytes() for p in workspace.rglob("*") if p.is_file()}
    with pytest.raises(SystemExit) as rejected:
        main(["revise", str(workspace), "--source", str(path), "--reason", "invalid"])
    assert rejected.value.code == 2
    assert all(p.read_bytes() == value for p, value in before.items())
    assert len(requests(tmp_path)) == 3


@pytest.mark.parametrize(
    "damage", ["stale", "unknown", "duplicate", "marker", "empty", "schema", "reason"]
)
def test_translation_patch_rejects_invalid_inputs_without_changing_workspace(
    tmp_path, capsys, damage
):
    workspace = tmp_path / "run"
    main(run_args(make_epub(tmp_path), workspace), process_command=helper(tmp_path))
    capsys.readouterr()
    item = load_translation(workspace).translations[0].model_dump(mode="json")
    data = {
        "schema_version": 1,
        "base_source_sha256": pointer(workspace, "source"),
        "base_translation_sha256": pointer(workspace, "translation"),
        "translations": [item],
    }
    if damage == "stale":
        data["base_translation_sha256"] = "0" * 64
    elif damage == "unknown":
        item["unit_id"] = "unknown"
    elif damage == "duplicate":
        data["translations"].append(item)
    elif damage == "marker":
        item["text"] = "保護対象なし"
    elif damage == "empty":
        item["text"] = " "
    elif damage == "schema":
        data["unexpected"] = True
    path = tmp_path / "patch.json"
    path.write_text(json.dumps(data))
    before = {p: p.read_bytes() for p in workspace.rglob("*") if p.is_file()}
    with pytest.raises(SystemExit) as rejected:
        main(
            [
                "revise",
                str(workspace),
                "--translations",
                str(path),
                "--reason",
                " " if damage == "reason" else "確認",
            ]
        )
    assert rejected.value.code == 2
    assert all(p.read_bytes() == value for p, value in before.items())
    assert len(requests(tmp_path)) == 3


def test_policy_file_updates_fixed_inputs_and_qa_without_acquisition(tmp_path, capsys):
    from doc_translate.preparation import load_preparation

    workspace = tmp_path / "run"
    main(run_args(make_epub(tmp_path), workspace), process_command=helper(tmp_path))
    capsys.readouterr()
    before = saved_files(workspace)
    source = pointer(workspace, "source")
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps({"version": "2", "text": "本文は常体。"}))
    main(
        ["resume", str(workspace), "--policy", str(policy)],
        process_command=helper(tmp_path),
    )
    result = json.loads(capsys.readouterr().out)
    assert result["processing"]["status"] == "completed"
    assert pointer(workspace, "source") == source
    assert load_preparation(workspace).policy.text == "本文は常体。"
    assert load_preparation(workspace).glossary.style == "本文は常体。"
    assert requests(tmp_path)[-1]["context"]["style_policy"] == {
        "version": "2",
        "text": "本文は常体。",
    }
    assert len(requests(tmp_path)) > 3
    assert result["stale_translation"] is False
    assert_preserved(before)


@pytest.mark.parametrize("stage", ["prepare", "translation"])
def test_changed_policy_never_renders_old_translation_as_current_after_failure(
    tmp_path, capsys, stage
):
    workspace = tmp_path / "run"
    main(run_args(make_epub(tmp_path), workspace), process_command=helper(tmp_path))
    capsys.readouterr()
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps({"version": "2", "text": "常体へ変更"}))
    with pytest.raises(SystemExit) as failed:
        main(
            ["resume", str(workspace), "--policy", str(policy)],
            process_command=helper(tmp_path, fail_stage=stage),
        )
    assert failed.value.code == 1
    result = json.loads(capsys.readouterr().out)
    assert (
        result["stale_translation"] is True
        or not load_translation(workspace).translations
    )
    main(["status", "--json", str(workspace)])
    assert json.loads(capsys.readouterr().out)["processing"]["status"] == "partial"


@pytest.mark.parametrize("damage", ["code", "unknown_id", "table"])
def test_pdf_source_replacement_checks_nontranslated_structure_and_references(
    tmp_path, capsys, damage
):
    from pathlib import Path

    from doc_translate.snapshot import SourceSnapshot, save_source_snapshot

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = json.loads(Path("tests/fixtures/render-source.pdf.json").read_bytes())
    save_source_snapshot(workspace, SourceSnapshot.model_validate(source))
    if damage == "code":
        source["blocks"][2]["structure"]["language"] = ["not a language"]
    elif damage == "unknown_id":
        source["blocks"][1]["structure"] = {"target_id": "unknown"}
    else:
        source["blocks"][2]["kind"] = "table"
        source["blocks"][2]["structure"] = {
            "structure_confirmed": False,
            "rows": -1,
            "columns": 1,
            "cells": [],
        }
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
    old = pointer(workspace, "source")
    with pytest.raises(SystemExit) as rejected:
        main(["revise", str(workspace), "--source", str(path), "--reason", "invalid"])
    assert rejected.value.code == 2
    assert pointer(workspace, "source") == old


@pytest.mark.parametrize("merge", [False, True])
def test_source_split_merge_uses_declared_new_ids_and_resubmits_ambiguous_translations(
    tmp_path, capsys, merge
):
    from copy import deepcopy
    from pathlib import Path

    from doc_translate.inference import GenerationConfig
    from doc_translate.preparation import StylePolicy, prepare_document
    from doc_translate.snapshot import (
        SourceSnapshot,
        load_source_snapshot,
        save_source_snapshot,
    )
    from doc_translate.translation import translate_document

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = SourceSnapshot.model_validate_json(
        Path("tests/fixtures/render-source.pdf.json").read_bytes()
    )
    source.blocks = source.blocks[1:2]
    source.uncertainties = []
    if merge:
        second = source.blocks[0].model_copy(
            update={"block_id": "second", "text": "Another paragraph."}
        )
        source.blocks.append(second)
    save_source_snapshot(workspace, source)
    config = GenerationConfig(provider="faux", model="text", thinking="off")
    command = helper(tmp_path)
    prepare_document(
        workspace=workspace,
        book="Test",
        policy=StylePolicy(version="1", text="ですます調"),
        config=config,
        process_command=command,
    )
    translate_document(workspace=workspace, config=config, process_command=command)
    old = pointer(workspace, "translation")
    source_data = source.model_dump(mode="json")
    block = source_data["blocks"][0]
    if merge:
        block["block_id"] = "merged"
        block["text"] = "An untranslated paragraph. Another paragraph."
        source_data["blocks"] = [block]
    else:
        block["block_id"] = "split-first"
        block["text"] = "An untranslated"
        second = deepcopy(block)
        second.update(block_id="split-second", text="paragraph.")
        source_data["blocks"] = [block, second]
    path = tmp_path / "source.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "base_source_sha256": pointer(workspace, "source"),
                "source": source_data,
            }
        )
    )
    main(
        ["revise", str(workspace), "--source", str(path), "--reason", "分割・統合照合"]
    )
    capsys.readouterr()
    count = len(requests(tmp_path))
    prepare_document(
        workspace=workspace,
        book="Test",
        policy=StylePolicy(version="1", text="ですます調"),
        config=config,
        process_command=command,
    )
    translate_document(workspace=workspace, config=config, process_command=command)
    assert len(requests(tmp_path)) > count
    expected = (
        ["An untranslated paragraph. Another paragraph."]
        if merge
        else ["An untranslated", "paragraph."]
    )
    assert [
        u.source_text for c in load_translation(workspace).chunks for u in c.units
    ] == expected
    assert load_translation(workspace, old).source_snapshot_sha256 != pointer(
        workspace, "source"
    )
    assert load_source_snapshot(workspace).change_reason == "分割・統合照合"


def test_failed_chunk_partial_manual_patch_resumes_only_unfilled_units(
    tmp_path, capsys
):
    source = make_epub(
        tmp_path,
        extra={
            "OPS/text/ch.xhtml": b'<html xmlns="http://www.w3.org/1999/xhtml"><body><p>First paragraph.</p><p>Second paragraph.</p></body></html>'
        },
    )
    workspace = tmp_path / "run"
    with pytest.raises(SystemExit) as failed:
        main(
            run_args(source, workspace),
            process_command=helper(tmp_path, fail_stage="translation"),
        )
    assert failed.value.code == 1
    capsys.readouterr()
    base = pointer(workspace, "translation")
    assert len(load_translation(workspace).chunks[0].units) == 2
    patch = tmp_path / "patch.json"
    patch.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "base_source_sha256": pointer(workspace, "source"),
                "base_translation_sha256": base,
                "translations": [
                    {
                        "unit_id": "unit-1",
                        "text": "手動で補完した段落。",
                        "uncertainties": [],
                    }
                ],
            }
        )
    )
    before = saved_files(workspace)
    count = len(requests(tmp_path))
    main(
        [
            "revise",
            str(workspace),
            "--translations",
            str(patch),
            "--reason",
            "失敗unitの手動補完",
        ]
    )
    capsys.readouterr()
    main(["status", "--json", str(workspace)])
    partial = json.loads(capsys.readouterr().out)
    assert partial["processing"]["status"] == "partial"
    assert load_translation(workspace).translations[0].text == "手動で補完した段落。"
    main(["resume", str(workspace)], process_command=helper(tmp_path))
    result = json.loads(capsys.readouterr().out)
    assert result["processing"]["status"] == "completed"
    assert (
        "手動で補完した段落。"
        in (workspace / result["artifacts"]["japanese"]).read_text()
    )
    sent = requests(tmp_path)[count:]
    assert len(sent) == 1
    assert [unit["unit_id"] for unit in json.loads(sent[0]["text"])] == ["unit-2"]
    assert not load_translation(workspace, base).translations
    assert_preserved(before)


@pytest.mark.parametrize("original_lookup", [True, False])
def test_epub_source_validates_only_dereferenced_original_nodes(
    tmp_path, capsys, monkeypatch, original_lookup
):
    from doc_translate.snapshot import load_source_snapshot

    workspace = tmp_path / "run"
    main(run_args(make_epub(tmp_path), workspace), process_command=helper(tmp_path))
    capsys.readouterr()
    source = load_source_snapshot(workspace).model_dump(mode="json")
    nodes = source["blocks"][0]["structure"]["tree"]["nodes"]
    old = "/1/2/1/1"
    node = nodes.pop(old)
    node["location"]["node_path"] = "/missing"
    if original_lookup:
        node["tag"] = "{http://www.w3.org/2000/svg}svg"
        node["disposition"] = "inert"
    nodes["/missing"] = node
    for parent in nodes.values():
        for part in parent["content"]:
            if isinstance(part, dict) and part["node_path"] == old:
                part["node_path"] = "/missing"
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
    if not original_lookup:
        main(["revise", str(workspace), "--source", str(path), "--reason", "照合"])
        assert (
            "/missing"
            in load_source_snapshot(workspace).model_dump(mode="json")["blocks"][0][
                "structure"
            ]["tree"]["nodes"]
        )
        return
    with pytest.raises(SystemExit) as rejected:
        main(["revise", str(workspace), "--source", str(path), "--reason", "照合"])
    assert rejected.value.code == 2
    assert all(p.read_bytes() == value for p, value in before.items())
    capsys.readouterr()
    main(["status", "--json", str(workspace)])
    assert json.loads(capsys.readouterr().out)["render_current"] is True
    main(["render", str(workspace)])
    assert json.loads(capsys.readouterr().out)["processing"]["status"] == "completed"


@pytest.mark.parametrize("record_type", ["checks", "uncertainties", "content_reviews"])
@pytest.mark.parametrize("target", ["unknown", "epub-1", "unit-1"])
def test_epub_source_validates_qa_targets_before_adoption(
    tmp_path, capsys, monkeypatch, record_type, target
):
    from doc_translate.snapshot import load_source_snapshot

    workspace = tmp_path / "run"
    main(run_args(make_epub(tmp_path), workspace), process_command=helper(tmp_path))
    capsys.readouterr()
    source = load_source_snapshot(workspace).model_dump(mode="json")
    records = {
        "checks": {"name": "Human check", "passed": True, "details": "Compared"},
        "uncertainties": {
            "reason": "Needs comparison",
            "locations": [],
            "stage": "source",
            "origin": "processing",
        },
        "content_reviews": {
            "reviewer": "Human",
            "method": "Original comparison",
            "details": "Compared",
        },
    }
    source[record_type].append({**records[record_type], "target_ids": [target]})
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
    command = ["revise", str(workspace), "--source", str(path), "--reason", "QA check"]
    if target == "unknown":
        with pytest.raises(SystemExit) as rejected:
            main(command)
        assert rejected.value.code == 2
        assert all(p.read_bytes() == value for p, value in before.items())
    else:
        main(command)
        assert load_source_snapshot(workspace).model_dump(mode="json")[record_type][-1][
            "target_ids"
        ] == [target]


@pytest.mark.parametrize(
    "page,ref,accepted",
    [(2, None, True), (999, None, False), (2, "#/texts/unknown", False)],
)
def test_synthetic_pdf_source_cell_evidence_needs_original_page_and_draft_for_refs(
    tmp_path, page, ref, accepted
):
    from pathlib import Path

    from doc_translate.snapshot import (
        SourceSnapshot,
        load_source_snapshot,
        save_source_snapshot,
    )

    workspace = tmp_path / "workspace"
    source = json.loads(Path("tests/fixtures/render-source.pdf.json").read_bytes())
    save_source_snapshot(workspace, SourceSnapshot.model_validate(source))
    source["blocks"][1].update(
        kind="table",
        text="",
        structure={
            "rows": 1,
            "columns": 1,
            "cells": [
                {
                    "cell_id": "human-cell",
                    "row": 0,
                    "column": 0,
                    "row_span": 1,
                    "column_span": 1,
                    "text": "Visible cell",
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
    base = pointer(workspace, "source")
    path.write_text(
        json.dumps({"schema_version": 1, "base_source_sha256": base, "source": source})
    )
    command = [
        "revise",
        str(workspace),
        "--source",
        str(path),
        "--reason",
        "Compared original",
    ]
    if accepted:
        main(command)
        assert (
            load_source_snapshot(workspace).model_dump(mode="json")["blocks"][1][
                "structure"
            ]["cells"][0]["text"]
            == "Visible cell"
        )
    else:
        with pytest.raises(SystemExit) as rejected:
            main(command)
        assert rejected.value.code == 2
        assert pointer(workspace, "source") == base
