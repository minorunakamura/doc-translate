"""Saved canonical source -> safe Markdown, source map and output QA."""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from doc_translate.snapshot import (
    SourceSnapshot,
    load_source_snapshot,
    save_source_snapshot,
)


def save_source(tmp_path, blocks, **extra):
    workspace = tmp_path / "workspace"
    source = SourceSnapshot.model_validate(
        {
            "schema_version": 1,
            "snapshot_id": "own-source",
            "document": {"format": "pdf", "sha256": "a" * 64, "artifact": "own.pdf"},
            "blocks": [
                {
                    "locations": [
                        {"format": "pdf", "page": 2, "evidence": "self-authored"}
                    ],
                    **block,
                }
                for block in blocks
            ],
            "processing": {"stage": "source", "status": "completed"},
            **extra,
        }
    )
    save_source_snapshot(workspace, source)
    return workspace


def artifact(workspace, result, name):
    return (workspace / result["artifacts"][name]).read_text()


def test_render_preserves_code_formula_and_marks_only_display_outside_protected_content(
    tmp_path,
):
    from doc_translate.render import render_document

    workspace = save_source(
        tmp_path,
        [
            {
                "block_id": "h",
                "kind": "heading",
                "text": "Own guide",
                "structure": {"level": 2},
            },
            {
                "block_id": "code",
                "kind": "code",
                "text": "print('```')\n  keep()",
                "structure": {"language": "python"},
            },
            {
                "block_id": "call",
                "kind": "callout",
                "text": "Keep indentation",
                "structure": {"code_id": "code", "marker": "1", "lines": [2]},
            },
            {"block_id": "formula", "kind": "formula", "text": "x < y"},
            {
                "block_id": "parent",
                "kind": "list",
                "text": "Parent",
                "structure": {"depth": 1, "marker": "1."},
            },
            {
                "block_id": "child",
                "kind": "list",
                "text": "Child",
                "structure": {"depth": 2, "marker": "-", "parent_id": "parent"},
            },
        ],
        uncertainties=[
            {
                "reason": "Missing closing code",
                "target_ids": ["code"],
                "locations": [
                    {"format": "pdf", "page": 2, "evidence": "self-authored"}
                ],
                "stage": "source",
                "origin": "original",
            }
        ],
    )
    before = load_source_snapshot(workspace).model_dump_json()
    result = render_document(workspace)
    english = artifact(workspace, result, "english")
    assert "## Own guide" in english
    assert "````python\nprint('```')\n  keep()\n````\n\n※" in english
    assert "1 (code; lines 2): Keep indentation" in english
    assert "$$\nx < y\n$$" in english
    assert (
        '1. <a id="block-5"></a>Parent※\n\n    - <a id="block-6"></a>Child※' in english
    )
    assert result["processing"]["status"] == "partial"
    assert result["processing"]["not_processed"] == [
        "unit-1",
        "unit-2",
        "unit-3",
        "unit-4",
    ]
    assert "Keep indentation※" in artifact(workspace, result, "japanese")
    qa = json.loads(artifact(workspace, result, "qa"))
    issue = next(
        item for item in qa["uncertainties"] if item["reason"] == "Missing closing code"
    )
    assert issue["block_ids"] == ["code"]
    assert issue["output_anchors"] == ["block-2"]
    assert issue["locations"][0]["page"] == 2
    mapping = json.loads(artifact(workspace, result, "source_map"))
    assert mapping["blocks"][1]["output_anchor"] == "block-2"
    assert mapping["units"][0]["unit_id"] == "unit-1"
    assert qa["content_review"]["unreviewed_translation_unit_ids"] == [
        "unit-1",
        "unit-2",
        "unit-3",
        "unit-4",
    ]
    assert load_source_snapshot(workspace).model_dump_json() == before


def test_render_and_status_cli_do_not_require_node_or_pdf_dependencies(tmp_path):
    workspace = tmp_path / "workspace"
    fixture = Path(__file__).parent / "fixtures/render-source.pdf.json"
    save_source_snapshot(
        workspace, SourceSnapshot.model_validate_json(fixture.read_bytes())
    )
    env = {**os.environ, "PATH": "", "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"}
    rendered = subprocess.run(
        [str(Path(sys.executable).parent / "doc-translate"), "render", str(workspace)],
        check=False,
        env=env,
        text=True,
        capture_output=True,
    )
    assert rendered.returncode == 1, rendered.stderr
    result = json.loads(rendered.stdout)
    assert result["processing"]["status"] == "partial"
    status = subprocess.run(
        [
            str(Path(sys.executable).parent / "doc-translate"),
            "status",
            str(workspace),
            "--json",
        ],
        check=False,
        env=env,
        text=True,
        capture_output=True,
    )
    assert status.returncode == 0, status.stderr
    assert json.loads(status.stdout)["artifacts"] == result["artifacts"]


