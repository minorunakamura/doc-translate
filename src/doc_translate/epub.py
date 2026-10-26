"""Local EPUB acceptance boundary; no extraction, execution, or external fetch."""

import os
import re
import stat
import struct
import unicodedata
import zlib
from collections.abc import Mapping
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from types import MappingProxyType
from typing import BinaryIO
from urllib.parse import unquote, urlsplit
from xml.etree.ElementTree import Element, ParseError, TreeBuilder
from xml.sax import SAXException
from xml.sax.handler import ContentHandler, feature_external_ges, feature_namespaces
from xml.sax.xmlreader import AttributesImpl
from zipfile import ZIP_DEFLATED, ZIP_STORED, BadZipFile, ZipFile, ZipInfo

from defusedxml import ElementTree as SafeET
from defusedxml import sax as SafeSAX
from defusedxml.common import DefusedXmlException

MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
MAX_MEMBERS = 4096
MAX_MEMBER_BYTES = 32 * 1024 * 1024
MAX_TOTAL_BYTES = 256 * 1024 * 1024
MAX_XML_DEPTH = 128
MAX_XML_NODES = 100_000
MAX_XML_NAME_CHARS = 4096
MAX_XML_TOTAL_NAME_CHARS = 8 * 1024 * 1024
MAX_XML_ATTRIBUTES = 1024
MAX_SPINE_FALLBACK_IDS = 100_000

XHTML = "{http://www.w3.org/1999/xhtml}"
EPUB_TYPE = "{http://www.idpf.org/2007/ops}type"

OPF = "{http://www.idpf.org/2007/opf}"
CONTAINER = "{urn:oasis:names:tc:opendocument:xmlns:container}"


class EPUBError(ValueError):
    """The archive cannot be safely read within the supported EPUB scope."""


@dataclass(frozen=True)
class ResourceReference:
    resource_path: str | None
    fragment: str | None
    query: str
    external_uri: str | None = None


@dataclass(frozen=True)
class ManifestItem:
    id: str
    href: str
    resource_path: str | None
    fragment: str | None
    media_type: str
    fallback: str | None
    properties: tuple[str, ...]


@dataclass(frozen=True)
class SpineItem:
    idref: str
    resolved_id: str
    fallback_chain: tuple[str, ...]
    linear: bool
    properties: tuple[str, ...]


@dataclass(frozen=True)
class EPUB:
    package_path: str
    resources: Mapping[str, bytes]
    manifest: Mapping[str, ManifestItem]
    spine: tuple[SpineItem, ...]
    encryption: Mapping[str, str]

    def resolve(self, base_path: str, uri: str) -> ResourceReference:
        """Resolve from a canonical package/XHTML path; external URIs stay inert."""
        if base_path not in self.resources:
            raise EPUBError(f"Unknown URI base resource: {base_path!r}")
        return _resolve(self.resources, base_path, uri)


def _decode_uri(value: str) -> str:
    if re.search(r"%(?![0-9a-fA-F]{2})", value):
        raise EPUBError("Malformed percent encoding in URI")
    try:
        return unquote(value, encoding="utf-8", errors="strict")
    except UnicodeError as exc:
        raise EPUBError("Invalid UTF-8 in URI") from exc


def _resolve(resources: Mapping[str, bytes], base: str, uri: str) -> ResourceReference:
    if any(ord(char) < 32 for char in uri) or "\\" in uri:
        raise EPUBError("Unsafe resource URI")
    try:
        split = urlsplit(uri)
    except ValueError as exc:
        raise EPUBError("Malformed resource URI") from exc
    fragment = _decode_uri(split.fragment) if "#" in uri else None
    if split.scheme or split.netloc:
        return ResourceReference(None, fragment, split.query, uri)
    path = _decode_uri(split.path)
    if path.startswith("/") or "\\" in path or any(ord(c) < 32 for c in path):
        raise EPUBError("Unsafe resource URI path")
    if path:
        parts = base.split("/")[:-1] if base else []
        for part in path.split("/"):
            if part == "..":
                if not parts:
                    raise EPUBError("Resource URI path escapes archive")
                parts.pop()
            elif part not in {"", "."}:
                parts.append(part)
        target = "/".join(parts)
    else:
        target = base
    if target not in resources:
        raise EPUBError(f"Missing archive resource for URI: {uri!r}")
    return ResourceReference(target, fragment, split.query)


