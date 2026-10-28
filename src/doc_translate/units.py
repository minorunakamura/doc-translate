"""Generate translation input from adopted source, never from retained acquisition data."""

import hashlib
import json
import re
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from collections.abc import Sequence
from typing import Any, Literal, cast

from pydantic import Field, JsonValue

from doc_translate.snapshot import (
    Contract,
    EPUBLocation,
    Nonempty,
    Sha256,
    SourceLocation,
    SourceSnapshot,
)


class InlineToken(Contract):
    token_id: Nonempty
    kind: Nonempty
    start_marker: Nonempty
    end_marker: Nonempty | None = None
    value: dict[str, JsonValue]


class UnitSourceRange(Contract):
    """Half-open Python character offsets in the original unit's marker source_text."""

    start: int = Field(ge=0)
    end: int = Field(gt=0)


class TranslationUnit(Contract):
    unit_id: Nonempty
    block_id: Nonempty
    cell_id: Nonempty | None = None
    node_path: Nonempty | None = None
    attribute: Nonempty | None = None
    kind: Nonempty
    format: Literal["pdf", "epub"]
    source_text: str
    tokens: list[InlineToken] = Field(default_factory=list)
    source_hash: Sha256
    context: dict[str, JsonValue]
    context_hash: Sha256
    locations: list[SourceLocation] = Field(min_length=1)
    snapshot_id: Nonempty
    source_snapshot_sha256: Sha256
    document_sha256: Sha256
    parent_unit_id: Nonempty | None = None
    parent_source_hash: Sha256 | None = None
    source_range: UnitSourceRange | None = None


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


def _source_hash(text: str, tokens: list[InlineToken]) -> str:
    protected = []
    for token in tokens:
        value: dict[str, Any] = {
            k: v for k, v in token.value.items() if k != "location"
        }
        if "references" in value:
            value["references"] = [
                {k: v for k, v in ref.items() if k not in {"targets", "target_index"}}
                for ref in value["references"]
            ]
        protected.append(
            {"token_id": token.token_id, "kind": token.kind, "value": value}
        )
    return _hash({"source_text": text, "protected": protected})


_MARKER = re.compile(r"⟦(?:=|/)?[0-9]+⟧")
# shortcut: lexical literals only; annotate inline when an identifier looks like prose.
_LITERAL = re.compile(
    r"https?://[^\s<>]+|`[^`\n]+`|\b[A-Za-z]\w*(?:_\w+|\.[A-Za-z_]\w*)+\b"
    r"|\b[A-Z][a-z]+(?:[A-Z][A-Za-z0-9]*)+\b"
    r"|(?<!\w)[+-]?(?:0[xX][0-9a-fA-F]+|0[bB][01]+|(?:\d+(?:\.\d+)*|\.\d+)(?:[eE][+-]?\d+)?)(?!\w)"
)


class _Inline:
    def __init__(self):
        self.parts: list[str] = []
        self.tokens: list[InlineToken] = []

    def token(self, kind, value, *, paired=False):
        number = str(len(self.tokens) + 1)
        token = InlineToken(
            token_id=number,
            kind=kind,
            value=value,
            start_marker=f"⟦{number}⟧" if paired else f"⟦={number}⟧",
            end_marker=f"⟦/{number}⟧" if paired else None,
        )
        self.tokens.append(token)
        self.parts.append(token.start_marker)
        return token

    def text(self, text):
        def prose(value):
            for piece in re.split(r"([⟦⟧])", value):
                if piece in {"⟦", "⟧"}:
                    self.token("literal", {"text": piece})
                elif piece:
                    self.parts.append(piece)

        offset = 0
        for match in _LITERAL.finditer(text):
            literal = match.group().rstrip(".,;:!?)")
            if not literal:
                continue
            prose(text[offset : match.start()])
            self.token("literal", {"text": literal})
            offset = match.start() + len(literal)
        prose(text[offset:])

    @property
    def source_text(self):
        return "".join(self.parts)


