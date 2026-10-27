"""Validated source snapshot -> traceable protected translation units."""

from pathlib import Path

import pytest

from doc_translate.snapshot import SourceSnapshot
from doc_translate.units import generate_translation_units

pytest_plugins = ["test_pdf_correction"]

FIXTURES = Path(__file__).parent / "fixtures"


def context(unit):
    return unit.model_dump()["context"]


def test_pdf_cells_keep_independent_identity_and_source_context():
    snapshot = SourceSnapshot.model_validate_json(
        (FIXTURES / "source-snapshot.pdf.json").read_bytes()
    )
    units = generate_translation_units(snapshot)
    assert [unit.source_text for unit in units] == [
        "Heading",
        "Value",
        "Example table.",
    ]
    assert len({unit.unit_id for unit in units}) == 3
    assert units[0].unit_id not in {"table-a", "cell-a", "#/tables/0"}
    assert units[0].block_id == "table-a"
    assert units[0].cell_id == "cell-a"
    assert context(units[0])["table"]["column_span"] == 2
    assert context(units[1])["table"]["row"] == 1
    assert units[0].locations == snapshot.blocks[0].locations
    assert units[0].snapshot_id == "source-pdf-1"
    assert units[0].document_sha256 == snapshot.document.sha256
    assert len(units[0].source_hash) == len(units[0].context_hash) == 64
    assert len(units[0].source_snapshot_sha256) == 64
    assert generate_translation_units(snapshot) == units


def pdf_snapshot(blocks):
    return SourceSnapshot.model_validate(
        {
            "schema_version": 1,
            "snapshot_id": "pdf-source",
            "document": {"format": "pdf", "sha256": "a" * 64, "artifact": "own.pdf"},
            "blocks": [
                {
                    "locations": [
                        {"format": "pdf", "page": 1, "evidence": "Visible source"}
                    ],
                    **b,
                }
                for b in blocks
            ],
            "processing": {"stage": "pdf_connection", "status": "completed"},
        }
    )


def test_pdf_figure_membership_excludes_only_confirmed_interiors():
    source = pdf_snapshot(
        [
            {
                "block_id": "h",
                "kind": "heading",
                "text": "Setup",
                "structure": {"level": 2},
            },
            {
                "block_id": "parent",
                "kind": "list",
                "text": "Install",
                "structure": {"depth": 1, "marker": "1."},
            },
            {
                "block_id": "child",
                "kind": "list",
                "text": "Continue",
                "structure": {"depth": 2, "marker": "a.", "parent_id": "parent"},
            },
            {"block_id": "inside", "kind": "paragraph", "text": "Figure internals"},
            {"block_id": "near", "kind": "body", "text": "Nearby explanation"},
            {"block_id": "uncertain", "kind": "body", "text": "Uncertain membership"},
            {
                "block_id": "table",
                "kind": "table",
                "text": "DO NOT USE PROJECTION",
                "structure": {
                    "rows": 1,
                    "columns": 2,
                    "cells": [
                        {
                            "cell_id": "hidden",
                            "row": 0,
                            "column": 0,
                            "text": "Internal cell",
                        },
                        {
                            "cell_id": "shown",
                            "row": 0,
                            "column": 1,
                            "text": "Adopted cell",
                        },
                    ],
                    "retained_cells": [{"cell_id": "old", "text": "Never adopted"}],
                    "fragments": [{"text": "Old raw content"}],
                },
            },
            {
                "block_id": "pic",
                "kind": "picture",
                "text": "Image label",
                "structure": {
                    "members": [
                        {"target_id": "inside", "status": "confirmed"},
                        {"target_id": "hidden", "status": "confirmed"},
                        {"target_id": "uncertain", "status": "uncertain"},
                    ]
                },
            },
            {
                "block_id": "caption",
                "kind": "caption",
                "text": "Caption",
                "structure": {"target_id": "pic"},
            },
            {"block_id": "code", "kind": "code", "text": "SECRET CODE"},
            {"block_id": "formula", "kind": "formula", "text": "SECRET FORMULA"},
        ]
    )
    units = generate_translation_units(source)
    assert [u.source_text for u in units] == [
        "Setup",
        "Install",
        "Continue",
        "Nearby explanation",
        "Uncertain membership",
        "Adopted cell",
        "Caption",
    ]
    assert context(units[2])["heading_path"] == [
        {"block_id": "h", "level": 2, "text": "Setup"}
    ]
    assert context(units[2])["list_path"] == [
        {"block_id": "parent", "depth": 1, "marker": "1.", "text": "Install"}
    ]
    assert context(units[2])["list"]["parent_id"] == "parent"
    assert context(units[-1])["target_id"] == "pic"