class _XMLPreflight(ContentHandler):
    def __init__(self, path: str):
        super().__init__()
        self.path = path
        self.depth = self.nodes = 0

    def startElement(self, name: str, attrs: AttributesImpl) -> None:
        self.depth += 1
        self.nodes += 1
        if self.depth > MAX_XML_DEPTH or self.nodes > MAX_XML_NODES:
            raise EPUBError(f"XML depth/node limit exceeded: {self.path!r}")
        if len(attrs) > MAX_XML_ATTRIBUTES:
            raise EPUBError(f"XML attribute limit exceeded: {self.path!r}")
        if len(name) > MAX_XML_NAME_CHARS or any(
            len(key) > MAX_XML_NAME_CHARS
            or (
                (key == "xmlns" or key.startswith("xmlns:"))
                and len(value) > MAX_XML_NAME_CHARS
            )
            for key, value in attrs.items()
        ):
            raise EPUBError(f"XML name limit exceeded: {self.path!r}")

    def endElement(self, name: str) -> None:
        self.depth -= 1


class _BoundedXMLTreeBuilder(TreeBuilder):
    def __init__(self, path: str):
        super().__init__()
        self.path = path
        self.depth = self.nodes = self.name_chars = 0

    def _check_name(self, name: str) -> None:
        self.name_chars += len(name)
        if len(name) > MAX_XML_NAME_CHARS or self.name_chars > MAX_XML_TOTAL_NAME_CHARS:
            raise EPUBError(f"XML name limit exceeded: {self.path!r}")

    def start_ns(self, prefix: str, uri: str) -> None:
        self._check_name(prefix)
        self._check_name(uri)

    def start(self, tag: str, attrs: dict[str, str]) -> Element:
        self.depth += 1
        self.nodes += 1
        if self.depth > MAX_XML_DEPTH or self.nodes > MAX_XML_NODES:
            raise EPUBError(f"XML depth/node limit exceeded: {self.path!r}")
        self._check_name(tag)
        for name in attrs:
            self._check_name(name)
        return super().start(tag, attrs)

    def end(self, tag: str) -> Element:
        self.depth -= 1
        return super().end(tag)


def _parse_xml(data: bytes, path: str) -> Element:
    try:
        # Bound a whole start tag before namespace expansion allocates its attribute names.
        preflight = SafeSAX.make_parser()
        preflight.setFeature(feature_namespaces, False)
        # SAX skips external DTDs; entity declarations remain forbidden by defusedxml.
        preflight.forbid_external = False
        preflight.setFeature(feature_external_ges, False)
        preflight.setContentHandler(_XMLPreflight(path))
        preflight.parse(BytesIO(data))
        parser = SafeET.DefusedXMLParser(
            target=_BoundedXMLTreeBuilder(path),
            forbid_dtd=False,
            forbid_entities=True,
            forbid_external=True,
        )
        parser.feed(data)
        return parser.close()
    except EPUBError:
        raise
    except (
        DefusedXmlException,
        ParseError,
        SAXException,
        LookupError,
        ValueError,
    ) as exc:
        raise EPUBError(f"Unsafe, malformed, or unsupported XML: {path!r}") from exc


def _required(element: Element, name: str) -> str:
    value = element.get(name)
    if not value:
        raise EPUBError(f"Missing required package/container attribute: {name}")
    return value


def _has_body_text(body: Element) -> bool:
    pending = [body]
    while pending:
        element = pending.pop()
        if (
            element.tag
            in {f"{XHTML}{tag}" for tag in ("script", "style", "nav", "title")}
            or "toc" in element.get(EPUB_TYPE, "").split()
        ):
            continue
        if element.tag.startswith("{http://www.w3.org/2000/svg}"):
            continue
        if element.text and element.text.strip():
            return True
        for child in element:
            if child.tail and child.tail.strip():
                return True
            pending.append(child)
    return False


def _member_path(name: str) -> str:
    parts = name.removesuffix("/").split("/")
    if (
        any(part in {"", ".", ".."} for part in parts)
        or "\\" in name
        or ":" in parts[0]
        or any(ord(char) < 32 for char in name)
    ):
        raise EPUBError(f"Unsafe or ambiguous archive path: {name!r}")
    return "/".join(parts)