def test_render_tables_keep_spans_empty_cells_and_never_re_adopt_figure_interiors(
    tmp_path,
):
    from doc_translate.render import render_document

    workspace = save_source(
        tmp_path,
        [
            {
                "block_id": "simple",
                "kind": "table",
                "text": "",
                "structure": {
                    "rows": 2,
                    "columns": 2,
                    "cells": [
                        {
                            "cell_id": "a",
                            "row": 0,
                            "column": 0,
                            "text": "Name",
                            "heading": True,
                        },
                        {
                            "cell_id": "b",
                            "row": 0,
                            "column": 1,
                            "text": "Value",
                            "heading": True,
                        },
                        {"cell_id": "c", "row": 1, "column": 0, "text": "x | y"},
                        {"cell_id": "d", "row": 1, "column": 1, "text": ""},
                    ],
                },
            },
            {
                "block_id": "merged",
                "kind": "table",
                "text": "",
                "structure": {
                    "rows": 1,
                    "columns": 2,
                    "cells": [
                        {
                            "cell_id": "e",
                            "row": 0,
                            "column": 0,
                            "column_span": 2,
                            "text": "<script>bad()</script>",
                        },
                    ],
                    "retained_cells": [{"text": "NEVER ADOPT"}],
                },
            },
            {"block_id": "inside", "kind": "body", "text": "HIDDEN FIGURE TEXT"},
            {
                "block_id": "figure",
                "kind": "picture",
                "text": "",
                "structure": {
                    "members": [{"target_id": "inside", "status": "confirmed"}]
                },
            },
        ],
    )
    result = render_document(workspace)
    english = artifact(workspace, result, "english")
    assert "| Name※ | Value※ |\n| --- | --- |\n| x &#124; y※ |  |" in english
    assert (
        '<td colspan="2">&#60;script&#62;bad&#40;&#41;&#60;/script&#62;※</td>'
        in english
    )
    assert "NEVER ADOPT" not in english and "HIDDEN FIGURE TEXT" not in english
    assert "※" in english  # Unavailable figure has a source reference and QA.


def epub_workspace(tmp_path, xhtml, resources=None):
    import shutil

    from test_epub import make_epub
    from test_epub_source import extract

    path = make_epub(
        tmp_path, extra={"OPS/text/ch.xhtml": xhtml.encode(), **(resources or {})}
    )
    workspace = tmp_path / "workspace"
    source = extract(path)
    save_source_snapshot(workspace, source)
    (workspace / "original").mkdir()
    shutil.copyfile(path, workspace / "original/book.epub")
    return workspace


def test_epub_render_restores_inline_links_notes_anchors_code_lists_and_mathml(
    tmp_path,
):
    from doc_translate.render import render_document

    workspace = epub_workspace(
        tmp_path,
        """<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><head/><body><h2 id="top">Guide</h2><p>Use <em>the <a href="#note">note</a></em> and <code>a &lt; b</code>.</p><ol start="3"><li>Outer<ul><li>Inner</li></ul></li></ol><pre><code>  a = '```'\n</code></pre><p>Equation <math xmlns="http://www.w3.org/1998/Math/MathML"><mfrac><mi>x</mi><mn>2</mn></mfrac></math>.</p><aside id="note" epub:type="footnote"><p>Note text.</p></aside></body></html>""",
    )
    before = load_source_snapshot(workspace).model_dump_json()
    result = render_document(workspace)
    english = artifact(workspace, result, "english")
    assert '<em>the <a href="#node-1-20">note</a></em>' in english
    assert "<code>a &lt; b</code>" in english
    assert (
        '3. <a id="node-1-10"></a>Outer※\n\n    - <a id="node-1-12"></a>Inner※'
        in english
    )
    assert "````\n  a = '```'\n````" in english
    assert (
        '<math xmlns="http://www.w3.org/1998/Math/MathML"><mfrac><mi>x</mi><mn>2</mn></mfrac></math>'
        in english
    )
    assert '<a id="node-1-20"></a>' in english
    assert "Note text&#46;" in english
    assert load_source_snapshot(workspace).model_dump_json() == before


