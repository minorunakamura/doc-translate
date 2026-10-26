"""Artificial EPUB acquisition -> source snapshot -> durable public storage."""

import hashlib
from pathlib import Path
from typing import Any

import pytest
from test_epub import make_epub

from doc_translate.epub import read_epub
from doc_translate.snapshot import (
    SourceDocument,
    load_source_snapshot,
    save_source_snapshot,
)


def extract(path: Path):
    from doc_translate.epub_source import extract_epub_source

    return extract_epub_source(
        read_epub(path),
        document=SourceDocument(
            format="epub",
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            artifact="original/book.epub",
        ),
        snapshot_id="source-1",
    )


def nodes(tree: Any) -> list[Any]:
    return list(tree["nodes"].values())


@pytest.mark.parametrize("version", ["2.0", "3.0"])
def test_epub_source_retains_mixed_order_and_snapshot_bound_positions(
    tmp_path: Path, version: str
):
    path = make_epub(tmp_path, version=version)
    snapshot = extract(path)
    block = snapshot.blocks[0]
    assert block.text == "Original mixed text."
    assert block.locations[0].model_dump(exclude_none=True) == {
        "format": "epub",
        "resource_path": "OPS/text/ch.xhtml",
        "node_path": "/1",
        "node_snapshot": "source-1",
    }
    paragraph = block.structure["tree"]["nodes"]["/1/2/1"]
    assert paragraph["location"] == {
        "format": "epub",
        "resource_path": "OPS/text/ch.xhtml",
        "element_id": "p1",
        "node_path": "/1/2/1",
        "node_snapshot": "source-1",
    }
    assert paragraph["content"] == ["Original ", {"node_path": "/1/2/1/1"}, " text."]
    assert block.structure["tree"]["nodes"]["/1/2/1/1"] == {
        "tag": "{http://www.w3.org/1999/xhtml}em",
        "location": {
            "format": "epub",
            "resource_path": "OPS/text/ch.xhtml",
            "node_path": "/1/2/1/1",
            "node_snapshot": "source-1",
        },
        "role": "body",
        "disposition": "structured",
        "attributes": {},
        "content": ["mixed"],
    }
    assert snapshot.document.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    digest = save_source_snapshot(tmp_path / "workspace", snapshot)
    assert load_source_snapshot(tmp_path / "workspace", digest) == snapshot
    assert snapshot.content_reviews == []