def test_pdf_mixed_inline_is_one_reorderable_unit_with_protected_payloads():
    from doc_translate.units import validate_unit_text

    source = pdf_snapshot(
        [
            {
                "block_id": "p",
                "kind": "paragraph",
                "text": "Use Widget with docs and x = 1 at https://example.test/v2.",
                "structure": {
                    "inline": [
                        {"kind": "text", "text": "Use "},
                        {
                            "kind": "emphasis",
                            "children": [{"kind": "identifier", "text": "Widget"}],
                        },
                        {"kind": "text", "text": " with "},
                        {
                            "kind": "link",
                            "target": "https://example.test/private",
                            "children": [{"kind": "text", "text": "docs"}],
                        },
                        {"kind": "text", "text": " and "},
                        {"kind": "code", "text": "x = 1"},
                        {"kind": "text", "text": " at https://example.test/v2."},
                    ]
                },
            }
        ]
    )
    (unit,) = generate_translation_units(source)
    assert unit.source_text == "Use ⟦1⟧⟦=2⟧⟦/1⟧ with ⟦3⟧docs⟦/3⟧ and ⟦=4⟧ at ⟦=5⟧."
    assert [t.kind for t in unit.tokens] == [
        "emphasis",
        "identifier",
        "link",
        "code",
        "literal",
    ]
    assert unit.tokens[2].value["target"] == "https://example.test/private"
    assert unit.tokens[3].value["text"] == "x = 1"
    validate_unit_text(unit, "⟦3⟧文書⟦/3⟧を参照し、⟦=5⟧で⟦=4⟧と⟦1⟧⟦=2⟧⟦/1⟧を使います。")
    import pytest

    for invalid in [
        unit.source_text.replace("⟦=4⟧", "changed"),
        unit.source_text + "⟦=4⟧",
        unit.source_text.replace("⟦/1⟧", "⟦/3⟧"),
    ]:
        with pytest.raises(ValueError):
            validate_unit_text(unit, invalid)


def test_epub_reader_mixed_paragraph_stays_together_and_keeps_node_provenance(tmp_path):
    from test_epub import make_epub
    from test_epub_source import extract

    from doc_translate.units import validate_unit_text

    xhtml = b"""<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Not body</title></head><body><h1>Setup</h1><p id="intro">Use <em>the <a href="https://example.test/private">guide</a></em> with <code>unsafe()</code>.<math xmlns="http://www.w3.org/1998/Math/MathML"><mi>SECRET FORMULA</mi></math> End.</p><pre>SECRET CODE</pre></body></html>"""
    snapshot = extract(make_epub(tmp_path, extra={"OPS/text/ch.xhtml": xhtml}))
    units = generate_translation_units(snapshot)
    assert [u.kind for u in units] == ["heading", "paragraph"]
    paragraph = units[1]
    assert paragraph.source_text == "Use ⟦1⟧the ⟦2⟧guide⟦/2⟧⟦/1⟧ with ⟦=3⟧.⟦=4⟧ End."
    assert paragraph.block_id == "epub-1"
    assert paragraph.node_path == "/1/2/2"
    assert paragraph.locations[0].format == "epub"
    assert paragraph.locations[0].element_id == "intro"
    assert paragraph.locations[0].node_snapshot == "source-1"
    assert context(paragraph)["heading_path"][0]["text"] == "Setup"
    assert (
        paragraph.tokens[1].model_dump()["value"]["references"][0]["uri"]
        == "https://example.test/private"
    )
    assert paragraph.tokens[2].value["text"] == "unsafe()"
    assert "SECRET FORMULA" not in paragraph.model_dump_json()
    assert paragraph.tokens[3].kind == "formula"
    validate_unit_text(paragraph, "⟦=4⟧と⟦=3⟧を使い、⟦1⟧⟦2⟧案内⟦/2⟧を⟦/1⟧読みます。")