def _pdf_inline(parts, inline):
    if not isinstance(parts, list):
        raise TypeError("PDF inline must be an ordered array")
    projection = []
    for part in parts:
        if not isinstance(part, dict):
            raise TypeError("PDF inline part must be an object")
        kind = part.get("kind")
        if kind in {"text", "code", "literal", "identifier", "formula"}:
            if set(part) != {"kind", "text"} or not isinstance(part["text"], str):
                raise ValueError("PDF inline text part needs kind/text only")
            projection.append(part["text"])
            if kind == "text":
                inline.text(part["text"])
            else:
                inline.token(kind, {"text": part["text"]})
        elif kind in {"emphasis", "strong", "link", "subscript", "superscript"}:
            fields = (
                {"kind", "children", "target"}
                if kind == "link"
                else {"kind", "children"}
            )
            if set(part) != fields or (
                kind == "link" and not isinstance(part["target"], str)
            ):
                raise ValueError("Invalid PDF inline structure")
            token = inline.token(
                kind, {"target": part["target"]} if kind == "link" else {}, paired=True
            )
            projection.append(_pdf_inline(part["children"], inline))
            inline.parts.append(token.end_marker)
        else:
            raise ValueError(f"Unsupported PDF inline kind: {kind}")
    return "".join(projection)


def _pdf_source(text, inline_parts=None):
    inline = _Inline()
    if inline_parts is None:
        inline.text(text)
    elif _pdf_inline(inline_parts, inline) != text:
        raise ValueError("Inline projection differs from adopted text")
    return inline


def _table_headings(cells, headers):
    """Index only related context candidates, without reinterpreting source cells."""
    columns = sorted({cell["column"] for cell in cells.values()})
    group_rows = defaultdict(set)
    for cell in cells.values():
        group_rows[cell.get("row_group")].add(cell["row"])
    group_rows = {group: sorted(rows) for group, rows in group_rows.items()}
    row_index = defaultdict(list)
    column_index = defaultdict(list)
    auto_columns = defaultdict(list)
    original_ids = defaultdict(list)
    order = {key: index for index, key in enumerate(headers)}

    def covered(points, start, span):
        return points[bisect_left(points, start) : bisect_left(points, start + span)]

    for key in headers:
        cell = cells[key]
        attrs = cell.get("attributes", {})
        scope = attrs.get("scope", "")
        group = cell.get("row_group")
        for original_id in cell.get("original_ids", {}).values():
            original_ids[original_id].append(key)
        if scope not in {"col", "colgroup"}:
            rows = group_rows[group]
            if scope != "rowgroup":
                rows = covered(rows, cell["row"], cell.get("row_span", 1))
            for row in rows:
                row_index[row].append(key)
        if scope not in {"row", "rowgroup"}:
            for column in covered(columns, cell["column"], cell.get("column_span", 1)):
                if scope in {"col", "colgroup"}:
                    column_index[column].append(key)
                else:
                    column_group = (
                        "thead" if cell.get("row_group_kind") == "thead" else group
                    )
                    auto_columns[column, column_group].append((cell["row"], key))
    for entries in auto_columns.values():
        entries.sort(key=lambda entry: entry[0])

    result = {}
    for key, cell in cells.items():
        attrs = cell.get("attributes", {})
        if "headers" in attrs:
            selected = dict.fromkeys(
                header
                for original_id in attrs["headers"].split()
                for header in original_ids[original_id]
            )
        else:
            selected = set()
            group = cell.get("row_group")
            for row in covered(group_rows[group], cell["row"], cell.get("row_span", 1)):
                for header in row_index[row]:
                    candidate = cells[header]
                    scope = candidate.get("attributes", {}).get("scope", "")
                    if (
                        scope in {"row", "rowgroup"}
                        or header == key
                        or candidate["column"] + candidate.get("column_span", 1)
                        <= cell["column"]
                    ):
                        selected.add(header)
            # shortcut: unscoped headers use rightward rows and nearest preceding columns, not semantic inference.
            for column in covered(columns, cell["column"], cell.get("column_span", 1)):
                selected.update(column_index[column])
                for column_group in {group, "thead"}:
                    entries = auto_columns[column, column_group]
                    index = (
                        bisect_right(entries, cell["row"], key=lambda entry: entry[0])
                        - 1
                    )
                    if index >= 0:
                        nearest = entries[index][0]
                        while index >= 0 and entries[index][0] == nearest:
                            selected.add(entries[index][1])
                            index -= 1
            selected = sorted(selected, key=order.__getitem__)
        result[key] = [headers[header] for header in selected]
    return result