def test_epub_source_preserves_nested_semantics_without_active_markup(tmp_path: Path):
    xhtml = b"""<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><head><title>Metadata</title></head><body><h2>Heading</h2><ol start="3"><li>Outer<ul><li>Inner</li></ul> tail</li></ol><table><caption>Caption</caption><tr><th rowspan="2" colspan="3" scope="col">A<em>B</em>C</th><td/></tr></table><pre><code>  x = 1\n\tprint(x)\n</code></pre><p style="display:none" onclick="run()">Before<script id="script">evil()<b>hidden</b></script>after<img alt="A diagram" title="Figure" onerror="run()"/><math xmlns="http://www.w3.org/1998/Math/MathML" id="formula"><mi>x</mi><annotation-xml><script>evil()</script></annotation-xml></math>end</p></body></html>"""
    snapshot = extract(make_epub(tmp_path, extra={"OPS/text/ch.xhtml": xhtml}))
    block = snapshot.blocks[0]
    assert (
        block.text
        == "HeadingOuterInner tailCaptionABC  x = 1\n\tprint(x)\nBeforeafterend"
    )
    tree_nodes = nodes(block.structure["tree"])
    assert [
        (node["tag"].split("}")[-1], node["attributes"])
        for node in tree_nodes
        if node["attributes"]
    ] == [
        ("ol", {"start": "3"}),
        ("th", {"rowspan": "2", "colspan": "3", "scope": "col"}),
        ("img", {"alt": "A diagram", "title": "Figure"}),
    ]
    assert [
        (node["tag"].split("}")[-1], node["disposition"]) for node in tree_nodes
    ] == [
        ("html", "structured"),
        ("head", "excluded"),
        ("title", "excluded"),
        ("body", "structured"),
        ("h2", "structured"),
        ("ol", "structured"),
        ("li", "structured"),
        ("ul", "structured"),
        ("li", "structured"),
        ("table", "structured"),
        ("caption", "structured"),
        ("tr", "structured"),
        ("th", "structured"),
        ("em", "structured"),
        ("td", "structured"),
        ("pre", "structured"),
        ("code", "structured"),
        ("p", "structured"),
        ("script", "inert"),
        ("b", "inert"),
        ("img", "structured"),
        ("math", "inert"),
        ("mi", "inert"),
        ("annotation-xml", "inert"),
        ("script", "inert"),
    ]
    table = block.structure["tree"]["nodes"]
    assert table["/1/2/2/1"]["content"] == [
        "Outer",
        {"node_path": "/1/2/2/1/1"},
        " tail",
    ]
    assert table["/1/2/2/1/1"]["content"] == [{"node_path": "/1/2/2/1/1/1"}]
    assert table["/1/2/3"]["content"] == [
        {"node_path": "/1/2/3/1"},
        {"node_path": "/1/2/3/2"},
    ]
    assert table["/1/2/3/2"]["content"] == [
        {"node_path": "/1/2/3/2/1"},
        {"node_path": "/1/2/3/2/2"},
    ]
    assert table["/1/2/3/2/1"]["content"] == ["A", {"node_path": "/1/2/3/2/1/1"}, "C"]
    assert table["/1/2/3/2/2"]["content"] == []
    assert table["/1/2/4/1"]["content"] == ["  x = 1\n\tprint(x)\n"]
    assert block.structure["coverage"] == {"structured": 17, "excluded": 2, "inert": 6}
    formula = next(
        node for node in tree_nodes if node["location"].get("element_id") == "formula"
    )
    assert formula["location"]["node_path"] == "/1/2/5/3"
    assert read_epub(tmp_path / "book.epub").resources["OPS/text/ch.xhtml"] == xhtml
    assert any("inert" in item.reason for item in snapshot.uncertainties)


def test_epub_source_uses_spine_then_reference_discovery_without_alias_duplicates(
    tmp_path: Path,
):
    main = b"""<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><body><p id="main">Main <a epub:type="noteref" href="../notes.xhtml?view=1#n%31">1</a></p></body></html>"""
    notes = b"""<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><body><aside epub:type="footnote" id="n1">Note <a href="text/ch.xhtml#main">back</a></aside></body></html>"""
    nav = b"""<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><body><nav epub:type="toc"><a href="text/ch.xhtml#main">Contents</a></nav></body></html>"""
    path = make_epub(
        tmp_path,
        manifest="""<item id="ch" href="text/ch.xhtml" media-type="application/xhtml+xml"/>
<item id="alias" href="text/ch.xhtml" media-type="application/xhtml+xml"/>
<item id="notes" href="notes.xhtml" media-type="application/xhtml+xml"/>
<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
<item id="second" href="second.xhtml" media-type="application/xhtml+xml"/>
<item id="unsupported" href="raw.bin" media-type="application/octet-stream" fallback="ch"/>""",
        spine='<itemref idref="second"/><itemref idref="unsupported"/><itemref idref="nav" linear="no"/><itemref idref="alias" linear="no"/>',
        extra={
            "OPS/text/ch.xhtml": main,
            "OPS/notes.xhtml": notes,
            "OPS/nav.xhtml": nav,
            "OPS/second.xhtml": b'<html xmlns="http://www.w3.org/1999/xhtml"><body><h1>First</h1></body></html>',
            "OPS/raw.bin": b"original fallback data",
        },
    )
    snapshot = extract(path)
    assert [block.text for block in snapshot.blocks] == [
        "First",
        "Main 1",
        "Contents",
        "Note back",
    ]
    assert [block.locations[0].resource_path for block in snapshot.blocks] == [
        "OPS/second.xhtml",
        "OPS/text/ch.xhtml",
        "OPS/nav.xhtml",
        "OPS/notes.xhtml",
    ]
    assert snapshot.blocks[1].structure["spine"] == [
        {
            "index": 2,
            "idref": "unsupported",
            "resolved_id": "ch",
            "fallback_chain": ["unsupported", "ch"],
            "linear": True,
            "properties": [],
        },
        {
            "index": 4,
            "idref": "alias",
            "resolved_id": "alias",
            "fallback_chain": ["alias"],
            "linear": False,
            "properties": [],
        },
    ]
    assert [
        block.structure["tree"]["nodes"]["/1"]["role"] for block in snapshot.blocks
    ] == [
        "body",
        "body",
        "toc",
        "supplement",
    ]
    assert snapshot.blocks[3].structure["spine"] == []
    link = next(
        node
        for node in nodes(snapshot.blocks[1].structure["tree"])
        if node["tag"].endswith("}a")
    )
    assert link["references"] == [
        {
            "attribute": "href",
            "uri": "../notes.xhtml?view=1#n%31",
            "resource_path": "OPS/notes.xhtml",
            "fragment": "n1",
            "query": "view=1",
            "external_uri": None,
            "status": "resolved",
            "targets": [
                {
                    "location": {
                        "format": "epub",
                        "resource_path": "OPS/notes.xhtml",
                        "node_path": "/1/1/1",
                        "node_snapshot": "source-1",
                    },
                    "disposition": "structured",
                }
            ],
        }
    ]
    assert snapshot.uncertainties == []


