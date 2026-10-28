"""Offline derived Markdown, correspondence and QA from immutable saved inputs."""

import hashlib
import html
import json
import re
import time
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from doc_translate.render_assets import RenderAssets, safe_latex
from doc_translate.snapshot import (
    Contract,
    Sha256,
    SnapshotReference,
    SourceSnapshot,
    Uncertainty,
    _atomic_write,
    load_source_snapshot,
    source_snapshot_sha256,
)
from doc_translate.translation import load_translation
from doc_translate.units import TranslationUnit, generate_translation_units
from doc_translate.workspace import workspace_lock


class RenderReference(Contract):
    schema_version: Literal[1]
    summary: str
    files: dict[str, Sha256]


def _json(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode()


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _escape(text: str) -> str:
    # Entities prevent authored Markdown/HTML from becoming active display markup.
    return "".join(f"&#{ord(c)};" if c in "&<>\\`*_{}[]()!#+-.|~$" else c for c in text)


def _fence(text: str, language: str = "") -> str:
    fence = "`" * max(
        3, 1 + max((len(m.group()) for m in re.finditer(r"`+", text)), default=0)
    )
    language = language if re.fullmatch(r"[a-zA-Z0-9_+-]*", language) else ""
    return f"{fence}{language}\n{text}" + ("" if text.endswith("\n") else "\n") + fence


def _inputs(workspace: Path):
    source = load_source_snapshot(workspace)
    source_digest = source_snapshot_sha256(workspace)
    translation = None
    translation_digest = None
    stale = False
    if (workspace / "translation-snapshot.json").exists():
        translation_digest = SnapshotReference.model_validate_json(
            (workspace / "translation-snapshot.json").read_bytes()
        ).sha256
        translation = load_translation(workspace, translation_digest)
        stale = translation.source_snapshot_sha256 != source_digest
        from doc_translate.preparation import (
            PreparationEvidence,
            _read,
            load_preparation,
        )

        preparation = load_preparation(workspace, translation.preparation_sha256)
        if (workspace / "preparation-snapshot.json").exists():
            current = load_preparation(workspace)
            stale |= (
                current.translation_inputs_sha256
                != preparation.translation_inputs_sha256
            )
        if (workspace / "run.json").exists():
            from doc_translate.run import RunManifest

            run = RunManifest.model_validate_json((workspace / "run.json").read_bytes())
            evidence = PreparationEvidence.model_validate(
                _read(workspace, "preparation/evidence", preparation.evidence_sha256)
            )
            stale |= (
                run.policy != preparation.policy
                or run.user_terms != evidence.user_terms
                or run.translation != translation.config
            )
    units = generate_translation_units(source, source_sha256=source_digest)
    if translation is not None and not stale:
        units = [u for chunk in translation.chunks for u in chunk.units]
    return source, source_digest, translation, translation_digest, stale, units


class _Markdown:
    def __init__(
        self,
        workspace: Path,
        source: SourceSnapshot,
        units: list[TranslationUnit],
        translation,
        review_findings: list[dict[str, Any]] | None = None,
    ):
        self.source = source
        self.source_digest = source_snapshot_sha256(workspace)
        self.assets = RenderAssets(workspace, source)
        self.current_block = source.blocks[0] if source.blocks else None
        self.node_anchors = {}
        for index, block in enumerate(source.blocks, 1):
            tree: Any = block.structure.get("tree", {})
            for number, (path, node) in enumerate(tree.get("nodes", {}).items(), 1):
                self.node_anchors[node["location"]["resource_path"], path] = (
                    f"node-{index}-{number}"
                )
        self.units = units
        self.translated = (
            {item.unit_id: item.text for item in translation.translations}
            if translation
            else {}
        )
        self.unit_by_block = {}
        for unit in units:
            if not unit.cell_id:
                self.unit_by_block.setdefault(unit.block_id, []).append(unit)
        self.anchors = {
            block.block_id: f"block-{i}" for i, block in enumerate(source.blocks, 1)
        }
        self.concerns: list[dict[str, Any]] = []
        self.marked = set()
        self.marked_units = set()
        self.mapping = {"blocks": [], "units": []}
        self.seen = {True: set(), False: set()}
        for item in [
            *source.uncertainties,
            *(translation.uncertainties if translation else []),
            *(
                Uncertainty(
                    reason=f["reason"],
                    target_ids=f["unit_ids"]
                    if f["target_kind"] == "unit"
                    else f["block_ids"],
                    locations=f["locations"],
                    stage="review",
                    origin="undetermined",
                )
                for f in (review_findings or [])
                if f["target_kind"] != "page"
            ),
        ]:
            targets = set(item.target_ids)
            block_ids = [
                b.block_id
                for b in source.blocks
                if b.block_id in targets
                or any(
                    u.block_id == b.block_id
                    and (u.unit_id in targets or u.cell_id in targets)
                    for u in units
                )
            ]
            related = [
                u
                for u in units
                if u.unit_id in targets or u.block_id in targets or u.cell_id in targets
            ]
            self.marked.update(b for b in block_ids if b in targets)
            self.marked_units.update(u.unit_id for u in related)
            self.concerns.append(
                {
                    **item.model_dump(mode="json"),
                    "block_ids": block_ids,
                    "unit_ids": [u.unit_id for u in related],
                    "output_anchors": list(
                        dict.fromkeys(
                            [self.anchors[b] for b in block_ids if b in targets]
                            + [
                                self.unit_anchor(u)
                                for u in related
                                if u.block_id not in targets
                            ]
                        )
                    ),
                }
            )
        for finding in review_findings or []:
            if finding["target_kind"] != "page":
                continue
            related = [u for u in units if u.unit_id in finding["unit_ids"]]
            # Keep block correspondence without marking units on other pages.
            block_targets = [
                bid
                for bid in finding["block_ids"]
                if not any(u.block_id == bid for u in related)
            ]
            self.marked.update(block_targets)
            self.marked_units.update(finding["unit_ids"])
            self.concerns.append(
                {
                    **Uncertainty(
                        reason=finding["reason"],
                        target_ids=finding["unit_ids"] + block_targets,
                        locations=finding["locations"],
                        stage="review",
                        origin="undetermined",
                    ).model_dump(mode="json"),
                    "block_ids": finding["block_ids"],
                    "unit_ids": finding["unit_ids"],
                    "output_anchors": [
                        self.anchors[bid] for bid in finding["block_ids"]
                    ],
                }
            )
        for unit in units:
            self.mapping["units"].append(
                {
                    **unit.model_dump(mode="json"),
                    "output_anchor": self.unit_anchor(unit),
                }
            )

    def unit_anchor(self, unit: TranslationUnit) -> str:
        location = unit.locations[0]
        return (
            self.node_anchors.get(
                (location.resource_path, unit.node_path), self.anchors[unit.block_id]
            )
            if location.format == "epub"
            else self.anchors[unit.block_id]
        )

    def link(self, value) -> str | None:
        target = value.get("target")
        if target is None:
            ref = next(
                (r for r in value.get("references", []) if r["attribute"] == "href"),
                None,
            )
            if not ref:
                return None
            if ref.get("status") == "resolved" and len(ref.get("targets", [])) == 1:
                location = ref["targets"][0]["location"]
                anchor = self.node_anchors.get(
                    (location["resource_path"], location["node_path"])
                )
                if anchor:
                    return "#" + anchor
            target = ref.get("external_uri")
        if (
            not isinstance(target, str)
            or any(ord(c) < 33 for c in target)
            or "\\" in target
        ):
            return None
        try:
            url = urlsplit(target)
        except ValueError:
            return None
        if url.scheme.lower() in {"http", "https"} and url.netloc:
            return target
        if url.scheme.lower() == "mailto" and url.path:
            return target
        return None

    def attributes(self, value, japanese: bool) -> str:
        location = value.get("location", {})
        units = [
            u
            for u in self.units
            if self.current_block
            and u.block_id == self.current_block.block_id
            and u.node_path == location.get("node_path")
            and u.attribute in {"alt", "title"}
        ]
        return "".join(
            f' {name}="{html.escape("".join(self.plain_text(u, japanese) for u in units if u.attribute == name), quote=True)}"'
            for name in ("alt", "title")
            if any(u.attribute == name for u in units)
        )

    def image(self, value, japanese: bool) -> str:
        assert self.current_block is not None
        location = value.get("location")
        refs = value.get("references", [])
        ref = next((r for r in refs if r["attribute"] == "src"), None)
        path = None
        if ref and ref.get("resource_path") and not ref.get("external_uri"):
            data = self.assets.epub().resources[ref["resource_path"]]
            path = self.assets.image(data)
        if path:
            attrs = self.attributes(value, japanese)
            return f'<img src="{path}"{attrs} />'
        self.problem(
            self.current_block,
            "Unsafe or unavailable image; no external resource loaded",
            location=location,
        )
        if location:
            path = self.assets.reference(location)
            return f"[source data]({path})※"
        return self.fallback(self.current_block, "Image source unavailable")

    def plain_text(self, unit, japanese):
        self.seen[japanese].add(unit.unit_id)
        text = (
            self.translated.get(unit.unit_id, unit.source_text)
            if japanese
            else unit.source_text
        )
        for token in unit.tokens:
            text = text.replace(token.start_marker, str(token.value.get("text", "")))
            if token.end_marker:
                text = text.replace(token.end_marker, "")
        return text

    def text(self, unit: TranslationUnit, japanese: bool) -> str:
        self.seen[japanese].add(unit.unit_id)
        text = (
            self.translated.get(unit.unit_id, unit.source_text)
            if japanese
            else unit.source_text
        )
        tokens = {t.start_marker: t for t in unit.tokens}
        chunks = re.split(r"(⟦(?:=|/)?[0-9]+⟧)", text)
        stack = []
        result = []
        for chunk in chunks:
            if chunk in tokens:
                token = tokens[chunk]
                value: Any = token.value
                kind = token.kind
                if token.end_marker:
                    tag = {
                        "emphasis": "em",
                        "strong": "strong",
                        "b": "strong",
                        "i": "em",
                        "subscript": "sub",
                        "superscript": "sup",
                        "sub": "sub",
                        "sup": "sup",
                    }.get(kind, "span")
                    attrs = ""
                    if kind == "link":
                        target = self.link(value)
                        if target:
                            tag, attrs = (
                                "a",
                                f' href="{html.escape(target, quote=True)}"',
                            )
                        else:
                            self.problem(
                                self.current_block,
                                "Unresolved or unsafe link; original target retained",
                                location=value.get("location"),
                            )
                    attrs += self.attributes(value, japanese)
                    result.append(f"<{tag}{attrs}>")
                    stack.append(f"</{tag}>")
                elif kind in {"code", "identifier"}:
                    result.append(
                        f"<code{self.attributes(value, japanese)}>"
                        + html.escape(str(value.get("text", "")))
                        + "</code>"
                    )
                elif kind == "formula":
                    if "text" in value:
                        formula = value["text"]
                        result.append(
                            "$" + formula + "$"
                            if safe_latex(formula)
                            else self.fallback(
                                self.current_block,
                                "Unsafe inline formula retained as source data",
                            )
                        )
                    else:
                        formula = self.assets.mathml(value["location"])
                        if formula is None:
                            self.problem(
                                self.current_block,
                                "Unsupported MathML retained as inert source data",
                                location=value["location"],
                            )
                            formula = f"[source data]({self.assets.reference(value['location'])})※"
                        result.append(formula)
                elif kind == "figure":
                    result.append(self.image(value, japanese))
                elif kind == "br":
                    result.append(f"<br{self.attributes(value, japanese)} />")
                else:
                    result.append(_escape(str(value.get("text", ""))))
            elif chunk.startswith("⟦/"):
                result.append(stack.pop())
            else:
                result.append(_escape(chunk))
        return "".join(result)

    def unit_text(self, unit, japanese):
        text = self.text(unit, japanese)
        marked = (
            japanese and unit.unit_id not in self.translated
        ) or unit.unit_id in self.marked_units
        return text + ("※" if marked else "")

    def epub_section(self, block, japanese):
        tree = block.structure["tree"]
        nodes = tree["nodes"]
        by_node = {}
        for unit in self.units:
            if unit.block_id == block.block_id and not unit.attribute:
                by_node.setdefault(unit.node_path, []).append(unit)

        def node_anchor(path):
            node = nodes[path]
            anchor = self.node_anchors[node["location"]["resource_path"], path]
            marked = any(
                u.attribute
                and u.node_path == path
                and (
                    u.unit_id in self.marked_units
                    or (japanese and u.unit_id not in self.translated)
                )
                for u in self.units
                if u.block_id == block.block_id
            )
            return f'<a id="{anchor}"{self.attributes(node, japanese)}></a>' + (
                "※" if marked else ""
            )

        def plain(path):
            return "".join(
                part if isinstance(part, str) else plain(part["node_path"])
                for part in nodes[path]["content"]
            )

        def children(path):
            return [
                p["node_path"] for p in nodes[path]["content"] if isinstance(p, dict)
            ]

        def walk(path, figure=False):
            node = nodes[path]
            name = node["tag"].removeprefix("{http://www.w3.org/1999/xhtml}")
            if node["disposition"] == "excluded":
                return ""
            if name in {"ul", "ol", "dl"}:
                return "\n\n".join(walk(p) for p in children(path))
            prefix = node_anchor(path) + "\n\n"
            if node["disposition"] == "inert":
                if node["tag"] == "{http://www.w3.org/1998/Math/MathML}math":
                    value = self.assets.mathml(node["location"])
                    if value is not None:
                        return prefix + value
                self.problem(
                    block,
                    "Inert markup retained as source data",
                    location=node["location"],
                )
                return (
                    prefix
                    + f"[source data]({self.assets.reference(node['location'])})※"
                )
            if name == "pre":
                return (
                    prefix
                    + _fence(plain(path))
                    + ("\n\n※" if block.block_id in self.marked else "")
                )
            if name == "img":
                return prefix + self.image(node, japanese)
            if figure and name not in {"figure", "figcaption"}:
                return prefix + "\n\n".join(walk(p, True) for p in children(path))
            if name == "table":
                cells = []
                captions = []
                rows = []

                def collect_rows(p, group):
                    for child in children(p):
                        name = nodes[child]["tag"].split("}")[-1]
                        if name == "tr":
                            rows.append((child, group))
                        elif name == "caption":
                            captions.append(walk(child))
                        elif name != "table":
                            collect_rows(
                                child,
                                child if name in {"thead", "tbody", "tfoot"} else group,
                            )

                collect_rows(path, path)

                def deferred_content(p):
                    return any(
                        nodes[child]["tag"].split("}")[-1]
                        in {"pre", "table", "ul", "ol", "dl", "figure"}
                        or deferred_content(child)
                        for child in children(p)
                        if nodes[child]["disposition"] != "excluded"
                    )

                table_anchors = []
                spans = []
                for row, (row_path, group) in enumerate(rows):
                    spans = (
                        [s for s in spans if s[0] > row]
                        if row and group == rows[row - 1][1]
                        else []
                    )
                    column = 0
                    for child in children(row_path):
                        name = nodes[child]["tag"].split("}")[-1]
                        if name not in {"th", "td"}:
                            continue
                        while occupied := next(
                            (s for s in spans if s[1] <= column < s[2]), None
                        ):
                            column = occupied[2]
                        child_units = by_node.get(child, [])
                        geometry = (
                            child_units[0].context["table"] if child_units else {}
                        )
                        column = geometry.get("column", column)
                        attrs = nodes[child]["attributes"]
                        row_span = geometry.get(
                            "row_span", int(attrs.get("rowspan", 1))
                        )
                        if row_span == 0:
                            row_span = sum(g == group for _, g in rows[row:])
                        column_span = geometry.get(
                            "column_span", int(attrs.get("colspan", 1))
                        )
                        table_anchors.append(node_anchor(child))
                        if deferred_content(child):
                            location = nodes[child]["location"]
                            self.problem(
                                block,
                                "Unsupported table cell structure retained as source data",
                                location=location,
                            )
                            reference = self.assets.reference(location)
                            rendered = f'<a href="{reference}">source data</a>※'
                        else:
                            rendered = "".join(
                                self.unit_text(u, japanese) for u in child_units
                            )
                        cells.append(
                            {
                                "row": row,
                                "column": column,
                                "row_span": row_span,
                                "column_span": column_span,
                                "heading": name == "th",
                                "rendered": rendered,
                                "rendered_attributes": self.attributes(
                                    nodes[child], japanese
                                ),
                            }
                        )
                        spans.append((row + row_span, column, column + column_span))
                        column += column_span
                return (
                    prefix
                    + "\n".join(table_anchors)
                    + "\n\n"
                    + "\n\n".join(captions + [self.table(cells, japanese)])
                )
            own = by_node.get(path, [])
            if own and own[0].kind == "body":
                groups = []
                for unit in own:
                    parent = unit.parent_unit_id or unit.unit_id
                    if not groups or groups[-1][0] != parent:
                        groups.append((parent, []))
                    groups[-1][1].append(unit)
                pieces = []
                pending = False
                index = 0

                def flush():
                    nonlocal pending, index
                    if pending:
                        if index < len(groups):
                            pieces.append(
                                "".join(
                                    self.unit_text(u, japanese)
                                    for u in groups[index][1]
                                )
                            )
                            index += 1
                        pending = False

                block_tags = {
                    "p",
                    "li",
                    "dt",
                    "dd",
                    "td",
                    "th",
                    "caption",
                    "figcaption",
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
                    *(f"h{i}" for i in range(1, 7)),
                }
                for part in node["content"]:
                    if isinstance(part, str):
                        pending = pending or bool(part.strip())
                    else:
                        child = part["node_path"]
                        child_node = nodes[child]
                        if child_node["tag"].split("}")[-1] in block_tags:
                            flush()
                            pieces.append(walk(child))
                        else:
                            pending = (
                                pending
                                or child_node["disposition"] == "structured"
                                or child_node["tag"]
                                in {
                                    "{http://www.w3.org/1998/Math/MathML}math",
                                    "{http://www.w3.org/2000/svg}svg",
                                }
                            )
                flush()
                return prefix + "\n\n".join(pieces)
            if own:
                text = "".join(self.unit_text(u, japanese) for u in own)
                if name in {"h1", "h2", "h3", "h4", "h5", "h6"}:
                    text = "#" * int(name[1]) + " " + text
                elif name in {"li", "dt", "dd"}:
                    context = own[0].context
                    containers = context.get("list", {}).get("containers", [])
                    marker = "-"
                    if containers and containers[-1]["tag"] == "ol":
                        container_path = containers[-1]["node_path"]
                        siblings = [
                            p
                            for p in children(container_path)
                            if nodes[p]["tag"].endswith("}li")
                        ]
                        start = int(containers[-1]["attributes"].get("start", 1))
                        marker = str(start + siblings.index(path)) + "."
                    text = (
                        "    " * (context["list"]["depth"] - 1)
                        + marker
                        + " "
                        + prefix.strip()
                        + text
                    )
                    prefix = ""
                # Nested lists/tables/pre are deferred by unit generation.
                deferred = []

                def find(p):
                    for child in children(p):
                        tag = nodes[child]["tag"].split("}")[-1]
                        if tag in {"ul", "ol", "dl", "table", "pre", "figure"}:
                            deferred.append(walk(child))
                        else:
                            find(child)

                find(path)

                def inert_references(p):
                    for child in children(p):
                        child_node = nodes[child]
                        if child_node["disposition"] == "inert":
                            if child_node["tag"] not in {
                                "{http://www.w3.org/1998/Math/MathML}math",
                                "{http://www.w3.org/2000/svg}svg",
                            }:
                                deferred.append(walk(child))
                        elif child_node["tag"].split("}")[-1] not in {
                            "ul",
                            "ol",
                            "dl",
                            "table",
                            "pre",
                            "figure",
                        }:
                            inert_references(child)

                inert_references(path)
                # Anchors for inline descendants are retained without duplicating text.
                inline_anchors = []

                def anchors(p):
                    for child in children(p):
                        if nodes[child]["tag"].split("}")[-1] in {
                            "ul",
                            "ol",
                            "dl",
                            "table",
                            "pre",
                            "figure",
                        }:
                            continue
                        if nodes[child].get("original_ids"):
                            inline_anchors.append(node_anchor(child))
                        anchors(child)

                anchors(path)
                return (
                    prefix
                    + text
                    + "".join(inline_anchors)
                    + ("\n\n" + "\n\n".join(deferred) if deferred else "")
                )
            return prefix + "\n\n".join(
                walk(p, figure or name == "figure") for p in children(path)
            )

        rendered = walk(tree["root"])
        # Non-text nodes still have addressable source positions (e.g. table rows).
        present = set(re.findall(r'id="([^"]+)"', rendered))
        extra = [
            node_anchor(path)
            for (resource, path), anchor in self.node_anchors.items()
            if resource == nodes[tree["root"]]["location"]["resource_path"]
            and anchor not in present
        ]
        return rendered + "\n\n" + "\n".join(extra)

    def problem(self, block, reason: str, *, location=None, unit=None):
        related = [
            u
            for u in self.units
            if u.block_id == block.block_id
            and (
                u.unit_id == unit.unit_id
                if unit
                else not location
                or u.locations[0].format != "epub"
                or (
                    u.locations[0].resource_path == location.get("resource_path")
                    and (
                        location.get("node_path", "").startswith(
                            (u.node_path or "") + "/"
                        )
                        or location.get("node_path") == u.node_path
                        or (u.node_path or "").startswith(
                            location.get("node_path", "") + "/"
                        )
                    )
                )
            )
        ]
        anchors = list(dict.fromkeys(self.unit_anchor(u) for u in related)) or [
            self.anchors[block.block_id]
        ]
        item = {
            "reason": reason,
            "target_ids": [unit.unit_id] if unit else [block.block_id],
            "block_ids": [block.block_id],
            "unit_ids": [u.unit_id for u in related],
            "locations": [location]
            if location
            else [p.model_dump(mode="json") for p in block.locations],
            "stage": "render",
            "origin": "processing",
            "output_anchors": anchors,
        }
        if item not in self.concerns:
            self.concerns.append(item)
        if not location and not unit:
            self.marked.add(block.block_id)
        self.marked_units.update(u.unit_id for u in related)

    def fallback(self, block, reason: str) -> str:
        self.problem(block, reason)
        digest = self.source_digest
        return f"[source data](../../snapshots/source/{digest}.json)※"

    def table(self, cells: list[dict], japanese: bool) -> str:
        if not cells:
            return ""
        rows = max(c["row"] + 1 for c in cells)
        columns = max(c["column"] + c.get("column_span", 1) for c in cells)
        by_position = {(c["row"], c["column"]): c for c in cells}

        def content(cell):
            return cell["rendered"]

        simple = (
            all(
                c.get("row_span", 1) == c.get("column_span", 1) == 1
                and not c.get("rendered_attributes")
                for c in cells
            )
            and columns <= len(cells)
            and all(by_position.get((0, c), {}).get("heading") for c in range(columns))
        )
        if simple:
            lines = []
            for row in range(rows):
                lines.append(
                    "| "
                    + " | ".join(
                        content(by_position[row, col])
                        if (row, col) in by_position
                        else ""
                        for col in range(columns)
                    )
                    + " |"
                )
                if row == 0:
                    lines.append("| " + " | ".join(["---"] * columns) + " |")
            return "\n".join(lines)
        lines = ["<table>"]
        for row in range(rows):
            lines.append("<tr>")
            for cell in cells:
                if cell["row"] != row:
                    continue
                tag = "th" if cell.get("heading") else "td"
                attrs = "".join(
                    f' {name}="{cell.get(key, 1)}"'
                    for key, name in (
                        ("row_span", "rowspan"),
                        ("column_span", "colspan"),
                    )
                    if cell.get(key, 1) != 1
                )
                attrs += cell.get("rendered_attributes", "")
                lines.append(f"<{tag}{attrs}>{content(cell)}</{tag}>")
            lines.append("</tr>")
        lines.append("</table>")
        return "\n".join(lines)

    def document(self, japanese: bool) -> str:
        parts = []
        for block in self.source.blocks:
            self.current_block = block
            structure: Any = block.structure
            block_units = self.unit_by_block.get(block.block_id, [])
            unit = block_units[0] if block_units else None
            marked = block.block_id in self.marked or (
                japanese and any(u.unit_id not in self.translated for u in block_units)
            )
            if block.kind == "section":
                rendered = self.epub_section(block, japanese)
            elif block.kind == "code":
                rendered = _fence(block.text, structure.get("language") or "")
                if marked:
                    rendered += "\n\n※"
            elif block.kind == "formula":
                rendered = (
                    "$$\n" + block.text + "\n$$" + ("\n\n※" if marked else "")
                    if safe_latex(block.text)
                    else self.fallback(block, "Unsafe formula retained as source data")
                )
            elif block.kind == "table":
                if structure.get("structure_confirmed") is False:
                    rendered = self.fallback(
                        block, "Unconfirmed table; original structure retained"
                    )
                    pages = {p.page for p in block.locations if p.format == "pdf"}
                    for page in structure.get("page_images", []):
                        if page["page"] in pages:
                            image = self.assets.pdf_picture(
                                {
                                    "page_images": [page],
                                    "region": {
                                        "page": page["page"],
                                        "left": 0,
                                        "top": 0,
                                        "right": page["width_pixels"],
                                        "bottom": page["height_pixels"],
                                    },
                                }
                            )
                            rendered += f'\n\n<img src="{image}" alt="" />'

                else:
                    cells = []
                    for cell in structure.get("cells", []):
                        cell_units = [
                            u
                            for u in self.units
                            if u.block_id == block.block_id
                            and u.cell_id == cell["cell_id"]
                        ]
                        if not cell_units and cell["text"].strip():
                            continue
                        cells.append(
                            {
                                **cell,
                                "rendered": "".join(
                                    self.unit_text(u, japanese) for u in cell_units
                                ),
                            }
                        )
                    rendered = self.table(cells, japanese)
                    if marked:
                        rendered += "\n\n※"
            elif block.kind in {"picture", "figure"}:
                image = self.assets.pdf_picture(structure)
                rendered = (
                    f'<img src="{image}" alt="" />' + ("\n\n※" if marked else "")
                    if image
                    else self.fallback(
                        block, "Figure image unavailable; original data retained"
                    )
                )
            elif unit:
                text = "".join(self.unit_text(u, japanese) for u in block_units)
                if block.kind == "heading":
                    rendered = "#" * min(6, structure.get("level", 1)) + " " + text
                elif block.kind == "list":
                    marker = structure.get("marker", "-")
                    marker = (
                        marker if re.fullmatch(r"(?:[-+*]|[0-9]+[.)])", marker) else "-"
                    )
                    rendered = (
                        "    " * (structure.get("depth", 1) - 1)
                        + marker
                        + f' <a id="{self.anchors[block.block_id]}"></a>'
                        + text
                    )
                elif block.kind == "callout":
                    rendered = f"{_escape(structure.get('marker', ''))} ({_escape(structure.get('code_id', ''))}; lines {', '.join(map(str, structure.get('lines', [])))}): {text}"
                else:
                    rendered = text
            else:
                continue
            anchor = self.anchors[block.block_id]
            parts.append(
                rendered
                if block.kind == "list"
                else f'<a id="{anchor}"></a>\n\n{rendered}'
            )
        return "\n\n".join(parts) + "\n"


def _usage(workspace: Path) -> dict:
    stages = {}
    for stage, directory in (
        ("preparation", "preparation"),
        ("correction", "pdf-correction"),
        ("translation", "translation"),
        ("review", "review"),
    ):
        records = []
        for path in sorted((workspace / "attempts" / directory).glob("*.json")):
            data = path.read_bytes()
            if _hash(data) != path.stem:
                raise ValueError("Usage attempt artifact SHA-256 mismatch")
            records.append(
                {
                    "artifact": str(path.relative_to(workspace)),
                    "sha256": path.stem,
                    "record": json.loads(data),
                }
            )
        stages[stage] = records
    return {
        "stages": stages,
        "wall_seconds": None,
        "local_processing_seconds": None,
        "actual_billing": "unknown",
        "note": "All saved attempts, including failures/retries; raw usage retained, unknown is not zero. No reasoning/output or price totals inferred. Lost responses or force termination can leave usage unknown; resending may duplicate provider charges (no exactly-once billing guarantee).",
    }


def _build(workspace: Path):
    source, source_digest, translation, translation_digest, stale, units = _inputs(
        workspace
    )
    usage = _usage(workspace)
    active = translation if not stale else None
    from doc_translate.review import load_reviews

    review_history = load_reviews(workspace)
    current_reviews = [
        r
        for r in review_history
        if r["source_snapshot_sha256"] == source_digest
        and r["translation_snapshot_sha256"] == translation_digest
        and not stale
    ]
    review_findings = [f for r in current_reviews for f in r["findings"]]
    renderer = _Markdown(workspace, source, units, active, review_findings)
    for unit in units:
        if unit.unit_id not in renderer.translated:
            block = next(b for b in source.blocks if b.block_id == unit.block_id)
            renderer.problem(
                block,
                "Translation unavailable"
                + (
                    "; saved translation belongs to different source or translation inputs"
                    if stale
                    else ""
                ),
                location=unit.locations[0].model_dump(mode="json"),
                unit=unit,
            )
    japanese, english = renderer.document(True), renderer.document(False)
    accepted = {item.unit_id for item in active.translations} if active else set()
    failed = (
        [u.unit_id for chunk in active.chunks if chunk.error for u in chunk.units]
        if active
        else []
    )
    not_processed = [
        u.unit_id
        for u in units
        if u.unit_id not in accepted and u.unit_id not in failed
    ]
    unit_ids = {u.unit_id for u in units}
    reached = renderer.seen[True] & renderer.seen[False]
    coverage = reached == unit_ids
    reference_checks = []
    for document in (japanese, english):
        anchors = re.findall(r'id="([^"]+)"', document)
        references = re.findall(r'href="#([^"]+)"', document)
        images = re.findall(r'src="([^"]+)"', document)
        reference_checks.append(
            len(anchors) == len(set(anchors))
            and set(references) <= set(anchors)
            and all(path in renderer.assets.files for path in images)
        )
    output_checks = [
        {
            "name": "render_unit_coverage",
            "passed": coverage,
            "target_ids": sorted(
                unit_ids - (renderer.seen[True] & renderer.seen[False])
            ),
            "details": "Every unit rendered in both languages (attributes included); does not prove semantic completeness.",
        },
        {
            "name": "render_references",
            "passed": all(reference_checks),
            "target_ids": [],
            "details": "Generated anchors are unique, internal targets exist and displayed assets are local validated files.",
        },
    ]
    completed = (
        coverage
        and all(reference_checks)
        and not failed
        and not not_processed
        and source.processing.status == "completed"
    )
    processing = {
        "status": "completed" if completed else "partial",
        "output_reached": [
            u.unit_id for u in units if u.unit_id in accepted and u.unit_id in reached
        ],
        "output_reached_blocks": [
            b.block_id
            for b in source.blocks
            if f'id="{renderer.anchors[b.block_id]}"' in japanese
        ],
        "not_processed": not_processed,
        "failed": failed,
        "source": source.processing.model_dump(),
        "render_failed": sorted(
            unit_ids - (renderer.seen[True] & renderer.seen[False])
        ),
    }
    translation_reviewed = {uid for r in current_reviews for uid in r["unit_ids"]}
    reviews = [r.model_dump(mode="json") for r in source.content_reviews]
    reviewed = (
        {target for r in source.content_reviews for target in r.target_ids}
        | translation_reviewed
        | {bid for r in current_reviews for bid in r["block_ids"]}
    )
    qa = {
        "checks": [c.model_dump(mode="json") for c in source.checks]
        + output_checks
        + [
            {
                "name": "saved_inputs_and_unit_structure",
                "passed": True,
                "details": "Source/translation hashes, IDs and protected markers validated; not a meaning check.",
            }
        ],
        "uncertainties": renderer.concerns,
        "content_review": {
            "records": reviews,
            "unreviewed_source_block_ids": [
                b.block_id for b in source.blocks if b.block_id not in reviewed
            ],
            "unreviewed_source_unit_ids": [
                u.unit_id
                for u in units
                if u.unit_id not in reviewed and u.block_id not in reviewed
            ],
            "translation_records": current_reviews,
            "review_history": review_history,
            "unreviewed_translation_unit_ids": [
                u.unit_id for u in units if u.unit_id not in translation_reviewed
            ],
            "meaning_quality_approved": False,
            "viewer_display_checked": False,
        },
    }
    for block in source.blocks:
        renderer.mapping["blocks"].append(
            {
                "block_id": block.block_id,
                "kind": block.kind,
                "locations": [p.model_dump(mode="json") for p in block.locations],
                "output_anchor": renderer.anchors[block.block_id]
                if f'id="{renderer.anchors[block.block_id]}"' in japanese
                else None,
            }
        )
    summary = {
        "schema_version": 1,
        "source_snapshot_sha256": source_digest,
        "translation_snapshot_sha256": translation_digest,
        "stale_translation": stale,
        "usage": {
            "artifact_sha256": _hash(_json(usage)),
            "attempts_by_stage": {
                stage: len(records) for stage, records in usage["stages"].items()
            },
            "wall_seconds": None,
            "actual_billing": "unknown",
            "note": usage["note"],
        },
        "processing": processing,
        "scope": {
            "format": source.document.format,
            "pages_with_source_blocks": sorted(
                {
                    p.page
                    for b in source.blocks
                    for p in b.locations
                    if p.format == "pdf"
                }
            ),
            "ownership_records": [
                c.details for c in source.checks if c.name == "pdf_chunk_ownership"
            ],
            "whole_document_verified": source.document.format == "epub",
            "note": "PDF ownership records describe only the selected source scope, not verified whole-PDF coverage. Pages list source block positions, not reference-page ownership.",
        },
        "uncertainty_count": len(renderer.concerns),
        "original_missing_count": sum(
            c["origin"] == "original" for c in renderer.concerns
        ),
        "machine_checks": qa["checks"],
        "content_review": qa["content_review"],
    }
    if (workspace / "run.json").exists():
        from doc_translate.run import RunManifest

        run = RunManifest.model_validate_json((workspace / "run.json").read_bytes())
        if run.document != source.document:
            raise ValueError("Run manifest/document mismatch")
        summary["scope"]["run"] = run.model_dump(mode="json")
        from doc_translate.run import source_adoption_status

        summary["scope"]["adopted_source"] = (
            source.machine_inputs.model_dump(mode="json")
            if source.machine_inputs
            else None
        )
        adoption = source_adoption_status(workspace, run)
        if adoption is not None:
            summary["source_adoption"] = adoption
        if run.error is not None or adoption is not None:
            summary["processing"]["status"] = "partial"
    files = {
        "japanese.md": japanese.encode(),
        "english.md": english.encode(),
        "source-map.json": _json(renderer.mapping),
        "qa.json": _json(qa),
        "usage.json": _json(usage),
    }
    files.update(renderer.assets.files)
    return summary, files


def render_document(workspace: Path) -> dict:
    """Regenerate derived output only; owns the non-reentrant workspace writer lock."""
    with workspace_lock(workspace):
        started = time.monotonic()
        summary, files = _build(workspace)
        summary["render_elapsed_seconds"] = time.monotonic() - started
        summary["render_current"] = True
        summary["adopted_output_current"] = not summary["stale_translation"]
        digest = _hash(
            _json(
                {
                    "version": 1,
                    "summary": summary,
                    "files": {name: _hash(data) for name, data in files.items()},
                }
            )
        )
        relative = f"renders/{digest}"
        directory = workspace / relative
        directory.mkdir(parents=True, exist_ok=True)
        summary["artifacts"] = {
            "japanese": f"{relative}/japanese.md",
            "english": f"{relative}/english.md",
            "source_map": f"{relative}/source-map.json",
            "qa": f"{relative}/qa.json",
            "usage": f"{relative}/usage.json",
            "summary": f"{relative}/summary.json",
        }
        files["summary.json"] = _json(summary)
        for name, data in files.items():
            (directory / name).parent.mkdir(parents=True, exist_ok=True)
            _atomic_write(directory / name, data)
        manifest = {
            "schema_version": 1,
            "summary": summary["artifacts"]["summary"],
            "files": {
                f"{relative}/{name}": _hash(data) for name, data in files.items()
            },
        }
        _atomic_write(workspace / "render.json", _json(manifest))
        return summary


def workspace_status(workspace: Path) -> dict:
    """Inspect current saved inputs and verified output without writing or inference."""
    if (
        not (workspace / "source-snapshot.json").exists()
        and (workspace / "run.json").exists()
    ):
        from doc_translate.run import RunManifest, failed_run_summary

        return failed_run_summary(
            workspace,
            RunManifest.model_validate_json((workspace / "run.json").read_bytes()),
        )
    summary, _files = _build(workspace)
    pointer = workspace / "render.json"
    summary["artifacts"] = {}
    summary["render_current"] = False
    summary["adopted_output_current"] = False
    if pointer.exists():
        manifest = RenderReference.model_validate_json(pointer.read_bytes())
        if manifest.summary not in manifest.files:
            raise ValueError("Render summary missing from hashed files")
        for relative, digest in manifest.files.items():
            path = (workspace / relative).resolve()
            if (
                not path.is_relative_to(workspace.resolve())
                or _hash(path.read_bytes()) != digest
            ):
                raise ValueError("Render artifact path/hash mismatch")
        saved = json.loads((workspace / manifest.summary).read_bytes())
        summary["adopted_output_current"] = (
            saved["source_snapshot_sha256"] == summary["source_snapshot_sha256"]
            and saved["translation_snapshot_sha256"]
            == summary["translation_snapshot_sha256"]
            and not summary["stale_translation"]
        )
        if saved.get("source_adoption") == summary.get("source_adoption") and all(
            saved[key] == summary[key]
            for key in (
                "source_snapshot_sha256",
                "translation_snapshot_sha256",
                "usage",
                "scope",
                "processing",
                "content_review",
            )
        ):
            summary = {
                **saved,
                "adopted_output_current": summary["adopted_output_current"],
            }
        else:
            summary["previous_artifacts"] = saved["artifacts"]
    return summary