def validate_unit_text(unit: TranslationUnit, text: str) -> None:
    """Check marker inventory/nesting, allowing Japanese reordering of complete spans."""
    expected = [m for t in unit.tokens for m in (t.start_marker, t.end_marker) if m]
    actual = _MARKER.findall(text)
    if Counter(actual) != Counter(expected):
        raise ValueError(f"Protected marker mismatch in {unit.unit_id}")
    if "⟦" in _MARKER.sub("", text) or "⟧" in _MARKER.sub("", text):
        raise ValueError(f"Unknown marker in {unit.unit_id}")
    opening = {t.start_marker: t.end_marker for t in unit.tokens if t.end_marker}
    closing = {t.end_marker for t in unit.tokens if t.end_marker}

    def parents(markers):
        stack = []
        result = {}
        for marker in markers:
            if marker in closing:
                if not stack or stack.pop() != marker:
                    raise ValueError(f"Unbalanced structural marker in {unit.unit_id}")
            else:
                result[marker] = tuple(stack)
                if marker in opening:
                    stack.append(opening[marker])
        if stack:
            raise ValueError(f"Unclosed structural marker in {unit.unit_id}")
        return result

    if parents(actual) != parents(_MARKER.findall(unit.source_text)):
        raise ValueError(f"Changed inline structure in {unit.unit_id}")


class UnitSplitError(ValueError):
    def __init__(self, unit_id: str, source_range: UnitSourceRange):
        self.unit_id = unit_id
        self.source_range = source_range
        super().__init__(
            f"Cannot split {unit_id} at {source_range.start}:{source_range.end}: protected inline span exceeds limit"
        )


def split_translation_units(
    units: Sequence[TranslationUnit], *, max_source_chars: int
) -> list[TranslationUnit]:
    """Split in workflow code; fail atomically if an inline span cannot fit.

    Ranges describe marker source, never PDF character-level acquisition precision.
    """
    if type(max_source_chars) is not int or max_source_chars < 1:
        raise ValueError("max_source_chars must be a positive integer")
    result = []
    for unit in units:
        validate_unit_text(unit, unit.source_text)
        if len(unit.source_text) <= max_source_chars:
            result.append(unit)
            continue
        atomic = []
        depth = 0
        start = 0
        paired = {t.start_marker for t in unit.tokens if t.end_marker}
        closing = {t.end_marker for t in unit.tokens if t.end_marker}
        for match in _MARKER.finditer(unit.source_text):
            marker = match.group()
            if depth == 0:
                start = match.start()
            if marker in paired:
                depth += 1
            elif marker in closing:
                depth -= 1
            if depth == 0:
                atomic.append((start, match.end()))
        offset = unit.source_range.start if unit.source_range else 0
        for start, end in atomic:
            if end - start > max_source_chars:
                raise UnitSplitError(
                    unit.parent_unit_id or unit.unit_id,
                    UnitSourceRange(start=offset + start, end=offset + end),
                )
        atomic_starts = [left for left, _right in atomic]

        def containing(position, atomic=atomic, atomic_starts=atomic_starts):
            index = bisect_right(atomic_starts, position) - 1
            if index >= 0 and atomic[index][0] < position < atomic[index][1]:
                return atomic[index]
            return None

        token_by_marker = {token.start_marker: token for token in unit.tokens}
        base = unit.model_dump(mode="json", exclude={"source_text", "tokens"})
        start = 0
        part_number = 0
        while start < len(unit.source_text):
            end = min(start + max_source_chars, len(unit.source_text))
            if span := containing(end):
                end = span[0]
            # Prefer word boundaries, but plain text can always split without loss.
            if end < len(unit.source_text):
                whitespace = [
                    m.end()
                    for m in re.finditer(r"\s+", unit.source_text[start:end])
                    if containing(start + m.end()) is None
                ]
                if whitespace:
                    end = start + whitespace[-1]
            text = unit.source_text[start:end]
            tokens = [
                token_by_marker[marker]
                for marker in _MARKER.findall(text)
                if marker in token_by_marker
            ]
            part_number += 1
            data = dict(base)
            data.update(
                unit_id=f"{unit.unit_id}.part-{part_number}",
                parent_unit_id=unit.parent_unit_id or unit.unit_id,
                parent_source_hash=unit.parent_source_hash or unit.source_hash,
                source_range={"start": offset + start, "end": offset + end},
                source_text=text,
                tokens=[t.model_dump(mode="json") for t in tokens],
                source_hash=_source_hash(text, tokens),
            )
            result.append(TranslationUnit.model_validate(data))
            start = end
    return result