@pytest.mark.parametrize(
    "note_type",
    ["footnote", "endnote", "rearnote", "footnotes", "endnotes", "rearnotes"],
)
def test_epub_source_inherits_explicit_note_roles_and_preserves_toc_precedence(
    tmp_path: Path, note_type: str
):
    xhtml = f'''<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><body><section epub:type="{note_type}"><p id="note">An endnote</p><nav epub:type="toc {note_type}"><a href="#note">Notes contents</a></nav></section><p>Body</p></body></html>'''.encode()
    snapshot = extract(make_epub(tmp_path, extra={"OPS/text/ch.xhtml": xhtml}))
    assert [
        (node["location"]["node_path"], node["role"])
        for node in nodes(snapshot.blocks[0].structure["tree"])
    ] == [
        ("/1", "body"),
        ("/1/1", "body"),
        ("/1/1/1", "supplement"),
        ("/1/1/1/1", "supplement"),
        ("/1/1/1/2", "toc"),
        ("/1/1/1/2/1", "toc"),
        ("/1/1/2", "body"),
    ]
    assert snapshot.blocks[0].text == "An endnoteNotes contentsBody"
    assert snapshot.uncertainties == []
    digest = save_source_snapshot(tmp_path / "workspace", snapshot)
    assert load_source_snapshot(tmp_path / "workspace", digest) == snapshot