def test_epub_items_cells_captions_and_attributes_remain_traceable(tmp_path):
    from test_epub import make_epub
    from test_epub_source import extract

    xhtml = b"""<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><head/><body><ol start="3"><li id="outer"><p>Outer <em>item</em>.</p><ul><li>Child</li></ul> tail</li></ol><table><caption>Values</caption><tr><th rowspan="2">Name</th><th>First</th></tr><tr><td colspan="2">Value <strong>cell</strong></td></tr></table><figure><p>SECRET INTERIOR</p><img src="../images/p.png" alt="A diagram" title="Diagram title"/><figcaption>Figure caption</figcaption></figure><aside epub:type="footnote"><p>Note text</p></aside><p>Nearby text</p></body></html>"""
    snapshot = extract(
        make_epub(
            tmp_path,
            extra={
                "OPS/text/ch.xhtml": xhtml,
                "OPS/images/p.png": b"own artificial image",
            },
        )
    )
    units = generate_translation_units(snapshot)
    assert [u.kind for u in units] == [
        "list",
        "list",
        "caption",
        "table_cell",
        "table_cell",
        "table_cell",
        "alt",
        "title",
        "caption",
        "footnote",
        "paragraph",
    ]
    assert units[0].source_text == "⟦1⟧Outer ⟦2⟧item⟦/2⟧.⟦/1⟧ tail"
    assert context(units[1])["list_path"][0]["node_path"] == "/1/2/1/1"
    assert context(units[1])["list"]["depth"] == 2
    assert context(units[5])["table"]["row"] == 1
    assert context(units[5])["table"]["column"] == 1
    assert context(units[5])["table"]["column_span"] == 2
    assert context(units[3])["table"]["row_span"] == 2
    assert [h["text"] for h in context(units[5])["table"]["headings"]] == [
        "Name",
        "First",
    ]
    assert units[6].attribute == "alt"
    assert units[6].source_text == "A diagram"
    assert units[8].source_text == "Figure caption"
    assert all("SECRET INTERIOR" not in u.model_dump_json() for u in units)


def test_workflow_splits_huge_units_without_losing_protected_structure():
    from doc_translate.units import split_translation_units, validate_unit_text

    source = pdf_snapshot(
        [
            {
                "block_id": "p",
                "kind": "body",
                "text": "Alpha beta. Alpha beta. Important words. Alpha beta. Alpha beta.",
                "structure": {
                    "inline": [
                        {"kind": "text", "text": "Alpha beta. Alpha beta. "},
                        {
                            "kind": "emphasis",
                            "children": [{"kind": "text", "text": "Important words."}],
                        },
                        {"kind": "text", "text": " Alpha beta. Alpha beta."},
                    ]
                },
            }
        ]
    )
    (parent,) = generate_translation_units(source)
    parts = split_translation_units([parent], max_source_chars=30)
    assert len(parts) > 1
    assert "".join(p.source_text for p in parts) == parent.source_text
    assert len({p.unit_id for p in parts}) == len(parts)
    assert all(p.parent_unit_id == parent.unit_id for p in parts)
    assert all(p.parent_source_hash == parent.source_hash for p in parts)
    assert all(len(p.source_text) <= 30 for p in parts)
    assert parts[0].source_range is not None
    assert parts[-1].source_range is not None
    assert parts[0].source_range.start == 0
    assert parts[-1].source_range.end == len(parent.source_text)
    for part in parts:
        assert part.source_range is not None
        assert (
            part.source_text
            == parent.source_text[part.source_range.start : part.source_range.end]
        )
        assert part.locations == parent.locations
        validate_unit_text(part, part.source_text)
    assert split_translation_units(parts, max_source_chars=30) == parts


