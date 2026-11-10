"""Issue #62 connection only: pin real imports, or generate an original v1 seed."""

import argparse
import json
import runpy
import shutil
import sys
import time
from importlib import import_module
from pathlib import Path

from performance_baseline import ROOT, digest


def imported_sources(source):
    result = {}
    for name in ("preparation", "translation", "render", "run", "snapshot"):
        module = import_module(f"doc_translate.{name}")
        assert module.__file__ is not None
        path = Path(module.__file__).resolve()
        assert path.is_relative_to(source.resolve()), (name, path, source)
        result[name] = {"path": str(path), "sha256": digest(path)}
    return result


def v1_seed(owned, destination, size):
    # Current test launchers require v2 _projection; use their public v1 launch seam.
    from preparation_baseline import seed_source

    from doc_translate.preparation import prepare_document
    from doc_translate.snapshot import save_source_snapshot
    from doc_translate.translation import translate_document
    from doc_translate.units import generate_translation_units

    fixture = import_module("test_preparation")
    workspace = owned / "workspace"
    source = seed_source(size)
    save_source_snapshot(workspace, source)
    units = generate_translation_units(source)
    answers = {
        "collect": [
            {
                "context": f"人工節 {i}。",
                "candidates": [
                    {
                        "term": "queue",
                        "translation": "キュー",
                        "evidence": [
                            {
                                "unit_id": units[2 * i + 1].unit_id,
                                "quote": units[2 * i + 1].source_text,
                            }
                        ],
                    }
                ],
            }
            for i in range(size)
        ],
        "organize": {"context": "キューの人工文書です。", "terms": {"queue": "キュー"}},
    }
    answer = owned / "answers.json"
    answer.write_text(json.dumps(answers))
    node = shutil.which("node")
    assert node
    started = time.perf_counter()
    prepare_document(
        workspace=workspace,
        book="Artificial book",
        policy=fixture.POLICY,
        config=fixture.CONFIG,
        process_command=[
            node,
            str(ROOT / "ai/tests/preparation-process.mjs"),
            str(owned / "credentials"),
            str(answer),
            str(owned / "preparation-requests.jsonl"),
        ],
    )
    translate_document(
        workspace=workspace,
        config=fixture.CONFIG,
        process_command=[
            node,
            str(ROOT / "ai/tests/translation-process.mjs"),
            str(owned / "credentials"),
            str(answer),
            str(owned / "translation-requests.jsonl"),
        ],
    )
    records = [
        json.loads(p.read_bytes()) for p in (workspace / "attempts").rglob("*.json")
    ]
    assert len(records) == 2 * size + 1
    assert all(record.get("wire_version", 1) == 1 for record in records)
    shutil.copytree(workspace, destination)
    return time.perf_counter() - started


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--imports", type=Path, required=True)
    parser.add_argument("--v1-seed", type=Path)
    parser.add_argument("--owned", type=Path)
    parser.add_argument("--size", type=int, choices=(2, 100), default=100)
    parser.add_argument("script", nargs="?")
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    args.imports.write_text(json.dumps(imported_sources(args.source_root), indent=2))
    if args.v1_seed:
        assert args.owned is not None
        print(
            json.dumps({"seed_seconds": v1_seed(args.owned, args.v1_seed, args.size)})
        )
    else:
        assert args.script
        sys.argv = [args.script, *args.arguments]
        runpy.run_path(args.script, run_name="__main__")


if __name__ == "__main__":
    main()
