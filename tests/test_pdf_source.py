"""Artificial correction answers -> accepted chunks -> joined source save/load."""

import pytest
from test_pdf_correction import answer, evidence, run

pytest_plugins = ["test_pdf_correction"]

from doc_translate.pdf_correction import load_pdf_correction
from doc_translate.snapshot import load_source_snapshot, save_source_snapshot


def fragment(block_id, text, page, *, kind="body", **fields):
    return {
        "block_id": block_id,
        "kind": kind,
        "text": text,
        "evidence": [evidence(page, f"#/texts/{page - 1}")],
        **fields,
    }


def continuation(page, mode="space"):
    return {"target": evidence(page, f"#/texts/{page - 1}"), "mode": mode}


def accept(tmp_path, draft, blocks, owned, reference, **kwargs):
    response = answer(draft[1])
    response.update(blocks=blocks, owned_pages=owned)
    digest = run(
        tmp_path,
        draft,
        response,
        owned_pages=owned,
        reference_pages=reference,
        **kwargs,
    )
    load_pdf_correction(draft[0], digest)
    return digest


def assemble(draft, digests):
    from doc_translate.pdf_source import assemble_pdf_source

    return assemble_pdf_source(
        workspace=draft[0], chunk_sha256s=digests, snapshot_id="joined-1"
    )


def test_body_continuation_skips_but_preserves_footnote_and_round_trips(
    tmp_path, draft
):
    first = accept(
        tmp_path,
        draft,
        [
            fragment("same", "A sentence", 1, outgoing=continuation(2)),
            fragment("note", "A footnote.", 1, kind="footnote", target_id="same")
            | {"evidence": [evidence(ref=None, position="page bottom")]},
        ],
        [1],
        [2],
    )
    second = accept(
        tmp_path,
        draft,
        [fragment("same", "continues.", 2, incoming=continuation(1))],
        [2],
        [1],
    )
    snapshot = assemble(draft, [second, first])
    digest = save_source_snapshot(draft[0], snapshot)
    loaded = load_source_snapshot(draft[0], digest)
    assert [block.text for block in loaded.blocks] == [
        "A sentence continues.",
        "A footnote.",
    ]
    body, note = loaded.blocks
    assert [location.model_dump()["page"] for location in body.locations] == [1, 2]
    assert note.structure["target_id"] == body.block_id
    structure = body.model_dump()["structure"]
    assert [part["block"]["text"] for part in structure["fragments"]] == [
        "A sentence",
        "continues.",
    ]
    assert len(structure["page_images"]) == 2
    assert loaded.document == draft[2].document
    assert loaded.uncertainties == []
    assert loaded.content_reviews == []


def test_dehyphenation_and_code_newline_preserve_original_fragments(tmp_path, draft):
    first = accept(
        tmp_path,
        draft,
        [fragment("word", "inter-", 1, outgoing=continuation(2, "dehyphenate"))],
        [1],
        [2],
    )
    second = accept(
        tmp_path,
        draft,
        [fragment("word", "face", 2, incoming=continuation(1, "dehyphenate"))],
        [2],
        [1],
    )
    snapshot = assemble(draft, [first, second])
    assert [block.text for block in snapshot.blocks] == ["interface"]
    assert snapshot.blocks[0].structure["fragments"][0]["block"]["text"] == "inter-"
    first = accept(
        tmp_path,
        draft,
        [
            fragment(
                "code",
                "if (ready) {",
                1,
                kind="code",
                language="c",
                outgoing=continuation(2, "newline"),
            )
        ],
        [1],
        [2],
        policy="Restore visible code, preserving original newlines.",
    )
    second = accept(
        tmp_path,
        draft,
        [
            fragment(
                "code",
                "  execute(",
                2,
                kind="code",
                language="c",
                incoming=continuation(1, "newline"),
            )
        ],
        [2],
        [1],
        policy="Restore visible code, preserving original newlines.",
    )
    snapshot = assemble(draft, [first, second])
    loaded = load_source_snapshot(draft[0], save_source_snapshot(draft[0], snapshot))
    assert [block.text for block in loaded.blocks] == ["if (ready) {\n  execute("]
    assert loaded.blocks[0].structure["language"] == "c"


