"""Command-line entry point; workflow operations are not implemented yet."""

import argparse


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="doc-translate",
        description="Traceable PDF and EPUB translation (operations not implemented yet).",
    )
    parser.add_argument(
        "operation",
        nargs=argparse.REMAINDER,
        help="Not implemented in this foundation.",
    )
    parser.parse_args()
    parser.error("Workflow operations are not implemented yet; use --help.")
