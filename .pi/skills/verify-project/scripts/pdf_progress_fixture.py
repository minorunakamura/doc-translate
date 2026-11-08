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
        pdf_fixtures.local_models.__wrapped__(args.fixture_dir)
    main(arguments, process_command=json.loads(args.helper))