def test_cross_page_list_parent_resolves_by_evidence_not_chunk_local_id(
    tmp_path, draft
):
    first = accept(
        tmp_path,
        draft,
        [fragment("item", "npm", 1, kind="list", depth=1, marker="-")],
        [1],
        [2],
    )
    second = accept(
        tmp_path,
        draft,
        [
            fragment(
                "item",
                "install",
                2,
                kind="list",
                depth=2,
                marker="-",
                parent_evidence=evidence(),
            )
        ],
        [2],
        [1],
    )
    snapshot = assemble(draft, [second, first])
    loaded = load_source_snapshot(draft[0], save_source_snapshot(draft[0], snapshot))
    assert [block.text for block in loaded.blocks] == ["npm", "install"]
    parent, child = loaded.blocks
    assert child.structure["parent_id"] == parent.block_id
    assert child.block_id != parent.block_id
    assert child.structure["depth"] == 2
    assert loaded.uncertainties == []


def test_unconfirmed_table_keeps_partial_cells_raw_images_and_qa(tmp_path, draft):
    digest = accept(
        tmp_path,
        draft,
        [
            fragment(
                "table",
                "unresolved table",
                1,
                kind="table",
                rows=2,
                columns=2,
                structure_confirmed=False,
                cells=[
                    {
                        "cell_id": "cell",
                        "row": 0,
                        "column": 0,
                        "row_span": 1,
                        "column_span": 1,
                        "text": "visible",
                        "heading": False,
                        "evidence": [evidence()],
                    }
                ],
            )
        ],
        [1],
        [],
    )
    snapshot = assemble(draft, [digest])
    loaded = load_source_snapshot(draft[0], save_source_snapshot(draft[0], snapshot))
    table = loaded.blocks[0]
    structure = table.model_dump()["structure"]
    assert table.text == "unresolved table"
    assert structure["cells"] == []
    assert structure["retained_cells"][0]["text"] == "visible"
    assert structure["fragments"][0]["block"]["cells"][0]["text"] == "visible"
    assert structure["page_images"][0]["image"] == draft[2].pages[0].image.model_dump()
    assert loaded.uncertainties[0].target_ids == [table.block_id]
    assert "table" in loaded.uncertainties[0].reason


def test_figure_internals_are_retained_not_readopted_and_ids_are_document_scoped(
    tmp_path, draft
):
    from test_pdf_correction import form_answer, form_draft

    draft = form_draft(draft)

    digest = run(tmp_path, draft, form_answer(draft[1]))
    snapshot = assemble(draft, [digest])
    loaded = load_source_snapshot(draft[0], save_source_snapshot(draft[0], snapshot))
    assert [block.text for block in loaded.blocks] == [
        "Outside text",
        "Figure 1. Form",
        "Ambiguous",
        "InsideOutside",
        "",
        "",
    ]
    structures = {
        block.kind: block.model_dump()["structure"] for block in loaded.blocks
    }
    table, picture = structures["table"], structures["picture"]
    assert [cell["text"] for cell in table["cells"]] == ["Value"]
    assert [cell["text"] for cell in table["retained_cells"]] == ["Name"]
    assert table["cells"][0]["cell_id"].startswith("pdf-")
    assert picture["caption_ids"] == [loaded.blocks[1].block_id]
    assert picture["members"][0]["target_id"].startswith("pdf-")
    assert all(
        key.startswith("pdf-")
        for item in loaded.uncertainties
        for key in item.target_ids
    )


