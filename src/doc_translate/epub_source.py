"""EPUB resource-sized source sections; structured data, never display markup."""

from typing import NotRequired, TypedDict, cast
from xml.etree.ElementTree import Element

from pydantic import JsonValue

from doc_translate.epub import EPUB, EPUB_TYPE, XHTML, EPUBError, _parse_xml
from doc_translate.snapshot import (
    EPUBLocation,
    MachineCheck,
    Processing,
    SourceBlock,
    SourceDocument,
    SourceSnapshot,
    Uncertainty,
)

_SEMANTIC_ATTRIBUTES = {
    "alt",
    "title",
    "rowspan",
    "colspan",
    "scope",
    "headers",
    "start",
    "value",
    "type",
    "reversed",
    "role",
    "lang",
    "{http://www.w3.org/XML/1998/namespace}lang",
    EPUB_TYPE,
}
_INERT_TAGS = {
    f"{XHTML}{tag}"
    for tag in (
        "script",
        "style",
        "iframe",
        "object",
        "embed",
        "audio",
        "video",
        "canvas",
        "form",
        "input",
        "button",
        "textarea",
        "select",
    )
}
_XML_ID = "{http://www.w3.org/XML/1998/namespace}id"
_REFERENCE_ATTRIBUTES = {
    "href",
    "src",
    "data",
    "poster",
    "longdesc",
    "cite",
    "{http://www.w3.org/1999/xlink}href",
}


class _Reference(TypedDict):
    attribute: str
    uri: str
    resource_path: str | None
    fragment: str | None
    query: str
    external_uri: str | None
    status: str
    targets: list[dict[str, JsonValue]]
    target_index: NotRequired[dict[str, str]]


class _Node(TypedDict):
    tag: str
    location: dict[str, JsonValue]
    role: str
    disposition: str
    attributes: dict[str, str]
    content: list[str | dict[str, str]]
    references: NotRequired[list[_Reference]]
    original_ids: NotRequired[dict[str, str]]


