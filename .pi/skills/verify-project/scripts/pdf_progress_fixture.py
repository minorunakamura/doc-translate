"""Public CLI entry using the existing self-authored Docling boundary fixtures only."""

import argparse
import json
import sys
from importlib import import_module
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "tests"))

from doc_translate.cli import main

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--fixture-dir", type=Path, required=True)
parser.add_argument("--helper", required=True, help="JSON process_command array")
parser.add_argument("--omitted-prose", action="store_true")
parser.add_argument("arguments", nargs=argparse.REMAINDER)
args = parser.parse_args()
arguments = args.arguments[1:]
with pytest.MonkeyPatch.context() as patch:
    if arguments and arguments[0] == "run":
        args.fixture_dir.mkdir(parents=True, exist_ok=False)
        pdf_fixtures = import_module("test_pdf")
        run_fixtures = import_module("test_run")
        converted = pdf_fixtures.conversion.__wrapped__(patch)
        run_fixtures.pdf_input.__wrapped__(args.fixture_dir, converted, patch)
        if args.omitted_prose:
            from docling_core.types.doc import BoundingBox, DocItemLabel, ProvenanceItem
            from PIL import Image, ImageDraw

            pages = []
            for number in range(1, 10):
                page = Image.new("RGB", (200, 100), "white")
                draw = ImageDraw.Draw(page)
                draw.text((10, 38), f"Page {number} source.", fill="black")
                if number in (2, 7, 8):
                    text = (
                        f"{ {2: 'Pack', 7: 'Seal', 8: 'Return'}[number] } the parcel."
                    )
                    draw.text((10, 60), text, fill="black")
                    converted.document.add_text(
                        label=DocItemLabel.TEXT,
                        text=text,
                        prov=ProvenanceItem(
                            page_no=number,
                            charspan=(0, len(text)),
                            bbox=BoundingBox(
                                l=10, t=60, r=130, b=73, coord_origin="TOPLEFT"
                            ),
                        ),
                    )
                pages.append(page)
            pages[0].save(
                args.fixture_dir / "own.pdf",
                save_all=True,
                append_images=pages[1:],
                resolution=72,
            )
        pdf_fixtures.local_models.__wrapped__(args.fixture_dir)
    main(arguments, process_command=json.loads(args.helper))
