"""Issue 79 preparation and Issue 80 isolated offline experiment (never real AI)."""

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
from collections import Counter
from importlib import import_module
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "tests"))

BOOK_SOURCE = "22325d32f4ed4babf3da97b7bfd94ea983b6629e2e805798b2e690c60575f471"
BOOK_CHUNKS = (
    "d3ec1fd401139b14d2b7b575333bb6007cf3e1fbcf0e07d4a36ffbab0fd180f5",
    "22df6ebc4b73be40d808c4cc02172493d8faf1a2220bd29db97e85a241cfd6b0",
    "6ecd126f3f096deff588b3df017850a7ff4b3b9ed1c8ef1deaffffa8ba7423fa",
    "e6c61dc45621910a42cd87c23de90c2a6ba1017a0142ad44c63436792a4dd3c7",
)
BOOK_OWNED = (
    list(range(36, 41)),
    list(range(81, 86)),
    list(range(181, 186)),
    list(range(186, 191)),
)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def new_directory(path, source=None):
    """Exclusive creation, with no symlink ancestors or existing workspace reuse."""
    path = path.absolute()
    if path != path.resolve():
        raise ValueError("Symlink output path refused")
    if source is not None:
        source = source.resolve()
        if path.is_relative_to(source) or source.is_relative_to(path):
            raise ValueError("Source/output overlap refused")
    if any(
        (parent / marker).exists()
        for parent in path.parents
        for marker in ("run.json", "source-snapshot.json", "pdf-draft.json")
    ):
        raise ValueError("Existing workspace output refused")
    if path.parent.stat().st_mode & 0o077:
        raise ValueError("Private parent directory (0700) required")
    path.mkdir(mode=0o700, exist_ok=False)
    return path


def inventory(directory):
    result = {}
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            raise ValueError("Symlink input refused")
        if path.is_file():
            result[str(path.relative_to(directory))] = [
                sha(path.read_bytes()),
                path.stat().st_mtime_ns,
            ]
    return result


def finish(output, observations):
    write_json(output / "observations.json", observations)
    hashes = {
        str(path.relative_to(output)): sha(path.read_bytes())
        for path in sorted(output.rglob("*"))
        if path.is_file()
    }
    write_json(output / "hashes.json", hashes)
    # Evidence is outside temporary workspaces; verify every retained byte after cleanup.
    for relative, digest in json.loads((output / "hashes.json").read_bytes()).items():
        if sha((output / relative).read_bytes()) != digest:
            raise ValueError("Evidence readback mismatch")


def synthetic(output):
    import pytest
    from pdf_source_samples import FAMILIES, acquire_fixture, recipe, response

    from doc_translate.pdf_source import assemble_pdf_source

    run = import_module("test_pdf_correction").run
    output = new_directory(output)
    payload = output / "synthetic-inputs"
    payload.mkdir()
    review = output / "review-only"
    review.mkdir()
    rows = []
    with tempfile.TemporaryDirectory(prefix="issue79-owned-") as temporary:
        owned = Path(temporary)
        for family in FAMILIES:
            for defective in (False, True):
                name = f"sample-{len(rows) + 1:02d}"
                case = owned / name
                case.mkdir()
                with pytest.MonkeyPatch.context() as patch:
                    draft = acquire_fixture(case, case / "workspace", family, patch)
                    pages = draft[2].target_pages
                    scopes = (
                        [[page] for page in pages]
                        if "connection" in family
                        else [pages]
                    )
                    digests = []
                    for index, scope in enumerate(scopes):
                        call = case / f"call-{index}"
                        call.mkdir()
                        digests.append(
                            run(
                                call,
                                draft,
                                response(draft[1], family, defective, scope),
                                owned_pages=scope,
                                reference_pages=[
                                    page for page in pages if page not in scope
                                ],
                            )
                        )
                    assembled = assemble_pdf_source(
                        workspace=draft[0],
                        chunk_sha256s=digests,
                        snapshot_id="prepared-synthetic",
                    )
                    destination = payload / name
                    # Copy, never hardlink; omit credential/model/response-control files.
                    shutil.copytree(draft[0], destination)
                    write_json(
                        destination / "current-source.json",
                        assembled.model_dump(mode="json"),
                    )
                    rows.append(
                        {
                            "sample": name,
                            "family": family,
                            "artificial_variant": "altered" if defective else "control",
                            "artificial_response": True,
                            "draft_sha256": draft[1],
                            "chunk_sha256s": digests,
                            "owned_pages": pages,
                            "images": [
                                f"synthetic-inputs/{name}/{page.image.artifact}"
                                for page in draft[2].pages
                            ],
                            "proposed_comparison": recipe(family, defective)[2],
                            "root_image_review": "pending",
                            "current_state": None,
                            "recoverability": None,
                            "expected_confirmation": None,
                            "expected_adoption": None,
                        }
                    )
    write_json(review / "proposed-comparisons.json", rows)
    finish(
        output,
        {
            "mode": "synthetic",
            "cases": len(rows),
            "real_ai_sends": 0,
            "operation": "acquire_pdf -> correct_pdf(real Node/faux/no-network) -> assemble_pdf_source",
            "artificial_responses_not_quality_results": True,
            "source_adoption": False,
            "root_labels_fixed": False,
            "cleanup": "owned temporary inputs/models/credentials/workspaces removed; synchronous helper children reaped",
        },
    )
    return output