def test_render_uses_validated_translation_splits_partial_failures_and_full_attempt_usage(
    tmp_path,
):
    from test_translation import prepare_blocks, translate

    from doc_translate.render import render_document, workspace_status

    workspace = prepare_blocks(
        tmp_path,
        [
            ("body", "First paragraph."),
            ("heading", "Second"),
            ("body", "Last paragraph."),
        ],
    )
    translate(
        tmp_path,
        workspace,
        {
            "answer": {
                "translations": [
                    {
                        "unit_id": "unit-1",
                        "text": "最初の段落です。",
                        "uncertainties": [
                            {"reason": "Ambiguous subject", "origin": "undetermined"}
                        ],
                    }
                ]
            }
        },
    )
    result = render_document(workspace)
    assert "最初の段落です。※" in artifact(workspace, result, "japanese")
    assert result["processing"]["status"] == "partial"
    assert result["processing"]["failed"]
    usage = json.loads(artifact(workspace, result, "usage"))
    assert usage["stages"]["preparation"] and usage["stages"]["translation"]
    assert usage["wall_seconds"] is None
    assert any(
        run["record"]["status"] == "rejected" for run in usage["stages"]["translation"]
    )
    assert workspace_status(workspace)["artifacts"] == result["artifacts"]


def test_render_split_translations_cover_each_source_range_without_losing_parts(
    tmp_path,
):
    from test_translation import prepare_blocks, translate

    from doc_translate.render import render_document

    workspace = prepare_blocks(
        tmp_path, [("body", "One two three four five six seven eight.")]
    )
    translate(tmp_path, workspace, max_source_chars=18)
    result = render_document(workspace)
    assert result["processing"]["status"] == "completed"
    assert "訳:One two three 訳:four five six 訳:seven eight&#46;" in artifact(
        workspace, result, "japanese"
    )
    mapping = json.loads(artifact(workspace, result, "source_map"))
    assert [u["source_range"] for u in mapping["units"]] == [
        {"start": 0, "end": 14},
        {"start": 14, "end": 28},
        {"start": 28, "end": 40},
    ]


def test_epub_render_never_activates_source_or_translation_markup_or_external_images(
    tmp_path,
):
    from doc_translate.render import render_document

    workspace = epub_workspace(
        tmp_path,
        """<html xmlns="http://www.w3.org/1999/xhtml"><head/><body><p>&lt;img src="https://evil.test/pixel" /&gt; <a href="javascript:alert(1)">bad</a> <a href="https://example.test/?x=&quot;y&quot;">safe</a><img src="https://evil.test/pixel" alt="Remote"/><script>run()</script><math xmlns="http://www.w3.org/1998/Math/MathML"><annotation-xml><script>run()</script></annotation-xml></math></p><figure><svg xmlns="http://www.w3.org/2000/svg" onload="run()"/><p>Not independent figure prose</p><figcaption>Caption remains</figcaption></figure></body></html>""",
    )
    result = render_document(workspace)
    english = artifact(workspace, result, "english")
    assert '<img src="https:' not in english
    assert (
        "<script" not in english
        and "<svg" not in english
        and "<annotation-xml" not in english
    )
    assert 'href="javascript:' not in english
    assert 'href="https://example.test/?x=&quot;y&quot;"' in english
    assert (
        "Caption remains" in english and "Not independent figure prose" not in english
    )
    assert "[source data](assets/" in english and "※" in english
    qa = json.loads(artifact(workspace, result, "qa"))
    render_problems = [
        item for item in qa["uncertainties"] if item["stage"] == "render"
    ]
    assert all(item["locations"] and item["output_anchors"] for item in render_problems)
    assert any("link" in item["reason"] for item in render_problems)
    assert all(item["unit_ids"] for item in render_problems if "link" in item["reason"])


def test_render_original_raster_assets_pdf_crop_and_epub_image_formula(tmp_path):
    import hashlib
    from io import BytesIO

    from PIL import Image

    from doc_translate.render import render_document

    png = BytesIO()
    Image.new("RGB", (8, 6), "red").save(png, format="PNG")
    data = png.getvalue()
    workspace = save_source(
        tmp_path,
        [
            {
                "block_id": "picture",
                "kind": "picture",
                "text": "",
                "structure": {
                    "region": {"page": 2, "left": 1, "top": 2, "right": 7, "bottom": 5},
                    "page_images": [
                        {
                            "page": 2,
                            "image": {
                                "artifact": "page.png",
                                "sha256": hashlib.sha256(data).hexdigest(),
                            },
                            "width_pixels": 8,
                            "height_pixels": 6,
                        }
                    ],
                },
            }
        ],
    )
    (workspace / "page.png").write_bytes(data)
    result = render_document(workspace)
    english = artifact(workspace, result, "english")
    import re

    match = re.search(r'src="([^"]+)"', english)
    assert match
    image_path = match.group(1)
    with Image.open(
        workspace / Path(result["artifacts"]["english"]).parent / image_path
    ) as image:
        assert image.size == (6, 3)
    assert result["processing"]["status"] == "completed"
    other = tmp_path / "epub"
    other.mkdir()
    workspace = epub_workspace(
        other,
        '<html xmlns="http://www.w3.org/1999/xhtml"><head/><body><p>Image formula <img src="../formula.png" alt="Formula"/></p><table><tr><th colspan="2">Merged</th></tr><tr><td>A</td><td/></tr></table></body></html>',
        {"OPS/formula.png": data},
    )
    result = render_document(workspace)
    english = artifact(workspace, result, "english")
    assert 'alt="Formula"' in english and '<img src="assets/' in english
    assert '<th colspan="2">Merged※</th>' in english
    assert "<td></td>" in english
    assets = list(
        (workspace / Path(result["artifacts"]["english"]).parent / "assets").glob(
            "*.png"
        )
    )
    assert len(assets) == 1 and assets[0].read_bytes() == data