def generate_translation_units(
    snapshot: SourceSnapshot, *, source_sha256: str | None = None
) -> list[TranslationUnit]:
    """Return reading-ordered units without saving, inference, or source mutation."""
    snapshot = SourceSnapshot.model_validate(snapshot.model_dump(mode="json"))
    digest = (
        source_sha256 or hashlib.sha256(snapshot.model_dump_json().encode()).hexdigest()
    )
    units = []
    headings = []
    blocks = {block.block_id: block for block in snapshot.blocks}
    structures = {
        block.block_id: cast(dict[str, Any], block.structure)
        for block in snapshot.blocks
    }
    # Picture members retain proposals; adopted uncertainty overrides exclusion.
    unresolved_ids = {
        target for item in snapshot.uncertainties for target in item.target_ids
    }
    unresolved_ids.update(
        member["target_id"]
        for block in snapshot.blocks
        if block.kind in {"picture", "figure"}
        for member in structures[block.block_id].get("members", [])
        if member.get("status") in {"uncertain", "partial"} and member.get("target_id")
    )
    for block in snapshot.blocks:
        if block.kind == "table" and any(
            cell["cell_id"] in unresolved_ids
            for cell in structures[block.block_id].get("cells", [])
        ):
            unresolved_ids.add(block.block_id)
    interior_ids = {
        member["target_id"]
        for block in snapshot.blocks
        if block.kind in {"picture", "figure"}
        for member in structures[block.block_id].get("members", [])
        if member.get("status") == "confirmed" and member.get("target_id")
    } - unresolved_ids

    confirmed_refs = [
        (member["ref"], block)
        for block in snapshot.blocks
        if block.kind in {"picture", "figure"}
        for member in structures[block.block_id].get("members", [])
        if member.get("status") == "confirmed" and member.get("ref")
    ]

    def internal_evidence(evidence):
        for ref, picture in confirmed_refs:
            if (
                ref.get("ref") is None
                or ref.get("page") != evidence.get("page")
                or ref["ref"] != evidence.get("ref")
            ):
                continue
            if any(
                ref.get(key) is not None and ref[key] != evidence.get(key)
                for key in ("charspan", "cell_index")
            ):
                continue
            drafts = {
                location.extraction_snapshot
                for location in picture.locations
                if location.format == "pdf"
                and location.page == ref["page"]
                and location.extraction_snapshot
            }
            if not drafts:
                drafts = {
                    location.extraction_snapshot
                    for owner in snapshot.blocks
                    for location in owner.locations
                    if location.format == "pdf"
                    and location.page == ref["page"]
                    and location.extraction_snapshot
                }
                if len(drafts) > 1:
                    continue
            if not drafts or evidence.get("extraction_snapshot") in drafts:
                return True
        return False

    for block in snapshot.blocks:
        if (
            block.kind in {"caption", "footnote", "picture", "figure"}
            or block.block_id in unresolved_ids
        ):
            continue
        evidence = []
        for location in block.locations:
            if location.format != "pdf":
                break
            item = {
                "page": location.page,
                "ref": location.extraction_ref,
                "extraction_snapshot": location.extraction_snapshot,
            }
            if location.evidence:
                try:
                    detail = json.loads(location.evidence)
                except ValueError, TypeError:
                    detail = None
                if isinstance(detail, dict):
                    item.update(
                        {
                            key: detail[key]
                            for key in ("charspan", "cell_index")
                            if key in detail
                        }
                    )
            evidence.append(item)
        if evidence and all(internal_evidence(item) for item in evidence):
            interior_ids.add(block.block_id)

    def add(
        block,
        text,
        kind,
        context,
        *,
        cell_id=None,
        inline_parts=None,
        inline=None,
        node=None,
        attribute=None,
    ):
        if inline is None:
            inline = _pdf_source(text, inline_parts)
        if not inline.source_text.strip():
            return
        units.append(
            TranslationUnit(
                unit_id=f"unit-{len(units) + 1}",
                block_id=block.block_id,
                cell_id=cell_id,
                node_path=node["location"].get("node_path") if node else None,
                attribute=attribute,
                kind=kind,
                format=snapshot.document.format,
                source_text=inline.source_text,
                tokens=inline.tokens,
                source_hash=_source_hash(inline.source_text, inline.tokens),
                context=context,
                context_hash=_hash(context),
                locations=[EPUBLocation.model_validate(node["location"])]
                if node
                else block.locations,
                snapshot_id=snapshot.snapshot_id,
                source_snapshot_sha256=digest,
                document_sha256=snapshot.document.sha256,
            )
        )

    for block in snapshot.blocks:
        if snapshot.document.format == "epub" and block.kind == "section":
            _epub_units(block, add, headings)
            continue
        if block.block_id in interior_ids and block.kind not in {"caption", "footnote"}:
            continue
        structure = structures[block.block_id]
        context = {"heading_path": list(headings)}
        for field in ("target_id", "code_id", "marker", "lines"):
            if field in structure:
                context[field] = structure[field]
        if block.kind == "list":
            context["list"] = {
                key: structure.get(key) for key in ("depth", "marker", "parent_id")
            }
            ancestors = []
            parent = structure.get("parent_id")
            seen = {block.block_id}
            while parent is not None:
                if (
                    parent in seen
                    or parent not in blocks
                    or blocks[parent].kind != "list"
                ):
                    raise ValueError(f"Invalid list parent: {parent}")
                seen.add(parent)
                ancestor = blocks[parent]
                ancestors.insert(
                    0,
                    {
                        "block_id": parent,
                        "text": _pdf_source(
                            ancestor.text, structures[parent].get("inline")
                        ).source_text,
                        "depth": ancestor.structure.get("depth"),
                        "marker": ancestor.structure.get("marker"),
                    },
                )
                parent = structures[parent].get("parent_id")
            context["list_path"] = ancestors
        if block.kind == "table":
            if structure.get("structure_confirmed") is False:
                continue
            if "cells" not in structure:
                raise ValueError(f"Table lacks adopted cells: {block.block_id}")

            def internal_cell(cell, block=block):
                if cell["cell_id"] in unresolved_ids:
                    return False
                return cell["cell_id"] in interior_ids or (
                    cell.get("evidence")
                    and all(
                        internal_evidence(
                            {
                                **item,
                                "extraction_snapshot": next(
                                    (
                                        loc.extraction_snapshot
                                        for loc in block.locations
                                        if loc.format == "pdf"
                                        and loc.page == item["page"]
                                    ),
                                    None,
                                ),
                            }
                        )
                        for item in cell["evidence"]
                    )
                )

            cells = [cell for cell in structure["cells"] if not internal_cell(cell)]
            headers = {
                cell["cell_id"]: {
                    "cell_id": cell["cell_id"],
                    "row": cell["row"],
                    "column": cell["column"],
                    "text": _pdf_source(cell["text"], cell.get("inline")).source_text,
                }
                for cell in cells
                if cell.get("heading")
            }
            related_headers = _table_headings(
                {cell["cell_id"]: cell for cell in cells}, headers
            )
            for cell in cells:
                add(
                    block,
                    cell["text"],
                    "table_cell",
                    {
                        **context,
                        "table": {
                            **{
                                k: v
                                for k, v in cell.items()
                                if k not in {"text", "inline"}
                            },
                            "headings": related_headers[cell["cell_id"]],
                        },
                    },
                    cell_id=cell["cell_id"],
                    inline_parts=cell.get("inline"),
                )
        elif block.kind in {
            "body",
            "paragraph",
            "heading",
            "list",
            "caption",
            "footnote",
            "callout",
        }:
            add(
                block,
                block.text,
                block.kind,
                context,
                inline_parts=structure.get("inline"),
            )
            if block.kind == "heading":
                level = structure.get("level", 1)
                headings = [h for h in headings if h["level"] < level]
                headings.append(
                    {
                        "block_id": block.block_id,
                        "level": level,
                        "text": _pdf_source(
                            block.text, structure.get("inline")
                        ).source_text,
                    }
                )
        elif block.kind not in {"code", "formula", "picture", "figure"}:
            raise ValueError(f"Unsupported source block kind: {block.kind}")
    return units