def test_impossible_split_reports_original_unit_without_truncation():
    import pytest

    from doc_translate.units import UnitSplitError, split_translation_units

    source = pdf_snapshot(
        [
            {
                "block_id": "p",
                "kind": "paragraph",
                "text": "Long emphasized phrase remains whole",
                "structure": {
                    "inline": [
                        {
                            "kind": "emphasis",
                            "children": [
                                {
                                    "kind": "text",
                                    "text": "Long emphasized phrase remains whole",
                                }
                            ],
                        },
                    ]
                },
            }
        ]
    )
    (unit,) = generate_translation_units(source)
    before = unit.model_dump_json()
    with pytest.raises(UnitSplitError) as caught:
        split_translation_units([unit], max_source_chars=15)
    assert caught.value.unit_id == unit.unit_id
    assert caught.value.source_range.start == 0
    assert caught.value.source_range.end == len(unit.source_text)
    assert "protected" in str(caught.value).lower()
    assert unit.model_dump_json() == before


def test_source_and_context_hashes_track_different_inputs(tmp_path):
    from test_epub import make_epub
    from test_epub_source import extract

    from doc_translate.epub import read_epub
    from doc_translate.epub_source import extract_epub_source
    from doc_translate.snapshot import save_source_snapshot

    path = make_epub(
        tmp_path,
        extra={
            "OPS/text/ch.xhtml": b'<html xmlns="http://www.w3.org/1999/xhtml"><head/><body><h1>Heading</h1><p>Read <a href="https://example.test/one">this</a>.</p></body></html>'
        },
    )
    first = extract(path)
    second = extract_epub_source(
        read_epub(path), document=first.document, snapshot_id="source-2"
    )
    a = generate_translation_units(first)[1]
    b = generate_translation_units(second)[1]
    assert a.source_hash == b.source_hash
    assert a.context_hash == b.context_hash
    assert a.source_snapshot_sha256 != b.source_snapshot_sha256
    assert a.source_snapshot_sha256 == save_source_snapshot(
        tmp_path / "workspace", first
    )
    changed_heading = first.model_dump()
    changed_heading["blocks"][0]["structure"]["tree"]["nodes"]["/1/2/1"]["content"] = [
        "New heading"
    ]
    c = generate_translation_units(SourceSnapshot.model_validate(changed_heading))[1]
    assert c.source_hash == a.source_hash
    assert c.context_hash != a.context_hash
    changed_link = first.model_dump()
    changed_link["blocks"][0]["structure"]["tree"]["nodes"]["/1/2/2/1"]["references"][
        0
    ]["uri"] = "https://example.test/two"
    d = generate_translation_units(SourceSnapshot.model_validate(changed_link))[1]
    assert d.source_text == a.source_text
    assert d.source_hash != a.source_hash


def test_structural_validation_allows_reordering_but_not_reparenting():
    import pytest

    from doc_translate.units import validate_unit_text

    (unit,) = generate_translation_units(
        pdf_snapshot(
            [
                {
                    "block_id": "p",
                    "kind": "body",
                    "text": "A linked name",
                    "structure": {
                        "inline": [
                            {"kind": "text", "text": "A "},
                            {
                                "kind": "link",
                                "target": "#target",
                                "children": [
                                    {
                                        "kind": "emphasis",
                                        "children": [
                                            {"kind": "text", "text": "linked name"}
                                        ],
                                    }
                                ],
                            },
                        ]
                    },
                }
            ]
        )
    )
    validate_unit_text(unit, "⟦1⟧⟦2⟧名前⟦/2⟧⟦/1⟧です。")
    with pytest.raises(ValueError, match="structure"):
        validate_unit_text(unit, "⟦2⟧名前⟦/2⟧は⟦1⟧リンク⟦/1⟧です。")