def test_epub_container_mixed_content_keeps_reading_order_and_reachable_table_anchors(
    tmp_path,
):
    from doc_translate.render import render_document

    workspace = epub_workspace(
        tmp_path,
        '<html xmlns="http://www.w3.org/1999/xhtml"><head/><body><div>Before<p>Middle <a href="#cell">cell</a></p>After</div><table><tr><td id="cell">Target</td></tr></table></body></html>',
    )
    result = render_document(workspace)
    english = artifact(workspace, result, "english")
    assert (
        english.index("Before")
        < english.index("Middle")
        < english.index("After")
        < english.index("Target")
    )
    import re

    match = re.search(r'href="#([^"]+)"', english)
    assert match
    target = match.group(1)
    assert f'id="{target}"' in english
    mapping = json.loads(artifact(workspace, result, "source_map"))
    assert all(
        f'id="{u["output_anchor"]}"' in english
        for u in mapping["units"]
        if not u["attribute"]
    )


def test_status_distinguishes_stale_output_and_translation_and_rejects_corruption(
    tmp_path,
):
    import pytest
    from test_translation import prepare_blocks, translate

    from doc_translate.render import render_document, workspace_status

    workspace = prepare_blocks(tmp_path, [("body", "Original source.")])
    translate(tmp_path, workspace)
    old = render_document(workspace)
    source = load_source_snapshot(workspace)
    changed = source.model_copy(deep=True)
    changed.blocks[0].text = "Changed source."
    save_source_snapshot(workspace, changed)
    status = workspace_status(workspace)
    assert status["stale_translation"] is True
    assert status["render_current"] is False
    assert status["previous_artifacts"] == old["artifacts"]
    result = render_document(workspace)
    assert "訳:Original" not in artifact(workspace, result, "japanese")
    assert "Changed source&#46;※" in artifact(workspace, result, "japanese")
    (workspace / result["artifacts"]["english"]).write_text("corrupt")
    with pytest.raises(ValueError, match="hash"):
        workspace_status(workspace)


def test_render_keeps_previous_output_on_atomic_pointer_failure_and_refuses_second_writer(
    tmp_path, monkeypatch
):
    import pytest

    from doc_translate.render import render_document, workspace_status
    from doc_translate.workspace import WorkspaceLockedError, workspace_lock

    workspace = save_source(
        tmp_path, [{"block_id": "p", "kind": "body", "text": "Before"}]
    )
    old = render_document(workspace)
    pointer = (workspace / "render.json").read_bytes()
    changed = load_source_snapshot(workspace)
    changed.blocks[0].text = "After"
    save_source_snapshot(workspace, changed)
    replace = os.replace

    def fail_pointer(source, target):
        if Path(target) == workspace / "render.json":
            raise OSError("injected pointer failure")
        return replace(source, target)

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", fail_pointer)
        with pytest.raises(OSError, match="injected"):
            render_document(workspace)
    assert (workspace / "render.json").read_bytes() == pointer
    assert "Before" in artifact(workspace, old, "english")
    with workspace_lock(workspace), pytest.raises(WorkspaceLockedError):
        render_document(workspace)
    assert workspace_status(workspace)["render_current"] is False


def test_render_qa_checks_real_output_coverage_and_reports_review_and_pdf_scope_separately(
    tmp_path,
):
    from doc_translate.render import render_document

    workspace = save_source(
        tmp_path,
        [{"block_id": "p", "kind": "body", "text": "Reviewed."}],
        content_reviews=[
            {
                "reviewer": "human",
                "method": "original comparison",
                "target_ids": ["p"],
                "details": "Only the source paragraph, not translation.",
            }
        ],
    )
    result = render_document(workspace)
    qa = json.loads(artifact(workspace, result, "qa"))
    assert any(
        c["name"] == "render_unit_coverage" and c["passed"] for c in qa["checks"]
    )
    assert any(c["name"] == "render_references" and c["passed"] for c in qa["checks"])
    assert qa["content_review"]["records"][0]["reviewer"] == "human"
    assert qa["content_review"]["unreviewed_translation_unit_ids"] == ["unit-1"]
    assert result["scope"]["whole_document_verified"] is False
    assert result["scope"]["pages_with_source_blocks"] == [2]
    assert result["render_elapsed_seconds"] >= 0