@pytest.mark.parametrize(
    ("left", "right", "joined", "line"),
    [
        ("one()\ntwo()", "three(\n  value", "one()\ntwo()\nthree(\n  value", 4),
        ("one()\r\ntwo()", "three()\r\nfour()", "one()\r\ntwo()\nthree()\r\nfour()", 4),
        ("one()\rtwo()", "three()\rfour()", "one()\rtwo()\nthree()\rfour()", 4),
        ("one()\ntwo()\n", "three()\nfour()", "one()\ntwo()\nthree()\nfour()", 4),
        (
            "one()\r\ntwo()\r\n",
            "three()\r\nfour()",
            "one()\r\ntwo()\r\nthree()\r\nfour()",
            4,
        ),
        ("one()\rtwo()\r", "three()\rfour()", "one()\rtwo()\r\nthree()\rfour()", 4),
        ("one()\ntwo()", "\nthree()", "one()\ntwo()\nthree()", 3),
        ("one()\r", "\nthree()", "one()\r\nthree()", 2),
    ],
)
def test_joined_code_callout_lines_follow_joined_code_not_local_fragment(
    tmp_path, draft, left, right, joined, line
):
    first = accept(
        tmp_path,
        draft,
        [
            fragment(
                "code",
                left,
                1,
                kind="code",
                language="py",
                outgoing=continuation(2, "newline"),
            )
        ],
        [1],
        [2],
    )
    second = accept(
        tmp_path,
        draft,
        [
            fragment(
                "code",
                right,
                2,
                kind="code",
                language="py",
                incoming=continuation(1, "newline"),
            ),
            fragment(
                "callout",
                "unfinished argument",
                2,
                kind="callout",
                code_id="code",
                marker="1",
                lines=[2],
            )
            | {"evidence": [evidence(2, None, position="margin")]},
        ],
        [2],
        [1],
    )
    snapshot = assemble(draft, [first, second])
    assert [block.text for block in snapshot.blocks] == [
        joined,
        "unfinished argument",
    ]
    code, callout = snapshot.blocks
    assert callout.structure["code_id"] == code.block_id
    assert callout.structure["lines"] == [line]
    assert [part["block"]["text"] for part in code.structure["fragments"]] == [
        left,
        right,
    ]


def test_list_text_continuation_is_separate_from_parent_child_relation(tmp_path, draft):
    first = accept(
        tmp_path,
        draft,
        [
            fragment(
                "item",
                "a long item",
                1,
                kind="list",
                depth=1,
                marker="-",
                outgoing=continuation(2),
            )
        ],
        [1],
        [2],
    )
    second = accept(
        tmp_path,
        draft,
        [
            fragment(
                "item",
                "continues",
                2,
                kind="list",
                depth=1,
                marker="-",
                incoming=continuation(1),
            )
        ],
        [2],
        [1],
    )
    snapshot = assemble(draft, [first, second])
    assert [block.text for block in snapshot.blocks] == ["a long item continues"]
    assert snapshot.blocks[0].structure["depth"] == 1


@pytest.mark.parametrize(
    "case", ["unknown", "multiple", "cycle", "kind", "depth", "order"]
)
def test_unresolved_list_parents_keep_items_images_and_qa(tmp_path, draft, case):
    parent = fragment("parent", "parent", 1, kind="list", depth=1, marker="-")
    child = fragment(
        "child",
        "child",
        2,
        kind="list",
        depth=2,
        marker="-",
        parent_evidence=evidence(),
    )
    parents = [parent]
    if case == "unknown":
        child["parent_evidence"] = evidence(3, "#/texts/2")
    elif case == "multiple":
        parents.append(parent | {"block_id": "another"})
    elif case == "cycle":
        parent.update(depth=3, parent_evidence=evidence(2, "#/texts/1"))
    elif case == "kind":
        parent = fragment("parent", "parent", 1)
        parents = [parent]
    elif case == "depth":
        child["depth"] = 3
    elif case == "order":
        parent["evidence"] = [evidence(2, "#/texts/1")]
        child["evidence"] = [evidence()]
        child["parent_evidence"] = evidence(2, "#/texts/1")
        parents, child = [child], parent
    first = accept(tmp_path, draft, parents, [1], [2, 3])
    second = accept(tmp_path, draft, [child], [2], [1, 3])
    snapshot = assemble(draft, [first, second])
    loaded = load_source_snapshot(draft[0], save_source_snapshot(draft[0], snapshot))
    assert len(loaded.blocks) == (3 if case == "multiple" else 2)
    assert all(block.structure.get("parent_id") is None for block in loaded.blocks)
    assert loaded.uncertainties
    assert any(not check.passed for check in loaded.checks)
    assert all(block.structure["page_images"] for block in loaded.blocks)
    assert all(
        item.locations and item.stage == "pdf_connection"
        for item in loaded.uncertainties
    )