def test_confirmed_pdf_ref_membership_does_not_remove_caption_or_partial_content():
    source = pdf_snapshot(
        [
            {
                "block_id": "inside",
                "kind": "body",
                "text": "Internal label",
                "locations": [
                    {
                        "format": "pdf",
                        "page": 1,
                        "extraction_ref": "#/texts/1",
                        "extraction_snapshot": "draft",
                    }
                ],
            },
            {
                "block_id": "partial",
                "kind": "body",
                "text": "Outside fragment",
                "locations": [
                    {
                        "format": "pdf",
                        "page": 1,
                        "extraction_ref": "#/texts/2",
                        "extraction_snapshot": "draft",
                    }
                ],
            },
            {
                "block_id": "caption",
                "kind": "caption",
                "text": "Caption",
                "locations": [
                    {
                        "format": "pdf",
                        "page": 1,
                        "extraction_ref": "#/texts/1",
                        "extraction_snapshot": "draft",
                    }
                ],
            },
            {
                "block_id": "picture",
                "kind": "picture",
                "text": "",
                "structure": {
                    "members": [
                        {"ref": {"page": 1, "ref": "#/texts/1"}, "status": "confirmed"},
                        {
                            "ref": {"page": 1, "ref": "#/texts/2", "charspan": [0, 3]},
                            "status": "confirmed",
                        },
                    ]
                },
            },
        ]
    )
    assert [u.source_text for u in generate_translation_units(source)] == [
        "Outside fragment",
        "Caption",
    ]


def test_pdf_figure_refs_remain_draft_local():
    source = pdf_snapshot(
        [
            {
                "block_id": "inside",
                "kind": "body",
                "text": "Internal",
                "locations": [
                    {
                        "format": "pdf",
                        "page": 1,
                        "extraction_ref": "#/texts/1",
                        "extraction_snapshot": "first-draft",
                    }
                ],
            },
            {
                "block_id": "outside",
                "kind": "body",
                "text": "Outside",
                "locations": [
                    {
                        "format": "pdf",
                        "page": 1,
                        "extraction_ref": "#/texts/1",
                        "extraction_snapshot": "other-draft",
                    }
                ],
            },
            {
                "block_id": "picture",
                "kind": "picture",
                "text": "",
                "locations": [
                    {
                        "format": "pdf",
                        "page": 1,
                        "extraction_ref": "#/pictures/1",
                        "extraction_snapshot": "first-draft",
                    }
                ],
                "structure": {
                    "members": [
                        {"ref": {"page": 1, "ref": "#/texts/1"}, "status": "confirmed"},
                    ]
                },
            },
        ]
    )
    assert [u.source_text for u in generate_translation_units(source)] == ["Outside"]


def test_pdf_table_context_uses_only_adopted_headers_and_not_inline_code_payloads():
    source = pdf_snapshot(
        [
            {
                "block_id": "h",
                "kind": "heading",
                "text": "Using secret_call()",
                "structure": {
                    "level": 1,
                    "inline": [
                        {"kind": "text", "text": "Using "},
                        {"kind": "code", "text": "secret_call()"},
                    ],
                },
            },
            {
                "block_id": "table",
                "kind": "table",
                "text": "",
                "structure": {
                    "cells": [
                        {
                            "cell_id": "header",
                            "row": 0,
                            "column": 0,
                            "text": "Name",
                            "heading": True,
                        },
                        {
                            "cell_id": "value",
                            "row": 1,
                            "column": 0,
                            "text": "Value",
                            "heading": False,
                        },
                    ],
                    "retained_cells": [
                        {
                            "cell_id": "hidden-header",
                            "text": "Internal heading",
                            "heading": True,
                        }
                    ],
                },
            },
        ]
    )
    units = generate_translation_units(source)
    assert context(units[-1])["table"]["headings"] == [
        {"cell_id": "header", "row": 0, "column": 0, "text": "Name"}
    ]
    assert context(units[-1])["heading_path"][0]["text"] == "Using ⟦=1⟧"
    assert "secret_call" not in str(units[-1].context)