def test_render_inert_formula_and_tampered_original_asset_are_not_activated(tmp_path):
    import pytest

    from doc_translate.render import render_document

    workspace = save_source(
        tmp_path,
        [
            {
                "block_id": "f",
                "kind": "formula",
                "text": "![pixel](https://evil.test/pixel)",
            }
        ],
    )
    result = render_document(workspace)
    assert "![pixel]" not in artifact(workspace, result, "english")
    assert "[source data]" in artifact(workspace, result, "english")
    other = tmp_path / "epub"
    other.mkdir()
    workspace = epub_workspace(
        other,
        '<html xmlns="http://www.w3.org/1999/xhtml"><head/><body><p>Math <math xmlns="http://www.w3.org/1998/Math/MathML"><mi>x</mi></math></p></body></html>',
    )
    (workspace / "original/book.epub").write_bytes(b"changed archive")
    with pytest.raises(ValueError, match="SHA-256"):
        render_document(workspace)
    assert not (workspace / "render.json").exists()


def test_unconfirmed_pdf_table_keeps_original_page_image_without_readopting_cells(
    tmp_path,
):
    import hashlib
    from io import BytesIO

    from PIL import Image

    from doc_translate.render import render_document

    png = BytesIO()
    Image.new("RGB", (4, 3), "white").save(png, format="PNG")
    data = png.getvalue()
    workspace = save_source(
        tmp_path,
        [
            {
                "block_id": "table",
                "kind": "table",
                "text": "",
                "structure": {
                    "structure_confirmed": False,
                    "retained_cells": [{"text": "Do not translate this candidate"}],
                    "page_images": [
                        {
                            "page": 2,
                            "image": {
                                "artifact": "page.png",
                                "sha256": hashlib.sha256(data).hexdigest(),
                            },
                            "width_pixels": 4,
                            "height_pixels": 3,
                        }
                    ],
                },
            }
        ],
    )
    (workspace / "page.png").write_bytes(data)
    result = render_document(workspace)
    english = artifact(workspace, result, "english")
    assert '<img src="assets/' in english and "※" in english
    assert "Do not translate" not in english


def test_epub_table_span_does_not_invent_rows(tmp_path):
    from doc_translate.render import render_document

    workspace = epub_workspace(
        tmp_path,
        '<html xmlns="http://www.w3.org/1999/xhtml"><head/><body><table><tr><td rowspan="25" colspan="25">Wide</td></tr><tr><td>Next</td></tr></table></body></html>',
    )
    result = render_document(workspace)
    english = artifact(workspace, result, "english")
    assert english.count("<tr>") == 2
    assert '<td rowspan="25" colspan="25">Wide※</td>' in english
    assert "<td>Next※</td>" in english