def extract_epub_source(
    book: EPUB, *, document: SourceDocument, snapshot_id: str
) -> SourceSnapshot:
    """Extract validated EPUB data with caller-supplied original archive provenance."""
    if document.format != "epub":
        raise ValueError("EPUB extraction needs EPUB document provenance")
    blocks: list[SourceBlock] = []
    uncertainties: list[Uncertainty] = []
    spine_by_path: dict[str, list[dict[str, JsonValue]]] = {}
    xhtml_paths = {
        item.resource_path
        for item in book.manifest.values()
        if item.media_type == "application/xhtml+xml" and item.resource_path is not None
    }
    nav_paths = {
        item.resource_path
        for item in book.manifest.values()
        if "nav" in item.properties
    }
    for index, spine in enumerate(book.spine, 1):
        path = book.manifest[spine.resolved_id].resource_path
        assert path is not None
        spine_by_path.setdefault(path, []).append(
            {
                "index": index,
                "idref": spine.idref,
                "resolved_id": spine.resolved_id,
                "fallback_chain": list(spine.fallback_chain),
                "linear": spine.linear,
                "properties": list(spine.properties),
            }
        )
    queue = list(spine_by_path)
    discovered = set(queue)
    trees: dict[str, _Node] = {}
    resource_nodes: dict[str, dict[str, _Node]] = {}
    ids: dict[str, dict[str, list[_Node]]] = {}
    pending: list[tuple[_Reference, EPUBLocation, str]] = []
    checks: list[MachineCheck] = []
    inert_resources: set[str] = set()

    def uncertain(reason: str, location: EPUBLocation, block_id: str) -> None:
        uncertainties.append(
            Uncertainty(
                reason=reason,
                target_ids=[block_id],
                locations=[location],
                stage="epub_extraction",
                origin="undetermined",
            )
        )

    def extract_resource(path: str) -> None:
        root = _parse_xml(book.resources[path], path)
        text: list[str] = []
        block_id = f"epub-{len(blocks) + 1}"
        ids[path] = {}
        resource_nodes[path] = {}
        spine_records = spine_by_path.get(path, [])
        coverage = {"structured": 0, "excluded": 0, "inert": 0}
        resource_role = (
            "toc"
            if path in nav_paths
            else (
                "body"
                if any(record["linear"] for record in spine_records)
                else "supplement"
            )
        )

        def visit(
            element: Element,
            position: str,
            in_body: bool = False,
            inert: bool = False,
            role: str = resource_role,
        ) -> _Node:
            newly_inert = element.tag in _INERT_TAGS or not element.tag.startswith(
                XHTML
            )
            parent_inert = inert
            inert = inert or newly_inert
            if "toc" in element.get(EPUB_TYPE, "").split():
                role = "toc"
            elif set(element.get(EPUB_TYPE, "").split()) & {
                "footnote",
                "endnote",
                "rearnote",
                "footnotes",
                "endnotes",
                "rearnotes",
            }:
                role = "supplement"
            location = EPUBLocation(
                format="epub",
                resource_path=path,
                element_id=element.get("id") or element.get(_XML_ID) or None,
                node_path=position,
                node_snapshot=snapshot_id,
            )
            node: _Node = {
                "tag": element.tag,
                "location": location.model_dump(mode="json", exclude_none=True),
                "role": role,
                "disposition": "inert"
                if inert
                else ("structured" if in_body or element is root else "excluded"),
                "attributes": {
                    key: value
                    for key, value in element.attrib.items()
                    if key in _SEMANTIC_ATTRIBUTES and in_body and not inert
                },
                "content": [],
            }
            resource_nodes[path][position] = node
            coverage[node["disposition"]] += 1
            original_ids = {
                key: element.attrib[key]
                for key in ("id", _XML_ID)
                if key in element.attrib
            }
            if original_ids:
                node["original_ids"] = original_ids
            for original_id in dict.fromkeys(original_ids.values()):
                if original_id:
                    ids[path].setdefault(original_id, []).append(node)
            if in_body and newly_inert and not parent_inert:
                uncertain(
                    "Content retained as inert source reference, not display markup",
                    location,
                    block_id,
                )
            for attribute, uri in element.attrib.items():
                if attribute not in _REFERENCE_ATTRIBUTES:
                    continue
                reference: _Reference = {
                    "attribute": attribute,
                    "uri": uri,
                    "resource_path": None,
                    "fragment": None,
                    "query": "",
                    "external_uri": None,
                    "status": "unresolved",
                    "targets": [],
                }
                node.setdefault("references", []).append(reference)
                try:
                    resolved = book.resolve(path, uri)
                except EPUBError as exc:
                    uncertain(str(exc), location, block_id)
                    continue
                reference["resource_path"] = resolved.resource_path
                reference["fragment"] = resolved.fragment
                reference["query"] = resolved.query
                reference["external_uri"] = resolved.external_uri
                if resolved.external_uri is not None:
                    reference["status"] = "external"
                else:
                    pending.append((reference, location, block_id))
                    target = resolved.resource_path
                    if (
                        target is not None
                        and target not in discovered
                        and node["disposition"] == "structured"
                        and (
                            target in xhtml_paths
                            or target.lower().endswith((".xhtml", ".html"))
                        )
                    ):
                        discovered.add(target)
                        queue.append(target)
            if in_body and not inert and element.text is not None:
                node["content"].append(element.text)
                text.append(element.text)
            for index, child in enumerate(element, 1):
                child_position = f"{position}/{index}"
                visit(
                    child,
                    child_position,
                    in_body or (element is root and child.tag == f"{XHTML}body"),
                    inert,
                    role,
                )
                node["content"].append({"node_path": child_position})
                if in_body and not inert and child.tail is not None:
                    node["content"].append(child.tail)
                    text.append(child.tail)
            return node

        renderable = (
            root.tag == f"{XHTML}html" and root.find(f"{XHTML}body") is not None
        )
        tree = visit(root, "/1", inert=not renderable)
        target_index: dict[str, JsonValue] = {}
        if not renderable:
            uncertain(
                "Referenced resource lacks XHTML html/body; retained as inert source",
                EPUBLocation.model_validate(tree["location"]),
                block_id,
            )
        for original_id, candidates in ids[path].items():
            if len(candidates) > 1:
                target_index[original_id] = [
                    {
                        "location": {
                            key: value
                            for key, value in candidate["location"].items()
                            if key != "element_id"
                        },
                        "disposition": candidate["disposition"],
                    }
                    for candidate in candidates
                ]
                uncertainties.append(
                    Uncertainty(
                        reason=f"Duplicate original ID: {original_id}",
                        target_ids=[block_id],
                        locations=[
                            EPUBLocation.model_validate(candidate["location"])
                            for candidate in candidates
                        ],
                        stage="epub_extraction",
                        origin="original",
                    )
                )
        checks.append(
            MachineCheck(
                name="epub_node_coverage",
                target_ids=[block_id],
                passed=sum(coverage.values()) == sum(1 for _ in root.iter()),
                details=f"{path}: {coverage}; every element has a snapshot-bound disposition",
            )
        )
        trees[path] = tree
        blocks.append(
            SourceBlock(
                block_id=block_id,
                kind="section",
                text="".join(text),
                locations=[EPUBLocation.model_validate(tree["location"])],
                structure={
                    "tree": {
                        "root": "/1",
                        "nodes": cast(JsonValue, resource_nodes[path]),
                    },
                    "spine": cast(JsonValue, spine_records),
                    "coverage": cast(JsonValue, coverage),
                    "target_index": target_index,
                },
            )
        )

    for path in queue:
        extract_resource(path)
    for reference, location, block_id in pending:
        target_path = reference["resource_path"]
        fragment = reference["fragment"]
        if target_path not in trees:
            reference["status"] = "inert_resource"
            assert target_path is not None
            inert_resources.add(target_path)
            uncertain(
                f"Target retained as inert resource: {reference['uri']}",
                location,
                block_id,
            )
            continue
        targets = (
            ids[target_path].get(fragment, []) if fragment else [trees[target_path]]
        )
        if len(targets) > 1:
            # Store duplicate-ID candidates once, not once per incoming link.
            assert target_path is not None and fragment is not None
            reference["target_index"] = {
                "resource_path": target_path,
                "element_id": fragment,
            }
        else:
            reference["targets"] = [
                {
                    "location": {
                        key: value
                        for key, value in target["location"].items()
                        if key != "element_id"
                    },
                    "disposition": target["disposition"],
                }
                for target in targets
            ]
        reference["status"] = (
            "resolved"
            if len(targets) == 1
            else ("missing_fragment" if not targets else "ambiguous_fragment")
        )
        if len(targets) != 1:
            uncertain(f"{reference['status']}: {reference['uri']}", location, block_id)
    # SourceBlock validation copies JSON: publish the completed reference resolutions.
    for block, node_table in zip(blocks, resource_nodes.values(), strict=True):
        block.structure["tree"] = {"root": "/1", "nodes": cast(JsonValue, node_table)}
    manifest_by_path: dict[str, list[JsonValue]] = {}
    for item in book.manifest.values():
        if item.resource_path is not None:
            manifest_by_path.setdefault(item.resource_path, []).append(
                {
                    "id": item.id,
                    "href": item.href,
                    "media_type": item.media_type,
                    "fragment": item.fragment,
                    "fallback": item.fallback,
                    "properties": list(item.properties),
                }
            )
    blocks[0].structure["package_path"] = book.package_path
    blocks[0].structure["resources"] = [
        {
            "resource_path": path,
            "byte_length": len(book.resources[path]),
            "manifest": manifest_by_path.get(path, []),
            "encryption": book.encryption.get(path),
            "disposition": trees[path]["disposition"]
            if path in trees
            else ("inert" if path in inert_resources else "unvisited"),
        }
        for path in sorted(book.resources)
    ]
    return SourceSnapshot(
        schema_version=1,
        snapshot_id=snapshot_id,
        document=document,
        blocks=blocks,
        uncertainties=uncertainties,
        checks=checks,
        processing=Processing(stage="epub_extraction", status="completed"),
    )