def test_epub_huge_unit_splits_and_resplits_in_original_coordinates(tmp_path):
    from test_epub import make_epub
    from test_epub_source import extract

    from doc_translate.units import split_translation_units, validate_unit_text

    xhtml = (
        '<html xmlns="http://www.w3.org/1999/xhtml"><head/><body><p>'
        + "Alpha beta. " * 2000
        + "<em>important words</em>"
        + " Gamma delta." * 2000
        + "</p></body></html>"
    ).encode()
    source = extract(make_epub(tmp_path, extra={"OPS/text/ch.xhtml": xhtml}))
    (original,) = generate_translation_units(source)
    parts = split_translation_units([original], max_source_chars=12000)
    smaller = split_translation_units(parts, max_source_chars=6000)
    assert len(smaller) > len(parts) > 1
    assert "".join(p.source_text for p in smaller) == original.source_text
    assert len({p.unit_id for p in smaller}) == len(smaller)
    for part in smaller:
        assert part.parent_unit_id == original.unit_id
        assert part.parent_source_hash == original.source_hash
        assert part.source_range is not None
        assert (
            part.source_text
            == original.source_text[part.source_range.start : part.source_range.end]
        )
        assert part.locations == original.locations
        validate_unit_text(part, part.source_text)


def test_inline_projection_mismatch_and_unknown_source_are_not_silent_success():
    import pytest

    source = pdf_snapshot(
        [
            {
                "block_id": "p",
                "kind": "body",
                "text": "Actual source",
                "structure": {"inline": [{"kind": "text", "text": "Wrong source"}]},
            }
        ]
    )
    with pytest.raises(ValueError, match="projection"):
        generate_translation_units(source)
    source.blocks[0].kind = "unknown-acquisition-kind"
    with pytest.raises(ValueError, match="Unsupported"):
        generate_translation_units(source)


def test_plain_literals_and_source_marker_delimiters_are_protected():
    from doc_translate.units import validate_unit_text

    source = pdf_snapshot(
        [
            {
                "block_id": "p",
                "kind": "body",
                "text": "Use snake_case, ConfigValue, foo.bar, -0.25, 0xFF, 2e-3 and ⟦=99⟧.",
            }
        ]
    )
    (unit,) = generate_translation_units(source)
    values = [token.value.get("text") for token in unit.tokens]
    assert values == [
        "snake_case",
        "ConfigValue",
        "foo.bar",
        "-0.25",
        "0xFF",
        "2e-3",
        "⟦",
        "99",
        "⟧",
    ]
    validate_unit_text(unit, unit.source_text)


@pytest.mark.parametrize(
    "membership", ["shared-ref", "conflicting-block", "conflicting-cell", "partial"]
)
def test_accepted_pdf_figure_membership_is_not_reclassified_by_units(
    tmp_path, draft, membership
):
    from test_pdf_correction import evidence, form_answer, form_draft, run

    from doc_translate.pdf_correction import load_pdf_correction
    from doc_translate.pdf_source import assemble_pdf_source

    draft = form_draft(draft)
    response = form_answer(draft[1])
    members = response["blocks"][-1]["members"]
    if membership == "shared-ref":
        response["blocks"].insert(
            3,
            {
                "block_id": "shared-outside",
                "kind": "body",
                "text": "Shared external prose",
                "evidence": [evidence(ref="#/texts/4")],
            },
        )
        expected = [
            "Outside text",
            "Inside form",
            "Shared external prose",
            "Figure ⟦=1⟧. Form",
            "Ambiguous",
            "⟦=1⟧",
            "Value",
        ]
    elif membership == "conflicting-block":
        members.append(
            {
                "target_id": "logo",
                "status": "uncertain",
                "reason": "May be external prose",
            }
        )
        expected = [
            "Outside text",
            "LOGO",
            "Figure ⟦=1⟧. Form",
            "Ambiguous",
            "⟦=1⟧",
            "Value",
        ]
    elif membership == "conflicting-cell":
        members[2]["status"] = "uncertain"
        members.append(
            {
                "target_id": "fields",
                "status": "confirmed",
                "reason": "Apparent whole form table",
            }
        )
        expected = [
            "Outside text",
            "Figure ⟦=1⟧. Form",
            "Ambiguous",
            "⟦=1⟧",
            "Name",
            "Value",
        ]
    else:
        members.append(
            {
                "target_id": "partial",
                "status": "confirmed",
                "reason": "Apparent internal paragraph",
            }
        )
        expected = ["Outside text", "Figure ⟦=1⟧. Form", "Ambiguous", "⟦=1⟧", "Value"]
    digest = run(tmp_path, draft, response)
    chunk = load_pdf_correction(draft[0], digest)
    assert chunk.uncertainties
    source = assemble_pdf_source(
        workspace=draft[0], chunk_sha256s=[digest], snapshot_id="accepted-source"
    )
    assert source.uncertainties
    units = generate_translation_units(source)
    assert [unit.source_text for unit in units] == expected
    assert any(
        token.value.get("text") == "InsideOutside"
        for unit in units
        for token in unit.tokens
    )