def test_epub_source_audits_missing_ambiguous_inert_and_empty_targets(tmp_path: Path):
    main = b"""<html xmlns="http://www.w3.org/1999/xhtml"><head id="metadata"><title>Title</title></head><body><p id="dup">One</p><p id="dup">Two</p><p xml:id="xml-original">Three</p><script id="active">run()</script><p><a href="#dup">ambiguous</a><a href="#absent">missing</a><a href="#metadata">excluded</a><a href="#active">inert</a><a href="#xml-original">xml id</a><a href="../empty.xhtml">empty</a><a href="../drawing.svg#shape">svg</a><img src="../image.png" alt="Original alt"/><a href="../lost.xhtml">lost</a><a href="../../../escape">unsafe</a><a href="https://example.invalid/a?x=1#f">external</a><a href="javascript:run()">active URI</a></p></body></html>"""
    path = make_epub(
        tmp_path,
        extra={
            "OPS/text/ch.xhtml": main,
            "OPS/empty.xhtml": b'<html xmlns="http://www.w3.org/1999/xhtml"><body/></html>',
            "OPS/drawing.svg": b'<svg xmlns="http://www.w3.org/2000/svg"><script id="shape">evil()</script></svg>',
            "OPS/image.png": b"inert artificial image bytes",
            "OPS/unused.bin": b"",
        },
    )
    snapshot = extract(path)
    assert [block.text for block in snapshot.blocks] == [
        "OneTwoThreeambiguousmissingexcludedinertxml idemptysvglostunsafeexternalactive URI",
        "",
    ]
    tree_nodes = nodes(snapshot.blocks[0].structure["tree"])
    references = [ref for node in tree_nodes for ref in node.get("references", [])]
    assert [ref["status"] for ref in references] == [
        "ambiguous_fragment",
        "missing_fragment",
        "resolved",
        "resolved",
        "resolved",
        "resolved",
        "inert_resource",
        "inert_resource",
        "unresolved",
        "unresolved",
        "external",
        "external",
    ]
    assert [
        target["location"]["node_path"]
        for target in snapshot.blocks[0].structure["target_index"]["dup"]
    ] == [
        "/1/2/1",
        "/1/2/2",
    ]
    assert references[2]["targets"][0]["disposition"] == "excluded"
    assert references[3]["targets"][0]["disposition"] == "inert"
    assert references[4]["targets"][0]["location"] == {
        "format": "epub",
        "resource_path": "OPS/text/ch.xhtml",
        "node_path": "/1/2/3",
        "node_snapshot": "source-1",
    }
    assert [
        node["location"].get("element_id")
        for node in tree_nodes
        if node["tag"].endswith("}p")
    ] == ["dup", "dup", "xml-original", None]
    reasons = [item.reason for item in snapshot.uncertainties]
    assert any("Duplicate original ID: dup" in reason for reason in reasons)
    assert any("missing_fragment" in reason for reason in reasons)
    assert any("ambiguous_fragment" in reason for reason in reasons)
    assert any("Missing archive resource" in reason for reason in reasons)
    assert any("escapes archive" in reason for reason in reasons)
    assert any("inert resource" in reason for reason in reasons)
    resources = snapshot.blocks[0].structure["resources"]
    assert [
        (item["resource_path"], item["disposition"], item["byte_length"])
        for item in resources
        if item["resource_path"] in {"OPS/empty.xhtml", "OPS/unused.bin"}
    ] == [
        ("OPS/empty.xhtml", "structured", 57),
        ("OPS/unused.bin", "unvisited", 0),
    ]
    assert [
        (node["tag"].split("}")[-1], node["disposition"])
        for node in nodes(snapshot.blocks[1].structure["tree"])
    ] == [("html", "structured"), ("body", "structured")]
    assert snapshot.checks[0].name == "epub_node_coverage"
    assert snapshot.checks[0].passed
    assert snapshot.processing.status == "completed"
    digest = save_source_snapshot(tmp_path / "workspace", snapshot)
    assert load_source_snapshot(tmp_path / "workspace", digest) == snapshot


def test_epub_source_keeps_unrenderable_reference_inert_without_rejecting_book(
    tmp_path: Path,
):
    path = make_epub(
        tmp_path,
        extra={
            "OPS/text/ch.xhtml": b'<html xmlns="http://www.w3.org/1999/xhtml"><head><body>not body content</body></head><body><p>Read <a href="../odd.xhtml#target">original</a></p></body></html>',
            "OPS/odd.xhtml": b'<div xmlns="http://www.w3.org/1999/xhtml" id="target"><body>unrenderable</body></div>',
        },
    )
    snapshot = extract(path)
    assert [block.text for block in snapshot.blocks] == ["Read original", ""]
    assert [
        (node["tag"].split("}")[-1], node["disposition"])
        for node in nodes(snapshot.blocks[1].structure["tree"])
    ] == [("div", "inert"), ("body", "inert")]
    assert any("html/body" in item.reason for item in snapshot.uncertainties)
    assert (
        next(
            resource
            for resource in snapshot.blocks[0].structure["resources"]
            if resource["resource_path"] == "OPS/odd.xhtml"
        )["disposition"]
        == "inert"
    )
    assert snapshot.processing.status == "completed"