def read_epub(path: Path) -> EPUB:
    """Read an EPUB without modifying the archive or writing its resources."""
    try:
        with path.open("rb") as source:
            if os.fstat(source.fileno()).st_size > MAX_ARCHIVE_BYTES:
                raise EPUBError("ZIP archive byte limit exceeded")
            return _read_archive(source)
    except (BadZipFile, zlib.error, EOFError, NotImplementedError, UnicodeError) as exc:
        raise EPUBError("Invalid ZIP archive or compression") from exc


def _validate_compressed_size(source: BinaryIO, info: ZipInfo) -> None:
    # ZipExtFile truncates output to file_size before checking CRC, hiding surplus bytes.
    if info.compress_type == ZIP_STORED:
        if info.compress_size != info.file_size:
            raise EPUBError("ZIP stored member size mismatch")
        return
    source.seek(info.header_offset)
    header = source.read(30)
    if len(header) != 30 or header[:4] != b"PK\x03\x04":
        raise EPUBError("Invalid ZIP local header")
    name_size, extra_size = struct.unpack_from("<HH", header, 26)
    source.seek(name_size + extra_size, os.SEEK_CUR)
    remaining = info.compress_size
    size = 0
    inflater = zlib.decompressobj(-zlib.MAX_WBITS)
    while remaining:
        chunk = source.read(min(64 * 1024, remaining))
        if not chunk:
            raise EPUBError("Truncated ZIP compressed member")
        remaining -= len(chunk)
        while chunk:
            expanded = inflater.decompress(chunk, 64 * 1024)
            size += len(expanded)
            if size > info.file_size:
                raise EPUBError("ZIP member size mismatch")
            chunk = inflater.unconsumed_tail
            if inflater.eof and (inflater.unused_data or remaining):
                raise EPUBError("ZIP compressed stream size mismatch")
    if not inflater.eof or size != info.file_size:
        raise EPUBError("ZIP compressed stream end/size mismatch")