def test_epub_container_collects_descendant_attributes_once(tmp_path):
    from test_epub import make_epub
    from test_epub_source import extract

    xhtml = b"""<html xmlns="http://www.w3.org/1999/xhtml"><head/><body><p>Guide</p><div><a href="https://example.test"><img alt="A diagram" title="Overview"/></a></div><p><a href="https://example.test"><img alt="Paragraph image"/></a></p><pre><span title="Not prose">code</span></pre><figure><span title="Figure interior">hidden</span><figcaption>Caption</figcaption></figure><script title="Inert">hidden</script></body></html>"""
    units = generate_translation_units(
        extract(make_epub(tmp_path, extra={"OPS/text/ch.xhtml": xhtml}))
    )
    assert [(u.kind, u.source_text) for u in units if u.attribute] == [
        ("alt", "A diagram"),
        ("title", "Overview"),
        ("alt", "Paragraph image"),
    ]
    assert sum(u.source_text == "Guide" for u in units) == 1
    assert sum(u.source_text == "Caption" for u in units) == 1
    assert len({(u.node_path, u.attribute) for u in units if u.attribute}) == 3


def test_complete_literals_protect_source_marker_delimiters_before_escaping():
    from doc_translate.units import validate_unit_text

    (unit,) = generate_translation_units(
        pdf_snapshot(
            [
                {
                    "block_id": "p",
                    "kind": "body",
                    "text": "Use `SECRET⟦x⟧VALUE` at https://example.test/⟦x⟧VALUE.",
                }
            ]
        )
    )
    assert unit.source_text == "Use ⟦=1⟧ at ⟦=2⟧."
    assert [t.value["text"] for t in unit.tokens] == [
        "`SECRET⟦x⟧VALUE`",
        "https://example.test/⟦x⟧VALUE",
    ]
    validate_unit_text(unit, "⟦=2⟧で⟦=1⟧を使います。")
    with pytest.raises(ValueError, match="marker"):
        validate_unit_text(unit, "Use MODIFIED at ⟦=2⟧.")


@pytest.mark.parametrize("group", ["thead", "tbody", "tfoot"])
def test_epub_zero_rowspan_stops_at_its_row_group(tmp_path, group):
    from test_epub import make_epub
    from test_epub_source import extract

    xhtml = (
        '<html xmlns="http://www.w3.org/1999/xhtml"><head/><body><p>Guide</p><table><'
        + group
        + '><tr><th rowspan="0">A</th><td>B</td></tr></'
        + group
        + "><tbody><tr><td>C</td><td>D</td></tr></tbody></table></body></html>"
    ).encode()
    units = generate_translation_units(
        extract(make_epub(tmp_path, extra={"OPS/text/ch.xhtml": xhtml}))
    )
    cells = [u for u in units if u.kind == "table_cell"]
    assert [
        (u.source_text, context(u)["table"]["row_span"], context(u)["table"]["column"])
        for u in cells
    ] == [("A", 1, 0), ("B", 1, 1), ("C", 1, 0), ("D", 1, 1)]


