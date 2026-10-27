"""Real CLI entry with an optional test-only OS replace pause (no workflow mocks)."""

import argparse
import json
import os
import time
from pathlib import Path
from unittest.mock import patch

from doc_translate.cli import main

parser = argparse.ArgumentParser()
parser.add_argument("--helper", required=True, help="JSON process_command array")
parser.add_argument("--boundary")
parser.add_argument("--edge", choices=("before", "after"), default="before")
parser.add_argument("--marker", type=Path)
parser.add_argument("--occurrence", type=int, default=1)
parser.add_argument("arguments", nargs=argparse.REMAINDER)
args = parser.parse_args()
replace = os.replace
matches = 0


def boundary_replace(
    src: str | bytes | os.PathLike[str] | os.PathLike[bytes],
    dst: str | bytes | os.PathLike[str] | os.PathLike[bytes],
    *,
    src_dir_fd: int | None = None,
    dst_dir_fd: int | None = None,
) -> None:
    global matches
    if args.boundary and str(dst).endswith(args.boundary):
        matches += 1
        match = matches == args.occurrence
    else:
        match = False
    if match and args.edge == "before":
        pause()
    replace(src, dst, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd)
    if match and args.edge == "after":
        pause()


def pause():
    assert args.marker is not None
    args.marker.write_text("paused")
    while not args.marker.with_suffix(".release").exists():
        time.sleep(0.01)


with patch.object(os, "replace", boundary_replace):
    main(args.arguments[1:], process_command=json.loads(args.helper))