def _read_archive(source: BinaryIO) -> EPUB:
    with ZipFile(source) as archive:
        resources = {}
        infos = archive.infolist()
        if len(infos) > MAX_MEMBERS:
            raise EPUBError("ZIP member count limit exceeded")
        if any(info.file_size > MAX_MEMBER_BYTES for info in infos):
            raise EPUBError("ZIP member byte limit exceeded")
        if sum(info.file_size for info in infos) > MAX_TOTAL_BYTES:
            raise EPUBError("ZIP total expanded byte limit exceeded")
        total = 0
        names: dict[str, bool] = {}
        for info in infos:
            name = _member_path(info.orig_filename)
            key = unicodedata.normalize("NFC", name)
            if key in names:
                raise EPUBError(f"Duplicate or ambiguous archive path: {name!r}")
            if stat.S_ISLNK(info.external_attr >> 16):
                raise EPUBError(f"Archive symlink is unsupported: {name!r}")
            if info.flag_bits & 1:
                raise EPUBError("Encrypted ZIP member is unsupported")
            if info.compress_type not in {ZIP_STORED, ZIP_DEFLATED}:
                raise EPUBError("Unsupported ZIP compression method")
            names[key] = info.is_dir()
            _validate_compressed_size(source, info)
            chunks = []
            size = 0
            with archive.open(info) as member:
                while chunk := member.read(64 * 1024):
                    size += len(chunk)
                    total += len(chunk)
                    if size > MAX_MEMBER_BYTES or total > MAX_TOTAL_BYTES:
                        raise EPUBError("ZIP actual expanded byte limit exceeded")
                    chunks.append(chunk)
            if size != info.file_size:
                raise EPUBError("ZIP member size mismatch")
            if info.is_dir() and size:
                raise EPUBError(f"Nonempty archive directory: {name!r}")
            if not info.is_dir():
                resources[name] = b"".join(chunks)
        previous = ""
        # Sort with the separator so siblings like 'a-b' cannot hide ancestor 'a'.
        for key in sorted(names, key=lambda name: name + "/"):
            if previous and not names[previous] and key.startswith(previous + "/"):
                raise EPUBError(f"File/directory archive path collision: {key!r}")
            previous = key
    if "META-INF/container.xml" not in resources:
        raise EPUBError("Missing container.xml")
    container = _parse_xml(
        resources["META-INF/container.xml"], "META-INF/container.xml"
    )
    rootfiles = container.findall(f"{CONTAINER}rootfiles/{CONTAINER}rootfile")
    if container.tag != f"{CONTAINER}container" or not rootfiles:
        raise EPUBError("Container must identify a package")
    if len(rootfiles) != 1:
        raise EPUBError("Multiple renditions are unsupported")
    package_ref = _resolve(resources, "", _required(rootfiles[0], "full-path"))
    package_path = package_ref.resource_path
    if package_path is None:
        raise EPUBError("Package must be an archive resource")
    package = _parse_xml(resources[package_path], package_path)
    if package.tag != f"{OPF}package" or package.get("version") not in {"2.0", "3.0"}:
        raise EPUBError("Only EPUB 2/3 packages are supported")
    if (
        len(package.findall(f"{OPF}manifest")) != 1
        or len(package.findall(f"{OPF}spine")) != 1
    ):
        raise EPUBError("Package requires one manifest and one spine")
    rendition_prefixes = {"rendition"} | {
        prefix
        for prefix, uri in re.findall(r"(\S+):\s+(\S+)", package.get("prefix", ""))
        if uri == "http://www.idpf.org/vocab/rendition/#"
    }
    display_path = "META-INF/com.apple.ibooks.display-options.xml"
    if display_path in resources:
        display = _parse_xml(resources[display_path], display_path)
        if any(
            option.get("name") == "fixed-layout"
            and (option.text or "").strip() == "true"
            for option in display.iter("option")
        ):
            raise EPUBError("Unsupported fixed-layout Apple display options")
    for meta in package.findall(f"{OPF}metadata/{OPF}meta"):
        if (
            meta.get("property")
            in {f"{prefix}:layout" for prefix in rendition_prefixes}
            and (meta.text or "").strip() == "pre-paginated"
        ) or (meta.get("name") == "fixed-layout" and meta.get("content") == "true"):
            raise EPUBError("Unsupported fixed-layout publication")
    manifest = {}
    resource_media_types: dict[str, set[str]] = {}
    for element in package.findall(f"{OPF}manifest/{OPF}item"):
        href = _required(element, "href")
        reference = _resolve(resources, package_path, href)
        item = ManifestItem(
            id=_required(element, "id"),
            href=href,
            resource_path=reference.resource_path,
            fragment=reference.fragment,
            media_type=_required(element, "media-type"),
            fallback=element.get("fallback"),
            properties=tuple(element.get("properties", "").split()),
        )
        if item.id in manifest:
            raise EPUBError(f"Duplicate manifest ID: {item.id!r}")
        manifest[item.id] = item
        if item.resource_path is not None:
            resource_media_types.setdefault(item.resource_path, set()).add(
                item.media_type
            )
    checked_fallbacks: set[str] = set()
    for item in manifest.values():
        seen = set()
        current = item
        while current.id not in checked_fallbacks:
            if current.id in seen:
                raise EPUBError(f"Fallback cycle at manifest ID: {current.id!r}")
            seen.add(current.id)
            if current.fallback is None:
                break
            if current.fallback not in manifest:
                raise EPUBError(f"Missing fallback ID: {current.fallback!r}")
            current = manifest[current.fallback]
        checked_fallbacks.update(seen)
    encryption = {}
    encryption_data = resources.get("META-INF/encryption.xml")
    if encryption_data is not None:
        encryption_xml = _parse_xml(encryption_data, "META-INF/encryption.xml")
        enc = "{http://www.w3.org/2001/04/xmlenc#}"
        if encryption_xml.tag != f"{CONTAINER}encryption":
            raise EPUBError("Invalid encryption.xml root")
        for data in encryption_xml:
            methods = data.findall(f"{enc}EncryptionMethod")
            ciphers = data.findall(f"{enc}CipherData/{enc}CipherReference")
            if (
                data.tag != f"{enc}EncryptedData"
                or len(methods) != 1
                or len(ciphers) != 1
            ):
                raise EPUBError(
                    "Encryption record requires one algorithm and target resource"
                )
            target = _resolve(resources, "", _required(ciphers[0], "URI")).resource_path
            algorithm = _required(methods[0], "Algorithm")
            if target is None:
                raise EPUBError("Encryption target must be an archive resource")
            font_types = {
                "application/vnd.ms-opentype",
                "application/font-sfnt",
                "application/font-woff",
                "application/x-font-ttf",
                "application/x-font-opentype",
            }
            target_types = resource_media_types.get(target, set())
            is_font = bool(target_types) and all(
                media_type.startswith("font/") or media_type in font_types
                for media_type in target_types
            )
            if (
                algorithm
                not in {
                    "http://www.idpf.org/2008/embedding",
                    "http://ns.adobe.com/pdf/enc#RC",
                }
                or not is_font
            ):
                raise EPUBError(
                    f"Unsupported encryption algorithm/resource: {algorithm!r}, {target!r}"
                )
            if target in encryption:
                raise EPUBError(f"Ambiguous encryption target resource: {target!r}")
            encryption[target] = algorithm
    xml_paths = {
        name
        for name in resources
        if Path(name).suffix.lower()
        in {".xml", ".opf", ".xhtml", ".html", ".ncx", ".svg"}
    }
    xml_paths.update(
        item.resource_path
        for item in manifest.values()
        if item.resource_path is not None
        and (
            item.media_type.endswith("+xml")
            or item.media_type in {"application/xml", "text/xml"}
        )
    )
    for name in xml_paths - {
        "META-INF/container.xml",
        package_path,
        "META-INF/encryption.xml",
    }:
        _parse_xml(resources[name], name)
    spine = []
    spine_fallback_ids = 0
    body_content: dict[str, tuple[bool, bool]] = {}
    has_text = has_images = False
    for element in package.findall(f"{OPF}spine/{OPF}itemref"):
        idref = _required(element, "idref")
        if idref not in manifest:
            raise EPUBError(f"Missing spine idref: {idref!r}")
        if element.get("linear", "yes") not in {"yes", "no"}:
            raise EPUBError(f"Invalid spine linear value: {idref!r}")
        chain = []
        selected = manifest[idref]
        while True:
            if spine_fallback_ids >= MAX_SPINE_FALLBACK_IDS:
                raise EPUBError("Spine fallback ID total limit exceeded")
            spine_fallback_ids += 1
            chain.append(selected.id)
            if (
                selected.media_type == "application/xhtml+xml"
                and selected.resource_path is not None
            ) or not selected.fallback:
                break
            selected = manifest[selected.fallback]
        if {
            f"{prefix}:layout-pre-paginated" for prefix in rendition_prefixes
        }.intersection(element.get("properties", "").split()):
            raise EPUBError(f"Unsupported fixed-layout spine item: {idref!r}")
        if selected.media_type != "application/xhtml+xml":
            reason = (
                "DTBook-only"
                if selected.media_type == "application/x-dtbook+xml"
                else selected.media_type
            )
            raise EPUBError(f"Unsupported spine body format {reason!r}: {idref!r}")
        if selected.resource_path is None:
            raise EPUBError(f"Unsupported remote spine body: {idref!r}")
        if selected.resource_path not in body_content:
            root = _parse_xml(resources[selected.resource_path], selected.resource_path)
            body = root.find(f"{XHTML}body")
            if root.tag != f"{XHTML}html" or body is None:
                raise EPUBError(
                    f"Unsupported body: expected XHTML html/body for {idref!r}"
                )
            body_content[selected.resource_path] = (
                _has_body_text(body),
                any(
                    node.tag
                    in {
                        f"{XHTML}img",
                        f"{XHTML}object",
                        "{http://www.w3.org/2000/svg}svg",
                    }
                    for node in body.iter()
                ),
            )
        if "nav" not in selected.properties:
            text, images = body_content[selected.resource_path]
            has_text |= text
            has_images |= images
        spine.append(
            SpineItem(
                idref=idref,
                resolved_id=selected.id,
                fallback_chain=tuple(chain),
                linear=element.get("linear", "yes") != "no",
                properties=tuple(element.get("properties", "").split()),
            )
        )
    if not spine:
        raise EPUBError("Spine must contain reading-order items")
    # shortcut: only text-free image books are detected, use extraction coverage for mixed content.
    if has_images and not has_text:
        raise EPUBError(
            "Unsupported image-only publication: no obtainable spine body text"
        )
    return EPUB(
        package_path,
        MappingProxyType(resources),
        MappingProxyType(manifest),
        tuple(spine),
        MappingProxyType(encryption),
    )
