"""Artificial archives tested only through the public EPUB reader boundary."""

from pathlib import Path
from typing import TypedDict
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

import pytest

from doc_translate.epub import read_epub

CONTAINER = b"""<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">
<rootfiles><rootfile full-path="OPS/book.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>"""
CHAPTER = b"""<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Example</title></head>
<body><p id="p1">Original <em>mixed</em> text.</p></body></html>"""


class EPUBOptions(TypedDict, total=False):
    manifest: str
    spine: str
    metadata: str
    version: str
    extra: dict[str, bytes]


def make_epub(
    tmp_path: Path,
    *,
    manifest: str = '<item id="ch" href="text/ch.xhtml" media-type="application/xhtml+xml"/>',
    spine: str = '<itemref idref="ch"/>',
    metadata: str = "",
    version: str = "3.0",
    extra: dict[str, bytes] | None = None,
) -> Path:
    path = tmp_path / "book.epub"
    package = f'''<package xmlns="http://www.idpf.org/2007/opf" version="{version}">
<metadata>{metadata}</metadata><manifest>{manifest}</manifest><spine>{spine}</spine></package>'''
    members = {
        "mimetype": b"application/epub+zip",
        "META-INF/container.xml": CONTAINER,
        "OPS/book.opf": package.encode(),
        "OPS/text/ch.xhtml": CHAPTER,
    }
    members.update(extra or {})
    with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return path


@pytest.mark.parametrize("version", ["2.0", "3.0"])
def test_read_epub_preserves_resources_and_spine_order(tmp_path: Path, version: str):
    path = make_epub(
        tmp_path,
        version=version,
        manifest="""<item id="ch" href="text/ch.xhtml" media-type="application/xhtml+xml"/>
<item id="notes" href="notes.xhtml" media-type="application/xhtml+xml"/>
<item id="other" href="other.xhtml" media-type="application/xhtml+xml"/>""",
        spine='<itemref idref="notes" linear="no"/><itemref idref="ch"/>',
        extra={"OPS/notes.xhtml": CHAPTER, "OPS/other.xhtml": CHAPTER},
    )
    original = path.read_bytes()
    book = read_epub(path)
    assert book.package_path == "OPS/book.opf"
    assert list(book.manifest) == ["ch", "notes", "other"]
    assert [(item.idref, item.resolved_id, item.linear) for item in book.spine] == [
        ("notes", "notes", False),
        ("ch", "ch", True),
    ]
    assert book.resources["OPS/text/ch.xhtml"] == CHAPTER
    assert book.resources["OPS/other.xhtml"] == CHAPTER
    assert path.read_bytes() == original


@pytest.mark.parametrize(
    "name",
    [
        "../escape",
        "/absolute",
        "C:/drive",
        "OPS/../alias",
        "OPS//alias",
        "OPS/./alias",
        "OPS\\alias",
        "bad\x00name",
    ],
)
def test_read_epub_rejects_unsafe_or_ambiguous_member_paths(tmp_path: Path, name: str):
    from doc_translate.epub import EPUBError

    path = make_epub(tmp_path)
    # zipfile's writer truncates NUL too; patch both stored names to exercise input.
    with ZipFile(path, "a") as archive:
        archive.writestr(name.replace("\x00", "!"), b"untrusted")
    if "\x00" in name:
        path.write_bytes(path.read_bytes().replace(b"bad!name", b"bad\x00name"))
    with pytest.raises(EPUBError, match="path"):
        read_epub(path)


@pytest.mark.parametrize(
    "kind", ["duplicate", "unicode-alias", "file-directory", "symlink"]
)
def test_read_epub_rejects_colliding_paths_and_symlinks(tmp_path: Path, kind: str):
    from zipfile import ZipInfo

    from doc_translate.epub import EPUBError

    path = make_epub(tmp_path)
    with ZipFile(path, "a") as archive:
        if kind == "duplicate":
            with pytest.warns(UserWarning, match="Duplicate name"):
                archive.writestr("OPS/text/ch.xhtml", CHAPTER)
        elif kind == "unicode-alias":
            archive.writestr("OPS/caf\u00e9", b"one")
            archive.writestr("OPS/cafe\u0301", b"two")
        elif kind == "file-directory":
            archive.writestr("OPS/text", b"not a directory")
        else:
            info = ZipInfo("OPS/link")
            info.create_system = 3
            info.external_attr = 0o120777 << 16
            archive.writestr(info, b"../../outside")
    with pytest.raises(EPUBError, match="path|symlink"):
        read_epub(path)


