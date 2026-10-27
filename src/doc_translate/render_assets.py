"""Bounded original-resource reads and inert/safe display assets; never fetch URLs."""

import hashlib
import html
import re
from io import BytesIO
from pathlib import Path
from xml.etree.ElementTree import tostring

from PIL import Image

from doc_translate.epub import EPUB, _parse_xml, read_epub
from doc_translate.snapshot import SourceSnapshot

_MATH = "{http://www.w3.org/1998/Math/MathML}"
_MATH_TAGS = {
    "math",
    "mrow",
    "mi",
    "mn",
    "mo",
    "mtext",
    "mspace",
    "ms",
    "mfrac",
    "msqrt",
    "mroot",
    "mstyle",
    "merror",
    "mpadded",
    "mphantom",
    "mfenced",
    "menclose",
    "msub",
    "msup",
    "msubsup",
    "munder",
    "mover",
    "munderover",
    "mmultiscripts",
    "mprescripts",
    "none",
    "mtable",
    "mtr",
    "mtd",
    "mlabeledtr",
}
_MATH_ATTRS = {
    "display",
    "mathvariant",
    "mathsize",
    "mathcolor",
    "mathbackground",
    "dir",
    "stretchy",
    "symmetric",
    "largeop",
    "movablelimits",
    "accent",
    "accentunder",
    "fence",
    "separator",
    "form",
    "linethickness",
    "bevelled",
    "notation",
    "rowspan",
    "columnspan",
    "rowalign",
    "columnalign",
    "rowspacing",
    "columnspacing",
    "open",
    "close",
    "separators",
    "width",
    "height",
    "depth",
    "lspace",
    "rspace",
    "displaystyle",
    "scriptlevel",
}


def safe_latex(text: str) -> bool:
    return not re.search(
        r"\$|!\[|<[A-Za-z/!]|\\(?:href|url|includegraphics|html\w*|style|class|require|def|newcommand|input|include|write|catcode)\b",
        text,
        re.IGNORECASE,
    )


class RenderAssets:
    def __init__(self, workspace: Path, source: SourceSnapshot):
        self.workspace = workspace
        self.source = source
        self.files: dict[str, bytes] = {}
        self.book: EPUB | None = None
        self.xml = {}

    def read(self, relative: str, digest: str) -> bytes:
        path = (self.workspace / relative).resolve()
        if not path.is_relative_to(self.workspace.resolve()):
            raise ValueError("Render source asset must be inside workspace")
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != digest:
            raise ValueError("Render source asset SHA-256 mismatch")
        return data

    def epub(self) -> EPUB:
        if self.book is None:
            self.read(self.source.document.artifact, self.source.document.sha256)
            self.book = read_epub(self.workspace / self.source.document.artifact)
        return self.book

    def original_node(self, location):
        path = location["resource_path"]
        if path not in self.xml:
            root = _parse_xml(self.epub().resources[path], path)
            nodes = {}

            def visit(element, position):
                nodes[position] = element
                for index, child in enumerate(element, 1):
                    visit(child, f"{position}/{index}")

            visit(root, "/1")
            self.xml[path] = nodes
        return self.xml[path][location["node_path"]]

    def store(self, data: bytes, suffix: str) -> str:
        name = f"assets/{hashlib.sha256(data).hexdigest()}.{suffix}"
        self.files[name] = data
        return name

    def reference(self, location) -> str:
        element = self.original_node(location)
        return self.store(tostring(element, encoding="utf-8"), "txt")

    def mathml(self, location) -> str | None:
        root = self.original_node(location)

        def serialize(node, root=False):
            if (
                not node.tag.startswith(_MATH)
                or node.tag.removeprefix(_MATH) not in _MATH_TAGS
            ):
                raise ValueError("Unsupported MathML element")
            attrs = ' xmlns="http://www.w3.org/1998/Math/MathML"' if root else ""
            for key, value in node.attrib.items():
                if key not in _MATH_ATTRS or re.search(
                    r"url\s*\(|[<>&\\]", value, re.IGNORECASE
                ):
                    raise ValueError("Unsupported MathML attribute")
                attrs += f' {key}="{html.escape(value, quote=True)}"'
            name = node.tag.removeprefix(_MATH)
            return (
                f"<{name}{attrs}>"
                + html.escape(node.text or "")
                + "".join(
                    serialize(child) + html.escape(child.tail or "") for child in node
                )
                + f"</{name}>"
            )

        try:
            return serialize(root, True)
        except ValueError:
            return None

    def image(self, data: bytes) -> str | None:
        try:
            with Image.open(BytesIO(data)) as image:
                if image.format not in {"PNG", "JPEG", "GIF", "WEBP"}:
                    return None
                image.load()
                suffix = {"PNG": "png", "JPEG": "jpg", "GIF": "gif", "WEBP": "webp"}[
                    image.format
                ]
        except OSError, ValueError:
            return None
        return self.store(data, suffix)

    def pdf_picture(self, structure) -> str | None:
        region = structure.get("region")
        if not region:
            return None
        page = next(
            (
                p
                for p in structure.get("page_images", [])
                if p["page"] == region["page"]
            ),
            None,
        )
        if page is None:
            return None
        data = self.read(page["image"]["artifact"], page["image"]["sha256"])
        with Image.open(BytesIO(data)) as image:
            image.load()
            if image.format != "PNG" or image.size != (
                page["width_pixels"],
                page["height_pixels"],
            ):
                raise ValueError("PDF page PNG format/dimensions mismatch")
            box = tuple(region[key] for key in ("left", "top", "right", "bottom"))
            if not (
                0 <= box[0] < box[2] <= image.width
                and 0 <= box[1] < box[3] <= image.height
            ):
                raise ValueError("PDF picture region outside page PNG")
            output = BytesIO()
            image.crop(box).save(output, format="PNG")
        return self.store(output.getvalue(), "png")