def test_epub_huge_table_span_renders_and_status_finishes_in_bounded_time(tmp_path):
    workspace = epub_workspace(
        tmp_path,
        '<html xmlns="http://www.w3.org/1999/xhtml"><head/><body><table><tr><td rowspan="1000000000" colspan="1000000000">x</td></tr><tr><td>Next</td></tr></table></body></html>',
    )
    results = []
    for command, exit_code in (("render", 1), ("status", 0)):
        process = subprocess.run(
            [
                str(Path(sys.executable).parent / "doc-translate"),
                command,
                str(workspace),
                *(["--json"] if command == "status" else []),
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        assert process.returncode == exit_code, process.stderr
        results.append(json.loads(process.stdout))
    assert results[0]["artifacts"] == results[1]["artifacts"]
    english = artifact(workspace, results[0], "english")
    assert '<td rowspan="1000000000" colspan="1000000000">x※</td>' in english
    assert english.count("<tr>") == 2
    assert len(english) < 2000


def test_epub_table_cell_deferred_content_is_retained_as_source_reference_with_qa(
    tmp_path,
):
    import re

    from doc_translate.render import render_document

    workspace = epub_workspace(
        tmp_path,
        '<html xmlns="http://www.w3.org/1999/xhtml"><head/><body><table><tr><td>Before<pre><code>KEEP()</code></pre>After<table><tr><td>NESTED</td></tr></table></td></tr></table></body></html>',
    )
    before = load_source_snapshot(workspace).model_dump_json()
    result = render_document(workspace)
    for language in ("english", "japanese"):
        document = artifact(workspace, result, language)
        match = re.search(
            r'<td><a href="(assets/[^\"]+\.txt)">source data</a>※</td>', document
        )
        assert match, document
        retained = (
            workspace / Path(result["artifacts"][language]).parent / match[1]
        ).read_text()
        assert (
            retained.index("Before")
            < retained.index("KEEP()")
            < retained.index("After")
            < retained.index("NESTED")
        )
        assert "```" not in document
    qa = json.loads(artifact(workspace, result, "qa"))
    assert any(
        "cell structure" in item["reason"]
        and item["stage"] == "render"
        and item["locations"]
        for item in qa["uncertainties"]
    )
    assert load_source_snapshot(workspace).model_dump_json() == before


def test_render_coverage_and_output_reached_exclude_units_only_retained_as_source_data(
    tmp_path,
):
    from test_preparation import run as prepare
    from test_translation import translate

    from doc_translate.render import render_document

    workspace = epub_workspace(
        tmp_path,
        '<html xmlns="http://www.w3.org/1999/xhtml"><head/><body><p>Visible</p><table><tr><td>Before<pre><code>KEEP()</code></pre>After<table><tr><td>NESTED</td></tr></table></td></tr></table></body></html>',
    )
    prepare(
        tmp_path,
        workspace,
        [],
        answers_override={
            "collect": {"context": "Own guide", "candidates": []},
            "organize": {"context": "Own guide", "terms": {}},
        },
    )
    translate(tmp_path, workspace)
    result = render_document(workspace)
    assert result["processing"]["not_processed"] == []
    assert result["processing"]["render_failed"] == ["unit-2", "unit-3"]
    assert result["processing"]["output_reached"] == ["unit-1"]
    assert result["processing"]["status"] == "partial"
    qa = json.loads(artifact(workspace, result, "qa"))
    check = next(c for c in qa["checks"] if c["name"] == "render_unit_coverage")
    assert check["passed"] is False and check["target_ids"] == ["unit-2", "unit-3"]


def test_epub_attributes_are_restored_at_inline_and_table_cell_positions(tmp_path):
    from doc_translate.render import render_document

    workspace = epub_workspace(
        tmp_path,
        '<html xmlns="http://www.w3.org/1999/xhtml"><head/><body><p>Before <em title="Helpful &quot;title&quot; &lt;safe&gt;">word</em> after.</p><table><tr><td title="Cell title">Value <span title="Cell inline title">inside</span></td></tr></table></body></html>',
    )
    result = render_document(workspace)
    english = artifact(workspace, result, "english")
    assert '<em title="Helpful &quot;title&quot; &lt;safe&gt;">word</em>' in english
    assert (
        '<td title="Cell title">Value <span title="Cell inline title">inside</span>※</td>'
        in english
    )
    assert result["processing"]["render_failed"] == []
    assert any(
        c["name"] == "render_unit_coverage" and c["passed"]
        for c in result["machine_checks"]
    )


def test_pdf_list_anchors_stay_inside_parent_and_child_items(tmp_path):
    from doc_translate.render import render_document

    workspace = save_source(
        tmp_path,
        [
            {
                "block_id": "parent",
                "kind": "list",
                "text": "Parent",
                "structure": {"depth": 1, "marker": "1."},
            },
            {
                "block_id": "child",
                "kind": "list",
                "text": "Child",
                "structure": {"depth": 2, "marker": "-", "parent_id": "parent"},
            },
        ],
    )
    result = render_document(workspace)
    assert (
        artifact(workspace, result, "english")
        == '1. <a id="block-1"></a>Parent※\n\n    - <a id="block-2"></a>Child※\n'
    )


def test_epub_list_anchors_do_not_interrupt_nested_items(tmp_path):
    from doc_translate.render import render_document

    workspace = epub_workspace(
        tmp_path,
        '<html xmlns="http://www.w3.org/1999/xhtml"><head/><body><ol start="3"><li>Outer<ul><li>Inner</li></ul></li></ol></body></html>',
    )
    result = render_document(workspace)
    english = artifact(workspace, result, "english")
    assert (
        '3. <a id="node-1-5"></a>Outer※\n\n    - <a id="node-1-7"></a>Inner※' in english
    )


def test_pdf_cell_uncertainty_marks_source_and_accepted_translation_only_in_display(
    tmp_path,
):
    from test_preparation import run as prepare
    from test_translation import translate

    from doc_translate.render import render_document
    from doc_translate.translation import load_translation

    workspace = save_source(
        tmp_path,
        [
            {
                "block_id": "table",
                "kind": "table",
                "text": "",
                "structure": {
                    "cells": [
                        {
                            "cell_id": "cell",
                            "row": 0,
                            "column": 0,
                            "text": "Uncertain value",
                        }
                    ]
                },
            }
        ],
        uncertainties=[
            {
                "reason": "Cell uncertain",
                "target_ids": ["cell"],
                "locations": [
                    {"format": "pdf", "page": 2, "evidence": "self-authored"}
                ],
                "stage": "source",
                "origin": "undetermined",
            }
        ],
    )
    prepare(
        tmp_path,
        workspace,
        [],
        answers_override={
            "collect": {"context": "Own table", "candidates": []},
            "organize": {"context": "Own table", "terms": {}},
        },
    )
    translate(tmp_path, workspace)
    before = load_source_snapshot(workspace).model_dump_json()
    translation_before = load_translation(workspace).model_dump_json()
    result = render_document(workspace)
    assert "<td>Uncertain value※</td>" in artifact(workspace, result, "english")
    assert "<td>訳:Uncertain value※</td>" in artifact(workspace, result, "japanese")
    assert result["processing"]["status"] == "completed"
    assert load_source_snapshot(workspace).model_dump_json() == before
    assert load_translation(workspace).model_dump_json() == translation_before


def test_epub_unit_uncertainty_does_not_mark_other_units_in_the_section(tmp_path):
    from test_preparation import run as prepare
    from test_translation import translate

    from doc_translate.render import render_document

    workspace = epub_workspace(
        tmp_path,
        '<html xmlns="http://www.w3.org/1999/xhtml"><head/><body><p>First</p><p>Second</p></body></html>',
    )
    prepare(
        tmp_path,
        workspace,
        [],
        answers_override={
            "collect": {"context": "Own guide", "candidates": []},
            "organize": {"context": "Own guide", "terms": {}},
        },
    )
    translate(
        tmp_path,
        workspace,
        {
            "answer": {
                "translations": [
                    {
                        "unit_id": "unit-1",
                        "text": "最初",
                        "uncertainties": [
                            {"reason": "Only first", "origin": "undetermined"}
                        ],
                    },
                    {"unit_id": "unit-2", "text": "次", "uncertainties": []},
                ]
            }
        },
    )
    result = render_document(workspace)
    english = artifact(workspace, result, "english")
    japanese = artifact(workspace, result, "japanese")
    assert "First※" in english and "Second※" not in english
    assert "最初※" in japanese and "次※" not in japanese
    assert english.count("※") == japanese.count("※") == 1
    qa = json.loads(artifact(workspace, result, "qa"))
    issue = next(i for i in qa["uncertainties"] if i["reason"] == "Only first")
    assert issue["unit_ids"] == ["unit-1"]
    assert issue["output_anchors"] == ["node-1-4"]
    assert '<a id="node-1-4"></a>\n\nFirst※' in english


def test_epub_structural_attribute_units_are_not_lost_on_lists_and_table_rows(tmp_path):
    from doc_translate.render import render_document

    workspace = epub_workspace(
        tmp_path,
        '<html xmlns="http://www.w3.org/1999/xhtml"><head/><body><ul title="List title"><li>Item</li></ul><table><tbody title="Group title"><tr title="Row title"><td>Value</td></tr></tbody></table></body></html>',
    )
    result = render_document(workspace)
    english = artifact(workspace, result, "english")
    assert 'title="List title"' in english
    assert 'title="Group title"' in english
    assert 'title="Row title"' in english
    assert result["processing"]["render_failed"] == []


def test_epub_unavailable_translation_does_not_mark_unrelated_protected_code(tmp_path):
    from doc_translate.render import render_document

    workspace = epub_workspace(
        tmp_path,
        '<html xmlns="http://www.w3.org/1999/xhtml"><head/><body><p>First</p><pre><code>KEEP()</code></pre><p>Second</p></body></html>',
    )
    result = render_document(workspace)
    english = artifact(workspace, result, "english")
    assert english.count("※") == 2
    qa = json.loads(artifact(workspace, result, "qa"))
    missing = [
        i for i in qa["uncertainties"] if i["reason"] == "Translation unavailable"
    ]
    assert [i["target_ids"] for i in missing] == [["unit-1"], ["unit-2"]]


def test_epub_list_inline_ids_stay_inside_items_without_duplicating_child_anchors(
    tmp_path,
):
    from doc_translate.render import render_document

    workspace = epub_workspace(
        tmp_path,
        '<html xmlns="http://www.w3.org/1999/xhtml"><head/><body><ol><li id="parent">Outer <em id="term">term</em><ul><li id="child">Inner</li></ul></li></ol></body></html>',
    )
    result = render_document(workspace)
    english = artifact(workspace, result, "english")
    assert (
        '1. <a id="node-1-5"></a>Outer <em>term</em>※<a id="node-1-6"></a>\n\n    - <a id="node-1-8"></a>Inner※'
        in english
    )
    assert any(
        c["name"] == "render_references" and c["passed"]
        for c in result["machine_checks"]
    )


@pytest.mark.parametrize(
    "inline, attribute, translated_body, english_prose, japanese_prose",
    [
        (
            '<em title="Helpful title">word</em>',
            "title",
            "前 ⟦1⟧語⟦/1⟧ 後。",
            'Before <em title="Helpful title">word</em> after&#46;',
            '前 <em title="役立つ題名">語</em> 後。',
        ),
        (
            '<img src="../image.png" alt="Helpful title"/>',
            "alt",
            "前 ⟦=1⟧ 後。",
            "Before <img ",
            "前 <img ",
        ),
    ],
    ids=["inline-title", "image-alt"],
)
def test_epub_attribute_uncertainty_marks_its_anchor_in_both_languages_only_for_display(
    tmp_path, inline, attribute, translated_body, english_prose, japanese_prose
):
    from io import BytesIO

    from PIL import Image
    from test_preparation import run as prepare
    from test_translation import translate

    from doc_translate.render import render_document
    from doc_translate.translation import load_translation

    png = BytesIO()
    Image.new("RGB", (2, 2), "white").save(png, format="PNG")
    workspace = epub_workspace(
        tmp_path,
        f'<html xmlns="http://www.w3.org/1999/xhtml"><head/><body><p>Before {inline} after.</p><p>Neighbor</p><pre><code>KEEP()</code></pre></body></html>',
        {"OPS/image.png": png.getvalue()},
    )
    prepare(
        tmp_path,
        workspace,
        [],
        answers_override={
            "collect": {"context": "Own guide", "candidates": []},
            "organize": {"context": "Own guide", "terms": {}},
        },
    )
    translate(
        tmp_path,
        workspace,
        {
            "answer": {
                "translations": [
                    {
                        "unit_id": "unit-1",
                        "text": translated_body,
                        "uncertainties": [],
                    },
                    {
                        "unit_id": "unit-2",
                        "text": "役立つ題名",
                        "uncertainties": [
                            {"reason": "Only attribute", "origin": "undetermined"}
                        ],
                    },
                    {"unit_id": "unit-3", "text": "隣", "uncertainties": []},
                ]
            }
        },
    )
    before = load_source_snapshot(workspace).model_dump_json()
    translation_before = load_translation(workspace).model_dump_json()
    result = render_document(workspace)
    assert result["processing"]["status"] == "completed"
    qa = json.loads(artifact(workspace, result, "qa"))
    issue = next(i for i in qa["uncertainties"] if i["reason"] == "Only attribute")
    assert issue["unit_ids"] == ["unit-2"]
    assert issue["output_anchors"] == ["node-1-5"]
    for language, title, prose in (
        ("english", "Helpful title", english_prose),
        ("japanese", "役立つ題名", japanese_prose),
    ):
        document = artifact(workspace, result, language)
        # Image resources also carry an independent source-level concern.
        if attribute == "title":
            assert document.count("※") == 1
        assert f'{attribute}="{title}"' in document
        assert prose in document
        assert re.search(r'<a id="node-1-5"[^>]*></a>※', document)
        assert ("Neighbor" if language == "english" else "隣") in document
        assert "```\nKEEP()\n```" in document
    assert load_source_snapshot(workspace).model_dump_json() == before
    assert load_translation(workspace).model_dump_json() == translation_before


def test_epub_untranslated_attribute_marker_stays_outside_value_and_inside_list_item(
    tmp_path,
):
    from doc_translate.render import render_document

    workspace = epub_workspace(
        tmp_path,
        '<html xmlns="http://www.w3.org/1999/xhtml"><head/><body><ol><li title="Item title">Parent<ul><li>Child</li></ul></li></ol></body></html>',
    )
    result = render_document(workspace)
    for language in ("english", "japanese"):
        document = artifact(workspace, result, language)
        assert (
            '1. <a id="node-1-5" title="Item title"></a>※Parent※\n\n    - <a id="node-1-7"></a>Child※'
            in document
        )
    qa = json.loads(artifact(workspace, result, "qa"))
    assert any(
        item["reason"] == "Translation unavailable"
        and item["target_ids"] == ["unit-2"]
        and item["output_anchors"] == ["node-1-5"]
        for item in qa["uncertainties"]
    )