def test_epub_description_lists_keep_nested_item_ancestry(tmp_path):
    from test_epub import make_epub
    from test_epub_source import extract

    xhtml = b"""<html xmlns="http://www.w3.org/1999/xhtml"><head/><body><p>Guide</p><dl><dt>Outer</dt><dd>Outer description<dl><dt>Inner</dt><dd>Inner description</dd></dl></dd></dl></body></html>"""
    units = generate_translation_units(
        extract(make_epub(tmp_path, extra={"OPS/text/ch.xhtml": xhtml}))
    )
    items = [u for u in units if u.kind in {"dt", "dd"}]
    assert [u.source_text for u in items] == [
        "Outer",
        "Outer description",
        "Inner",
        "Inner description",
    ]
    assert [context(u)["list"]["depth"] for u in items] == [1, 1, 2, 2]
    assert context(items[2])["list_path"] == [
        {"node_path": "/1/2/2/2", "text": "Outer description", "attributes": {}}
    ]
    assert context(items[3])["list_path"] == context(items[2])["list_path"]


@pytest.mark.parametrize("format", ["pdf", "epub"])
def test_table_row_headers_do_not_multiply_unrelated_context(tmp_path, format):
    if format == "pdf":
        cells = []
        for row in range(100):
            cells.extend(
                [
                    {
                        "cell_id": f"header-{row}",
                        "row": row,
                        "column": 0,
                        "row_span": 1,
                        "column_span": 1,
                        "heading": True,
                        "text": f"Row {row}",
                    },
                    {
                        "cell_id": f"value-{row}",
                        "row": row,
                        "column": 1,
                        "row_span": 1,
                        "column_span": 1,
                        "heading": False,
                        "text": "Value",
                    },
                ]
            )
        source = pdf_snapshot(
            [
                {
                    "block_id": "table",
                    "kind": "table",
                    "text": "",
                    "structure": {"rows": 100, "columns": 2, "cells": cells},
                }
            ]
        )
    else:
        from test_epub import make_epub
        from test_epub_source import extract

        xhtml = (
            '<html xmlns="http://www.w3.org/1999/xhtml"><head/><body><p>Guide</p><table>'
            + "".join(
                f'<tr><th scope="row">Row {row}</th><td>Value</td></tr>'
                for row in range(100)
            )
            + "</table></body></html>"
        ).encode()
        source = extract(make_epub(tmp_path, extra={"OPS/text/ch.xhtml": xhtml}))
    units = [u for u in generate_translation_units(source) if u.kind == "table_cell"]
    assert len(units) == 200
    assert sum(len(context(u)["table"]["headings"]) for u in units) == 200
    assert [h["text"] for h in context(units[-1])["table"]["headings"]] == ["Row ⟦=1⟧"]
    if format == "pdf":
        assert context(units[-1])["table"]["headings"][0]["cell_id"] == "header-99"
    else:
        assert (
            context(units[-1])["table"]["headings"][0]["node_path"]
            == units[-2].node_path
        )


def test_epub_related_headers_respect_explicit_ids_scope_and_row_groups(tmp_path):
    from test_epub import make_epub
    from test_epub_source import extract

    xhtml = b"""<html xmlns="http://www.w3.org/1999/xhtml"><head/><body><p>Guide</p><table><thead><tr><th id="key" scope="col">Key</th><th id="value" scope="col">Value</th></tr></thead><tbody><tr><th id="group" scope="rowgroup" rowspan="0">Group</th><td headers="key">Chosen</td></tr><tr><td>Ordinary</td></tr></tbody><tbody><tr><th scope="row">Other</th><td>Later</td></tr></tbody></table></body></html>"""
    units = generate_translation_units(
        extract(make_epub(tmp_path, extra={"OPS/text/ch.xhtml": xhtml}))
    )
    cells = {
        u.source_text: context(u)["table"] for u in units if u.kind == "table_cell"
    }
    assert [h["text"] for h in cells["Chosen"]["headings"]] == ["Key"]
    assert [h["text"] for h in cells["Ordinary"]["headings"]] == ["Value", "Group"]
    assert [h["text"] for h in cells["Later"]["headings"]] == ["Value", "Other"]
    assert cells["Group"]["row_span"] == 2
    assert cells["Later"]["column"] == 1