def fragment_key(block):
    return tuple(
        (part["chunk_sha256"], part["block"]["block_id"])
        for part in block.structure["fragments"]
    )


def correspond(full, selected, owned):
    """Compare fragment identity, not renumbered source IDs; retain warning multiplicity."""
    selected_keys = {fragment_key(block) for block in selected.blocks}
    original = {
        fragment_key(block): block
        for block in full.blocks
        if any(location.page in owned for location in block.locations)
    }
    if set(original) != selected_keys or len(original) != len(selected.blocks):
        raise ValueError("Selected fragment series is not preserved")
    ids = {}
    rows = []
    for block in selected.blocks:
        for fragment in block.structure["fragments"]:
            for direction in ("incoming", "outgoing"):
                link = fragment["block"].get(direction)
                if link and link["target"] and link["target"]["page"] not in owned:
                    raise ValueError(
                        "Continuation requires ownership outside fixed scopes"
                    )
        previous = original[fragment_key(block)]
        if (previous.kind, previous.text, previous.locations) != (
            block.kind,
            block.text,
            block.locations,
        ):
            raise ValueError("Partial assembly changed current content/locations")
        ids[previous.block_id] = block.block_id
        for before, after in zip(
            previous.structure["fragments"], block.structure["fragments"], strict=True
        ):
            for local, old_id in before["id_map"].items():
                ids[old_id] = after["id_map"][local]
        rows.append(
            {
                "original_block_id": previous.block_id,
                "selected_block_id": block.block_id,
                "fragments": fragment_key(block),
            }
        )

    def warning(item, remap):
        data = item.model_dump(mode="json")
        data["target_ids"] = [remap[key] for key in item.target_ids]
        return json.dumps(data, sort_keys=True)

    warnings = [
        item
        for item in full.uncertainties
        if any(location.page in owned for location in item.locations)
    ]
    if Counter(warning(item, ids) for item in warnings) != Counter(
        warning(item, {key: key for key in item.target_ids})
        for item in selected.uncertainties
    ):
        raise ValueError("Partial assembly changed warning multiset")
    return rows