@pytest.mark.parametrize(
    "kind", ["not-zip", "unsupported-compression", "encrypted", "corrupt"]
)
def test_read_epub_rejects_invalid_zip(tmp_path: Path, kind: str):
    import struct

    from doc_translate.epub import EPUBError

    path = make_epub(tmp_path)
    data = bytearray(path.read_bytes())
    if kind == "not-zip":
        data = bytearray(b"not an archive")
    elif kind == "corrupt":
        # Damage the first member's compressed stream, preserving ZIP headers.
        offset = (
            30
            + struct.unpack_from("<H", data, 26)[0]
            + struct.unpack_from("<H", data, 28)[0]
        )
        data[offset] ^= 255
    else:
        central = data.index(b"PK\x01\x02")
        if kind == "encrypted":
            struct.pack_into("<H", data, 6, 1)
            struct.pack_into("<H", data, central + 8, 1)
        else:
            struct.pack_into("<H", data, 8, 99)
            struct.pack_into("<H", data, central + 10, 99)
    path.write_bytes(data)
    with pytest.raises(EPUBError, match="ZIP|compression|encrypt"):
        read_epub(path)


@pytest.mark.parametrize(
    "limit", ["members", "member-bytes", "total-bytes", "archive-bytes"]
)
def test_read_epub_enforces_finite_zip_limits(tmp_path: Path, limit: str):
    from doc_translate.epub import (
        MAX_ARCHIVE_BYTES,
        MAX_MEMBER_BYTES,
        MAX_MEMBERS,
        MAX_TOTAL_BYTES,
        EPUBError,
    )

    path = make_epub(tmp_path)
    if limit == "archive-bytes":
        with path.open("r+b") as stream:
            stream.truncate(MAX_ARCHIVE_BYTES + 1)
    else:
        with ZipFile(path, "a", compression=ZIP_DEFLATED) as archive:
            if limit == "members":
                for i in range(MAX_MEMBERS):
                    archive.writestr(f"unused/{i}", b"")
            elif limit == "member-bytes":
                archive.writestr("unused/large", b"x" * (MAX_MEMBER_BYTES + 1))
            else:
                for i in range(MAX_TOTAL_BYTES // MAX_MEMBER_BYTES + 1):
                    archive.writestr(f"unused/{i}", b"x" * MAX_MEMBER_BYTES)
    with pytest.raises(EPUBError, match="limit"):
        read_epub(path)


@pytest.mark.parametrize(
    "kind", ["malformed", "entity", "external-entity", "depth", "nodes"]
)
def test_read_epub_validates_xhtml_xml_with_limits(tmp_path: Path, kind: str):
    from doc_translate.epub import MAX_XML_DEPTH, MAX_XML_NODES, EPUBError

    if kind == "malformed":
        data = b"<html><broken></html>"
    elif kind == "entity":
        data = b'<!DOCTYPE html [<!ENTITY text "expanded">]><html>&text;</html>'
    elif kind == "external-entity":
        data = b'<!DOCTYPE html [<!ENTITY text SYSTEM "file:///never-read">]><html>&text;</html>'
    elif kind == "depth":
        data = b"<n>" * (MAX_XML_DEPTH + 1) + b"</n>" * (MAX_XML_DEPTH + 1)
    else:
        data = b"<html>" + b"<n/>" * MAX_XML_NODES + b"</html>"
    path = make_epub(tmp_path, extra={"OPS/text/ch.xhtml": data})
    with pytest.raises(EPUBError, match="XML"):
        read_epub(path)


@pytest.mark.parametrize("limit", ["depth", "nodes"])
def test_read_epub_stops_at_xml_structure_limit_before_malformed_tail(
    tmp_path: Path, limit: str
):
    from doc_translate.epub import EPUBError

    if limit == "depth":
        data = b"<n>" * 129 + b"</n>" * 129 + b"<"
    else:
        data = b"<r>" + b"<n/>" * 100_000 + b"</r><"
    path = make_epub(tmp_path, extra={"OPS/unused.xml": data})
    with pytest.raises(EPUBError, match="XML depth/node limit exceeded"):
        read_epub(path)


@pytest.mark.parametrize(
    "doctype",
    [
        b"<!DOCTYPE html>",
        b'<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.1//EN" "https://example.invalid/xhtml.dtd">',
    ],
)
def test_read_epub_accepts_inert_doctype_and_keeps_active_markup_as_bytes(
    tmp_path: Path, doctype: bytes
):
    data = doctype + CHAPTER.replace(
        b"</body>",
        b'<script src="https://example.invalid/no-fetch.js">never_execute()</script></body>',
    )
    path = make_epub(tmp_path, extra={"OPS/text/ch.xhtml": data})
    assert read_epub(path).resources["OPS/text/ch.xhtml"] == data


def test_read_epub_resolves_package_and_xhtml_relative_uris(tmp_path: Path):
    path = make_epub(
        tmp_path,
        manifest='<item id="ch" href="text/a%20b.xhtml#p%201" media-type="application/xhtml+xml"/>',
        extra={"OPS/text/a b.xhtml": CHAPTER, "OPS/image%20one.png": b"image"},
    )
    book = read_epub(path)
    assert book.manifest["ch"].resource_path == "OPS/text/a b.xhtml"
    assert book.manifest["ch"].fragment == "p 1"
    ref = book.resolve("OPS/text/a b.xhtml", "../image%2520one.png#part%202")
    assert (ref.resource_path, ref.fragment) == ("OPS/image%20one.png", "part 2")
    assert (
        book.resolve("OPS/text/a b.xhtml", "#p1").resource_path == "OPS/text/a b.xhtml"
    )
    external = book.resolve("OPS/text/a b.xhtml", "https://example.invalid/image.png#x")
    assert external.resource_path is None
    assert external.external_uri == "https://example.invalid/image.png#x"


@pytest.mark.parametrize(
    "uri",
    [
        "../../../outside",
        "%2fetc/passwd",
        "..%2f..%2f..%2foutside",
        "..%5csecret",
        "missing.xhtml",
        "bad%xx",
        "bad%00name",
    ],
)
def test_read_epub_resource_resolution_rejects_escape_or_invalid_uri(
    tmp_path: Path, uri: str
):
    from doc_translate.epub import EPUBError

    book = read_epub(make_epub(tmp_path))
    with pytest.raises(EPUBError, match="URI|resource|path"):
        book.resolve("OPS/text/ch.xhtml", uri)


def test_read_epub_follows_fallback_but_preserves_original_spine_identity(
    tmp_path: Path,
):
    path = make_epub(
        tmp_path,
        manifest="""<item id="art" href="page.svg" media-type="image/svg+xml" fallback="foreign"/>
<item id="foreign" href="page.bin" media-type="application/x-example" fallback="ch"/>
<item id="ch" href="text/ch.xhtml" media-type="application/xhtml+xml"/>""",
        spine='<itemref idref="art" linear="no"/>',
        extra={
            "OPS/page.svg": b'<svg xmlns="http://www.w3.org/2000/svg"/>',
            "OPS/page.bin": b"original",
        },
    )
    item = read_epub(path).spine[0]
    assert (item.idref, item.resolved_id, item.fallback_chain, item.linear) == (
        "art",
        "ch",
        ("art", "foreign", "ch"),
        False,
    )


@pytest.mark.parametrize(
    "kind",
    [
        "missing-container",
        "multiple-renditions",
        "missing-package",
        "version",
        "duplicate-id",
        "missing-idref",
        "missing-fallback",
        "fallback-cycle",
        "no-spine",
        "linear",
    ],
)
def test_read_epub_rejects_invalid_package_graph(tmp_path: Path, kind: str):
    from doc_translate.epub import EPUBError

    options: EPUBOptions = {}
    if kind == "missing-container":
        options["extra"] = {
            "META-INF/container.xml": b'<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"/>'
        }
    elif kind == "multiple-renditions":
        options["extra"] = {
            "META-INF/container.xml": CONTAINER.replace(
                b"</rootfiles>",
                b'<rootfile full-path="other.opf" media-type="application/oebps-package+xml"/></rootfiles>',
            )
        }
    elif kind == "missing-package":
        options["extra"] = {
            "META-INF/container.xml": CONTAINER.replace(b"OPS/book.opf", b"missing.opf")
        }
    elif kind == "version":
        options["version"] = "4.0"
    elif kind == "duplicate-id":
        options["manifest"] = (
            '<item id="ch" href="text/ch.xhtml" media-type="application/xhtml+xml"/>'
            * 2
        )
    elif kind == "missing-idref":
        options["spine"] = '<itemref idref="unknown"/>'
    elif kind == "missing-fallback":
        options["manifest"] = (
            '<item id="ch" href="text/ch.xhtml" media-type="application/xhtml+xml" fallback="unknown"/>'
        )
    elif kind == "fallback-cycle":
        options["manifest"] = (
            '<item id="ch" href="text/ch.xhtml" media-type="application/xhtml+xml" fallback="ch"/>'
        )
    elif kind == "no-spine":
        options["spine"] = ""
    else:
        options["spine"] = '<itemref idref="ch" linear="maybe"/>'
    path = make_epub(tmp_path, **options)
    with pytest.raises(EPUBError):
        read_epub(path)


@pytest.mark.parametrize(
    "kind",
    [
        "global-fixed",
        "item-fixed",
        "epub2-fixed",
        "dtbook",
        "image-spine",
        "foreign-spine",
        "remote-spine",
        "image-only",
    ],
)
def test_read_epub_stops_for_unsupported_layout_or_body(tmp_path: Path, kind: str):
    from doc_translate.epub import EPUBError

    options: EPUBOptions = {}
    if kind == "global-fixed":
        options["metadata"] = '<meta property="rendition:layout">pre-paginated</meta>'
    elif kind == "item-fixed":
        options["spine"] = (
            '<itemref idref="ch" properties="rendition:layout-pre-paginated"/>'
        )
    elif kind == "epub2-fixed":
        options["version"] = "2.0"
        options["metadata"] = '<meta name="fixed-layout" content="true"/>'
    elif kind == "image-only":
        options["extra"] = {
            "OPS/text/ch.xhtml": b"""<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><head><title>Not body text</title></head><body>
<script>not prose</script><style>not prose</style><nav epub:type="toc">Contents</nav><img src="scan.png" alt="not body text"/></body></html>"""
        }
    else:
        media = {
            "dtbook": "application/x-dtbook+xml",
            "image-spine": "image/png",
            "foreign-spine": "application/x-example",
            "remote-spine": "application/xhtml+xml",
        }[kind]
        href = (
            "https://example.invalid/ch.xhtml"
            if kind == "remote-spine"
            else "text/ch.xhtml"
        )
        options["manifest"] = f'<item id="ch" href="{href}" media-type="{media}"/>'
    path = make_epub(tmp_path, **options)
    with pytest.raises(EPUBError, match="fixed-layout|DTBook|image|Unsupported|remote"):
        read_epub(path)


@pytest.mark.parametrize(
    "algorithm",
    ["http://www.idpf.org/2008/embedding", "http://ns.adobe.com/pdf/enc#RC"],
)
def test_read_epub_keeps_obfuscated_font_without_requiring_it(
    tmp_path: Path, algorithm: str
):
    encryption = f'''<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container" xmlns:enc="http://www.w3.org/2001/04/xmlenc#">
<enc:EncryptedData><enc:EncryptionMethod Algorithm="{algorithm}"/>
<enc:CipherData><enc:CipherReference URI="OPS/fonts/unused.otf"/></enc:CipherData></enc:EncryptedData></encryption>'''.encode()
    path = make_epub(
        tmp_path,
        manifest="""<item id="ch" href="text/ch.xhtml" media-type="application/xhtml+xml"/>
<item id="font" href="fonts/unused.otf" media-type="font/otf"/>""",
        extra={
            "META-INF/encryption.xml": encryption,
            "OPS/fonts/unused.otf": b"obfuscated-font-bytes",
        },
    )
    book = read_epub(path)
    assert book.resources["OPS/fonts/unused.otf"] == b"obfuscated-font-bytes"
    assert book.encryption["OPS/fonts/unused.otf"] == algorithm
    assert book.resources["META-INF/encryption.xml"] == encryption
    assert book.spine[0].resolved_id == "ch"


@pytest.mark.parametrize(
    "target,algorithm",
    [
        ("OPS/text/ch.xhtml", "http://www.w3.org/2001/04/xmlenc#aes256-cbc"),
        ("OPS/text/ch.xhtml", "http://www.idpf.org/2008/embedding"),
        ("OPS/image.png", "http://www.w3.org/2001/04/xmlenc#aes256-cbc"),
        ("OPS/fonts/font.otf", "unknown-algorithm"),
        ("OPS/fonts/font.otf", ""),
        ("../../outside", "unknown"),
        ("OPS/missing", "unknown"),
    ],
)
def test_read_epub_rejects_unsupported_or_invalid_encryption(
    tmp_path: Path, target: str, algorithm: str
):
    from doc_translate.epub import EPUBError

    encryption = f'''<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container" xmlns:enc="http://www.w3.org/2001/04/xmlenc#">
<enc:EncryptedData><enc:EncryptionMethod Algorithm="{algorithm}"/>
<enc:CipherData><enc:CipherReference URI="{target}"/></enc:CipherData></enc:EncryptedData></encryption>'''.encode()
    path = make_epub(
        tmp_path,
        manifest="""<item id="ch" href="text/ch.xhtml" media-type="application/xhtml+xml"/>
<item id="font" href="fonts/font.otf" media-type="font/otf"/>
<item id="img" href="image.png" media-type="image/png"/>""",
        extra={
            "META-INF/encryption.xml": encryption,
            "OPS/fonts/font.otf": b"font",
            "OPS/image.png": b"image",
        },
    )
    with pytest.raises(EPUBError):
        read_epub(path)


def test_read_epub_accepts_image_cover_in_text_publication_and_keeps_unused_remote(
    tmp_path: Path,
):
    cover = b'<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Cover</title></head><body><img src="cover.png"/></body></html>'
    path = make_epub(
        tmp_path,
        manifest="""<item id="ch" href="text/ch.xhtml" media-type="application/xhtml+xml"/>
<item id="cover" href="cover.xhtml" media-type="application/xhtml+xml"/>
<item id="remote" href="https://example.invalid/unused.png" media-type="image/png"/>""",
        spine='<itemref idref="cover"/><itemref idref="ch"/>',
        extra={"OPS/cover.xhtml": cover},
    )
    book = read_epub(path)
    assert [item.idref for item in book.spine] == ["cover", "ch"]
    assert book.manifest["remote"].href == "https://example.invalid/unused.png"
    assert book.manifest["remote"].resource_path is None
    assert "https://example.invalid/unused.png" not in book.resources


def test_read_epub_uses_local_fallback_for_remote_spine_resource(tmp_path: Path):
    path = make_epub(
        tmp_path,
        manifest="""<item id="remote" href="https://example.invalid/body.xhtml" media-type="application/xhtml+xml" fallback="ch"/>
<item id="ch" href="text/ch.xhtml" media-type="application/xhtml+xml"/>""",
        spine='<itemref idref="remote"/>',
    )
    item = read_epub(path).spine[0]
    assert (item.idref, item.resolved_id, item.fallback_chain) == (
        "remote",
        "ch",
        ("remote", "ch"),
    )


def test_read_epub_does_not_treat_navigation_resource_as_image_body_text(
    tmp_path: Path,
):
    from doc_translate.epub import EPUBError

    path = make_epub(
        tmp_path,
        manifest="""<item id="ch" href="text/ch.xhtml" media-type="application/xhtml+xml"/>
<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>""",
        spine='<itemref idref="nav"/><itemref idref="ch"/>',
        extra={
            "OPS/nav.xhtml": CHAPTER,
            "OPS/text/ch.xhtml": b'<html xmlns="http://www.w3.org/1999/xhtml"><body><img src="scan.png"/></body></html>',
        },
    )
    with pytest.raises(EPUBError, match="image-only"):
        read_epub(path)


def test_read_epub_accepts_empty_encryption_metadata(tmp_path: Path):
    path = make_epub(
        tmp_path,
        extra={
            "META-INF/encryption.xml": b'<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container"/>'
        },
    )
    assert not read_epub(path).encryption


@pytest.mark.parametrize(
    "record",
    [
        "<enc:EncryptedData/>",
        '<enc:EncryptedData><enc:EncryptionMethod Algorithm="http://www.idpf.org/2008/embedding"/><enc:CipherData><enc:CipherReference URI="OPS/font.otf"/><enc:CipherReference URI="OPS/text/ch.xhtml"/></enc:CipherData></enc:EncryptedData>',
        '<EncryptedData><EncryptionMethod Algorithm="unknown"/><CipherData><CipherReference URI="OPS/font.otf"/></CipherData></EncryptedData>',
    ],
)
def test_read_epub_rejects_ambiguous_encryption_records(tmp_path: Path, record: str):
    from doc_translate.epub import EPUBError

    encryption = f'<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container" xmlns:enc="http://www.w3.org/2001/04/xmlenc#">{record}</encryption>'.encode()
    path = make_epub(
        tmp_path,
        manifest="""<item id="ch" href="text/ch.xhtml" media-type="application/xhtml+xml"/>
<item id="font" href="font.otf" media-type="font/otf"/>""",
        extra={"META-INF/encryption.xml": encryption, "OPS/font.otf": b"font"},
    )
    with pytest.raises(EPUBError, match="Encryption|encryption"):
        read_epub(path)


@pytest.mark.parametrize("kind", ["apple-display-options", "rendition-prefix-alias"])
def test_read_epub_rejects_explicit_fixed_layout_declarations(
    tmp_path: Path, kind: str
):
    from doc_translate.epub import EPUBError

    if kind == "apple-display-options":
        path = make_epub(
            tmp_path,
            version="2.0",
            extra={
                "META-INF/com.apple.ibooks.display-options.xml": b'<display_options><platform name="*"><option name="fixed-layout">true</option></platform></display_options>'
            },
        )
    else:
        path = make_epub(
            tmp_path, metadata='<meta property="layout:layout">pre-paginated</meta>'
        )
        # Alias is declared on the package rather than assuming a hard-coded spelling.
        with ZipFile(path) as archive:
            members = {info.filename: archive.read(info) for info in archive.infolist()}
        members["OPS/book.opf"] = members["OPS/book.opf"].replace(
            b"<package ",
            b'<package prefix="layout: http://www.idpf.org/vocab/rendition/#" ',
        )
        with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
            for name, data in members.items():
                archive.writestr(name, data)
    with pytest.raises(EPUBError, match="fixed-layout"):
        read_epub(path)


def test_read_epub_does_not_apply_font_exception_to_mixed_media_alias(tmp_path: Path):
    from doc_translate.epub import EPUBError

    encryption = b"""<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container" xmlns:enc="http://www.w3.org/2001/04/xmlenc#"><enc:EncryptedData><enc:EncryptionMethod Algorithm="http://www.idpf.org/2008/embedding"/><enc:CipherData><enc:CipherReference URI="OPS/text/ch.xhtml"/></enc:CipherData></enc:EncryptedData></encryption>"""
    path = make_epub(
        tmp_path,
        manifest="""<item id="ch" href="text/ch.xhtml" media-type="application/xhtml+xml"/>
<item id="font" href="text/ch.xhtml" media-type="font/otf"/>""",
        extra={"META-INF/encryption.xml": encryption},
    )
    with pytest.raises(EPUBError, match="algorithm/resource"):
        read_epub(path)


def test_read_epub_rejects_nonempty_directory_instead_of_discarding_bytes(
    tmp_path: Path,
):
    from doc_translate.epub import EPUBError

    path = make_epub(tmp_path)
    with ZipFile(path, "a") as archive:
        archive.writestr("OPS/extra/", b"hidden data")
    with pytest.raises(EPUBError, match="directory"):
        read_epub(path)


@pytest.mark.parametrize("size_delta", [-1, 1])
def test_read_epub_rejects_zip_declared_size_disagreement(
    tmp_path: Path, size_delta: int
):
    import struct

    from doc_translate.epub import EPUBError

    path = make_epub(tmp_path)
    data = bytearray(path.read_bytes())
    central = data.index(b"PK\x01\x02")
    struct.pack_into("<I", data, central + 24, 20 + size_delta)
    path.write_bytes(data)
    with pytest.raises(EPUBError, match="ZIP"):
        read_epub(path)


@pytest.mark.parametrize("boundary", ["xml-depth", "xml-nodes", "member-bytes"])
def test_read_epub_accepts_exact_limits(tmp_path: Path, boundary: str):
    from doc_translate.epub import MAX_MEMBER_BYTES, MAX_XML_DEPTH, MAX_XML_NODES

    if boundary == "xml-depth":
        extra = {"OPS/extra.xml": b"<n>" * MAX_XML_DEPTH + b"</n>" * MAX_XML_DEPTH}
    elif boundary == "xml-nodes":
        extra = {
            "OPS/extra.xml": b"<root>" + b"<n/>" * (MAX_XML_NODES - 1) + b"</root>"
        }
    else:
        extra = {"OPS/unused.bin": b"x" * MAX_MEMBER_BYTES}
    book = read_epub(make_epub(tmp_path, extra=extra))
    assert book.resources[next(iter(extra))] == next(iter(extra.values()))


@pytest.mark.parametrize(
    "kind", ["unknown-xml-encoding", "unsupported-xml-encoding", "invalid-zip-utf8"]
)
def test_read_epub_reports_invalid_encodings_as_input_errors(tmp_path: Path, kind: str):
    import struct

    from doc_translate.epub import EPUBError

    path = make_epub(tmp_path)
    if kind in {"unknown-xml-encoding", "unsupported-xml-encoding"}:
        encoding = "unknown" if kind == "unknown-xml-encoding" else "cp932"
        path = make_epub(
            tmp_path,
            extra={
                "OPS/text/ch.xhtml": f'<?xml version="1.0" encoding="{encoding}"?><html/>'.encode()
            },
        )
    else:
        data = bytearray(path.read_bytes())
        central = data.index(b"PK\x01\x02")
        struct.pack_into("<H", data, 6, 0x800)
        struct.pack_into("<H", data, central + 8, 0x800)
        data[30] = data[central + 46] = 0xFF
        path.write_bytes(data)
    with pytest.raises(EPUBError, match="XML|ZIP"):
        read_epub(path)


def test_read_epub_does_not_misclassify_mathml_text_as_image_only(tmp_path: Path):
    data = b"""<html xmlns="http://www.w3.org/1999/xhtml"><body><math xmlns="http://www.w3.org/1998/Math/MathML"><mtext>Original mathematical text</mtext></math><img src="figure.png"/></body></html>"""
    book = read_epub(make_epub(tmp_path, extra={"OPS/text/ch.xhtml": data}))
    assert book.resources["OPS/text/ch.xhtml"] == data


@pytest.mark.parametrize("spine_count", [100, 101])
def test_read_epub_bounds_total_spine_fallback_ids(tmp_path: Path, spine_count: int):
    from doc_translate.epub import EPUBError

    manifest = (
        "".join(
            f'<item id="f{i}" href="foreign.bin" media-type="application/x-example" fallback="{f"f{i + 1}" if i < 998 else "ch"}"/>'
            for i in range(999)
        )
        + '<item id="ch" href="text/ch.xhtml" media-type="application/xhtml+xml"/>'
    )
    path = make_epub(
        tmp_path,
        manifest=manifest,
        spine='<itemref idref="f0"/>' * spine_count,
        extra={"OPS/foreign.bin": b"original foreign resource"},
    )
    if spine_count == 101:
        with pytest.raises(EPUBError, match="fallback.*limit"):
            read_epub(path)
    else:
        book = read_epub(path)
        assert len(book.spine) == 100
        assert len(book.spine[-1].fallback_chain) == 1000
        assert book.spine[-1].resolved_id == "ch"


def test_read_epub_reuses_body_resource_across_navigation_alias_and_itemrefs(
    tmp_path: Path,
):
    chapter = CHAPTER.replace(b'<p id="p1">', b"<span/>" * 20_000 + b'<p id="p1">')
    path = make_epub(
        tmp_path,
        manifest="""<item id="nav" href="text/ch.xhtml" media-type="application/xhtml+xml" properties="nav"/>
<item id="ch" href="text/ch.xhtml" media-type="application/xhtml+xml"/>
<item id="cover" href="cover.xhtml" media-type="application/xhtml+xml"/>""",
        spine='<itemref idref="nav"/><itemref idref="cover"/>'
        + '<itemref idref="ch" linear="no"/>' * 200,
        extra={
            "OPS/text/ch.xhtml": chapter,
            "OPS/cover.xhtml": b'<html xmlns="http://www.w3.org/1999/xhtml"><body><img src="cover.png"/></body></html>',
        },
    )
    book = read_epub(path)
    assert len(book.spine) == 202
    assert [item.idref for item in book.spine[:3]] == ["nav", "cover", "ch"]
    assert book.spine[-1].resolved_id == "ch"
    assert book.spine[-1].linear is False
    assert book.resources["OPS/text/ch.xhtml"] == chapter


def test_read_epub_rejects_namespace_name_amplification(tmp_path: Path):
    from doc_translate.epub import EPUBError

    data = (
        b'<r xmlns:a="'
        + b"u" * 4096
        + b'">'
        + b"".join(f"<a:n{i}/>".encode() for i in range(4000))
        + b"</r>"
    )
    path = make_epub(tmp_path, extra={"OPS/unused.xml": data})
    with pytest.raises(EPUBError, match="XML.*name.*limit"):
        read_epub(path)


def test_read_epub_rejects_single_tag_namespace_attribute_amplification(tmp_path: Path):
    from doc_translate.epub import EPUBError

    data = (
        b'<r xmlns:a="'
        + b"u" * 4000
        + b'" '
        + b" ".join(f'a:n{i}=""'.encode() for i in range(1024))
        + b"/>"
    )
    path = make_epub(tmp_path, extra={"OPS/unused.xml": data})
    with pytest.raises(EPUBError, match="XML.*attribute.*limit"):
        read_epub(path)


@pytest.mark.parametrize(
    "kind",
    [
        "namespace",
        "element",
        "attribute",
        "expanded-attribute",
        "total-elements",
        "total-attributes",
    ],
)
def test_read_epub_bounds_xml_names_before_building_dom(tmp_path: Path, kind: str):
    from doc_translate.epub import EPUBError

    if kind == "namespace":
        data = b'<r xmlns:a="' + b"u" * 4097 + b'"/>'
    elif kind == "element":
        data = b"<" + b"n" * 4097 + b"/>"
    elif kind == "attribute":
        data = b"<r " + b"n" * 4097 + b'=""/>'
    elif kind == "expanded-attribute":
        data = b'<r xmlns:a="' + b"u" * 4093 + b'" a:long=""/>'
    else:
        child = b"<a:n/>" if kind == "total-elements" else b'<n a:attr=""/>'
        data = b'<r xmlns:a="' + b"u" * 1024 + b'">' + child * 8200 + b"</r>"
    path = make_epub(tmp_path, extra={"OPS/unused.xml": data})
    with pytest.raises(EPUBError, match="XML.*name.*limit"):
        read_epub(path)


def test_read_epub_accepts_bounded_namespace_attributes(tmp_path: Path):
    data = (
        b'<r xmlns:a="'
        + b"u" * 4000
        + b'" '
        + b" ".join(f'a:n{i}=""'.encode() for i in range(1023))
        + b"/>"
    )
    book = read_epub(make_epub(tmp_path, extra={"OPS/unused.xml": data}))
    assert book.resources["OPS/unused.xml"] == data


def test_read_epub_preserves_deep_zip_paths_without_depth_rejection(tmp_path: Path):
    prefix = "a/" * 4096
    path = make_epub(tmp_path, extra={prefix + "one": b"one", prefix + "two": b"two"})
    book = read_epub(path)
    assert book.resources[prefix + "one"] == b"one"
    assert book.resources[prefix + "two"] == b"two"


@pytest.mark.parametrize(
    "kind", ["implicit", "explicit", "unicode", "boundary-sibling"]
)
def test_read_epub_checks_sorted_path_ancestor_boundaries(tmp_path: Path, kind: str):
    from doc_translate.epub import EPUBError

    extra = {"OPS/part": b"file", "OPS/part-sibling": b"sibling"}
    if kind == "unicode":
        extra = {"OPS/caf\u00e9": b"file", "OPS/cafe\u0301/ch": b"child"}
    elif kind == "explicit":
        extra["OPS/part/ch/"] = b""
    elif kind == "implicit":
        extra["OPS/part/ch"] = b"child"
    path = make_epub(tmp_path, extra=extra)
    if kind == "boundary-sibling":
        assert read_epub(path).resources["OPS/part-sibling"] == b"sibling"
    else:
        with pytest.raises(EPUBError, match="path collision"):
            read_epub(path)


@pytest.mark.parametrize(
    "compression", [ZIP_STORED, ZIP_DEFLATED], ids=["stored", "deflate"]
)
def test_read_epub_rejects_zip_size_truncation_with_matching_prefix_crc(
    tmp_path: Path, compression: int
):
    import struct
    import zlib

    from doc_translate.epub import EPUBError

    path = make_epub(tmp_path)
    with ZipFile(path, "a", compression=compression) as archive:
        archive.writestr("unused.bin", b"AB")
        local = archive.getinfo("unused.bin").header_offset
    data = bytearray(path.read_bytes())
    central = data.rindex(b"PK\x01\x02")
    struct.pack_into("<I", data, local + 14, zlib.crc32(b"A"))
    struct.pack_into("<I", data, local + 22, 1)
    struct.pack_into("<I", data, central + 16, zlib.crc32(b"A"))
    struct.pack_into("<I", data, central + 24, 1)
    path.write_bytes(data)
    with pytest.raises(EPUBError, match="ZIP"):
        read_epub(path)


def test_read_epub_rejects_unfinished_deflate_with_complete_output(tmp_path: Path):
    import struct

    from doc_translate.epub import EPUBError

    path = make_epub(tmp_path)
    with ZipFile(path, "a", compression=ZIP_DEFLATED) as archive:
        archive.writestr("unused.bin", b"AB")
        local = archive.getinfo("unused.bin").header_offset
    data = bytearray(path.read_bytes())
    name_size, extra_size = struct.unpack_from("<HH", data, local + 26)
    # Clear BFINAL without changing the output or CRC of the tiny fixed-Huffman block.
    data[local + 30 + name_size + extra_size] &= 0xFE
    path.write_bytes(data)
    with pytest.raises(EPUBError, match="ZIP.*stream end"):
        read_epub(path)


@pytest.mark.parametrize(
    "compression", [ZIP_STORED, ZIP_DEFLATED], ids=["stored", "deflate"]
)
def test_read_epub_accepts_data_descriptors_and_empty_members(
    tmp_path: Path, compression: int
):
    from io import BytesIO, UnsupportedOperation

    class NonSeekable(BytesIO):
        def seek(self, offset: int, whence: int = 0) -> int:
            raise UnsupportedOperation("streaming ZIP writer")

    path = make_epub(tmp_path)
    with ZipFile(path) as archive:
        members = {info.filename: archive.read(info) for info in archive.infolist()}
    members.update({"unused.bin": b"AB" * 65_537, "empty.bin": b""})
    output = NonSeekable()
    with ZipFile(output, "w", compression=compression) as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    path.write_bytes(output.getvalue())
    with ZipFile(path) as archive:
        assert all(info.flag_bits & 8 for info in archive.infolist())
    book = read_epub(path)
    assert dict(book.resources) == members


@pytest.mark.parametrize("mixed_media", [False, True])
def test_read_epub_checks_multiple_encrypted_fonts_with_manifest_aliases(
    tmp_path: Path, mixed_media: bool
):
    from doc_translate.epub import EPUBError

    manifest = [
        '<item id="ch" href="text/ch.xhtml" media-type="application/xhtml+xml"/>'
    ]
    records = []
    extra = {}
    for font in range(32):
        target = f"OPS/fonts/{font}.otf"
        extra[target] = f"obfuscated-font-{font}".encode()
        for alias in range(8):
            media_type = "font/otf" if alias % 2 else "application/vnd.ms-opentype"
            if mixed_media and font == 31 and alias == 7:
                media_type = "application/octet-stream"
            manifest.append(
                f'<item id="font{font}-{alias}" href="fonts/{font}.otf" media-type="{media_type}"/>'
            )
        records.append(
            f'<enc:EncryptedData><enc:EncryptionMethod Algorithm="http://www.idpf.org/2008/embedding"/>'
            f'<enc:CipherData><enc:CipherReference URI="{target}"/></enc:CipherData></enc:EncryptedData>'
        )
    extra["META-INF/encryption.xml"] = (
        '<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container" xmlns:enc="http://www.w3.org/2001/04/xmlenc#">'
        + "".join(records)
        + "</encryption>"
    ).encode()
    path = make_epub(tmp_path, manifest="".join(manifest), extra=extra)
    if mixed_media:
        with pytest.raises(EPUBError, match="algorithm/resource"):
            read_epub(path)
    else:
        book = read_epub(path)
        assert len(book.encryption) == 32
        assert len(book.manifest) == 257
        assert book.resources["OPS/fonts/31.otf"] == b"obfuscated-font-31"
        assert book.manifest["font31-7"].resource_path == "OPS/fonts/31.otf"
        assert (
            book.encryption["OPS/fonts/31.otf"] == "http://www.idpf.org/2008/embedding"
        )