_XHTML = "{http://www.w3.org/1999/xhtml}"
_UNIT_TAGS = {
    "p",
    "li",
    "dt",
    "dd",
    "td",
    "th",
    "caption",
    "figcaption",
    *(f"h{i}" for i in range(1, 7)),
}
_BLOCK_TAGS = _UNIT_TAGS | {
    "html",
    "body",
    "div",
    "section",
    "article",
    "aside",
    "nav",
    "header",
    "footer",
    "main",
    "blockquote",
    "ul",
    "ol",
    "dl",
    "table",
    "thead",
    "tbody",
    "tfoot",
    "tr",
    "figure",
    "pre",
}


def _epub_units(block, add, headings):
    tree = block.structure.get("tree")
    if not isinstance(tree, dict) or not isinstance(tree.get("nodes"), dict):
        raise TypeError(f"EPUB section lacks a source tree: {block.block_id}")
    nodes = tree["nodes"]
    visited = set()
    table_contexts = {}

    def tag(node):
        name = node["tag"]
        return name.removeprefix(_XHTML) if name.startswith(_XHTML) else name

    def children(node):
        return [part["node_path"] for part in node["content"] if isinstance(part, dict)]

    def check(path):
        if path not in nodes or path in visited:
            raise ValueError(f"Invalid EPUB tree reference: {path}")
        visited.add(path)
        node = nodes[path]
        EPUBLocation.model_validate(node["location"])
        for child in children(node):
            check(child)

    check(tree["root"])
    if visited != set(nodes):
        raise ValueError("EPUB tree contains unreferenced nodes")

    def metadata(node):
        return {
            key: node[key]
            for key in ("location", "tag", "attributes", "original_ids", "references")
            if key in node
        }

    def plain(path):
        node = nodes[path]
        if node["disposition"] != "structured":
            return ""
        return "".join(
            part if isinstance(part, str) else plain(part["node_path"])
            for part in node["content"]
        )

    def collect(path, inline, *, root=False, deferred=None):
        node = nodes[path]
        name = tag(node)
        if node["disposition"] != "structured":
            if name.startswith(
                "{http://www.w3.org/1998/Math/MathML}"
            ) and name.endswith("}math"):
                inline.token("formula", {"location": node["location"]})
            elif name == "{http://www.w3.org/2000/svg}svg":
                inline.token("figure", {"location": node["location"]})
            return
        if not root and name in {"ul", "ol", "dl", "table", "figure", "pre"}:
            if deferred is not None:
                deferred.append(path)
            return
        if name in {"code", "kbd", "samp", "var"}:
            inline.token(
                "identifier" if name == "var" else "code",
                {**metadata(node), "text": plain(path)},
            )
            return
        if name == "img":
            inline.token("figure", metadata(node))
            return
        token = None
        if not root:
            token = inline.token(
                {"em": "emphasis", "a": "link"}.get(name, name),
                metadata(node),
                paired=name not in {"br", "hr", "wbr"},
            )
        for part in node["content"]:
            if isinstance(part, str):
                inline.text(part)
            else:
                collect(part["node_path"], inline, deferred=deferred)
        if token is not None and token.end_marker:
            inline.parts.append(token.end_marker)

    def context_for(node, ancestors):
        context = {"heading_path": list(headings), "role": node["role"]}
        list_path = [p for p in ancestors if tag(nodes[p]) in {"li", "dt", "dd"}]
        if list_path or tag(node) in {"li", "dt", "dd"}:
            context["list_path"] = [
                {
                    "node_path": p,
                    "text": own_text(p),
                    "attributes": nodes[p]["attributes"],
                }
                for p in list_path
            ]
            context["list"] = {
                "depth": len(list_path) + 1,
                "attributes": node["attributes"],
                "containers": [
                    {
                        "node_path": p,
                        "tag": tag(nodes[p]),
                        "attributes": nodes[p]["attributes"],
                    }
                    for p in ancestors
                    if tag(nodes[p]) in {"ol", "ul", "dl"}
                ],
            }
        if tag(node) in {"td", "th"}:
            table_path = next(
                (p for p in reversed(ancestors) if tag(nodes[p]) == "table"), None
            )
            row_path = next(
                (p for p in reversed(ancestors) if tag(nodes[p]) == "tr"), None
            )
            if table_path is None or row_path is None:
                raise ValueError("EPUB table cell lacks table/row context")
            if table_path not in table_contexts:
                table_contexts[table_path] = table_cells(table_path)
            context["table"] = table_contexts[table_path][node["location"]["node_path"]]
        return context

    def table_cells(table_path):
        rows = []

        def find_rows(path, group):
            for child in children(nodes[path]):
                name = tag(nodes[child])
                if name == "tr":
                    rows.append((child, group))
                elif name != "table":
                    find_rows(
                        child, child if name in {"thead", "tbody", "tfoot"} else group
                    )

        find_rows(table_path, table_path)
        group_ends = {}
        for row in reversed(range(len(rows))):
            if row == len(rows) - 1 or rows[row][1] != rows[row + 1][1]:
                group_end = row + 1
            group_ends[row] = group_end
        result = {}
        spans = []
        headers = {}
        geometry = {}
        for row, (row_path, group) in enumerate(rows):
            column = 0
            spans = (
                [s for s in spans if s[0] > row]
                if row and group == rows[row - 1][1]
                else []
            )
            for cell_index, path in enumerate(
                p for p in children(nodes[row_path]) if tag(nodes[p]) in {"td", "th"}
            ):
                while occupied := next(
                    (s for s in spans if s[1] <= column < s[2]), None
                ):
                    column = occupied[2]
                attrs = nodes[path]["attributes"]
                row_span = int(attrs.get("rowspan", "1"))
                column_span = int(attrs.get("colspan", "1"))
                if row_span == 0:
                    row_span = group_ends[row] - row
                if (
                    row_span < 1
                    or column_span < 1
                    or any(column < s[2] and column + column_span > s[1] for s in spans)
                ):
                    raise ValueError(f"Unresolvable EPUB table spans: {path}")
                result[path] = {
                    "node_path": table_path,
                    "row_path": row_path,
                    "row_group": group,
                    "row": row,
                    "column": column,
                    "cell_index": cell_index,
                    "row_span": row_span,
                    "column_span": column_span,
                    "attributes": attrs,
                }
                geometry[path] = {
                    **result[path],
                    "row_group_kind": tag(nodes[group]),
                    "original_ids": nodes[path].get("original_ids", {}),
                }
                spans.append((row + row_span, column, column + column_span))
                column += column_span
                if tag(nodes[path]) == "th":
                    headers[path] = {
                        "node_path": path,
                        "text": own_text(path),
                        "attributes": attrs,
                    }
        related_headers = _table_headings(geometry, headers)
        for path, cell_context in result.items():
            cell_context["headings"] = related_headers[path]
        return result

    def own_text(path):
        inline = _Inline()
        collect(path, inline, root=True)
        return inline.source_text

    def attributes(path, context):
        node = nodes[path]
        if node["disposition"] != "structured" or tag(node) in {"pre", "code"}:
            return
        for attribute in ("alt", "title"):
            text = node["attributes"].get(attribute)
            if text:
                add(block, text, attribute, context, node=node, attribute=attribute)

    def descendant_attributes(path, context, deferred):
        for child in children(nodes[path]):
            if (
                child not in deferred
                and nodes[child]["disposition"] == "structured"
                and tag(nodes[child]) not in {"pre", "code"}
            ):
                attributes(child, context)
                descendant_attributes(child, context, deferred)

    def walk(path, ancestors, figure=False):
        node = nodes[path]
        name = tag(node)
        if node["disposition"] != "structured" or name in {"pre", "code"}:
            return
        if figure and name != "figcaption":
            if name == "img":
                attributes(path, context_for(node, ancestors))
            for child in children(node):
                walk(child, [*ancestors, path], figure=True)
            return
        context = context_for(node, ancestors)
        attributes(path, context)
        if name == "figure":
            for child in children(node):
                walk(child, [*ancestors, path], figure=True)
        elif name in _UNIT_TAGS:
            inline = _Inline()
            deferred = []
            collect(path, inline, root=True, deferred=deferred)
            kind = (
                "heading"
                if re.fullmatch(r"h[1-6]", name)
                else {
                    "p": "paragraph",
                    "li": "list",
                    "td": "table_cell",
                    "th": "table_cell",
                    "figcaption": "caption",
                }.get(name, name)
            )
            if kind == "paragraph" and any(
                "note"
                in nodes[p]["attributes"].get("{http://www.idpf.org/2007/ops}type", "")
                for p in [*ancestors, path]
            ):
                kind = "footnote"
            add(block, "", kind, context, inline=inline, node=node)
            if kind == "heading":
                level = int(name[1])
                headings[:] = [h for h in headings if h["level"] < level]
                headings.append(
                    {
                        "block_id": block.block_id,
                        "node_path": path,
                        "level": level,
                        "text": inline.source_text,
                    }
                )

            # Attributes are independent text, not duplicated paragraph units.
            descendant_attributes(path, context, deferred)
            for child in deferred:
                walk(child, [*ancestors, path])
        else:
            inline = _Inline()

            def flush():
                nonlocal inline
                add(block, "", "body", context, inline=inline, node=node)
                inline = _Inline()

            for part in node["content"]:
                if isinstance(part, str):
                    inline.text(part)
                elif tag(nodes[part["node_path"]]) in _BLOCK_TAGS:
                    flush()
                    walk(part["node_path"], [*ancestors, path])
                else:
                    child = part["node_path"]
                    deferred = []
                    collect(child, inline, deferred=deferred)
                    attributes(child, context)
                    if nodes[child]["disposition"] == "structured" and tag(
                        nodes[child]
                    ) not in {"pre", "code"}:
                        descendant_attributes(child, context, deferred)
                    for nested in deferred:
                        flush()
                        walk(nested, [*ancestors, path, child])
            flush()

    walk(tree["root"], [])