def book(output, workspace):
    from doc_translate.pdf_source import assemble_pdf_source
    from doc_translate.snapshot import load_source_snapshot

    workspace = workspace.absolute()
    if workspace != workspace.resolve():
        raise ValueError("Symlink source path refused")
    output = new_directory(output, workspace)
    before = inventory(workspace)
    write_json(output / "original-inventory.json", before)
    try:
        full = load_source_snapshot(workspace)
        pointer = json.loads((workspace / "source-snapshot.json").read_bytes())
        if pointer["sha256"] != BOOK_SOURCE:
            raise ValueError("Unexpected original source hash")
        selected = assemble_pdf_source(
            workspace=workspace, chunk_sha256s=BOOK_CHUNKS, snapshot_id="prepared-book"
        )
        owned = {page for pages in BOOK_OWNED for page in pages}
        rows = correspond(full, selected, owned)
        payload = output / "book-inputs"
        payload.mkdir()
        images = payload / "images"
        images.mkdir()
        review = output / "review-only"
        review.mkdir()
        write_json(payload / "current-source.json", selected.model_dump(mode="json"))
        references = []
        page_images = {}
        for digest, expected in zip(BOOK_CHUNKS, BOOK_OWNED, strict=True):
            relative = f"corrections/pdf/{digest}.json"
            data = (workspace / relative).read_bytes()
            if sha(data) != digest:
                raise ValueError("Chunk hash changed")
            chunk = json.loads(data)
            if chunk["owned_pages"] != expected:
                raise ValueError("Unexpected owned scope")
            (payload / f"{digest}.json").write_bytes(data)
            references.append(
                {
                    "chunk_sha256": digest,
                    "owned_pages": expected,
                    "reference_pages": chunk["reference_pages"],
                    "original_chunk": str(workspace / relative),
                    "original_draft": str(
                        workspace / f"drafts/pdf/{chunk['draft_sha256']}.json"
                    ),
                    "draft_sha256": chunk["draft_sha256"],
                }
            )
            for page in chunk["page_images"]:
                number = page["page"]
                if number in page_images and page_images[number] != page:
                    raise ValueError("Inconsistent page image")
                page_images[number] = page
        page_rows = []
        for number, page in sorted(page_images.items()):
            data = (workspace / page["image"]["artifact"]).read_bytes()
            if sha(data) != page["image"]["sha256"]:
                raise ValueError("Page hash changed")
            relative = f"book-inputs/images/page-{number}.png"
            (output / relative).write_bytes(data)
            blocks = [
                block.model_dump(mode="json")
                for block in selected.blocks
                if any(
                    location.format == "pdf" and location.page == number
                    for location in block.locations
                )
            ]
            warnings = [
                item.model_dump(mode="json")
                for item in selected.uncertainties
                if any(
                    location.format == "pdf" and location.page == number
                    for location in item.locations
                )
            ]
            write_json(
                payload / f"page-{number}.json",
                {
                    "page": number,
                    "owned": number in owned,
                    "image": relative,
                    "blocks": blocks,
                    "warnings": warnings,
                },
            )
            page_rows.append(
                {
                    "page": number,
                    "owned": number in owned,
                    "image": relative,
                    "current_content": f"book-inputs/page-{number}.json",
                    "root_image_review": "pending",
                    "current_state": None,
                    "recoverability": None,
                    "expected_confirmation": None,
                    "expected_adoption": None,
                }
            )
        write_json(review / "page-review.json", page_rows)
        write_json(output / "correspondence.json", rows)
        write_json(
            output / "references.json",
            {
                "original_workspace": str(workspace),
                "source_sha256": BOOK_SOURCE,
                "scopes": references,
                "connection_group": [BOOK_CHUNKS[2], BOOK_CHUNKS[3]],
                "standalone_loadable_workspace": False,
            },
        )
    finally:
        unchanged = inventory(workspace) == before
        write_json(
            output / "preservation.json", {"original_hash_mtime_unchanged": unchanged}
        )
        if not unchanged:
            raise ValueError("Original workspace changed")
    finish(
        output,
        {
            "mode": "book-read-only",
            "owned_pages": len(owned),
            "images": len(page_images),
            "blocks": len(selected.blocks),
            "warnings": len(selected.uncertainties),
            "warnings_are_not_error_counts": True,
            "real_ai_sends": 0,
            "source_adoption": False,
            "root_labels_fixed": False,
            "cleanup": "no temporary workspace or child created; retained evidence reread",
        },
    )
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("synthetic", "book", "offline"))
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="New directory under a private 0700 evidence parent",
    )
    parser.add_argument(
        "--workspace", type=Path, help="Read-only original book; required only for book"
    )
    parser.add_argument(
        "--input-sha256", help="Fixed preparation hashes.json SHA-256 (offline)"
    )
    parser.add_argument("--revision", help="Exact current git HEAD (offline)")
    parser.add_argument(
        "--cap",
        type=int,
        help="Total before_send reservations across all cases/retries (offline)",
    )
    args = parser.parse_args()
    if (args.mode in {"book", "offline"}) != (args.workspace is not None):
        parser.error("--workspace is required for book/offline only")
    if args.mode == "offline" and any(
        value is None for value in (args.input_sha256, args.revision, args.cap)
    ):
        parser.error("offline requires --input-sha256, --revision and --cap")
    if args.mode != "offline" and any(
        value is not None for value in (args.input_sha256, args.revision, args.cap)
    ):
        parser.error("offline settings are not preparation options")
    if not args.output.absolute().is_relative_to(ROOT / "work"):
        parser.error("Output must be in the repository's ignored work directory")
    os.umask(0o077)
    if args.mode == "offline":
        from pdf_source_offline import offline

        result = offline(
            args.output, args.workspace, args.input_sha256, args.revision, args.cap
        )
        print(f"Offline contract exercise: {result}; real AI quality not evaluated")
    else:
        result = (
            book(args.output, args.workspace)
            if args.mode == "book"
            else synthetic(args.output)
        )
        print(
            f"Prepared without real AI/auth/preflight: {result}; Root image review pending"
        )
    return 0


def private_error(exception_type, _exception, _traceback):
    # Unhandled exception values/tracebacks may contain private source text.
    print(
        f"Experiment stopped ({exception_type.__name__}); retained private evidence is not a success.",
        file=sys.stderr,
    )


if __name__ == "__main__":
    sys.excepthook = private_error
    raise SystemExit(main())