@pytest.mark.parametrize(
    "case",
    [
        "mode",
        "language",
        "kind",
        "order",
        "intervening",
        "one-sided",
        "missing-hyphen",
        "tail",
    ],
)
def test_contradictory_continuations_keep_fragments_and_qa(tmp_path, draft, case):
    left = fragment(
        "part",
        "first(",
        1,
        kind="code",
        language="py",
        outgoing=continuation(2, "newline"),
    )
    right = fragment(
        "part",
        "  last",
        2,
        kind="code",
        language="py",
        incoming=continuation(1, "newline"),
    )
    first_blocks = [left]
    if case == "mode":
        right["incoming"]["mode"] = "space"
    elif case == "language":
        right["language"] = "js"
    elif case == "kind":
        right = fragment("part", "last", 2, incoming=continuation(1, "newline"))
    elif case == "order":
        left.pop("outgoing")
        left["incoming"] = continuation(2, "newline")
        right.pop("incoming")
        right["outgoing"] = continuation(1, "newline")
    elif case == "intervening":
        first_blocks.append(
            fragment("other", "unrelated paragraph", 1)
            | {"evidence": [evidence(ref=None)]}
        )
    elif case == "one-sided":
        right.pop("incoming")
    elif case == "missing-hyphen":
        left = fragment("part", "no hyphen", 1, outgoing=continuation(2, "dehyphenate"))
        right = fragment("part", "tail", 2, incoming=continuation(1, "dehyphenate"))
        first_blocks = [left]
    elif case == "tail":
        digest = accept(tmp_path, draft, first_blocks, [1], [2])
        snapshot = assemble(draft, [digest])
        assert [block.text for block in snapshot.blocks] == ["first("]
        assert (
            snapshot.uncertainties and not (draft[0] / "source-snapshot.json").exists()
        )
        return
    first = accept(tmp_path, draft, first_blocks, [1], [2])
    second = accept(tmp_path, draft, [right], [2], [1])
    snapshot = assemble(draft, [first, second])
    assert [block.text for block in snapshot.blocks] == [
        block["text"] for block in [*first_blocks, right]
    ]
    assert snapshot.uncertainties
    assert all(
        len(block.model_dump()["structure"]["fragments"]) == 1
        for block in snapshot.blocks
    )


def test_page_order_is_independent_of_chunk_order_and_local_ids(tmp_path, draft):
    outer = accept(
        tmp_path,
        draft,
        [fragment("a", "page one", 1), fragment("b", "page three", 3)],
        [1, 3],
        [],
    )
    middle = accept(tmp_path, draft, [fragment("a", "page two", 2)], [2], [])
    snapshot = assemble(draft, [middle, outer])
    assert [block.text for block in snapshot.blocks] == [
        "page one",
        "page two",
        "page three",
    ]


def test_assembly_rejects_overlapping_chunks_and_tampered_artifacts_without_adopting(
    tmp_path, draft
):
    digest = accept(tmp_path, draft, [fragment("a", "one", 1)], [1], [])
    with pytest.raises(ValueError, match="overlapping"):
        assemble(draft, [digest, digest])
    path = draft[0] / f"corrections/pdf/{digest}.json"
    original = path.read_bytes()
    path.write_bytes(b"{}")
    with pytest.raises(ValueError, match="SHA-256"):
        assemble(draft, [digest])
    path.write_bytes(original)
    assert not (draft[0] / "source-snapshot.json").exists()
    assert [block.text for block in assemble(draft, [digest]).blocks] == ["one"]


def test_continuation_cannot_reverse_pages_hidden_in_multievidence_block(
    tmp_path, draft
):
    left = fragment("part", "first", 1, outgoing=continuation(2))
    left["evidence"].append(evidence(3, "#/texts/2"))
    first = accept(tmp_path, draft, [left], [1, 3], [2])
    second = accept(
        tmp_path,
        draft,
        [
            fragment(
                "part",
                "second",
                2,
                incoming=continuation(1),
            )
        ],
        [2],
        [1],
    )
    snapshot = assemble(draft, [first, second])
    assert [block.text for block in snapshot.blocks] == ["first", "second"]
    assert snapshot.uncertainties