def test_epub_source_bounds_repeated_target_locations_with_long_original_id(
    tmp_path: Path,
):
    original_id = "i" * 65_536
    xhtml = (
        f'<html xmlns="http://www.w3.org/1999/xhtml" id="{original_id}"><body>'
        + '<a href="">go</a>' * 128
        + "</body></html>"
    ).encode()
    path = make_epub(tmp_path, extra={"OPS/text/ch.xhtml": xhtml})
    original_archive = path.read_bytes()
    snapshot = extract(path)
    workspace = tmp_path / "workspace"
    digest = save_source_snapshot(workspace, snapshot)
    serialized = (workspace / "snapshots" / "source" / f"{digest}.json").read_bytes()
    # Incoming links must not each serialize another copy of the 64 KiB root ID.
    assert len(serialized) < 512 * 1024
    loaded = load_source_snapshot(workspace, digest)
    assert loaded == snapshot
    tree: Any = loaded.blocks[0].structure["tree"]
    table = tree["nodes"]
    assert table["/1"]["original_ids"] == {"id": original_id}
    assert table["/1"]["location"]["element_id"] == original_id
    links = [node for node in table.values() if node["tag"].endswith("}a")]
    assert len(links) == 128
    for node in links:
        reference = node["references"][0]
        assert reference["status"] == "resolved"
        assert reference["targets"] == [
            {
                "location": {
                    "format": "epub",
                    "resource_path": "OPS/text/ch.xhtml",
                    "node_path": "/1",
                    "node_snapshot": "source-1",
                },
                "disposition": "structured",
            }
        ]
    assert loaded.uncertainties == []
    assert read_epub(path).resources["OPS/text/ch.xhtml"] == xhtml
    assert path.read_bytes() == original_archive


def test_epub_source_indexes_ambiguous_targets_once_for_repeated_links(tmp_path: Path):
    path = make_epub(
        tmp_path,
        extra={
            "OPS/text/ch.xhtml": b'<html xmlns="http://www.w3.org/1999/xhtml"><body><p id="same">A</p><p id="same">B</p><a href="#same">one</a><a href="#same">two</a></body></html>',
        },
    )
    snapshot = extract(path)
    links = [
        node
        for node in nodes(snapshot.blocks[0].structure["tree"])
        if node["tag"].endswith("}a")
    ]
    assert [node["references"][0].get("target_index") for node in links] == [
        {"resource_path": "OPS/text/ch.xhtml", "element_id": "same"},
        {"resource_path": "OPS/text/ch.xhtml", "element_id": "same"},
    ]
    assert [node["references"][0]["targets"] for node in links] == [[], []]
    assert snapshot.blocks[0].structure["target_index"]["same"] == [
        {
            "location": {
                "format": "epub",
                "resource_path": "OPS/text/ch.xhtml",
                "node_path": "/1/1/1",
                "node_snapshot": "source-1",
            },
            "disposition": "structured",
        },
        {
            "location": {
                "format": "epub",
                "resource_path": "OPS/text/ch.xhtml",
                "node_path": "/1/1/2",
                "node_snapshot": "source-1",
            },
            "disposition": "structured",
        },
    ]
    digest = save_source_snapshot(tmp_path / "workspace", snapshot)
    assert load_source_snapshot(tmp_path / "workspace", digest) == snapshot


def test_epub_source_uses_explicit_original_provenance_and_is_deterministic(
    tmp_path: Path,
):
    from doc_translate.epub_source import extract_epub_source

    path = make_epub(tmp_path)
    original = path.read_bytes()
    book = read_epub(path)
    document = SourceDocument(
        format="epub", sha256="a" * 64, artifact="caller-owned/original.epub"
    )
    first = extract_epub_source(book, document=document, snapshot_id="caller-snapshot")
    assert first == extract_epub_source(
        book, document=document, snapshot_id="caller-snapshot"
    )
    assert first.document == document
    location = first.blocks[0].locations[0]
    assert location.format == "epub"
    assert location.node_snapshot == "caller-snapshot"
    assert path.read_bytes() == original
    with pytest.raises(ValueError, match="EPUB document provenance"):
        extract_epub_source(
            book,
            document=SourceDocument(
                format="pdf", sha256="a" * 64, artifact="original.pdf"
            ),
            snapshot_id="caller-snapshot",
        )


def test_epub_source_round_trips_reader_maximum_xml_depth(tmp_path: Path):
    path = make_epub(
        tmp_path,
        extra={
            "OPS/text/ch.xhtml": (
                b'<html xmlns="http://www.w3.org/1999/xhtml"><body>'
                + b"<div>" * 126
                + b"Deep"
                + b"</div>" * 126
                + b"</body></html>"
            ),
        },
    )
    snapshot = extract(path)
    assert snapshot.blocks[0].text == "Deep"
    assert snapshot.blocks[0].structure["coverage"] == {
        "structured": 128,
        "excluded": 0,
        "inert": 0,
    }
    digest = save_source_snapshot(tmp_path / "workspace", snapshot)
    assert load_source_snapshot(tmp_path / "workspace", digest) == snapshot