def test_open_range_tail_needs_no_unsent_page_evidence_and_is_not_completed(
    tmp_path, draft
):
    digest = accept(
        tmp_path,
        draft,
        [
            fragment(
                "code",
                "function unfinished(",
                1,
                kind="code",
                language="js",
                outgoing={"target": None, "mode": "newline"},
            )
        ],
        [1],
        [],
    )
    snapshot = assemble(draft, [digest])
    assert [block.text for block in snapshot.blocks] == ["function unfinished("]
    assert snapshot.uncertainties
    assert snapshot.uncertainties[0].locations[0].model_dump()["page"] == 1
    assert len(snapshot.blocks[0].model_dump()["structure"]["page_images"]) == 1


def test_different_drafts_of_same_document_keep_real_extraction_snapshots(
    tmp_path, draft
):
    import json

    from test_pdf_correction import rewrite_raw

    first = accept(
        tmp_path,
        draft,
        [
            fragment(
                "part",
                "first",
                1,
                outgoing=continuation(2),
            )
        ],
        [1],
        [2],
    )
    first_draft = draft[1]
    raw = json.loads((draft[0] / draft[2].raw.artifact).read_text())
    raw["texts"][0]["text"] = "Other target"
    other = rewrite_raw(draft, raw)
    second = accept(
        tmp_path,
        other,
        [
            fragment(
                "part",
                "second",
                2,
                incoming=continuation(1),
            )
        ],
        [2],
        [1],
    )
    snapshot = assemble(draft, [first, second])
    loaded = load_source_snapshot(draft[0], save_source_snapshot(draft[0], snapshot))
    assert [block.text for block in loaded.blocks] == ["first", "second"]
    assert [
        block.locations[0].model_dump()["extraction_snapshot"]
        for block in loaded.blocks
    ] == [first_draft, other[1]]
    assert loaded.uncertainties


def test_equivalent_cross_draft_items_can_join_without_treating_refs_as_global(
    tmp_path, draft
):
    import json

    from test_pdf_correction import rewrite_raw

    first = accept(
        tmp_path,
        draft,
        [
            fragment(
                "part",
                "first",
                1,
                outgoing=continuation(2),
            )
        ],
        [1],
        [2],
    )
    raw = json.loads((draft[0] / draft[2].raw.artifact).read_text())
    raw["unrelated_metadata"] = "another extraction"
    other = rewrite_raw(draft, raw)
    second = accept(
        tmp_path,
        other,
        [
            fragment(
                "part",
                "second",
                2,
                incoming=continuation(1),
            )
        ],
        [2],
        [1],
    )
    snapshot = assemble(draft, [first, second])
    assert [block.text for block in snapshot.blocks] == ["first second"]
    assert snapshot.uncertainties == []
    assert [
        item["draft_sha256"]
        for item in snapshot.blocks[0].model_dump()["structure"]["fragments"]
    ] == [draft[1], other[1]]


def test_picture_references_follow_joined_caption_and_round_trip(tmp_path, draft):
    first = accept(
        tmp_path,
        draft,
        [fragment("cap", "Figure 1.", 1, kind="caption", outgoing=continuation(2))],
        [1],
        [2],
    )
    second = accept(
        tmp_path,
        draft,
        [
            fragment("cap", "Form.", 2, kind="caption", incoming=continuation(1)),
            fragment(
                "figure",
                "",
                2,
                kind="picture",
                region={"page": 2, "left": 0, "top": 0, "right": 20, "bottom": 10},
                caption_ids=["cap"],
                members=[
                    {"target_id": "cap", "status": "uncertain", "reason": "near figure"}
                ],
            )
            | {"evidence": [evidence(2, None, position="figure region")]},
        ],
        [2],
        [1],
    )
    snapshot = assemble(draft, [first, second])
    loaded = load_source_snapshot(draft[0], save_source_snapshot(draft[0], snapshot))
    caption, picture = loaded.blocks
    assert caption.text == "Figure 1. Form."
    structure = picture.model_dump()["structure"]
    assert structure["caption_ids"] == [caption.block_id]
    assert structure["members"][0]["target_id"] == caption.block_id


def test_assembly_decodes_each_draft_image_once_without_persistent_cache(
    tmp_path, draft, monkeypatch
):
    from PIL import Image

    first = accept(tmp_path, draft, [fragment("a", "one", 1)], [1], [2])
    second = accept(tmp_path, draft, [fragment("b", "two", 2)], [2], [1])
    opened = []
    image_open = Image.open

    def observe_open(*args, **kwargs):
        image = image_open(*args, **kwargs)
        opened.append(image.format)
        return image

    monkeypatch.setattr(Image, "open", observe_open)
    assert [block.text for block in assemble(draft, [first, second]).blocks] == [
        "one",
        "two",
    ]
    assert opened == ["PNG", "PNG", "PNG"]
    assemble(draft, [first, second])
    assert len(opened) == 6
    load_pdf_correction(draft[0], second)
    assert len(opened) == 9


@pytest.mark.parametrize(
    "target", ["chunk", "draft", "original", "raw", "image", "run"]
)
def test_assembly_revalidates_artifacts_after_a_previous_assembly(
    tmp_path, draft, target
):
    workspace, digest, model = draft
    first = accept(tmp_path, draft, [fragment("a", "one", 1)], [1], [2])
    second = accept(tmp_path, draft, [fragment("b", "two", 2)], [2], [1])
    chunk = load_pdf_correction(workspace, second)
    assemble(draft, [first, second])
    paths = {
        "chunk": workspace / f"corrections/pdf/{second}.json",
        "draft": workspace / f"drafts/pdf/{digest}.json",
        "original": workspace / model.document.artifact,
        "raw": workspace / model.raw.artifact,
        "image": workspace / model.pages[2].image.artifact,
        "run": workspace / chunk.run.artifact,
    }
    path = paths[target]
    original = path.read_bytes()
    path.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        assemble(draft, [first, second])
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        load_pdf_correction(workspace, second)
    path.write_bytes(original)
    assert [block.text for block in assemble(draft, [first, second]).blocks] == [
        "one",
        "two",
    ]
    assert not (workspace / "source-snapshot.json").exists()


@pytest.mark.parametrize(
    "target", ["schema", "answer", "ownership", "reference", "adoption"]
)
def test_assembly_revalidates_each_chunk_even_when_the_draft_was_already_checked(
    tmp_path, draft, target
):
    import json

    from test_pdf_correction import artifact

    workspace, _, model = draft
    first = accept(tmp_path, draft, [fragment("a", "one", 1)], [1], [2])
    second_blocks = [fragment("b", "two", 2)]
    if target == "adoption":
        second_blocks.append(
            fragment(
                "figure",
                "",
                2,
                kind="picture",
                region={"page": 2, "left": 0, "top": 0, "right": 20, "bottom": 10},
                caption_ids=[],
                members=[
                    {"target_id": "b", "status": "confirmed", "reason": "inside figure"}
                ],
            )
            | {"evidence": [evidence(2, None, position="figure region")]}
        )
    second = accept(tmp_path, draft, second_blocks, [2], [1])
    data = json.loads((workspace / f"corrections/pdf/{second}.json").read_text())
    if target == "schema":
        del data["run"]
    elif target == "answer":
        record = json.loads((workspace / data["run"]["artifact"]).read_text())
        response = answer(draft[1])
        response.update(owned_pages=[2], blocks=[fragment("b", "two", 1)])
        record["attempts"][-1]["final"]["message"]["content"] = [
            {"type": "text", "text": json.dumps(response)}
        ]
        data["run"] = artifact(
            workspace, "attempts/pdf-correction", "json", json.dumps(record).encode()
        )
    elif target == "ownership":
        data["reference_pages"] = [2]
    elif target == "reference":
        data["reference_pages"] = [3]
        data["page_images"] = [
            model.pages[1].model_dump(),
            model.pages[2].model_dump(),
        ]
    else:
        data["blocks"].extend(data["retained_blocks"])
        data["retained_blocks"] = []
    forged = artifact(workspace, "corrections/pdf", "json", json.dumps(data).encode())
    with pytest.raises(ValueError):
        assemble(draft, [first, forged["sha256"]])
    assert not (workspace / "source-snapshot.json").exists()
